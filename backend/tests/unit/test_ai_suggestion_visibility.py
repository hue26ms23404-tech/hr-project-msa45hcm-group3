from datetime import date

from app.services.ai_suggestions import section_unavailability


def row(**changes):
    value = {"document_status": "PUBLISHED", "version_status": "PUBLISHED",
             "document_role": "EMPLOYEE", "version_role": "EMPLOYEE",
             "effective_from": None, "effective_to": None, "is_answerable": True}
    value.update(changes)
    return value


def test_internal_suggestion_reasons_are_distinct():
    today = date(2026, 10, 13)
    assert section_unavailability([], ["EMPLOYEE"], today) == "DOCUMENT_NOT_FOUND"
    assert section_unavailability([row(document_status="DRAFT")], ["EMPLOYEE"], today) == "NOT_PUBLISHED"
    assert section_unavailability([row(document_role="HR")], ["EMPLOYEE"], today) == "INSUFFICIENT_PERMISSION"
    assert section_unavailability([row(effective_from=date(2026, 11, 1))], ["EMPLOYEE"], today) == "NOT_EFFECTIVE"
    assert section_unavailability([row(is_answerable=False)], ["EMPLOYEE"], today) == "SECTION_NOT_ANSWERABLE"


def test_duplicate_section_codes_are_not_selected_as_canonical_reference():
    from app.services.ai_suggestions import unique_visible_sections
    rows = [
        {"section_code": "LEAVE.ANNUAL", "document_id": 1},
        {"section_code": "LEAVE.ANNUAL", "document_id": 2},
        {"section_code": "ATTENDANCE.FIX", "document_id": 3},
    ]
    visible, ambiguous = unique_visible_sections(rows)
    assert "LEAVE.ANNUAL" not in visible
    assert ambiguous == {"LEAVE.ANNUAL"}
    assert visible["ATTENDANCE.FIX"]["document_id"] == 3


def test_one_visible_section_is_still_linked():
    from app.services.ai_suggestions import unique_visible_sections
    visible, ambiguous = unique_visible_sections([
        {"section_code": "LEAVE.ANNUAL", "document_id": 1},
    ])
    assert not ambiguous
    assert visible["LEAVE.ANNUAL"]["document_id"] == 1
