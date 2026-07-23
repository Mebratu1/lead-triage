# Milestone 2 Testing Guide - Copy & Paste Commands

## Prerequisites

1. Update `.env` with your actual Supabase and OpenAI credentials
2. Run database schema in Supabase (001_init_schema.sql)
3. Start the server: `uvicorn app.main:app --reload`

## Test 1: Health Check

Verify the API is running:

```bash
curl http://127.0.0.1:8000/health
```

**Expected Response:**
```json
{
  "status": "healthy",
  "version": "0.1.0"
}
```

---

## Test 2: Ingest a New Lead

Send a lead for classification:

```bash
curl -X POST http://127.0.0.1:8000/leads/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "email": "alice@techcorp.com",
    "first_name": "Alice",
    "last_name": "Johnson",
    "phone": "+1-555-987-6543",
    "company": "TechCorp Inc",
    "job_title": "VP Engineering"
  }'
```

**What happens:**
1. Validates the input
2. Checks for duplicate email in Supabase (none found)
3. Calls OpenAI API to classify the lead
4. Stores lead + tags in Supabase database
5. Returns 201 Created with lead details

**Expected Response (201 Created):**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "alice@techcorp.com",
  "first_name": "Alice",
  "last_name": "Johnson",
  "phone": "+1-555-987-6543",
  "company": "TechCorp Inc",
  "job_title": "VP Engineering",
  "lead_score": 82,
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

**⚠️ If you get 500 Internal Server Error:**
- Check that OpenAI API key is set in .env
- Check that you have OpenAI credits available
- Check console logs for detailed error message

---

## Test 3: Duplicate Detection

Ingest the SAME email again (should be marked as duplicate):

```bash
curl -X POST http://127.0.0.1:8000/leads/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "email": "alice@techcorp.com",
    "first_name": "Alice",
    "last_name": "Johnson",
    "phone": "+1-555-987-6543",
    "company": "TechCorp Inc",
    "job_title": "VP Engineering"
  }'
```

**Expected Response (201 Created):**
```json
{
  "id": "660f5501-f30c-52e5-b827-557766551111",
  "email": "alice@techcorp.com",
  "first_name": "Alice",
  "last_name": "Johnson",
  "phone": "+1-555-987-6543",
  "company": "TechCorp Inc",
  "job_title": "VP Engineering",
  "lead_score": 0,
  "status": null,
  "tags": [],
  "is_duplicate": true,
  "original_lead_id": "550e8400-e29b-41d4-a716-446655440000",
  "received_at": "2024-01-15T10:35:00Z",
  "classified_at": null,
  "created_at": "2024-01-15T10:35:00Z",
  "updated_at": "2024-01-15T10:35:00Z"
}
```

**Key differences from first request:**
- `"is_duplicate": true` (marked as duplicate)
- `"original_lead_id": "550e8400..."` (linked to original)
- `"status": null` (not classified, just stored)
- `"lead_score": 0` (default value)
- No tags assigned

---

## Test 4: Retrieve a Lead

Get lead details by ID (use the ID from Test 2):

```bash
curl http://127.0.0.1:8000/leads/550e8400-e29b-41d4-a716-446655440000
```

**Expected Response (200 OK):**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "alice@techcorp.com",
  "first_name": "Alice",
  "last_name": "Johnson",
  "phone": "+1-555-987-6543",
  "company": "TechCorp Inc",
  "job_title": "VP Engineering",
  "lead_score": 82,
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

**If you get 404 Not Found:**
- Check that the lead_id is correct
- Verify the lead exists in Supabase: `SELECT * FROM leads;`

---

## Test 5: Error Cases

### Invalid Email

```bash
curl -X POST http://127.0.0.1:8000/leads/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "email": "not-an-email",
    "first_name": "John",
    "last_name": "Doe",
    "phone": "+1-555-1234",
    "company": "Acme",
    "job_title": "CEO"
  }'
```

**Expected Response (400 Bad Request):**
```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "email"],
      "msg": "value is not a valid email address: The email address is not valid",
      "input": "not-an-email"
    }
  ]
}
```

### Missing Required Field

```bash
curl -X POST http://127.0.0.1:8000/leads/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "first_name": "John",
    "last_name": "Doe"
  }'
```

**Expected Response (400 Bad Request):**
```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "email"],
      "msg": "Field required",
      "input": {...}
    },
    {
      "type": "missing",
      "loc": ["body", "company"],
      "msg": "Field required",
      "input": {...}
    }
  ]
}
```

### Non-existent Lead ID

```bash
curl http://127.0.0.1:8000/leads/00000000-0000-0000-0000-000000000000
```

**Expected Response (404 Not Found):**
```json
{
  "detail": "Lead 00000000-0000-0000-0000-000000000000 not found"
}
```

---

## Test 6: View Data in Supabase

Query the database directly:

```bash
# In Supabase SQL Editor: https://app.supabase.com/projects/irxnvbnneczqukseyyoj/sql

# See all leads
SELECT * FROM leads;

# See lead tags
SELECT * FROM lead_tags;

# See duplicate log
SELECT * FROM duplicate_log;

# See a specific lead with tags
SELECT 
  l.id, l.email, l.lead_score, l.status,
  ARRAY_AGG(lt.tag) as tags
FROM leads l
LEFT JOIN lead_tags lt ON l.id = lt.lead_id
GROUP BY l.id
ORDER BY l.created_at DESC;
```

---

## Test 7: API Documentation

Open the interactive API documentation in your browser:

```
http://127.0.0.1:8000/docs
```

This shows:
- All available endpoints
- Request/response schemas
- Try-it-out functionality
- Error codes and descriptions

---

## Server Logs - What to Look For

When running the server, watch for these log entries:

### Successful New Lead Ingestion
```
INFO:app.services.lead_service:Ingesting lead: alice@techcorp.com
DEBUG:app.services.dedup:Checking for duplicate email: alice@techcorp.com (since 2024-01-08)
DEBUG:app.services.dedup:No duplicate email found: alice@techcorp.com
INFO:app.services.lead_service:New lead detected: alice@techcorp.com, classifying...
DEBUG:app.services.classifier:Calling OpenAI API for lead {id}: alice@techcorp.com
INFO:app.services.classifier:Classification successful for alice@techcorp.com: score=82
INFO:app.services.lead_service:Lead classified: alice@techcorp.com -> score=82, status=qualified
INFO:app.services.lead_service:Lead stored: 550e8400-e29b-41d4-a716-446655440000 with score 82
INFO:app.services.lead_service:Stored 2 tags for lead 550e8400-e29b-41d4-a716-446655440000
```

### Successful Duplicate Detection
```
INFO:app.services.lead_service:Ingesting lead: alice@techcorp.com
DEBUG:app.services.dedup:Checking for duplicate email: alice@techcorp.com (since 2024-01-08)
INFO:app.services.dedup:Duplicate email found: alice@techcorp.com -> lead_id: 550e8400-e29b-41d4-a716-446655440000
INFO:app.services.lead_service:Duplicate detected: alice@techcorp.com -> original: 550e8400-e29b-41d4-a716-446655440000
INFO:app.services.lead_service:Lead stored: 660f5501-f30c-52e5-b827-557766551111 with score 0
INFO:app.services.dedup:Duplicate logged: 550e8400-e29b-41d4-a716-446655440000 -> 660f5501-f30c-52e5-b827-557766551111
```

### OpenAI Error
```
ERROR:app.services.classifier:Classification failed for lead {id} (alice@techcorp.com): 401 Unauthorized
ERROR:app.services.lead_service:Classification failed for lead alice@techcorp.com: 401 Unauthorized
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| 500 Internal Server Error | Check .env credentials, see console logs |
| Connection refused | Is server running? Check `uvicorn app.main:app --reload` |
| 404 Lead not found | Check lead_id is correct, verify in Supabase |
| Duplicate always fails | Check dedup_window_days setting in config |
| No tags returned | Check that lead_tags table has data |
| OpenAI 401 error | Verify OPENAI_API_KEY is correct and has credits |
| Supabase connection timeout | Check SUPABASE_URL and internet connection |

---

## Success Checklist

- [ ] `curl http://127.0.0.1:8000/health` returns 200 OK
- [ ] Can POST a new lead and get 201 Created with classification
- [ ] Lead has lead_score, status, and tags
- [ ] Duplicate email returns is_duplicate=true
- [ ] Can GET lead by ID and get all details
- [ ] Database shows records in Supabase
- [ ] No errors in console logs
- [ ] All tests pass with expected responses

---

**Once all tests pass, Milestone 2 is verified complete!**

Next: Confirm everything works and we'll move to Milestone 3 (Authentication & Rate Limiting)
