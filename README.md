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
```

`GET /health/database` performs a read-only Supabase query against the `leads` table. Tests override the database dependency with a mock.

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

1. Deployment/runbook hardening for the classification daemon
2. Metrics and alerting for repeated worker failures, retry backlog, and exhausted attempts
3. Read endpoints or admin views for classified leads
4. CRM integration from classified lead records
5. Hosted deployment configuration
