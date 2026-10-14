"""Prompt interpretation adapter; never reads DB or executes business commands."""
import json
import re
from calendar import monthrange
from datetime import date, timedelta
from typing import Protocol
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, StrictStr
from app.core.config import settings
from app.ai.providers.gemini import ProviderFailure, generate_json, record
from app.schemas.reports import ReportKind, ReportScope
from app.services.knowledge_retrieval import normalize
from app.services.report_catalog import report_catalog


def report_answers(message, today, params=None, missing=()):
    text = normalize(message)
    answers = {}
    if "tong hop luong" in text:
        answers["kind"] = "PAYROLL_SUMMARY"
    elif "phieu luong" in text:
        answers["kind"] = "MY_PAYSLIP"
    elif "headcount" in text or "nhan su hien tai" in text or "nhan su theo phong ban" in text:
        answers["kind"] = "HEADCOUNT"
    elif "cho duyet" in text:
        answers["kind"] = "APPROVAL_QUEUE"
    elif "giai trinh" in text:
        answers["kind"] = "ATTENDANCE_FIX"
    elif "nghi phep" in text or "bao cao phep" in text:
        answers["kind"] = "LEAVE"
    elif "cham cong" in text or "bao cao cong" in text:
        answers["kind"] = "ATTENDANCE"
    if "toan cong ty" in text:
        answers["scope"] = "COMPANY"
    elif "truc tiep" in text or "nhom" in text:
        answers["scope"] = "DIRECT_REPORTS"
    elif "cua toi" in text or "ban than" in text:
        answers["scope"] = "SELF"
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", message)
    if len(dates) == 2:
        answers.update(start_date=dates[0], end_date=dates[1])
    elif len(dates) == 1 and missing:
        field = next((key for key in missing if key in {"start_date", "end_date"}), None)
        if field:
            answers[field] = dates[0]
    else:
        end = None
        if "thang truoc" in text:
            end = today.replace(day=1) - timedelta(days=1)
        elif "thang sau" in text or "thang toi" in text:
            start = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
            end = start.replace(day=monthrange(start.year, start.month)[1])
        elif "thang nay" in text:
            end = today
        else:
            month = re.search(r"\bthang\s+(\d{1,2})[ /-](\d{4})\b", text)
            if month:
                try:
                    year, number = int(month[2]), int(month[1])
                    end = date(year, number, monthrange(year, number)[1])
                except ValueError as exc:
                    raise HTTPException(422, "INVALID_INPUT") from exc
        if end:
            kind = answers.get("kind", (params or {}).get("kind"))
            if kind in {"MY_PAYSLIP", "PAYROLL_SUMMARY"}:
                end = end.replace(day=monthrange(end.year, end.month)[1])
            answers.update(start_date=end.replace(day=1).isoformat(), end_date=end.isoformat())
    return answers


# Only date vocabulary crosses the provider boundary. The business kind and
# permitted scopes are supplied separately from trusted report_catalog().
_TEMPORAL_WORDS = frozenset({
    "tu", "den", "toi", "ngay", "mung", "thang", "nam", "tuan", "quy", "ky",
    "thu", "hai", "ba", "tu", "bon", "nam", "sau", "bay", "chu", "nhat",
    "dau", "tien", "cuoi", "cung", "nay", "truoc", "sau", "lien", "ke",
    "hom", "qua", "mai", "mot", "kia", "cuoi", "dau", "theo",
    "trong", "cua", "va", "hoac", "khoang", "den", "so",
})


def report_temporal_hint(message: str) -> str:
    """Extract temporal tokens only; unrelated free-form HR details are private.

    Stop at the first non-temporal token after the date phrase, rather than
    attempting to blacklist the many possible confidential reasons.
    """
    tokens = re.findall(
        r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/.]\d{1,2}(?:[/.]\d{4})?\b"
        r"|\b[a-z]+\b|\b\d{1,4}\b", normalize(message)
    )
    markers = {"tu", "ngay", "mung", "thang", "tuan", "quy", "thu", "hom", "ky"}
    start = next((i for i, word in enumerate(tokens) if word in markers), None)
    if start is None:
        return ""
    result = []
    for token in tokens[start:start + 30]:
        if token in _TEMPORAL_WORDS or re.fullmatch(
            r"\d{1,4}(?:[-/.]\d{1,4}){0,2}", token
        ):
            result.append(token)
        else:
            break
    return " ".join(result)


class ReportPromptResult(BaseModel):
    """Only template filters may be supplied by the provider."""
    model_config = ConfigDict(extra="forbid")
    kind: ReportKind | None = None
    scope: ReportScope | None = None
    start_date: StrictStr | None = None
    end_date: StrictStr | None = None


class ReportPromptInterpreter(Protocol):
    async def interpret(self, message: str, today: date, user, current: dict | None = None) -> dict: ...


class GeminiReportInterpreter:
    async def interpret(self, message, today, user, current=None):
        local = report_answers(message, today, current)
        text = normalize(message)
        # Common periods, fixed labels and known commands are handled locally.
        if {"start_date", "end_date"}.issubset(local) or text in {"ban than", "nhan vien truc tiep", "toan cong ty"}:
            record("interpret_report_filters", "SKIPPED_DETERMINISTIC")
            return {}
        if local.get("kind") and not re.search(r"\b(tuan|quy|nam truoc|tu ngay|hom qua|hom nay)\b", text):
            record("interpret_report_filters", "SKIPPED_DETERMINISTIC")
            return {}
        if not message.strip():
            record("interpret_report_filters", "SKIPPED_DETERMINISTIC")
            return {}
        date_hint = report_temporal_hint(message)
        if not date_hint:
            record("interpret_report_filters", "SKIPPED_DETERMINISTIC")
            return {}
        try:
            answers = await generate_json("interpret_report_filters",
                json.dumps({"date_hint": date_hint, "today": today.isoformat(),
                            "templates": [{"kind": item["kind"], "scopes": item["scopes"]}
                                          for item in report_catalog(user)["reports"]],
                            "current_filters": {k: v for k, v in (current or {}).items()
                                                if k in {"kind", "scope", "start_date", "end_date"}}}, ensure_ascii=False),
                (
                    "Extract explicitly requested dates from the sanitized date_hint only. "
                    "Use only supplied templates and scopes. Never infer permission or identity. "
                    "Return JSON with optional kind, scope, start_date, end_date (YYYY-MM-DD). "
                    "Omit unspecified or ambiguous filters so the app asks. Resolve relative periods "
                    "using today in Asia/Ho_Chi_Minh. Payroll requires one full calendar month; "
                    "other current-month reports end at today. No SQL, prose or extra fields."
                ), ReportPromptResult, timeout=5, max_tokens=256)
            parsed = {k: v for k, v in answers.model_dump(exclude_none=True).items()
                      if k in {"start_date", "end_date"}}
            for key in ("start_date", "end_date"):
                if key in parsed and date.fromisoformat(parsed[key]).isoformat() != parsed[key]:
                    record("interpret_report_filters", "OUTPUT_REJECTED", category="SCHEMA_VALIDATION_FAILED")
                    return {}
            return parsed
        except (ProviderFailure, ValueError):
            record("interpret_report_filters", "FALLBACK_USED")
            return {}  # Keep deterministic parsing and explicit questions available.
