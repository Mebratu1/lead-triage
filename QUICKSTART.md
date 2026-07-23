# 🚀 Quick Start Guide

## TL;DR - Get Running in 5 Minutes

### 1. Install
```bash
cd lead-triage
pip install -e ".[dev]"
```

### 2. Configure
```bash
cp .env.example .env
# Edit .env with your Supabase + OpenAI credentials
```

### 3. Test
```bash
pytest -m unit -v
```

Expected output:
```
test_health_check_returns_200 PASSED
test_root_endpoint PASSED
test_ingest_lead_requires_email PASSED
test_parse_valid_classification_response PASSED
...
```

### 4. Run
```bash
uvicorn app.main:app --reload
# Open: http://localhost:8000/docs
```

---

## 📁 What Each File Does

### Core Application
| File | Purpose |
|------|---------|
| `app/main.py` | Creates FastAPI app, manages lifecycle |
| `app/config.py` | Loads environment variables, validates with Pydantic |
| `app/db/client.py` | Connects to Supabase, manages connection pool |
| `app/models/lead.py` | Lead domain object (business logic) |
| `app/models/schemas.py` | Pydantic validation for API requests/responses |
| `app/services/lead_service.py` | Orchestrates lead ingestion workflow |
| `app/services/classifier.py` | Calls OpenAI GPT-4 to classify leads |
| `app/services/dedup.py` | Detects duplicate leads |
| `app/routes/health.py` | Health check endpoint |
| `app/routes/leads.py` | Lead ingestion & retrieval endpoints |

### Configuration
| File | Purpose |
|------|---------|
| `pyproject.toml` | Project metadata + dependencies |
| `pytest.ini` | Testing configuration |
| `.env.example` | Template for environment variables |
| `.gitignore` | What Git should ignore |

### Testing
| File | Purpose |
|------|---------|
| `tests/conftest.py` | Reusable test fixtures |
| `tests/test_leads.py` | API endpoint tests |
| `tests/test_classifier.py` | LLM classification logic tests |
| `tests/test_dedup.py` | Deduplication logic tests |

### Database
| File | Purpose |
|------|---------|
| `app/db/migrations/001_init_schema.sql` | Database schema (run in Supabase) |

### Scripts
| File | Purpose |
|------|---------|
| `scripts/setup_db.py` | Database initialization helper |

### Documentation
| File | Purpose |
|------|---------|
| `README.md` | Full project documentation |
| `MILESTONE_1.md` | Detailed architecture decisions |
| `MILESTONE_1_SUMMARY.md` | Overview + checklist |
| `QUICKSTART.md` | This file |

---

## 🔧 Common Commands

### Testing
```bash
pytest                           # Run all tests
pytest -m unit                   # Unit tests only (no external deps)
pytest -m integration            # Integration tests
pytest -k "test_dedup"           # Run specific test
pytest --cov=app                 # Coverage report
pytest -v                        # Verbose output
```

### Development
```bash
uvicorn app.main:app --reload   # Hot reload server
uvicorn app.main:app --port 9000  # Custom port
```

### Database
```bash
python scripts/setup_db.py       # Show migration SQL
```

### Dependencies
```bash
pip install -e ".[dev]"          # Install with dev dependencies
pip install -e "."               # Install production only
pip list                         # Show installed packages
```

---

## 📝 Project Structure at a Glance

```
lead-triage/
├── app/                         # Application code
│   ├── main.py                 # Entry point
│   ├── config.py               # Settings
│   ├── db/                     # Database layer
│   ├── models/                 # Domain models
│   ├── services/               # Business logic
│   └── routes/                 # API endpoints
├── tests/                       # Test suite
├── scripts/                     # Utilities
├── pyproject.toml              # Dependencies
├── pytest.ini                  # Test config
├── .env.example                # Env template
└── README.md                   # Documentation
```

---

## 🎯 What's Working Now (Milestone 1)

✅ **Project structure** - DDD pattern, ready for scaling
✅ **Database schema** - 6 tables + audit trails
✅ **Pydantic models** - Validation + type safety
✅ **Services** - Lead orchestration, classification, dedup
✅ **Health check** - GET /health endpoint
✅ **Tests** - Unit test fixtures + markers
✅ **Configuration** - Environment-based settings

## ⏳ What's Next (Milestone 2)

📋 **Database integration** - Connect to real Supabase
📋 **Lead ingestion** - POST /leads/ingest endpoint
📋 **Deduplication** - Prevent duplicate processing
📋 **LLM classification** - Call OpenAI to score leads
📋 **Integration tests** - End-to-end testing
📋 **Error handling** - Graceful degradation

---

## 🤔 FAQ

### Q: Do I need Supabase?
**A**: Yes. Sign up free at https://supabase.com

### Q: Do I need OpenAI API key?
**A**: Yes for real classification. Milestone 2 will include a mock option for testing.

### Q: Can I run without async?
**A**: The code is async-first. We could refactor, but async is production-ready.

### Q: How do I test without Supabase?
**A**: Use the mock_db fixture in tests/conftest.py. See test_dedup.py for examples.

### Q: What's the database schema?
**A**: See app/db/migrations/001_init_schema.sql. 6 tables with indexes + RLS.

### Q: How do I deploy?
**A**: Milestone 5 covers Vercel deployment. For now, uvicorn works locally.

---

## 📖 Learning Resources

### Concepts Demonstrated

| Concept | File | Why It Matters |
|---------|------|----------------|
| Async/await | `app/main.py`, `app/services/` | Handles 1000s concurrent requests |
| Dependency Injection | `app/routes/leads.py` | Testability, composition |
| Pydantic Validation | `app/models/schemas.py` | Type safety, API docs |
| Service Layer | `app/services/lead_service.py` | Business logic isolation |
| LLM Integration | `app/services/classifier.py` | AI-powered features |
| Database Design | `app/db/migrations/001_init_schema.sql` | Scalability, audit trail |

### Further Reading

- FastAPI Docs: https://fastapi.tiangolo.com
- Pydantic v2: https://docs.pydantic.dev/latest/
- Supabase: https://supabase.com/docs
- OpenAI API: https://platform.openai.com/docs
- pytest: https://docs.pytest.org

---

## 🐛 Troubleshooting

### Import errors?
```bash
pip install -e ".[dev]"
```

### Tests failing?
```bash
# Check environment
python -c "from app.config import settings; print(settings)"

# Run specific test with verbose output
pytest tests/test_leads.py::TestHealthCheck::test_health_check_returns_200 -vv
```

### Can't connect to Supabase?
```bash
# Check credentials in .env
# Verify SUPABASE_URL and SUPABASE_KEY
python -c "from app.db.client import SupabaseClient; import asyncio; asyncio.run(SupabaseClient.get_client())"
```

### LLM parsing failing?
```bash
# Test classifier directly
pytest tests/test_classifier.py -vv
```

---

## 🎓 Next Steps

1. **Read** `README.md` - Full project overview
2. **Study** `MILESTONE_1.md` - Architecture decisions explained
3. **Run** tests to verify everything works
4. **Explore** the code - Start with `app/main.py` → `app/services/` → `app/routes/`
5. **Wait for** Milestone 2 - Database integration

---

## ✨ You're Ready!

Your project is set up and ready to build. All the foundation is in place:

- ✅ Clean architecture
- ✅ Type-safe code
- ✅ Comprehensive tests
- ✅ Production patterns
- ✅ Detailed documentation

**Ready for Milestone 2?** 🚀

Just confirm:
- [ ] Supabase credentials ready?
- [ ] OpenAI API key ready?
- [ ] Tests passing locally?

Then we'll implement the full lead ingestion pipeline!
