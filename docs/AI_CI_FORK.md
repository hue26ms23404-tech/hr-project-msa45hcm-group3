# Fork-only AI Copilot test workflow

This is an experimental, **fork-only** CI workflow. It runs solely on
`hue26ms23404-tech/hr-project-msa45hcm-group3` branch
`research/ai-intent-policy-lazycode-20261016`. It does not deploy or merge code,
and it does not connect to production HR data.

## 1. Offline checks (no API key)

Each push to the branch triggers `.github/workflows/fork-ai-copilot-quality.yml`.
Job **Offline pytest (no Gemini key)** compiles AI files and runs
`python -m pytest -q tests/unit --disable-warnings --maxfail=10`.
Feature flags are forced off. The workflow provisions a disposable PostgreSQL 16 service, runs Alembic migrations, and creates synthetic demo records for unit tests that access the database. No external database credentials or Gemini key are needed.

## 2. Opt in to LIVE Gemini semantic evaluation

**Do not paste your API key into chat, Git commits, issues, workflow YAML, or .env files that get committed.**

1. In the **fork**, open **Settings > Environments > New environment**.
   Name it `ai-research`.
2. In its **Deployment branches and tags** section choose **Selected branches
   and tags**, then allow only
   `research/ai-intent-policy-lazycode-20261016`.
3. Under that environment's **Environment secrets**, add a secret named
   `GEMINI_API_KEY` with the API key for your *test* Gemini project.
   Do not put it in repository variables.
4. In **Settings > Secrets and variables > Actions > Variables** create
   repository variable `AI_GEMINI_MODEL` with the exact model name known to
   work for your test project (from local `GEMINI_MODEL_NAME`).
5. In the same Variables section create `AI_LIVE_TESTS_ENABLED` with value
   `true`. Setting `false` disables future live tests.
6. Tell the assistant **only** "Đã cấu hình CI", without sending secret values.
   A new push to this research branch triggers the opt-in evaluation after
   offline tests pass.

The live job runs `python scripts/ai_semantic_live_eval.py` with
`GEMINI_ENABLED=true`, `GEMINI_SEMANTIC_ENABLED=true`, and
`GEMINI_PUBLIC_SEARCH_ENABLED=false`. It sends only synthetic, fixed prompts
to Gemini, and tests planner output without reading employee tables, sending
personal records, uploading company PDFs, or taking report/leave actions.
It may use API quota. Failures such as 403, 404, quota, and timeout are reported
as configuration/provider failures, not scored as an intent regression.

**Note:** GitHub's `workflow_dispatch` manual trigger requires the workflow to
exist on the repository default branch. We intentionally keep the workflow off
`main`, so a new push to the research branch is the repeatable trigger.

## 3. Local testing (optional)

Create untracked `backend/.env` using your own non-production key:

```dotenv
ENVIRONMENT=development
GEMINI_ENABLED=true
GEMINI_SEMANTIC_ENABLED=true
GEMINI_PUBLIC_SEARCH_ENABLED=false
GEMINI_API_KEY=<put-test-key-here-locally-only>
GEMINI_MODEL_NAME=<your-working-model>
```

The backend loads `backend/.env` after root `.env`. Environment variables
override both. Never commit a filled `.env`; it should remain ignored.
From `backend/` run `python -m pytest -q tests/unit` for offline tests, or
`python scripts/ai_semantic_live_eval.py` for the live semantic benchmark.

## 4. What these jobs do NOT prove

The live evaluation covers a small set of multi-language planner outputs,
including report discovery and a combined policy-and-database question.
It does not prove end-to-end report execution, retrieved policy correctness,
conflict resolution, full conversation memory, or live database permission safety.
Those require separately authorized, isolated integration fixtures and tests.
