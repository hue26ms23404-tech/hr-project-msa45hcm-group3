import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.ai import AIGroundedAnswer, AIChatRequest
from app.services.ai_policy_answer import concise_fallback, grounded_reply, needs_draft_notice
from app.services.knowledge_retrieval import Passage
from app.services.ai_service import AIService


def test_fallback_omits_pdf_cover_and_incomplete_sentences_but_warns_about_draft():
    content = "PEOPLE OPERATIONS / HR POLICY LIBRARY\nDỰ THẢO - CHƯA PHÊ DUYỆT\nMã tài liệu HR-POL-002\n1. Mục đích\nPhân loại các trường hợp nghỉ.\n2. Nghỉ phép năm\nNghỉ phép năm cần được duyệt theo số dư.\n3. Nghỉ ốm\nNhân viên thông báo sớm cho quản lý.\n4. Nghỉ không lương\nKh"
    passage = Passage(1, 'HR-POL-002', content, None)
    warning = needs_draft_notice([{'document_id': 1, 'content': content}], [passage])
    reply = concise_fallback('Chính sách nghỉ phép năm?', [passage], warning)
    assert 'Nghỉ phép năm cần được duyệt theo số dư.' in reply
    assert 'bản dự thảo' in reply
    assert 'PEOPLE OPERATIONS' not in reply and 'Mã tài liệu' not in reply
    assert '\nKh' not in reply and len(reply) < 600


@pytest.mark.parametrize('source,text,evidence', [
    (2, 'Cần được duyệt.', 'Nghỉ phép cần được duyệt.'),
    (1, 'Cần được duyệt.', 'Không tồn tại trong nguồn.'),
    (1, 'Bạn được nghỉ 99 ngày.', 'Nghỉ phép cần được duyệt.'),
])
def test_grounding_rejects_forged_sources_evidence_and_numbers(source, text, evidence):
    answer = AIGroundedAnswer(statements=[{'source': source, 'text': text, 'evidence': evidence}])
    with pytest.raises(ValueError):
        grounded_reply(answer, [Passage(1, 'Nghỉ phép', 'Nghỉ phép cần được duyệt.', None)])


@pytest.mark.asyncio
@pytest.mark.parametrize('question,content,response', [
    ('Chính sách nghỉ phép như thế nào?', 'Nghỉ phép năm cần được quản lý trực tiếp duyệt.', 'Bạn cần được quản lý trực tiếp duyệt trước khi nghỉ phép năm.'),
    ('Quy định đặt phòng họp?', 'Nhân viên đặt phòng họp qua lịch chung trước khi sử dụng.', 'Bạn hãy đặt phòng trên lịch chung trước khi dùng phòng họp.'),
])
async def test_gemini_writes_dynamic_paraphrase_for_existing_or_new_policy(monkeypatch, question, content, response):
    from app.services import ai_service as module
    # Isolate policy generation from the separately tested semantic planner.
    monkeypatch.setattr(module.settings, 'GEMINI_SEMANTIC_ENABLED', False)
    monkeypatch.setattr(module.settings, 'GEMINI_ENABLED', True)
    monkeypatch.setattr(module.settings, 'GEMINI_API_KEY', 'test-key')
    model = AsyncMock(return_value=AIGroundedAnswer(statements=[
        {'source': 1, 'text': response, 'evidence': content}]))
    monkeypatch.setattr(module, 'generate_json', model)
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [{
        'document_id': 1, 'title': 'Chính sách công ty', 'status': 'PUBLISHED', 'content': content,
        'version_id': 2, 'section_id': 3, 'heading': 'Mục liên quan', 'page_start': 1, 'page_end': 1,
    }]))
    result = await AIService.process_chat(db, 7, AIChatRequest(message=question), {'EMPLOYEE'})
    assert result.answer_mode == 'GEMINI' and result.reply == response + ' [1]'
    assert result.sources[0].viewer_path == '/knowledge/view/1?version=2&section=3'
    assert model.call_args.args[3] is AIGroundedAnswer
    payload = json.loads(model.call_args.args[1])
    assert payload['question'] == question and payload['sources'][0]['text'] == content


def test_fallback_quotes_complete_pdf_line_without_period():
    passage = Passage(5, "Chính sách HR",
        "2. Điều kiện\nNhân viên phải được quản lý duyệt trước khi nghỉ phép\n", None)
    reply = concise_fallback("Điều kiện nghỉ phép?", [passage])
    assert "phải được quản lý duyệt" in reply and "[1]" in reply


@pytest.mark.asyncio
async def test_semantic_query_rewrite_retrieves_unseen_paraphrase(monkeypatch):
    from datetime import date
    from app.services import ai_service as module
    from app.services.ai_policy_answer import PolicySearchQueries
    from app.schemas.ai import AIGroundedAnswer
    monkeypatch.setattr(module.settings, "GEMINI_ENABLED", True)
    docs = [{"document_id": 1, "title": "Nội quy", "status": "PUBLISHED",
             "heading": "Chế độ thai sản", "content": "Người lao động được hưởng chế độ thai sản.",
             "is_answerable": True, "section_id": 2, "version_id": 3}]
    monkeypatch.setattr(AIService, "_knowledge_documents", AsyncMock(return_value=docs))
    provider = AsyncMock(side_effect=[
        PolicySearchQueries(queries=["chế độ thai sản"]),
        AIGroundedAnswer(statements=[
            {"source": 1, "text": "Tài liệu có quy định về chế độ thai sản.",
             "evidence": "Người lao động được hưởng chế độ thai sản."}]),
    ])
    monkeypatch.setattr(module, "generate_json", provider)
    response = await AIService._answer_knowledge(None, {"EMPLOYEE"}, date(2026, 10, 16),
                                                  AIChatRequest(message="nghỉ đẻ"),
                                                  policy_question=True)
    assert response.answer_mode == "GEMINI"
    assert response.sources[0].section_id == 2
    assert provider.await_count == 2
    assert provider.await_args_list[0].args[0] == "rewrite_policy_search"
    assert provider.await_args_list[1].args[0] == "ground_policy_answer"
