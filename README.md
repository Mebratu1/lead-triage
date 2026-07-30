# LeadTriage

LeadTriage is a FastAPI backend portfolio project for an AI-assisted lead classification API. The current implementation accepts unstructured lead inquiries, persists them to Supabase, deduplicates repeat submissions, and classifies pending leads through an isolated OpenAI-backed worker.

## Current Status

- FastAPI project scaffold complete
- Health endpoints complete
- Supabase configuration complete
- Database schema complete
- Lead request contract aligned
- Lead persistence connected
- Idempotency processing connected
- AI classification contracts and OpenAI client connected
- Manual classification runner connected
- Autonomous classification daemon connected
- Queue health metrics connected
- Admin lead read API foundation connected
- CRM integrations not yet connected

## Runtime

LeadTriage targets Python `>=3.12,<3.13`.

The repository includes `.python-version` with:

```text
3.12
```

The current workstation may temporarily run tests with Python 3.14.3 until Python 3.12 is installed. Do not claim full Python 3.12 verification until the suite passes under Python 3.12.

## API

### Health

```http
GET /health
GET /health/database
GET /health/queue
```

`GET /health/database` performs a read-only Supabase query against the `leads` table. Tests override the database dependency with a mock.

`GET /health/queue` returns aggregate classification queue counters for monitoring:

```json
{
  "status": "ok",
  "pending_count": 3,
  "backoff_count": 1,
  "exhausted_count": 0,
  "max_attempts": 5
}
```

When `QUEUE_METRICS_TOKEN` is configured, callers must send:

```http
Authorization: Bearer <QUEUE_METRICS_TOKEN>
```

Production requires `QUEUE_METRICS_TOKEN`, so `/health/queue` is protected in deployed environments. The endpoint exposes only aggregate counters and never returns raw lead text, contact details, or lead IDs.

### Lead Inquiry Contract

```http
POST /api/leads
```

Request:

```json
{
  "source": "website",
  "message": "I need emergency plumbing service today. Please call me at 301-555-0144."
}
```

Rules:

- `source` is optional and defaults to `website`
- `source` length must be 2 to 50 characters
- `message` is required
- `message` length must be 10 to 5000 characters
- leading and trailing whitespace is trimmed
- unsupported extra fields are rejected

Created response:

```json
{
  "id": "7d5c90ff-3cb0-4c16-a0fb-6af5e8988d4f",
  "source": "website",
  "created_at": "2026-07-23T12:34:56Z",
  "classification_status": "pending",
  "duplicate": false
}
```

Duplicate response:

```json
{
  "id": "7d5c90ff-3cb0-4c16-a0fb-6af5e8988d4f",
  "source": "website",
  "created_at": "2026-07-23T12:34:56Z",
  "classification_status": "pending",
  "duplicate": true
}
```

The endpoint returns `201 Created` for a newly saved lead and `200 OK` for a duplicate that returns an existing lead. It saves the lead with `classification_status = "pending"` and does not echo the full raw customer message. The API route does not call OpenAI, generate fake classification values, or push to a CRM; classification is handled separately by the worker jobs.

### Admin Lead Read API

```http
GET /api/leads
GET /api/leads/{id}
```

Both read endpoints require:

```http
X-Admin-Token: <QUEUE_METRICS_TOKEN>
```

Supported list query parameters:

- `classification_status`: `pending`, `classified`, or `failed`
- `urgency`: `hot`, `warm`, or `cold`
- `source`: normalized source label
- `start_date`: inclusive `created_at` lower bound
- `end_date`: inclusive `created_at` upper bound
- `limit`: `1` to `100`, default `50`
- `offset`: `0` or greater, default `0`

Example:

```powershell
$headers = @{
    "X-Admin-Token" = $env:QUEUE_METRICS_TOKEN
}

Invoke-RestMethod `
    -Uri "http://127.0.0.1:8000/api/leads?classification_status=classified&limit=25" `
    -Headers $headers
```

Read responses intentionally expose only the admin-safe fields needed for review: contact fields, original message, classification status, urgency, summary, attempt count, and timestamps. They do not return `idempotency_key`, `deduplication_bucket`, service-role keys, classification error internals, worker claim fields, or retry error details.

## Idempotency

The API builds a deterministic idempotency key with SHA-256:

```text
SHA-256(normalized_source + "\n" + normalized_message)
```

Normalization:

- `source`: trim and lowercase
- `message`: trim and collapse whitespace runs to a single space

The message is not lowercased, punctuation is not removed, numbers are not removed, and customer wording is not semantically rewritten. The original cleaned request text is stored in `raw_message`; the normalized message is only used to derive the hash.

Duplicate detection is scoped by `DEDUP_WINDOW_DAYS`. The service derives a UTC fixed-window `deduplication_bucket` and the database enforces uniqueness on `(idempotency_key, deduplication_bucket)`. This avoids permanent deduplication and allows the same normalized source and message in a later configured window. The tradeoff is fixed bucket boundaries: a repeat near a bucket edge can be accepted sooner than a rolling window, but the rule is deterministic and enforceable with a normal unique index.

## Database

Apply migrations in order:

1. `app/db/migrations/001_init_schema.sql` defines the baseline `leads` table shape.
2. `app/db/migrations/002_idempotent_lead_persistence.sql` upgrades older tables and adds windowed idempotency support.
3. `app/db/migrations/003_relax_legacy_lead_required_columns.sql` relaxes required legacy customer fields that intake intentionally does not populate.
4. `app/db/migrations/004_classification_tracking_columns.sql` adds classified output, error, timestamp, and model tracking columns.
5. `app/db/migrations/005_classification_claim_retry.sql` adds worker claim ownership, attempt counts, retry backoff, indexes, and the atomic claim RPC.

- `id`
- `idempotency_key`
- `deduplication_bucket`
- `source`
- `raw_message`
- `customer_name`
- `email`
- `phone`
- `requested_service`
- `urgency`
- `lead_score`
- `ai_summary`
- `classification_status`
- `classification_error`
- `classified_at`
- `classification_model`
- `classification_claimed_at`
- `classification_claimed_by`
- `classification_attempt_count`
- `last_classification_error`
- `next_classification_attempt_at`
- `created_at`

The request field `message` is stored as `raw_message`.

Do not run destructive migrations automatically. Apply the SQL manually in the Supabase SQL Editor when needed.

## Supabase Keys

Server-side database access uses `SUPABASE_SERVICE_ROLE_KEY`.

Do not expose the service-role key to frontend code, API responses, logs, screenshots, source control, or client-side environment variables. The current backend does not require `SUPABASE_KEY` for database access.

## Setup

Install Python 3.12 on Windows:

```powershell
winget install Python.Python.3.12
py -0p
py -3.12 --version
```

Create the virtual environment:

```powershell
cd "C:\Users\mebbr\OneDrive\Documents\lead-triage"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

Copy environment variables:

```powershell
Copy-Item .env.example .env
```

Never commit `.env` or service-role keys.

## Classification Jobs

The API stores new leads as `pending`. Classification is performed out of band so lead intake remains fast and predictable.

Dry-run the manual batch runner without calling OpenAI or writing classification results:

```powershell
.\.venv\Scripts\python.exe -m app.jobs.classify_pending_leads --limit 10 --dry-run
```

Run one manual batch:

```powershell
.\.venv\Scripts\python.exe -m app.jobs.classify_pending_leads --limit 10 --worker-id manual-local-1
```

Run one daemon iteration and exit:

```powershell
.\.venv\Scripts\python.exe -m app.jobs.classification_daemon --run-once --worker-id daemon-local-1
```

Run the long-lived daemon:

```powershell
.\.venv\Scripts\python.exe -m app.jobs.classification_daemon --limit 10 --sleep-seconds 30 --worker-id daemon-local-1
```

The daemon handles `SIGINT` and `SIGTERM` gracefully: an active batch is allowed to finish before the process exits. Standard logs include counts, worker IDs, model names, retry/backoff events, and error types, but never full raw customer messages.

## Docker

The Docker image uses Python 3.12 to match the project runtime constraint in `pyproject.toml`. The same application image is used for both services. `docker-compose.yml` starts:

- `api`: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- `worker`: `python -m app.jobs.classification_daemon --worker-id docker-worker-1`

Both services read environment variables from `.env`. Keep `.env` local only; it contains Supabase and OpenAI secrets and is excluded from Git and Docker build context.

Validate the Compose file without printing resolved secret values:

```powershell
docker compose config --quiet
```

Build the image:

```powershell
docker compose build
```

Start both services:

```powershell
docker compose up -d api worker
```

Verify the API health endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Inspect daemon activity without exposing raw customer messages:

```powershell
docker compose logs --tail 50 worker
```

Stop the stack:

```powershell
docker compose down
```

## Production Readiness

Production startup intentionally fails fast for unsafe configuration. When `ENVIRONMENT=production`, the app rejects:

- `DEBUG=true`
- wildcard, localhost, or `127.0.0.1` CORS origins
- placeholder or short `JWT_SECRET` values
- blank required service credentials

### Production Environment Validation

Generate production secrets locally and paste them into the hosting provider secret manager. Do not paste real secrets into `README.md`, `docker-compose.yml`, `Dockerfile`, screenshots, logs, tickets, or source control.

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Use one generated value for `JWT_SECRET` and a different generated value for `QUEUE_METRICS_TOKEN`.

Required production environment variables:

| Variable | Production expectation |
| --- | --- |
| `ENVIRONMENT` | `production` |
| `APP_ENV` | `production` |
| `DEBUG` | `false` |
| `LOG_LEVEL` | `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | server-only service-role key |
| `OPENAI_API_KEY` | server-only OpenAI API key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` unless using a compatible gateway |
| `OPENAI_MODEL` | known supported model, currently `gpt-4.1-mini` |
| `ALLOWED_ORIGINS` | JSON list of deployed frontend origins only |
| `QUEUE_METRICS_TOKEN` | generated monitoring token with at least 24 characters |
| `JWT_SECRET` | generated secret with at least 32 characters |
| `REQUEST_MAX_BYTES` | request body limit, default `32768` |
| `DEDUP_WINDOW_DAYS` | idempotency bucket window, default `7` |

Example production values, with fake secret placeholders:

```env
ENVIRONMENT=production
APP_ENV=production
DEBUG=false
LOG_LEVEL=INFO
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<server-only-service-role-key>
OPENAI_API_KEY=<server-only-openai-key>
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1-mini
ALLOWED_ORIGINS=["https://your-frontend.example.com"]
QUEUE_METRICS_TOKEN=<generated-monitoring-token>
JWT_SECRET=<generated-jwt-secret>
REQUEST_MAX_BYTES=32768
DEDUP_WINDOW_DAYS=7
```

### Production Database Constraints

Before deploying API or worker containers, apply Supabase migrations `001` through `005` in order and verify these database safeguards exist:

- `raw_message`, `source`, `classification_status`, `idempotency_key`, and `deduplication_bucket` support intake persistence.
- The unique index on `(idempotency_key, deduplication_bucket)` prevents duplicate lead inserts inside the configured deduplication bucket.
- `classification_status` only allows the lifecycle states used by the backend.
- `classification_attempt_count >= 0` is enforced.
- Worker claim columns exist: `classification_claimed_at`, `classification_claimed_by`, `classification_attempt_count`, `last_classification_error`, and `next_classification_attempt_at`.
- The `claim_pending_leads_for_classification` RPC exists and is executable by the server-side role used by the backend.

Live database verification:

```powershell
Invoke-RestMethod https://<api-host>/health/database
```

Expected response:

```json
{
  "status": "ok",
  "database": "connected"
}
```

### Protected Queue Metrics

`GET /health/queue` is intended for monitoring systems, not public dashboards. In production, set `QUEUE_METRICS_TOKEN`; startup validation fails if it is missing or too short.

PowerShell check:

```powershell
$headers = @{
    Authorization = "Bearer $env:QUEUE_METRICS_TOKEN"
}

Invoke-RestMethod `
    -Uri https://<api-host>/health/queue `
    -Headers $headers
```

Expected response shape:

```json
{
  "status": "ok",
  "pending_count": 0,
  "backoff_count": 0,
  "exhausted_count": 0,
  "max_attempts": 5
}
```

Monitoring guidance:

- Alert if `/health` or `/health/database` returns non-2xx.
- Alert if `pending_count` grows continuously while the worker is running.
- Alert if `backoff_count` stays elevated for several worker cycles.
- Alert immediately if `exhausted_count` increases.
- Never log the `Authorization` header or `QUEUE_METRICS_TOKEN`.
- Treat `pending_count`, `backoff_count`, and `exhausted_count` as aggregate operational metrics only; they intentionally do not expose lead IDs or customer text.

### Cloud Deployment Runbook

Use two runtime services from the same Docker image:

| Service | Public | Command |
| --- | --- | --- |
| API | Yes | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| Worker | No | `python -m app.jobs.classification_daemon --worker-id <stable-worker-id> --limit 10 --sleep-seconds 30` |

The API service must expose container port `8000`. The worker should not expose an HTTP port.

#### Render Deployment

Official references: [Docker on Render](https://render.com/docs/docker), [Render web services](https://render.com/docs/web-services), [Render background workers](https://render.com/docs/background-workers), and [Render health checks](https://render.com/docs/health-checks).

1. Push the repository to GitHub, GitLab, or Bitbucket.
2. In Render, create a new Web Service from the repository.
3. Set the runtime/language to Docker and keep the Dockerfile path as `Dockerfile`.
4. Configure the API service:
   - Name: `lead-triage-api`
   - Docker command: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
   - Port: `8000`
   - Health check path: `/health`
   - Environment variables: all required production variables from the table above
5. Create a Background Worker from the same repository and Dockerfile.
6. Configure the worker service:
   - Name: `lead-triage-worker`
   - Docker command: `python -m app.jobs.classification_daemon --worker-id render-worker-1 --limit 10 --sleep-seconds 30`
   - Environment variables: same production variables as the API service
   - Instances: start with `1` worker until queue metrics show sustained backlog
7. Deploy the API first, then the worker.
8. Run smoke checks:

```powershell
Invoke-RestMethod https://<api-host>/health
Invoke-RestMethod https://<api-host>/health/database

$headers = @{ Authorization = "Bearer $env:QUEUE_METRICS_TOKEN" }
Invoke-RestMethod -Uri https://<api-host>/health/queue -Headers $headers
```

9. Submit one test lead to `POST /api/leads`, confirm it returns `201` or `200`, then confirm the worker eventually reduces `pending_count`.

#### Fly.io Deployment Notes

Official references: [Deploy with a Dockerfile](https://fly.io/docs/languages-and-frameworks/dockerfile/) and [fly deploy](https://fly.io/docs/flyctl/deploy/).

- Use the repository `Dockerfile`.
- Store secrets with `fly secrets set`; do not put service-role keys in `fly.toml`.
- Run the API as the public process listening on internal port `8000`.
- Run the worker as a separate process or separate Fly app with the daemon command.
- Set a stable worker ID such as `fly-worker-1`.
- Keep the first deployment to one worker process until queue metrics justify scaling.

#### Railway Deployment Notes

Official references: [Railway variables](https://docs.railway.com/variables) and [Railway environments](https://docs.railway.com/environments).

- Create one service for the API and one service for the worker from the same repository.
- Use the Dockerfile builder for both services.
- Add production variables in the Railway Variables tab for each service.
- Set the API start command to `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- Set the worker start command to `python -m app.jobs.classification_daemon --worker-id railway-worker-1 --limit 10 --sleep-seconds 30`.
- Keep production variables scoped to the production environment.

### Deployment Checklist

1. Apply Supabase migrations `001` through `005` in order.
2. Confirm `SUPABASE_SERVICE_ROLE_KEY` is available only to server containers.
3. Confirm `OPENAI_API_KEY` is available only to server containers.
4. Set `ENVIRONMENT=production`, `APP_ENV=production`, `DEBUG=false`, and explicit `ALLOWED_ORIGINS`.
5. Set `QUEUE_METRICS_TOKEN` and `JWT_SECRET` to generated, distinct values.
6. Run `docker compose config --quiet` locally before deploying.
7. Build and start both API and worker services from the same image.
8. Verify `GET /health`, `GET /health/database`, and protected `GET /health/queue`.
9. Submit one test lead and confirm the worker processes it.
10. Confirm worker logs show batch counts, queue counters, retry/backoff visibility, and no raw customer message text.
11. Monitor repeated daemon errors, retry backlog, exhausted classification attempts, and API health check failures.

Do not use `docker compose config` without `--quiet` in environments with real secrets; the non-quiet command prints resolved environment values.

## Development Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -ra -W default
```

Run locally:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Test the persisted lead contract:

```powershell
$body = @{
    source = "website"
    message = "I need emergency plumbing service today."
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8000/api/leads `
    -ContentType "application/json" `
    -Body $body
```

## Testing Notes

Current API tests are synchronous and use `TestClient`. Database behavior is tested with stateful Supabase fakes so the unit suite does not need live credentials or network access.

`pytest.ini` disables pytest's cache provider only for this local OneDrive-backed repository because cache creation previously hung. Moving the repository outside a synced OneDrive folder may allow normal pytest caching again.

## Later Milestones

Next implementation work should add, in order:

1. Admin dashboard UI for classified lead review
2. CRM integration from classified lead records
3. Alert routing for queue metrics and repeated worker failures
4. Deployment automation with provider-specific infrastructure files
