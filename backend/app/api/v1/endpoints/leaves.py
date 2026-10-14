from datetime import date
from typing import List, Optional, Literal
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_user, require_roles
from app.models.auth import UserAccount
from app.models.organization import Employee, Department
from app.models.leave import LeaveType, LeaveRequest, EmployeeLeaveBalance
from app.models.organization import Employee
from app.schemas.leave import (
    LeaveTypeResponse, LeaveBalanceResponse,
    LeaveRequestCreate, LeaveRequestReview, LeaveRequestResponse
)
from app.services.leave_service import LeaveService
from app.services.ai_access import require_known_role, require_approver_role

router = APIRouter()


@router.get("/types", response_model=List[LeaveTypeResponse])
async def list_leave_types(db: AsyncSession = Depends(get_db)):
    """List all active leave types (auto-ensures standard types exist)"""
    default_types = [
        ("ANNUAL", "Nghỉ phép năm", True),
        ("UNPAID", "Nghỉ không hưởng lương", False),
        ("SICK", "Nghỉ ốm đau", True),
        ("REMOTE", "Làm việc từ xa (Remote / WFH)", True),
    ]
    result = await db.execute(select(LeaveType).where(LeaveType.is_active == True))
    types = list(result.scalars().all())
    existing_codes = {t.leave_code for t in types}
    created = False
    for code, name, is_paid in default_types:
        if code not in existing_codes:
            lt = LeaveType(
                leave_code=code,
                leave_name=name,
                is_paid=is_paid,
                is_active=True
            )
            db.add(lt)
            created = True
            types.append(lt)
    if created:
        await db.commit()
        for t in types:
            await db.refresh(t)
    return types


# Leave Balances
@router.get("/balances/me", response_model=LeaveBalanceResponse)
async def get_my_leave_balance(
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user)
):
    """Get current user's annual leave balance for a given year"""
    target_year = year or date.today().year
    balance = await LeaveService.get_or_create_balance(db, current_user.employee_id, target_year)
    return balance


@router.get("/balances/employees/{employee_id}", response_model=LeaveBalanceResponse)
async def get_employee_leave_balance(
    employee_id: int,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    _ = Depends(require_roles(["HR", "ADMIN", "MANAGER"]))
):
    """HR or Manager views employee's leave balance"""
    target_year = year or date.today().year
    balance = await LeaveService.get_or_create_balance(db, employee_id, target_year)
    return balance


# Leave Requests
@router.post("/requests", response_model=LeaveRequestResponse, status_code=status.HTTP_201_CREATED)
async def submit_leave_request(
    req_in: LeaveRequestCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user)
):
    """Employee submits a leave request (MORNING, AFTERNOON, or FULL_DAY)"""
    leave_req = await LeaveService.create_leave_request(db, current_user.employee_id, req_in)
    await db.commit()
    
    # Reload with relations for response serialization
    stmt = (
        select(LeaveRequest)
        .where(LeaveRequest.leave_request_id == leave_req.leave_request_id)
        .options(
            selectinload(LeaveRequest.leave_type),
            selectinload(LeaveRequest.employee),
            selectinload(LeaveRequest.reviewer)
        )
    )
    reloaded = (await db.execute(stmt)).scalar_one()
    return reloaded


@router.get("/requests", response_model=List[LeaveRequestResponse])
async def list_leave_requests(
    status_filter: Optional[str] = Query(None, alias="status"),
    employee_id: Optional[int] = Query(None),
    view: Optional[Literal["mine", "approvals", "visible"]] = None,
    mine: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user)
):
    """List leave requests (Employee views own, Manager views subordinate's and own)"""
    user_roles = [ra.role.role_code for ra in current_user.role_assignments]
    role_set = set(user_roles)
    require_known_role(role_set)
    stmt = (
        select(LeaveRequest)
        .options(
            selectinload(LeaveRequest.leave_type),
            selectinload(LeaveRequest.employee),
            selectinload(LeaveRequest.reviewer)
        )
        .order_by(LeaveRequest.created_at.desc())
    )

    if mine is True or view == "mine":
        stmt = stmt.where(LeaveRequest.employee_id == current_user.employee_id)
    elif view == "approvals":
        require_approver_role(role_set)
        if status_filter not in (None, "PENDING"):
            raise HTTPException(status_code=422, detail="Approvals view only lists pending requests")
        direct_reports = (
            select(Employee.employee_id)
            .outerjoin(Department, Employee.department_id == Department.department_id)
            .where(
                (Employee.manager_employee_id == current_user.employee_id) |
                (Department.manager_employee_id == current_user.employee_id)
            )
        )
        stmt = stmt.where(
            LeaveRequest.employee_id.in_(direct_reports),
            LeaveRequest.status == "PENDING",
        )
    elif "ADMIN" in user_roles or "HR" in user_roles:
        if employee_id is not None:
            stmt = stmt.where(LeaveRequest.employee_id == employee_id)
    elif "MANAGER" in user_roles:
        direct_reports = (
            select(Employee.employee_id)
            .outerjoin(Department, Employee.department_id == Department.department_id)
            .where(
                (Employee.manager_employee_id == current_user.employee_id) |
                (Department.manager_employee_id == current_user.employee_id)
            )
        )
        if employee_id is not None:
            stmt = stmt.where(
                (LeaveRequest.employee_id == employee_id) &
                (
                    (LeaveRequest.employee_id == current_user.employee_id) |
                    LeaveRequest.employee_id.in_(direct_reports) |
                    (LeaveRequest.reviewer_employee_id == current_user.employee_id)
                )
            )
        else:
            stmt = stmt.where(
                (LeaveRequest.employee_id == current_user.employee_id) |
                LeaveRequest.employee_id.in_(direct_reports) |
                (LeaveRequest.reviewer_employee_id == current_user.employee_id)
            )
    else:
        stmt = stmt.where(LeaveRequest.employee_id == current_user.employee_id)

    if status_filter:
        stmt = stmt.where(LeaveRequest.status == status_filter)

    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/requests/{request_id}/review", response_model=LeaveRequestResponse)
async def review_leave_request(
    request_id: int,
    review_in: LeaveRequestReview,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user)
):
    """Direct Manager or Admin reviews (approve/reject) leave request"""
    user_roles = [ra.role.role_code for ra in current_user.role_assignments]
    is_admin = "ADMIN" in user_roles
    require_approver_role(set(user_roles))

    req = await LeaveService.review_leave_request(
        db, request_id, current_user.employee_id, review_in, is_admin=is_admin
    )
    await db.commit()
    
    stmt = (
        select(LeaveRequest)
        .where(LeaveRequest.leave_request_id == req.leave_request_id)
        .options(
            selectinload(LeaveRequest.leave_type),
            selectinload(LeaveRequest.employee),
            selectinload(LeaveRequest.reviewer)
        )
    )
    return (await db.execute(stmt)).scalar_one()


@router.post("/requests/{request_id}/approve", response_model=LeaveRequestResponse)
async def approve_leave_request(
    request_id: int,
    review_in: LeaveRequestReview,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user)
):
    """Direct Manager approves leave request"""
    user_roles = [ra.role.role_code for ra in current_user.role_assignments]
    is_admin = "ADMIN" in user_roles
    require_approver_role(set(user_roles))

    review_in.status = "APPROVED"
    req = await LeaveService.review_leave_request(
        db, request_id, current_user.employee_id, review_in, is_admin=is_admin
    )
    await db.commit()
    
    stmt = (
        select(LeaveRequest)
        .where(LeaveRequest.leave_request_id == req.leave_request_id)
        .options(
            selectinload(LeaveRequest.leave_type),
            selectinload(LeaveRequest.employee),
            selectinload(LeaveRequest.reviewer)
        )
    )
    return (await db.execute(stmt)).scalar_one()


@router.post("/requests/{request_id}/reject", response_model=LeaveRequestResponse)
async def reject_leave_request(
    request_id: int,
    review_in: LeaveRequestReview,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user)
):
    """Direct Manager rejects leave request"""
    user_roles = [ra.role.role_code for ra in current_user.role_assignments]
    is_admin = "ADMIN" in user_roles
    require_approver_role(set(user_roles))

    review_in.status = "REJECTED"
    req = await LeaveService.review_leave_request(
        db, request_id, current_user.employee_id, review_in, is_admin=is_admin
    )
    await db.commit()
    
    stmt = (
        select(LeaveRequest)
        .where(LeaveRequest.leave_request_id == req.leave_request_id)
        .options(
            selectinload(LeaveRequest.leave_type),
            selectinload(LeaveRequest.employee),
            selectinload(LeaveRequest.reviewer)
        )
    )
    return (await db.execute(stmt)).scalar_one()

