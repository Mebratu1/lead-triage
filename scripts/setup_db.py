"""Database initialization script."""

import asyncio
import sys
from pathlib import Path

from supabase import create_client

from app.config import settings

# Read migration file
MIGRATION_SQL = Path(__file__).parent.parent / "app" / "db" / "migrations" / "001_init_schema.sql"


async def setup_database() -> None:
    """Initialize database schema in Supabase."""
    print("🗄️  Initializing LeadTriage database schema...")

    if not MIGRATION_SQL.exists():
        print(f"❌ Migration file not found: {MIGRATION_SQL}")
        sys.exit(1)

    try:
        # Read SQL migrations
        sql_content = MIGRATION_SQL.read_text()
        print(f"📄 Read migration file: {MIGRATION_SQL}")

        # Create Supabase client (sync)
        supabase = create_client(
            supabase_url=settings.supabase_url,
            supabase_key=settings.supabase_service_role_key,
        )

        # Execute SQL
        # Note: Direct SQL execution requires service role key and proper Supabase setup
        # This example shows the structure; actual implementation depends on Supabase client capabilities
        print("⚠️  Note: Direct SQL execution requires Supabase SQL Editor or API with service role key")
        print("\n📋 SQL Migration Content:")
        print("-" * 80)
        print(sql_content)
        print("-" * 80)
        print("\n✅ Migration file ready for manual execution in Supabase SQL Editor")
        print("📍 Steps:")
        print("   1. Go to: https://app.supabase.com/project/_/sql/new")
        print("   2. Copy the migration file content above")
        print("   3. Execute in Supabase SQL Editor")

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(setup_database())
