import pytest
import secrets
from unittest.mock import patch
from datetime import date, timedelta
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import settings


@pytest.mark.asyncio
async def test_health_check():
    """Verify healthcheck endpoint returns healthy status"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_login_flow():
    """Verify admin login returns JWT access and refresh tokens"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/v1/auth/login", json={
            "login_email": "admin@hrgroup3.com",
            "password": "Password@123"
        })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_qr_card_issue_and_kiosk_scan():
    """Verify issuing a QR card and scanning at Kiosk Web"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        auth_resp = await ac.post("/api/v1/auth/login", json={
            "login_email": "admin@hrgroup3.com",
            "password": "Password@123"
        })
        admin_token = auth_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        card_resp = await ac.post("/api/v1/attendance/employees/3/qr-cards", headers=headers)
        assert card_resp.status_code == 201
        card_data = card_resp.json()
        assert "raw_token" in card_data
        raw_token = card_data["raw_token"]

        kiosk_headers = {"X-Kiosk-Secret": settings.KIOSK_API_SECRET_KEY}
        scan_resp = await ac.post("/api/v1/attendance/scans", json={
            "qr_token": raw_token,
            "event_type": "CHECK_IN",
            "device_id": "KIOSK-GATE-01",
            "idempotency_key": f"TEST-SCAN-{secrets.token_hex(8)}"
        }, headers=kiosk_headers)
        assert scan_resp.status_code == 200
        scan_data = scan_resp.json()
        assert scan_data["status"] == "SUCCESS"
        assert scan_data["event_type"] == "CHECK_IN"


@pytest.mark.asyncio
async def test_kiosk_scan_multiple_times_principles():
    """Verify Principle 1 & 2: 1st scan is CHECK_IN, 2nd & 3rd scans are CHECK_OUT, records latest CHECK_OUT"""
    from datetime import datetime, timezone, timedelta, time
    from app.services.attendance_service import AttendanceService
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import select, delete
    from app.models.attendance import AttendanceDay, AttendanceEvent

    # Clear prior test data for employee 2
    async with AsyncSessionLocal() as db:
        await db.execute(delete(AttendanceEvent).where(AttendanceEvent.employee_id == 2))
        await db.execute(delete(AttendanceDay).where(AttendanceDay.employee_id == 2))
        await db.commit()

    # Test calculate_worked_minutes with UTC datetimes representing 08:30 VN and 17:00 VN
    # 08:30 VN = 01:30 UTC, 17:00 VN = 10:00 UTC
    # Working 08:30 to 17:00 minus 1h lunch (12:00-13:00) = 7.5h = 450 minutes
    t_in_utc = datetime(2026, 10, 12, 1, 30, tzinfo=timezone.utc)
    t_out_utc = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    worked = AttendanceService.calculate_worked_minutes(t_in_utc, t_out_utc)
    assert worked == 450, f"Expected 450 minutes, got {worked}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        auth_resp = await ac.post("/api/v1/auth/login", json={
            "login_email": "admin@hrgroup3.com",
            "password": "Password@123"
        })
        admin_token = auth_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Issue unique card for employee 2
        card_resp = await ac.post("/api/v1/attendance/employees/2/qr-cards", headers=headers)
        assert card_resp.status_code == 201
        raw_token = card_resp.json()["raw_token"]

        kiosk_headers = {"X-Kiosk-Secret": settings.KIOSK_API_SECRET_KEY}

        # Clear any existing attendance events for employee 2 today in test database if any
        # Scan 1
        with patch("app.api.v1.endpoints.attendance.datetime") as mock_dt1, \
             patch("app.services.attendance_service.datetime") as mock_dt2:
            t1 = datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc)
            for m in (mock_dt1, mock_dt2):
                m.now.return_value = t1
                m.combine = datetime.combine
                m.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            res1 = await ac.post("/api/v1/attendance/kiosk/scan", json={
                "qr_code": raw_token,
                "device_id": "KIOSK-01"
            }, headers=kiosk_headers)
            assert res1.status_code == 200
            assert res1.json()["event_type"] == "CHECK_IN"

        # Scan 2: 2026-10-15 05:00 UTC (12:00 VN)
        with patch("app.api.v1.endpoints.attendance.datetime") as mock_dt1, \
             patch("app.services.attendance_service.datetime") as mock_dt2:
            t2 = datetime(2026, 10, 15, 5, 0, tzinfo=timezone.utc)
            for m in (mock_dt1, mock_dt2):
                m.now.return_value = t2
                m.combine = datetime.combine
                m.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            res2 = await ac.post("/api/v1/attendance/kiosk/scan", json={
                "qr_code": raw_token,
                "device_id": "KIOSK-01"
            }, headers=kiosk_headers)
            assert res2.status_code == 200
            assert res2.json()["event_type"] == "CHECK_OUT"

        # Scan 3: 2026-10-15 10:00 UTC (17:00 VN) - Must STILL be CHECK_OUT!
        with patch("app.api.v1.endpoints.attendance.datetime") as mock_dt1, \
             patch("app.services.attendance_service.datetime") as mock_dt2:
            t3 = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
            for m in (mock_dt1, mock_dt2):
                m.now.return_value = t3
                m.combine = datetime.combine
                m.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            res3 = await ac.post("/api/v1/attendance/kiosk/scan", json={
                "qr_code": raw_token,
                "device_id": "KIOSK-01"
            }, headers=kiosk_headers)
            assert res3.status_code == 200
            assert res3.json()["event_type"] == "CHECK_OUT"

        # Check attendance day aggregate
        async with AsyncSessionLocal() as db:
            from datetime import date
            stmt = select(AttendanceDay).where(
                AttendanceDay.employee_id == 2,
                AttendanceDay.work_date == date(2026, 10, 15)
            )
            day = (await db.execute(stmt)).scalar_one_or_none()
            assert day is not None
            assert day.first_check_in_at is not None
            assert day.last_check_out_at is not None
            # Verify last_check_out_at recorded the LAST scan (t3, not t2)
            assert day.last_check_out_at.hour == 10


@pytest.mark.asyncio
async def test_leave_request_and_manager_approval():
    """Verify leave request submission and direct manager review"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Test leave types endpoint
        types_resp = await ac.get("/api/v1/leaves/types")
        assert types_resp.status_code == 200
        types_data = types_resp.json()
        assert len(types_data) > 0
        assert any(t["leave_code"] == "REMOTE" for t in types_data)

        staff_auth = await ac.post("/api/v1/auth/login", json={
            "login_email": "employee@hrgroup3.com",
            "password": "Password@123"
        })
        staff_token = staff_auth.json()["access_token"]

        # Use unique future date
        future_date = (date.today() + timedelta(days=secrets.randbelow(500) + 50)).isoformat()

        leave_resp = await ac.post("/api/v1/leaves/requests", json={
            "leave_type_id": 1,
            "leave_date": future_date,
            "session": "MORNING",
            "reason": "Khám sức khỏe định kỳ"
        }, headers={"Authorization": f"Bearer {staff_token}"})
        assert leave_resp.status_code == 201
        req_id = leave_resp.json()["leave_request_id"]
        assert leave_resp.json()["status"] == "PENDING"
        assert float(leave_resp.json()["leave_days"]) == 0.5

        mgr_auth = await ac.post("/api/v1/auth/login", json={
            "login_email": "manager@hrgroup3.com",
            "password": "Password@123"
        })
        mgr_token = mgr_auth.json()["access_token"]

        # Verify manager can see subordinate's pending request in /leaves/requests?status=PENDING
        pending_resp = await ac.get("/api/v1/leaves/requests?status=PENDING", headers={"Authorization": f"Bearer {mgr_token}"})
        assert pending_resp.status_code == 200
        pending_list = pending_resp.json()
        assert any(r["leave_request_id"] == req_id for r in pending_list)
        found_req = next(r for r in pending_list if r["leave_request_id"] == req_id)
        assert found_req["employee"] is not None
        assert found_req["employee"]["employee_code"] == "EMP003"

        approve_resp = await ac.post(
            f"/api/v1/leaves/requests/{req_id}/approve",
            json={"status": "APPROVED", "review_note": "Đồng ý cho nghỉ"},
            headers={"Authorization": f"Bearer {mgr_token}"}
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_payroll_calculation_and_export():
    """Verify calculating monthly payroll and downloading Excel spreadsheet"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        auth_resp = await ac.post("/api/v1/auth/login", json={
            "login_email": "admin@hrgroup3.com",
            "password": "Password@123"
        })
        admin_token = auth_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Check existing periods
        periods_resp = await ac.get("/api/v1/payroll/periods", headers=headers)
        periods = periods_resp.json()
        if periods:
            period_id = periods[0]["payroll_period_id"]
        else:
            period_resp = await ac.post("/api/v1/payroll/periods", json={
                "period_year": 2026,
                "period_month": 10
            }, headers=headers)
            assert period_resp.status_code == 201
            period_id = period_resp.json()["payroll_period_id"]

        # Calculate payroll
        calc_resp = await ac.post(f"/api/v1/payroll/periods/{period_id}/calculate", headers=headers)
        assert calc_resp.status_code == 200
        assert calc_resp.json()["status"] == "CALCULATED"

        # Check payroll lines
        lines_resp = await ac.get(f"/api/v1/payroll/periods/{period_id}/lines", headers=headers)
        assert lines_resp.status_code == 200
        lines = lines_resp.json()
        assert len(lines) >= 3

        # Unreleased payroll must stay unavailable even to an export request.
        unreleased = await ac.get(f"/api/v1/payroll/periods/{period_id}/export", headers=headers)
        assert unreleased.status_code == 404
        closed = await ac.post(f"/api/v1/payroll/periods/{period_id}/close", headers=headers)
        assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"

        # Export the released Excel spreadsheet.
        export_resp = await ac.get(f"/api/v1/payroll/periods/{period_id}/export", headers=headers)
        assert export_resp.status_code == 200
        assert export_resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert len(export_resp.content) > 1000


@pytest.mark.asyncio
async def test_ai_chat_flow():
    """Verify AI chatbot answers policy and checks leave balance"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        auth_resp = await ac.post("/api/v1/auth/login", json={
            "login_email": "employee@hrgroup3.com",
            "password": "Password@123"
        })
        token = auth_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        chat_resp = await ac.post("/api/v1/ai/chat", json={
            "message": "Tôi còn bao nhiêu ngày phép năm?"
        }, headers=headers)
        assert chat_resp.status_code == 200
        chat_data = chat_resp.json()
        assert "reply" in chat_data
        assert "phép" in chat_data["reply"].lower()


@pytest.mark.asyncio
async def test_google_login_invalid_token():
    """Verify Google login blocks invalid token"""
    settings.GOOGLE_CLIENT_ID = "mock_client_id"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/v1/auth/login/google", json={"id_token": "fake_invalid_token"})
    assert response.status_code == 401
    assert "Invalid Google token" in response.json()["detail"]


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.auth.id_token.verify_oauth2_token")
async def test_google_login_unverified_email(mock_verify):
    """Verify Google login blocks unverified Google email"""
    settings.GOOGLE_CLIENT_ID = "mock_client_id"
    mock_verify.return_value = {"email": "admin@hrgroup3.com", "email_verified": False}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/v1/auth/login/google", json={"id_token": "valid_token_but_unverified_email"})

    assert response.status_code == 400
    assert "Email Google chưa được xác thực" in response.json()["detail"]


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.auth.id_token.verify_oauth2_token")
async def test_google_login_success(mock_verify):
    """Verify Google login succeeds for valid verified employee"""
    mock_verify.return_value = {"email": "admin@hrgroup3.com", "email_verified": True}
    settings.GOOGLE_CLIENT_ID = "mock_client_id"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/v1/auth/login/google", json={"id_token": "valid_token_all_good"})

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
