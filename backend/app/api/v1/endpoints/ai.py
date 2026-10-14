import asyncio
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.auth import UserAccount
from app.schemas.ai import AIChatRequest, AIChatResponse, AISuggestion, AISource
from app.ai.orchestrator import ChatOrchestrator
from app.services.ai_access import allowed_knowledge_roles, user_roles
from app.core.authorization import capabilities, known_roles
from app.services.ai_role_scopes import scoped_ai_access
from app.models.knowledge import KnowledgeDocument, KnowledgeDocumentVersion, KnowledgeSection
from app.services.ai_request_security import secured_operation
from app.services.ai_suggestions import SUGGESTIONS, section_unavailability, suggestion_allowed, unique_visible_sections
from app.services.ai_report_draft_service import suggestion_report_defaults
from app.services.ai_knowledge_tool import AIKnowledgeTool
from app.services.knowledge_retrieval import retrieve

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/suggestions", response_model=list[AISuggestion])
async def get_suggestions(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
    _policy: None = Depends(scoped_ai_access),
):
    """Return only actions permitted by the authenticated role and citations visible to it."""
    roles = known_roles(user_roles(current_user))
    allowed_roles = allowed_knowledge_roles(roles)
    policy_ids = {"leave_policy", "attendance_policy", "report_security"}
    suggestions = [AISuggestion(suggestion_id=key, prompt=value.prompt)
        for key, value in SUGGESTIONS.items()
        if key not in policy_ids and suggestion_allowed(key, roles, current_user.employee_id)]

    # Add policy prompts only when an answerable, currently published PDF is readable.
    citation_specs = [
        ("leave_policy", "Quy định nghỉ phép hằng năm như thế nào?", "LEAVE.ANNUAL"),
        ("attendance_policy", "Tôi cần làm gì để giải trình lượt chấm công?", "ATTENDANCE.FIX"),
        ("report_security", "Ai được xem báo cáo nhân sự?", "REPORT.ACCESS"),
        ("leave_request", None, "LEAVE.REQUEST"),
        ("report_export", None, "REPORT.CATALOG"),
    ]
    references = {}
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    rows = (await db.execute(select(
            KnowledgeDocument.document_id,
            KnowledgeDocumentVersion.version_id,
            KnowledgeDocument.title,
            KnowledgeSection.section_id,
            KnowledgeSection.section_code,
            KnowledgeSection.heading,
            KnowledgeSection.page_start,
            KnowledgeSection.page_end,
        ).join(
            KnowledgeDocumentVersion,
            KnowledgeDocumentVersion.version_id == KnowledgeDocument.current_version_id,
        ).join(
            KnowledgeSection,
            KnowledgeSection.version_id == KnowledgeDocumentVersion.version_id,
        ).where(
            KnowledgeDocument.status == "PUBLISHED",
            KnowledgeDocument.minimum_role.in_(allowed_roles),
            KnowledgeDocumentVersion.status == "PUBLISHED",
            KnowledgeDocumentVersion.minimum_role.in_(allowed_roles),
            KnowledgeDocumentVersion.effective_from.is_(None) | (KnowledgeDocumentVersion.effective_from <= today),
            KnowledgeDocumentVersion.effective_to.is_(None) | (KnowledgeDocumentVersion.effective_to >= today),
            KnowledgeSection.section_code.in_([spec[2] for spec in citation_specs]),
            KnowledgeSection.is_answerable.is_(True),
        ))).mappings().all()
    visible_sections, ambiguous_codes = unique_visible_sections(rows)
    missing_codes = [spec[2] for spec in citation_specs if spec[2] not in visible_sections and spec[2] not in ambiguous_codes]
    diagnostics = {}
    if missing_codes:
        audit_rows = (await db.execute(select(
            KnowledgeSection.section_code,
            KnowledgeDocument.status.label("document_status"),
            KnowledgeDocument.minimum_role.label("document_role"),
            KnowledgeDocumentVersion.status.label("version_status"),
            KnowledgeDocumentVersion.minimum_role.label("version_role"),
            KnowledgeDocumentVersion.effective_from,
            KnowledgeDocumentVersion.effective_to,
            KnowledgeSection.is_answerable,
        ).join(KnowledgeDocumentVersion, KnowledgeSection.version_id == KnowledgeDocumentVersion.version_id)
         .join(KnowledgeDocument, KnowledgeDocument.current_version_id == KnowledgeDocumentVersion.version_id)
         .where(KnowledgeSection.section_code.in_(missing_codes)))).mappings().all()
        diagnostics = {code: section_unavailability([row for row in audit_rows if row["section_code"] == code], allowed_roles, today)
                       for code in missing_codes}
    for suggestion_id, prompt, section_code in citation_specs:
        row = visible_sections.get(section_code)
        logging.getLogger(__name__).info(json.dumps({"event": "ai_suggestion_visibility", "suggestion_id": suggestion_id,
                                                       "reason": "AMBIGUOUS_SOURCE" if section_code in ambiguous_codes else "AVAILABLE" if row else diagnostics[section_code]}))
        if section_code in ambiguous_codes and suggestion_id in policy_ids:
            # The policy is still an allowed question. Cite only after retrieval
            # of the actual answer, not an arbitrary one of several documents.
            suggestions.append(AISuggestion(suggestion_id=suggestion_id, prompt=prompt))
        if row:
            label = f"tham khảo mục {row['section_code']} {row['heading']}, trang {row['page_start']}"
            source = {
                "document_id": row["document_id"], "title": row["title"], "excerpt": "",
                "version_id": row["version_id"], "section_id": row["section_id"],
                "section_code": row["section_code"], "heading": row["heading"],
                "page_start": row["page_start"], "page_end": row["page_end"],
                "viewer_path": f"/knowledge/view/{row['document_id']}?version={row['version_id']}&section={row['section_id']}",
            }
            references[suggestion_id] = AISource.model_validate(source)
            if prompt:
                suggestions.append(AISuggestion(suggestion_id=suggestion_id, prompt=f"{prompt} ({label})", source=source))
    # Demo document codes are optional. Published, readable production sections can
    # support the same prompts without carrying seed-specific identifiers.
    missing = {item[0] for item in citation_specs if item[0] not in references and item[2] not in ambiguous_codes}
    if missing:
        documents = await AIKnowledgeTool.documents(db, roles, today)
        reference_queries = {
            "leave_policy": SUGGESTIONS["leave_policy"].prompt,
            "attendance_policy": SUGGESTIONS["attendance_policy"].prompt,
            "report_security": SUGGESTIONS["report_security"].prompt,
            "leave_request": SUGGESTIONS["draft_leave"].prompt,
            "report_export": SUGGESTIONS["team_attendance_report"].prompt,
        }
        for suggestion_id in missing:
            passage = next(iter(retrieve(reference_queries[suggestion_id], documents)), None)
            if passage is None:
                reason = "DOCUMENT_NOT_FOUND" if not documents else "SECTION_NOT_ANSWERABLE"
                logger.info(json.dumps({"event": "ai_suggestion_visibility", "suggestion_id": suggestion_id, "reason": reason}))
                continue
            source = AISource(
                document_id=passage.document_id, title=passage.title, excerpt="",
                source_url=passage.source_url if passage.version_id is None else None,
                version_id=passage.version_id, section_id=passage.section_id,
                section_code=passage.section_code, heading=passage.heading,
                page_start=passage.page_start, page_end=passage.page_end,
                viewer_path=(f"/knowledge/view/{passage.document_id}?version={passage.version_id}&section={passage.section_id}"
                             if passage.version_id and passage.section_id and passage.page_start else None),
            )
            references[suggestion_id] = source
            if suggestion_id in policy_ids:
                suggestions.append(AISuggestion(suggestion_id=suggestion_id,
                    prompt=SUGGESTIONS[suggestion_id].prompt, source=source))
    for suggestion in suggestions:
        suggestion.default_inputs = suggestion_report_defaults(current_user, SUGGESTIONS[suggestion.suggestion_id].prompt, today)
        if suggestion.source:
            continue
        reference_id = SUGGESTIONS[suggestion.suggestion_id].reference_id
        suggestion.source = references.get(reference_id)
        if suggestion.source:
            source = suggestion.source
            suggestion.prompt += f" (tham khảo mục {source.section_code} {source.heading}, trang {source.page_start})"
    return suggestions


@router.post("/chat", response_model=AIChatResponse)
async def chat_with_hr_ai(
    req: AIChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
    _policy: None = Depends(scoped_ai_access),
):
    """
    AI Assistant chat endpoint.
    Answers company policy, checks attendance and leave balance,
    and supports drafting leave requests or attendance fixes.
    """
    try:
        async with asyncio.timeout(30):
            async with secured_operation(db, current_user.user_account_id, "CHAT", "CHAT") as audit:
                result = await ChatOrchestrator.process_chat(
                    db,
                    current_user.employee_id,
                    req,
                    user_roles(current_user),
                    owner_user_account_id=current_user.user_account_id,
                    authenticated_user=current_user,
                )
                result.request_id = audit["request_id"]
                audit["command"] = result.action.action_type if result.action else result.answer_mode
                audit["scope"] = "SELF" if result.draft_id or result.action and result.action.action_type in {"SHOW_DATA", "DRAFT_LEAVE", "DRAFT_FIX"} else "NONE"
                if result.answer_mode == "OUT_OF_SCOPE":
                    audit["outcome"] = "DENIED" if result.reply.startswith("Bạn không có quyền") else "REJECTED"
                    if audit["outcome"] == "DENIED":
                        raise HTTPException(403, "PERMISSION_DENIED")
                return result
    except (SQLAlchemyError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=503, detail="DATA_SERVICE_UNAVAILABLE") from exc
    except HTTPException as exc:
        # Preserve the expiration state update; get_db rolls back on HTTP errors.
        if exc.status_code == 410 and exc.detail == "AI_DRAFT_EXPIRED":
            await db.commit()
        raise
