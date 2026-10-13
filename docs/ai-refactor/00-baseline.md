# Baseline (2026-10-13)

`origin/main` and local `HEAD` both resolve to `5db8e768f1066d4b6b624ce063633ebad3563edc`. The local tree has unrelated edits in `.env.example`, `README.md`, and several docs; these were preserved. Work is on `fix/ai-chat-stabilization-20261013` because `fix/ai-chat-stabilization` already existed.

## Request path

`AIChatModal` posts `/api/v1/ai/chat` with a JWT, optional suggestion ID, draft ID/revision, and history. `get_current_user` reloads the account and role assignments from HR tables. The endpoint wraps `AIService.process_chat` in a 30-second timeout and an audit operation. `AIService` routes to owner-bound drafts, `AIEmployeeTool`, report drafts/runs, or `AIKnowledgeTool`; Gemini may classify unmatched HR text, interpret a date or report filters, or ground an already permission-filtered policy answer. The response can carry a form action; leave and attendance form submission stays in the HR UI.

Provider calls were directly in `ai_service.py` (date, intent, policy) and `ai_report_interpreter.py` (filters). Disabled/missing key skips date, filters, and policy; intent raises 503. Local regex or published-source retrieval can skip intent. Three broad provider handlers returned `None`, `{}`, or fallback without a failure category.

Roles came from `UserAccount.role_assignments`, but `ai_access.require_known_role` rejected the whole set when one code was unknown. `require_roles` in the HR API grants known assignments and an ADMIN override. The UI also derived suggestions from local role names before asking the server. Suggestions queried five demo document/section pairs individually; each required published status, effective version, allowed minimum role, and answerable section. `AIKnowledgeTool.documents` queries published, role-filtered legacy documents or effective published versions/answerable sections before lexical retrieval. Report draft and run services use the report catalog, owner-bound report runs, and scope checks.

`AIDraftService` creates account-owned drafts with a 15-minute TTL and revision. Reads lock by both ID and owner; updates validate allowed typed fields and increment revision. Chat continuation checks the supplied revision. A personal lookup interruption reads and preserves the draft revision. Date fallback in `_interpret_chat_date` removes non-allowlisted words, losing relationships in compound dates.

## Reproductions and runtime checks

- `tests/unit/test_ai_stabilization_regressions.py` initially failed both cases: the first Monday of next month resolved to `None`; ADMIN plus `EXPERIMENTAL` was denied. Existing `test_ai_service.py`: 92 passed before edits.
- Inspect `AIChatModal` with a failed `/ai/suggestions` request: locally generated suggestions remain, including privileged options inferred from client role labels. Verify after repair with a network failure and retry.
- Disable Gemini or use an invalid key/model in a non-production environment, then submit an unresolved date or report period. Before repair these paths returned indistinguishable `None` or `{}`. Validate new categorized logs and retained fallback.
- Against a dedicated test database, test unpublished and inaccessible sections, report ownership, manager/direct-report filters, and cross-user draft IDs. No production database was queried or changed.
- Cloud Run revision, effective environment, Secret Manager wiring, model access, and actual Gemini generation require deployment credentials and a staging test. They are not established by unit tests.
