"""Lightweight additive migration runner.

`db.init_db()` already applies idempotent CREATE TABLE / ALTER TABLE upgrades.
This script wraps that plus the additive tables, verifies integrity, and takes a
backup first, so a schema change can be applied deliberately instead of on the
next process start.

    python backend/scripts/migrate.py            # backup, migrate, verify
    python backend/scripts/migrate.py --no-backup
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import db, extras_db  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='Apply idempotent database migrations.')
    parser.add_argument('--no-backup', action='store_true', help='skip the pre-migration backup')
    args = parser.parse_args()

    if not args.no_backup:
        try:
            print(f'backup: {extras_db.backup_database()}')
        except FileNotFoundError:
            print('backup: skipped (no existing database)')

    db.init_db()
    extras_db.init_extras_db()
    stats = extras_db.database_stats()
    schema = extras_db.schema_version()
    print('migration complete')
    print(f"  schema version: {schema['current']} of {schema['latest_known']}")
    for entry in schema['applied']:
        print(f"    v{entry['version']} {entry['name']} ({entry['applied_at']})")
    if schema['pending']:
        print(f"  pending: {schema['pending']}")
    for key in ('faculty', 'metrics', 'publications', 'vfstr_authors', 'sync_log'):
        print(f'  {key}: {stats.get(key)}')
    print(f"  integrity: {stats.get('integrity')}")
    if stats.get('integrity') != 'ok':
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
