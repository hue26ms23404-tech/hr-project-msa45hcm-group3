# Deploy React + FastAPI to Cloud Run

Build context must be the repository root, with Dockerfile path `Dockerfile`.
The root Dockerfile builds `frontend/` with npm ci, installs `backend/`
dependencies, and serves both React and /api/v1 from FastAPI on the injected PORT.
No server.ts is required.

## Rebuild the current source

The failed builds fetched commit `fc24f97b688b50a921058a82477794cf479895b6`,
which has no root Dockerfile. Retrying that build reuses the old source.
Run the Cloud Build trigger against the latest `main` commit instead, or create
a new Cloud Run source deployment selecting branch `main`. Confirm FETCHSOURCE
shows the new commit, not fc24f97.

## Runtime configuration

Provide a reachable PostgreSQL database and configure:
- DATABASE_URL: `postgresql+asyncpg://USER:PASSWORD@HOST:5432/DB`
- SYNC_DATABASE_URL: `postgresql+psycopg2://USER:PASSWORD@HOST:5432/DB`
- SECRET_KEY and KIOSK_API_SECRET_KEY: production secrets
- GEMINI_API_KEY: optional, for the AI provider
- GEMINI_ENABLED: set `true` only on revisions intended to call Gemini; default `false`
- GEMINI_MODEL_NAME: verify this exact identifier using the deployed key before use

Use Secret Manager for secrets. Configure Cloud Run connectivity to the database.
The local docker-compose PostgreSQL service is not deployed by this image.
Run `alembic upgrade head` as a separate migration job with the same database
configuration before using the application. Seed demo data only when intended.

Before staging release, compare the intended source commit with the Cloud Build
FETCHSOURCE commit and the Cloud Run revision's container image digest. Inspect
environment variable names and nonsecret values, Secret Manager references,
egress/VPC settings, and database connectivity without printing secret values.
Enabling Gemini can incur usage; do it explicitly in staging. Retain the previous
deployable revision for rollback. Before moving traffic, the migration job must
apply revision `c4d5e6f70819` (via `alembic upgrade head`), which creates
`hr_ai_role_scope_policies`. Chat, suggestions and report analysis query this
table even with Gemini disabled; a missing table causes 503 responses. An empty
table uses default policies, so existing deployments do not need demo data reseeded.
Verify migration completion against the target database before moving traffic.

From `backend/` with the revision's effective configuration:

```bash
python -B -m app.ai.providers.diagnose
python -B -m app.ai.providers.diagnose --live
```

The first command reports safe configuration only. `--live` makes bounded,
potentially billable calls for model access, text generation and structured JSON.
A model lookup alone does not prove generation. Check logs for
`SKIPPED_DETERMINISTIC`, `SKIPPED_DISABLED`, `CALLED_SUCCESS`, `CALLED_FAILED`,
`OUTPUT_REJECTED` and `FALLBACK_USED` on sanitized test requests. Production
revision, model access, database roles and secret wiring are unverified here.

## Verify

- /health returns JSON (process health; it does not test database connectivity).
- / serves React; refreshing /login or /attendance still serves React.
- /api/v1/docs serves Swagger.
- Login and data operations require the configured database and migrations.

Local container check:
```bash
docker build -t hrms .
docker run --rm -p 8080:8080 --env-file backend/.env hrms
```
