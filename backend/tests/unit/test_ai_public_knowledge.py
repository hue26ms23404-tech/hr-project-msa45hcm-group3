from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.schemas.ai import AIGroundedAnswer, AIChatRequest, AIChatResponse, AIExternalSource
from app.services import ai_public_knowledge as module
from app.services.ai_service import AIService


TODAY = date(2026, 10, 15)


@pytest.fixture(autouse=True)
def public_search_enabled(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_PUBLIC_SEARCH_ENABLED", True)


def response(url="https://vanban.chinhphu.vn/law", supported=True, finish="STOP"):
    text = "Điều kiện được quy định trong văn bản hiện hành."
    return NS(text=text, candidates=[NS(finish_reason=finish, grounding_metadata=NS(
        grounding_chunks=[NS(web=NS(uri=url, title="Văn bản Chính phủ"))],
        grounding_supports=[NS(segment=NS(text=text), grounding_chunk_indices=[0])] if supported else [],
        search_entry_point=NS(rendered_content="<div>Google Search</div>"),
    ))])


@pytest.mark.asyncio
async def test_public_answer_uses_grounded_official_sources_and_no_company_context(monkeypatch):
    generate = AsyncMock(return_value=response())
    monkeypatch.setattr(module, "generate_search_answer", generate)
    answer = await module.answer_public_question("chinh sach thai san", TODAY)
    assert answer.message_code == "PUBLIC_POLICY_ANSWER" and answer.sources == []
    assert answer.external_sources[0].url == "https://vanban.chinhphu.vn/law"
    assert "[1]" in answer.reply and "quy định chung" in answer.reply
    assert "sources" not in generate.call_args.args[0] and "2026-10-15" in generate.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    response("https://example.com/law"), response(supported=False), response(finish="MAX_TOKENS"),
])
async def test_no_answer_from_unofficial_unsupported_or_truncated_response(monkeypatch, answer):
    monkeypatch.setattr(module, "generate_search_answer", AsyncMock(return_value=answer))
    assert await module.answer_public_question("quy định lao động", TODAY) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["QUOTA_EXCEEDED", "PROVIDER_TIMEOUT", "NETWORK_ERROR", "SCHEMA_VALIDATION_FAILED"])
async def test_search_failure_has_friendly_reply_and_no_irrelevant_sources(monkeypatch, category):
    from app.ai.providers.gemini import ProviderFailure
    monkeypatch.setattr(module, "generate_search_answer", AsyncMock(side_effect=ProviderFailure(category)))
    answer = await module.answer_public_question("chinh sach thai san", TODAY)
    assert answer.status == "UNAVAILABLE" and answer.message_code == f"PUBLIC_SEARCH_{category}"
    assert "thử lại sau" in answer.reply
    assert not any(word in answer.reply.lower() for word in ("gemini", "google", "hạn mức", "quota", "schema", category.lower()))
    assert not answer.sources and not answer.external_sources


@pytest.mark.parametrize("url", ["http://moj.gov.vn/a", "https://moj.gov.vn.evil.test/a", "https://moj.gov.vn@evil.test/a", "https://moj.gov.vn:8443/a"])
def test_official_url_validation_rejects_untrusted_hosts_and_schemes(url):
    assert not module.official_url(url)


@pytest.mark.asyncio
async def test_unknown_url_is_never_fetched(monkeypatch):
    client = AsyncMock(side_effect=AssertionError("Do not fetch arbitrary grounding URLs"))
    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    assert await module.source_url("https://127.0.0.1/private") is None
    client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("location,expected", [
    ("https://moj.gov.vn/law", "https://moj.gov.vn/law"),
    ("http://127.0.0.1/private", None),
])
async def test_google_redirect_only_accepts_official_destination_without_fetching_it(monkeypatch, location, expected):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = NS(is_redirect=True, headers={"location": location})
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: client)
    url = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/test"
    assert await module.source_url(url) == expected
    client.get.assert_awaited_once_with(url)


def test_public_query_preserves_legal_effective_date_but_removes_phone():
    question = module.public_question("Quy định từ 01-07-2026, số điện thoại 0912345678")
    assert "01-07-2026" in question and "0912345678" not in question


def test_public_policy_question_preserves_reason_topic():
    question = "Tôi muốn biết lý do bị trừ ngày phép"
    assert module.public_question(question) == question


@pytest.mark.asyncio
async def test_empty_internal_grounding_uses_public_answer_and_drops_unrelated_pdf(monkeypatch):
    from app.services import ai_service
    docs = [{"document_id": 1, "title": "Chấm công", "status": "PUBLISHED", "content": "Chính sách chấm công ghi nhận thời gian đi làm."}]
    monkeypatch.setattr(AIService, "_knowledge_documents", AsyncMock(return_value=docs))
    monkeypatch.setattr(ai_service, "generate_json", AsyncMock(return_value=AIGroundedAnswer(statements=[])))
    public = AIChatResponse(reply="public result", external_sources=[AIExternalSource(title="Văn bản", url="https://moj.gov.vn/law")])
    search = AsyncMock(return_value=public)
    monkeypatch.setattr(ai_service, "answer_public_question", search)
    req = AIChatRequest(message="chính sách chấm công và thai sản")
    answer = await AIService._answer_knowledge(None, {"EMPLOYEE"}, TODAY, req, policy_question=True)
    assert answer is public and not answer.sources
    search.assert_awaited_once_with(req.message, TODAY)


@pytest.mark.asyncio
async def test_no_lexical_hit_still_reaches_gemini_public_search(monkeypatch):
    from app.services import ai_service
    monkeypatch.setattr(AIService, "_knowledge_documents", AsyncMock(return_value=[]))
    public = AIChatResponse(reply="public result")
    search = AsyncMock(return_value=public)
    monkeypatch.setattr(ai_service, "answer_public_question", search)
    assert await AIService._answer_knowledge(None, {"EMPLOYEE"}, TODAY, AIChatRequest(message="chinh sach thai san"), policy_question=True) is public
    search.assert_awaited_once()
