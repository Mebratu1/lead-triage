"""Print the database migration for manual Supabase setup."""

import sys
from pathlib import Path

MIGRATION_SQL = Path(__file__).parent.parent / "app" / "db" / "migrations" / "001_init_schema.sql"


def show_database_migration() -> None:
    """Print the SQL migration for manual Supabase execution."""
    if not MIGRATION_SQL.exists():
        print(f"Migration file not found: {MIGRATION_SQL}")
        sys.exit(1)

    try:
        sql_content = MIGRATION_SQL.read_text()
        print(f"Read migration file: {MIGRATION_SQL}")
        print("Run this SQL manually in the Supabase SQL Editor.")
        print("\nSQL Migration Content:")
        print("-" * 80)
        print(sql_content)
        print("-" * 80)
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    show_database_migration()
