"""Create a new, empty shots database. Never change an existing database."""
import argparse
import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


def initialize(database):
    database = Path(database).resolve()
    if database.exists():
        return False
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as conn:
        conn.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path,
                        default=Path(os.environ.get('GGAB_DB_PATH', ROOT / 'ggab.db')))
    args = parser.parse_args()
    created = initialize(args.database)
    print(('Created empty database: ' if created else 'Existing database unchanged: ') + str(args.database.resolve()))


if __name__ == '__main__':
    main()
