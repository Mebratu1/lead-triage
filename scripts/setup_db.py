"""Print database migrations for manual Supabase setup."""

import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent.parent / "app" / "db" / "migrations"


def show_database_migrations() -> None:
    """Print SQL migrations for manual Supabase execution."""
    if not MIGRATIONS_DIR.exists():
        print(f"Migrations directory not found: {MIGRATIONS_DIR}")
        sys.exit(1)

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        print(f"No migration files found in: {MIGRATIONS_DIR}")
        sys.exit(1)

    try:
        print(f"Read migrations directory: {MIGRATIONS_DIR}")
        print("Run these SQL files manually in Supabase SQL Editor order.")
        for migration_file in migration_files:
            print("\n" + "=" * 80)
            print(migration_file.name)
            print("=" * 80)
            print(migration_file.read_text())
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    show_database_migrations()
