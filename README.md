# LeadTriage

LeadTriage is a FastAPI backend portfolio project for an AI-assisted lead classification API. The current implementation accepts unstructured lead inquiries, persists them to Supabase, and deduplicates repeat submissions before later AI classification work is added.

## Current Status

- FastAPI project scaffold complete
- Health endpoints complete
- Supabase configuration complete
- Database schema complete
- Lead request contract aligned
- Lead persistence connected
- Idempotency processing connected
- AI classification not yet connected
- Queue and CRM integrations not yet connected

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

The endpoint returns `201 Created` for a newly saved lead and `200 OK` for a duplicate that returns an existing lead. It saves the lead with `classification_status = "pending"` and does not echo the full raw customer message. It does not call OpenAI, generate fake classification values, enqueue background work, or push to a CRM.

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

The migration in `app/db/migrations/001_init_schema.sql` defines the baseline `leads` table shape. The non-destructive migration in `app/db/migrations/002_idempotent_lead_persistence.sql` upgrades older tables and adds windowed idempotency support. The compatibility migration in `app/db/migrations/003_relax_legacy_lead_required_columns.sql` relaxes required legacy customer fields that Milestone 3A intentionally does not populate.

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

1. Typed classified lead output contracts
2. OpenAI-compatible structured classification
3. Persistence of classified fields
4. Queue or background processing if classification becomes slow
5. Vercel deployment configuration
