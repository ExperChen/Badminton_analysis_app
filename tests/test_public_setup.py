import sqlite3

from tools.init_db import initialize


def test_new_database_is_empty_and_has_shots_schema(tmp_path):
    target = tmp_path / 'new' / 'shots.db'
    assert initialize(target)
    with sqlite3.connect(target) as conn:
        assert conn.execute('SELECT count(*) FROM shots').fetchone()[0] == 0


def test_existing_file_is_not_opened_or_changed(tmp_path):
    target = tmp_path / 'existing.db'
    target.write_bytes(b'existing user file')
    assert not initialize(target)
    assert target.read_bytes() == b'existing user file'
