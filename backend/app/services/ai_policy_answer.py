"""Presentation and evidence checks shared by dynamic policy answers/fallback."""
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictStr
from app.services.knowledge_retrieval import normalize, tokens


class PolicySearchQueries(BaseModel):
    """Semantic search phrases, never an executable policy decision."""
    model_config = ConfigDict(extra="forbid")
    queries: list[Annotated[StrictStr, Field(min_length=2, max_length=120)]] = Field(min_length=1, max_length=3)


DRAFT_NOTICE = "Có nguồn vẫn ghi là bản dự thảo hoặc có nội dung chưa xác nhận. Bạn nên xác nhận với HR trước khi áp dụng."


def needs_draft_notice(documents, passages):
    relevant = {passage.document_id for passage in passages}
    for document in documents:
        if document["document_id"] not in relevant:
            continue
        text = normalize(document.get("content", ""))
        if any(marker in text for marker in ("du thao", "chua phe duyet", "truong can xac nhan", "cho hr/phap che xac nhan")):
            return True
    return False


def _blocks(content):
    headings = list(re.finditer(r"^\s*(?:\d+(?:\.\d+)*[.)]\s+|Điều\s+\d+[.:]\s*)[^\n]+", content, re.MULTILINE | re.IGNORECASE))
    if not headings:
        return [("", content)]
    return [(heading.group(), content[heading.end():headings[index + 1].start() if index + 1 < len(headings) else len(content)])
            for index, heading in enumerate(headings)]


def concise_fallback(question, passages, draft_notice=False):
    query = tokens(question)
    candidates = []
    for source, passage in enumerate(passages, 1):
        for heading, body in _blocks(passage.content):
            # Preserve original source wording. PDF lines often omit periods,
            # so accept complete bounded lines in addition to sentence spans.
            spans = []
            for line in body.splitlines():
                if 24 <= len(line.strip()) <= 400:
                    spans.append(line.strip())
                spans.extend(re.split(r"(?<=[.!?])\s+", line.strip()))
            for span in spans:
                sentence = " ".join(span.split())
                if not 24 <= len(sentence) <= 400:
                    continue
                overlap = query & tokens(sentence)
                if overlap or query & tokens(heading):
                    score = len(overlap) + len(query & tokens(heading))
                    candidates.append((score, source, sentence))
    candidates.sort(key=lambda item: item[0], reverse=True)
    selected, seen = [], set()
    for _, source, sentence in candidates:
        if sentence in seen:
            continue
        seen.add(sentence)
        selected.append(f"{sentence} [{source}]")
        if len(selected) == 3:
            break
    if selected:
        reply = "Phần liên quan đến câu hỏi của bạn:\n\n" + "\n\n".join(selected)
    else:
        reply = "Tôi tìm được tài liệu liên quan. Bạn có thể mở mục nguồn bên dưới để xem nội dung chi tiết."
    return (DRAFT_NOTICE + "\n\n" if draft_notice else "") + reply


def grounded_reply(answer, passages, draft_notice=False):
    """Validate source IDs, verbatim evidence and numerical claims before display.

    Semantic paraphrasing remains model work; evidence checks are not a proof
    that every nuance of a paraphrase is correct.
    """
    selected = []
    for statement in answer.statements:
        if not 1 <= statement.source <= len(passages):
            raise ValueError("Unknown citation")
        evidence = statement.evidence.strip()
        if not evidence or evidence not in passages[statement.source - 1].content:
            raise ValueError("Evidence is outside the authorized passage")
        numbers = lambda value: set(re.findall(r"\d+(?:[.,]\d+)*", value))
        if not numbers(statement.text) <= numbers(evidence):
            raise ValueError("Unsupported numerical claim")
        selected.append(f"{statement.text.strip()} [{statement.source}]")
    if not selected:
        return "Tài liệu hiện có chưa đủ căn cứ để trả lời câu hỏi này. Bạn có thể liên hệ HR để được xác nhận."
    return (DRAFT_NOTICE + "\n\n" if draft_notice else "") + "\n\n".join(selected)
