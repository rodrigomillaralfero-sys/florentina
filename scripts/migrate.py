"""Aplica las migraciones de db/migrations en orden, una vez cada una.

Uso:
    python scripts/migrate.py            # usa DATABASE_URL del .env
    python scripts/migrate.py --dry-run  # solo lista lo que aplicaria
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL no esta definido (ver .env.example)", file=sys.stderr)
        return 2

    files = sorted(p for p in MIGRATIONS.glob("*.sql"))
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("create schema if not exists motor")
        conn.execute(
            "create table if not exists motor.schema_migrations (name text primary key, applied_at timestamptz not null default now())"
        )
        applied = {r[0] for r in conn.execute("select name from motor.schema_migrations").fetchall()}
        for path in files:
            name = path.stem
            if name in applied:
                print(f"= {name} (ya aplicada)")
                continue
            if args.dry_run:
                print(f"? {name} (pendiente)")
                continue
            print(f"+ {name}")
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("insert into motor.schema_migrations (name) values (%s) on conflict do nothing", (name,))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
