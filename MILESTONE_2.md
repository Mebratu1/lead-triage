# Milestone 2: Database Integration & Lead Ingestion

## Overview

Milestone 2 implements the complete database integration layer and lead ingestion endpoints. The API now connects to Supabase PostgreSQL for data persistence and OpenAI for lead classification.

**Status:** ✅ Ready for Testing

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                       HTTP Request                              │
│                    (POST /leads/ingest)                         │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│                   Routes Layer (leads.py)                       │
│              ├─ Request validation (Pydantic)                   │
│              ├─ Dependency injection                            │
│              └─ HTTP response formatting                        │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│              Services Layer (lead_service.py)                   │
│         ├─ Orchestration of dedup, classify, store             │
│         ├─ Error handling & recovery                           │
│         └─ Business logic (e.g., duplicate → error state)      │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
             ┌───────────────┴───────────────┐
             ↓                               ↓
    ┌─────────────────────┐      ┌──────────────────────┐
    │  DeduplicationSvc   │      │ ClassificationSvc    │
    │  (dedup.py)         │      │ (classifier.py)      │
    │                     │      │                      │
    │ ├─check_duplicate   │      │ ├─classify_lead      │
    │ ├─log_duplicate     │      │ └─_parse_response    │
    │ └─Supabase queries  │      └─→ OpenAI API Call   │
    └─────────────────────┘      └──────────────────────┘
             ↓                               ↓
    ┌─────────────────────────────────────────────┐
    │    Data Access Layer (lead_service.py)      │
    │  ├─ _store_lead()      (INSERT)            │
    │  ├─ _store_lead_tags() (INSERT)            │
    │  └─ get_lead()         (SELECT + JOIN)     │
    └───────────┬─────────────────────────────────┘
                ↓
    ┌──────────────────────────────┐
    │   Database Client (client.py) │
    │    Supabase AsyncClient       │
    └───────────┬──────────────────┘
                ↓
    ┌──────────────────────────────┐
    │  Supabase PostgreSQL          │
    │  ├─ leads table              │
    │  ├─ lead_tags table          │
    │  ├─ duplicate_log table      │
    │  └─ Other schema tables      │
    └──────────────────────────────┘
```

## What's Implemented

### 1. Configuration (app/config.py)

**Key Changes:**
- Supabase credentials (URL, anon key, service role key) marked as required
- OpenAI API key and model configuration
- Deduplication window and strategy settings
- Proper Pydantic v2 configuration (model_config with SettingsConfigDict)

**Security:**
- Credentials loaded from environment variables (`.env` file)
- No hardcoded secrets in code

### 2. Database Client (app/db/client.py)

**Features:**
- Singleton pattern for connection pooling
- AsyncClient for non-blocking I/O
- Automatic initialization on first use
- Comprehensive logging for debugging
- Graceful shutdown on application close

**Usage:**
```python
from app.db.client import get_db

# In route/service
async def some_endpoint(db: AsyncClient = Depends(get_db)):
    # Use db for queries
```

### 3. Deduplication Service (app/services/dedup.py)

**Capabilities:**
- Email-based duplicate detection (exact match, case-insensitive)
- Phone-based duplicate detection (optional)
- Configurable deduplication window (default: 7 days)
- Audit logging of detected duplicates

**Logic:**
```
1. Check if email exists in leads table (within dedup window)
2. If found → return existing Lead object
3. If not found → return None (new lead)
4. Log all duplicate detections in duplicate_log table
```

**Database Queries:**
```python
# Check duplicate email
SELECT * FROM leads 
WHERE LOWER(email) = 'john@example.com' 
AND created_at >= (NOW() - 7 days)
LIMIT 1

# Log duplicate
INSERT INTO duplicate_log 
(original_lead_id, duplicate_lead_id, match_type, similarity_score)
VALUES (...)
```

### 4. Classification Service (app/services/classifier.py)

**Features:**
- AsyncOpenAI integration (non-blocking API calls)
- Comprehensive system prompt with qualification rubric
- Robust JSON parsing (handles edge cases in LLM output)
- Validation of all classification fields
- 30-second timeout on API calls
- Temperature 0.3 for deterministic results

**Classification Output:**
```python
{
    "lead_score": 85,        # 0-100, integer
    "status": "qualified",   # qualified | needs_nurture | low_value
    "tags": ["sales_ready", "high_priority"],
    "rationale": "CTO at 500+ person company signals budget authority"
}
```

**Error Handling:**
- Invalid JSON parsed gracefully
- Missing fields detected and reported
- Invalid tags filtered (not rejected entirely)
- Timeouts caught and re-raised

### 5. Lead Service (app/services/lead_service.py)

**Orchestration:**
```
ingest_lead(lead_data, source)
  ├─ Check for duplicate email
  │  ├─ If duplicate → store with is_duplicate=True, original_lead_id=...
  │  └─ If new → proceed to classification
  ├─ Classify lead using OpenAI
  │  ├─ Update lead_score, status, tags, classified_at
  │  └─ If error → store with status=processing_error
  ├─ Store lead in database
  │  └─ INSERT into leads table
  └─ Store tags
     └─ INSERT into lead_tags table (one row per tag)
```

**Database Operations:**
- `_store_lead()`: INSERT with automatic UUID generation
- `_store_lead_tags()`: Bulk insert of tag associations
- `get_lead()`: SELECT with left join to tags

**Datetime Handling:**
- All datetime objects converted to ISO 8601 strings before insert
- Supabase returns ISO strings, automatically converted back to datetime
- Timezone-aware (UTC) to prevent drift issues

### 6. Lead Routes (app/routes/leads.py)

**Endpoints:**

#### POST /leads/ingest
**Purpose:** Submit a new lead for classification

**Request:**
```json
{
  "email": "john@example.com",
  "first_name": "John",
  "last_name": "Doe",
  "phone": "+1-555-123-4567",
  "company": "Acme Corp",
  "job_title": "CTO"
}
```

**Response (201 Created):**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "john@example.com",
  "first_name": "John",
  "last_name": "Doe",
  "phone": "+1-555-123-4567",
  "company": "Acme Corp",
  "job_title": "CTO",
  "lead_score": 85,
  "status": "qualified",
  "tags": ["sales_ready", "high_priority"],
  "is_duplicate": false,
  "original_lead_id": null,
  "received_at": "2024-01-15T10:30:00Z",
  "classified_at": "2024-01-15T10:30:05Z",
  "created_at": "2024-01-15T10:30:05Z",
  "updated_at": "2024-01-15T10:30:05Z"
}
```

**Errors:**
- `400 Bad Request`: Invalid input (missing fields, invalid email)
- `500 Internal Server Error`: Database or LLM error

#### GET /leads/{lead_id}
**Purpose:** Retrieve a classified lead by ID

**Response (200 OK):**
Same as POST /leads/ingest response

**Errors:**
- `404 Not Found`: Lead doesn't exist
- `500 Internal Server Error`: Database error

## Database Schema

See `app/db/migrations/001_init_schema.sql` for full schema. Key tables:

### leads
```sql
CREATE TABLE leads (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT NOT NULL UNIQUE,
  first_name TEXT NOT NULL,
  last_name TEXT NOT NULL,
  phone TEXT,
  company TEXT,
  job_title TEXT,
  lead_score INT CHECK (lead_score >= 0 AND lead_score <= 100),
  status TEXT CHECK (status IN ('qualified', 'needs_nurture', 'low_value', 'processing_error')),
  classification_rationale TEXT,
  is_duplicate BOOLEAN DEFAULT FALSE,
  original_lead_id UUID REFERENCES leads(id),
  source TEXT DEFAULT 'api',
  received_at TIMESTAMP DEFAULT NOW(),
  classified_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);
```

### lead_tags
```sql
CREATE TABLE lead_tags (
  id BIGSERIAL PRIMARY KEY,
  lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
  tag TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);
```

### duplicate_log
```sql
CREATE TABLE duplicate_log (
  id BIGSERIAL PRIMARY KEY,
  original_lead_id UUID REFERENCES leads(id),
  duplicate_lead_id UUID REFERENCES leads(id),
  match_type TEXT,
  similarity_score FLOAT DEFAULT 1.0,
  detected_at TIMESTAMP DEFAULT NOW()
);
```

## Setup & Testing Instructions

### Step 1: Update Environment Variables

Edit `.env` file with your actual credentials:

```bash
# Supabase
SUPABASE_URL=https://irxnvbnneczqukseyyoj.supabase.co
SUPABASE_KEY=<your-anon-key>                              # Get from Supabase Dashboard
SUPABASE_SERVICE_ROLE_KEY=<your-service-role-key>        # Get from Supabase Settings

# OpenAI
OPENAI_API_KEY=your-openai-api-key                        # Your OpenAI API key
OPENAI_MODEL=gpt-4-turbo-preview

# Application
ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=DEBUG  # Set to DEBUG for detailed logging
```

**How to get Supabase keys:**
1. Go to https://app.supabase.com/projects
2. Select your project (irxnvbnneczqukseyyoj)
3. Settings → API Keys
4. Copy "Anon public" and "Service role secret"

### Step 2: Create Database Schema

Run the migration in Supabase SQL Editor:

1. Go to https://app.supabase.com/projects/irxnvbnneczqukseyyoj/sql
2. Create new query
3. Copy entire contents of `app/db/migrations/001_init_schema.sql`
4. Run the query
5. Verify: Check Tables section in SQL Editor to confirm 6 tables created

### Step 3: Start the API Server

```bash
# Activate virtual environment (if not already active)
source venv/bin/activate  # macOS/Linux
# or
venv\Scripts\activate     # Windows

# Start server
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Expected output:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete
INFO:     Supabase client initialized successfully
```

### Step 4: Test Health Endpoint

```bash
curl http://127.0.0.1:8000/health
```

Response:
```json
{
  "status": "healthy",
  "version": "0.1.0"
}
```

### Step 5: Test Lead Ingestion (New Lead)

```bash
curl -X POST http://127.0.0.1:8000/leads/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "email": "alice@techcorp.com",
    "first_name": "Alice",
    "last_name": "Johnson",
    "phone": "+1-555-987-6543",
    "company": "TechCorp",
    "job_title": "VP Engineering"
  }'
```

Expected:
- Status: 201 Created
- Response includes lead_score (e.g., 82), status (e.g., "qualified"), tags
- The lead is now stored in Supabase

### Step 6: Test Duplicate Detection

Ingest the same email again:

```bash
curl -X POST http://127.0.0.1:8000/leads/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "email": "alice@techcorp.com",
    "first_name": "Alice",
    "last_name": "Johnson",
    "phone": "+1-555-987-6543",
    "company": "TechCorp",
    "job_title": "VP Engineering"
  }'
```

Expected:
- Status: 201 Created
- Response includes `"is_duplicate": true`
- Response includes `"original_lead_id": "550e8400-..."`
- New lead stored but linked to original

### Step 7: Retrieve a Lead

```bash
curl http://127.0.0.1:8000/leads/{lead_id}
```

Replace `{lead_id}` with ID from previous response. Expected:
- Status: 200 OK
- Full lead details with tags populated

### Step 8: View Logs in Supabase

Check data in Supabase:
1. Go to SQL Editor
2. Run queries:
   ```sql
   SELECT * FROM leads;
   SELECT * FROM lead_tags;
   SELECT * FROM duplicate_log;
   ```

## Performance Expectations

### Latency per Lead

- Duplicate check: ~50-100ms (Supabase query)
- OpenAI classification: ~2-5 seconds (depends on API load)
- Database store: ~50-100ms (Supabase insert + tag insert)
- **Total time: 2-5+ seconds per lead**

### Capacity

- Supabase Free tier: 500MB database, good for ~50,000 leads
- OpenAI rate limits: Default ~3 requests/minute (may need increase)
- API rate limits: Not yet configured (Milestone 3)

## Common Issues & Solutions

### Issue: "SUPABASE_URL not configured"
**Solution:** Update `.env` file with your actual Supabase URL and ensure .env is in project root

### Issue: "Pydantic validation error: invalid EmailStr"
**Solution:** Install email-validator: `pip install email-validator`

### Issue: "OpenAI API error: 401 Unauthorized"
**Solution:** Verify OPENAI_API_KEY in .env is correct and has credits available

### Issue: "Supabase connection timeout"
**Solution:** 
1. Check internet connection
2. Verify SUPABASE_URL is correct
3. Check Supabase project status in Dashboard

### Issue: "UNIQUE constraint violation on email"
**Solution:** Email already exists in database. Use new email or delete existing record from Supabase

### Issue: "Lead status = 'processing_error'"
**Solution:** Check application logs for classification error. Usually an OpenAI API issue.

## Key Design Decisions

### 1. ISO 8601 Datetime Serialization
**Why:** Supabase expects ISO format strings, not Python datetime objects. Ensures compatibility across time zones.

### 2. UUID Primary Keys
**Why:** Generated by Supabase, prevents conflicts in distributed systems, enables replication.

### 3. Separate lead_tags Table
**Why:** Flexibility - tags can be added/removed without updating leads table. Supports many-to-many relationship.

### 4. Singleton Database Client
**Why:** Connection pooling. Reuses single Supabase client across all requests (more efficient than new connection per request).

### 5. Classification Errors Don't Fail Ingestion
**Why:** If OpenAI fails, lead still stored with status=processing_error. Can retry classification later.

### 6. Deterministic Classification (temperature=0.3)
**Why:** Same lead always gets same classification. Predictable behavior for testing.

## Files Modified in Milestone 2

```
app/
├── config.py              ✅ Fixed Pydantic v2, added credential validation
├── main.py                ✅ Uncommented leads router, added logging
├── db/
│   └── client.py          ✅ Enhanced error handling & logging
├── services/
│   ├── lead_service.py    ✅ Complete refactor for real DB operations
│   ├── classifier.py      ✅ AsyncOpenAI + robust parsing
│   └── dedup.py           ✅ Added comprehensive logging
├── routes/
│   └── leads.py           ✅ Added error handling, logging, docstrings
└── db/migrations/
    └── 001_init_schema.sql ✓ Ready (no changes, already complete)

.env                       ✅ Created with placeholder credentials
```

## Next Steps (Milestone 3)

- Rate limiting (prevent API abuse)
- Authentication (JWT tokens)
- Request caching
- Batch ingestion endpoint
- Advanced filtering/search
- Analytics & reporting

## Verification Checklist

Before marking Milestone 2 complete:

- [ ] `.env` file updated with real Supabase and OpenAI keys
- [ ] Database schema created in Supabase (6 tables visible in SQL Editor)
- [ ] Server starts without errors: `uvicorn app.main:app --reload`
- [ ] Health endpoint responds: `curl http://127.0.0.1:8000/health` → 200 OK
- [ ] Can ingest new lead: `curl -X POST .../leads/ingest` → 201 Created with score
- [ ] Duplicate detection works: Ingest same email twice → second has is_duplicate=true
- [ ] Can retrieve lead: `curl http://127.0.0.1:8000/leads/{id}` → 200 OK with tags
- [ ] Database shows data: Query leads/lead_tags/duplicate_log in Supabase SQL
- [ ] Logs show classifier working: Check console for "OpenAI response", "Classification successful"
- [ ] No errors in console: All errors properly logged and handled

---

**Status:** Ready for user testing. Waiting for confirmation of Supabase schema and endpoint testing.
