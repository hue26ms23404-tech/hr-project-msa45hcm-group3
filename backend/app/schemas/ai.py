from typing import Optional, List, Literal
from datetime import date, time
import re
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator


# Shared bound keeps every generated reply valid in the next chat request.
MAX_AI_REPLY_CHARS = 8000


class AIChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_AI_REPLY_CHARS)


class AIChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(default="", max_length=2000)
    conversation_history: List[AIChatMessage] = Field(default_factory=list, max_length=12)
    # A draft id is only a lookup key. The server must bind it to the authenticated owner.
    draft_id: StrictStr | None = Field(default=None, min_length=36, max_length=36)
    draft_revision: StrictInt | None = Field(default=None, gt=0)
    report_run_id: StrictStr | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    request_key: StrictStr | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    suggestion_id: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    inputs: dict[str, StrictStr | StrictInt] | None = None

    @model_validator(mode="after")
    def validate_operation(self):
        if not self.message.strip() and not self.suggestion_id and not (self.draft_id and self.inputs):
            raise ValueError("AI_MESSAGE_REQUIRED")
        if self.draft_id and self.draft_revision is None:
            raise ValueError("AI_DRAFT_REVISION_REQUIRED")
        if self.suggestion_id and self.draft_id:
            raise ValueError("AI_OPERATION_AMBIGUOUS")
        if self.inputs and not (self.draft_id or self.suggestion_id):
            raise ValueError("AI_DRAFT_REQUIRED")
        if self.draft_revision is not None and not self.draft_id:
            raise ValueError("AI_DRAFT_REQUIRED")
        return self


class AIChatDateResult(BaseModel):
    """Closed date-only provider output; no draft fields or executable actions."""
    model_config = ConfigDict(extra="forbid")
    date: StrictStr | None = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class AIIntentResult(BaseModel):
    """Closed Gemini classification contract; it contains no executable params."""
    model_config = ConfigDict(extra="forbid")
    intent: Literal[
        "LEAVE_BALANCE", "ATTENDANCE_SUMMARY", "DRAFT_LEAVE", "DRAFT_FIX",
        "REPORT_ATTENDANCE", "REPORT_LEAVE", "REPORT_ATTENDANCE_FIX",
        "REPORT_LEAVE_QUEUE", "REPORT_ATTENDANCE_QUEUE", "REPORT_HEADCOUNT",
        "REPORT_PAYSLIP", "REPORT_PAYROLL", "ANALYZE_REPORT", "OPEN_KNOWLEDGE", "POLICY_QUERY", "OUT_OF_SCOPE",
    ]


class AIQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: StrictInt = Field(ge=1)
    text: StrictStr = Field(min_length=1, max_length=10000)


class AIQuotesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quotes: list[AIQuote] = Field(max_length=3)


class AIGroundedStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: StrictInt = Field(ge=1)
    text: StrictStr = Field(min_length=1, max_length=600)
    evidence: StrictStr = Field(min_length=1, max_length=1800)


class AIGroundedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statements: list[AIGroundedStatement] = Field(max_length=3)


class AIChatAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_type: Literal["NAVIGATE", "SHOW_DATA", "DRAFT_LEAVE", "DRAFT_FIX", "OPEN_REPORT", "OPEN_APPROVALS", "OPEN_KNOWLEDGE", "OPEN_SOURCE"]
    data: Optional[dict[str, StrictStr | StrictInt | list[StrictStr] | None]] = None

    @model_validator(mode="after")
    def validate_payload(self):
        """Validate the legacy wire shape by its discriminator, including routes."""
        data = self.data or {}
        fields = {
            "NAVIGATE": ({"path"}, {"path"}),
            "DRAFT_LEAVE": ({"leave_date", "session", "leave_type_id", "reason"}, {"leave_date", "session", "leave_type_id"}),
            "DRAFT_FIX": ({"work_date", "event_type", "requested_time", "reason"}, {"work_date", "event_type"}),
            "OPEN_REPORT": ({"path", "kind", "start_date", "end_date", "department_id", "employee_id", "scope", "run_id"}, {"kind", "start_date", "end_date"}),
            "OPEN_APPROVALS": ({"path", "target", "tab"}, {"path", "target", "tab"}),
            "OPEN_KNOWLEDGE": ({"path"}, {"path"}),
            "OPEN_SOURCE": ({"document_id", "version_id", "section_id"}, {"document_id", "version_id", "section_id"}),
            "SHOW_DATA": ({"type", "balance", "present_days", "incomplete"}, {"type"}),
        }
        allowed, required = fields[self.action_type]
        if set(data) - allowed or not required.issubset(data):
            raise ValueError("AI_ACTION_FIELDS_INVALID")
        for key in ("leave_date", "work_date", "start_date", "end_date"):
            if key in data and (not isinstance(data[key], str) or date.fromisoformat(data[key]).isoformat() != data[key]):
                raise ValueError("AI_ACTION_DATE_INVALID")
        for key in ("leave_type_id", "department_id", "employee_id", "document_id", "version_id", "section_id"):
            if key in data and (type(data[key]) is not int or data[key] <= 0):
                raise ValueError("AI_ACTION_ID_INVALID")
        if "reason" in data and (not isinstance(data["reason"], str) or not 1 <= len(data["reason"].strip()) <= 1000):
            raise ValueError("AI_ACTION_REASON_INVALID")
        if "session" in data and data["session"] not in {"MORNING", "AFTERNOON", "FULL_DAY"}:
            raise ValueError("AI_ACTION_SESSION_INVALID")
        if "event_type" in data and data["event_type"] not in {"CHECK_IN", "CHECK_OUT"}:
            raise ValueError("AI_ACTION_EVENT_INVALID")
        if "requested_time" in data and (not isinstance(data["requested_time"], str) or len(data["requested_time"]) != 8 or time.fromisoformat(data["requested_time"]).isoformat() != data["requested_time"]):
            raise ValueError("AI_ACTION_TIME_INVALID")
        if "path" in data:
            routes = {"NAVIGATE": {"/leaves", "/attendance", "/reports", "/knowledge"}, "OPEN_REPORT": {"/reports"}, "OPEN_KNOWLEDGE": {"/knowledge"}, "OPEN_APPROVALS": {"/leaves", "/attendance"}}
            if data["path"] not in routes.get(self.action_type, set()):
                raise ValueError("AI_ACTION_ROUTE_INVALID")
        if self.action_type == "OPEN_APPROVALS" and (data["tab"] != "approvals" or data["target"] not in {"leave", "attendance"} or data["path"] != ("/leaves" if data["target"] == "leave" else "/attendance")):
            raise ValueError("AI_ACTION_APPROVALS_INVALID")
        if self.action_type == "OPEN_REPORT":
            from app.schemas.reports import ReportRunCreate
            ReportRunCreate.model_validate({key: value for key, value in data.items() if key not in {"path", "run_id"}})
            if "run_id" in data and (not isinstance(data["run_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", data["run_id"])):
                raise ValueError("AI_ACTION_RUN_INVALID")
        if self.action_type == "SHOW_DATA":
            if data["type"] == "LEAVE_BALANCE":
                if set(data) != {"type", "balance"} or data["balance"] is not None and not isinstance(data["balance"], str):
                    raise ValueError("AI_ACTION_DATA_INVALID")
            elif data["type"] == "ATTENDANCE_SUMMARY":
                if set(data) != {"type", "present_days", "incomplete"} or type(data["present_days"]) is not int or data["present_days"] < 0 or not isinstance(data["incomplete"], list):
                    raise ValueError("AI_ACTION_DATA_INVALID")
                for day in data["incomplete"]:
                    date.fromisoformat(day)
            else:
                raise ValueError("AI_ACTION_DATA_INVALID")
        return self


class AISource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: int
    title: str
    excerpt: str
    source_url: str | None = None
    version_id: int | None = None
    section_id: int | None = None
    section_code: str | None = None
    heading: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    viewer_path: str | None = None


class AISuggestion(BaseModel):
    suggestion_id: str
    prompt: str
    source: AISource | None = None
    catalog_version: str = "v1"
    default_inputs: dict[str, StrictStr | StrictInt] = Field(default_factory=dict)


class AIInputOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["kind", "scope", "leave_type_id"]
    value: StrictStr | StrictInt
    label: StrictStr


class AIExternalSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: StrictStr = Field(min_length=1, max_length=300)
    url: StrictStr = Field(pattern=r"^https://", max_length=4096)


class AIChatResponse(BaseModel):
    reply: str = Field(max_length=MAX_AI_REPLY_CHARS)
    action: Optional[AIChatAction] = None
    sources: List[AISource] = Field(default_factory=list)
    external_sources: list[AIExternalSource] = Field(default_factory=list, max_length=8)
    search_suggestions_html: str | None = Field(default=None, max_length=50000)
    answer_mode: Literal["RULE", "RAG", "GEMINI", "OUT_OF_SCOPE"] = "RULE"
    draft_id: StrictStr | None = None
    draft_revision: StrictInt | None = None
    missing_fields: list[str] = Field(default_factory=list)
    input_options: list[AIInputOption] = Field(default_factory=list)
    status: Literal["OK", "NEEDS_INPUT", "DENIED", "UNAVAILABLE", "OUT_OF_SCOPE"] = "OK"
    message_code: str | None = None
    request_id: str | None = None

    @model_validator(mode="after")
    def derive_status(self):
        if self.draft_id and self.status == "OK":
            self.status = "NEEDS_INPUT"
        elif self.answer_mode == "OUT_OF_SCOPE" and self.status == "OK":
            self.status = "OUT_OF_SCOPE"
        return self
