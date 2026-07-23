#!/usr/bin/env python
"""
Milestone 2 Verification Script

Checks that all components are in place and correctly configured.
Run this before starting the server.
"""

import sys
import importlib
from pathlib import Path

def check_module(module_name: str) -> bool:
    """Check if a module can be imported."""
    try:
        importlib.import_module(module_name)
        print(f"[OK] {module_name}")
        return True
    except ImportError as e:
        print(f"[FAIL] {module_name}: {e}")
        return False

def check_file(file_path: str) -> bool:
    """Check if a file exists."""
    if Path(file_path).exists():
        print(f"[OK] {file_path}")
        return True
    else:
        print(f"[FAIL] {file_path} NOT FOUND")
        return False

def main():
    print("=" * 60)
    print("MILESTONE 2 VERIFICATION")
    print("=" * 60)
    
    all_ok = True
    
    # Check Python modules
    print("\n1. Python Modules:")
    print("-" * 40)
    modules = [
        "fastapi",
        "uvicorn",
        "pydantic",
        "pydantic_settings",
        "supabase",
        "openai",
        "email_validator",
        "app.main",
        "app.config",
        "app.db.client",
        "app.services.lead_service",
        "app.services.classifier",
        "app.services.dedup",
        "app.routes.leads",
    ]
    
    for module in modules:
        if not check_module(module):
            all_ok = False
    
    # Check files
    print("\n2. Configuration Files:")
    print("-" * 40)
    files = [
        ".env",
        "pyproject.toml",
        "app/config.py",
        "app/main.py",
        "app/db/client.py",
        "app/db/migrations/001_init_schema.sql",
        "app/services/lead_service.py",
        "app/services/classifier.py",
        "app/services/dedup.py",
        "app/routes/leads.py",
        "MILESTONE_2.md",
    ]
    
    for file_path in files:
        if not check_file(file_path):
            all_ok = False
    
    # Check .env content
    print("\n3. Environment Configuration:")
    print("-" * 40)
    try:
        from app.config import settings
        
        # Check that credentials are loaded
        if settings.supabase_url:
            print(f"[OK] Supabase URL: {settings.supabase_url[:30]}...")
        else:
            print("[FAIL] Supabase URL not configured")
            all_ok = False
            
        if settings.supabase_key and settings.supabase_key != "YOUR_ANON_KEY_HERE":
            print("[OK] Supabase anon key configured")
        else:
            print("[WARN] Supabase anon key NOT SET (will fail at runtime)")
            
        if settings.openai_api_key and settings.openai_api_key != "YOUR_OPENAI_API_KEY_HERE":
            print("[OK] OpenAI API key configured")
        else:
            print("[WARN] OpenAI API key NOT SET (will fail at runtime)")
            
    except Exception as e:
        print(f"✗ Failed to load config: {e}")
        all_ok = False
    
    # Summary
    print("\n" + "=" * 60)
    if all_ok:
        print("STATUS: All checks passed! [OK]")
        print("\nNext steps:")
        print("1. Update .env with your Supabase and OpenAI credentials")
        print("2. Create database schema in Supabase (run 001_init_schema.sql)")
        print("3. Start server: uvicorn app.main:app --reload")
        print("4. Read MILESTONE_2.md for detailed testing instructions")
        return 0
    else:
        print("STATUS: Some checks failed! [FAIL]")
        print("\nPlease fix the issues above before running the server.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
