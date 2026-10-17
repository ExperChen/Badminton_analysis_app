"""Replace the authoritative GGAB CSV and shots in one recoverable import."""
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import accounts

from refresh_shots import backup_database, database_columns, validate_text, write_shots

CSV_PATH = Path(__file__).resolve().parent / "exports" / "ggab-shots-latest.csv"
LOCK = threading.RLock()
MAX_CSV_BYTES = 50_000_000


def stamp():
    return datetime.now(timezone.utc).isoformat()


def tables(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS full_csv_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
    conn.execute("""CREATE TABLE IF NOT EXISTS match_video_links (
        match_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, project_id TEXT NOT NULL)""")


def read_state(conn):
    row = conn.execute("SELECT payload FROM full_csv_state WHERE id=1").fetchone()
    return json.loads(row[0]) if row else {"state": "idle"}


def save_state(conn, state):
    conn.execute("INSERT OR REPLACE INTO full_csv_state VALUES (1,?)", (json.dumps(state),))


def status(database):
    try:
        with closing(sqlite3.connect(Path(database).as_uri() + "?mode=ro", uri=True)) as conn:
            state = read_state(conn)
        return {key: value for key, value in state.items() if key not in ("ticket", "expires", "trigger")}
    except sqlite3.OperationalError:
        return {"state": "idle"}


def aliases(database):
    try:
        with closing(sqlite3.connect(Path(database).as_uri() + "?mode=ro", uri=True)) as conn:
            return dict(conn.execute("SELECT match_id,session_id FROM match_video_links"))
    except sqlite3.OperationalError:
        return {}


def begin(database, remote_id, force=False):
    with LOCK, closing(sqlite3.connect(database, timeout=30)) as conn:
        tables(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        state = read_state(conn)
        now = time.time()
        if state.get("state") == "exporting" and state.get("expires", 0) > now:
            conn.rollback()
            return {"skipped": True}
        # Coalesce finalized projects already included in the latest snapshot.
        # Periodic refresh also picks up corrected annotations in old matches.
        if not force and now - state.get("checked_epoch", 0) < 300 and remote_id in state.get("match_ids", []):
            conn.rollback()
            return {"skipped": True}
        ticket = secrets.token_urlsafe(32)
        state.update(state="exporting", ticket=ticket, expires=now + 300,
                     trigger=remote_id, error=None, started_at=stamp())
        save_state(conn, state)
        conn.commit()
        return {"ticket": ticket}


def check_ticket(state, ticket):
    if (state.get("state") != "exporting" or state.get("expires", 0) < time.time() or
            not secrets.compare_digest(state.get("ticket", ""), ticket)):
        raise ValueError("全量导出任务已过期；保留当前 CSV 和数据")


def fail(database, ticket, error):
    with LOCK, closing(sqlite3.connect(database, timeout=30)) as conn:
        tables(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        state = read_state(conn)
        check_ticket(state, ticket)
        state.update(state="failed", error=str(error)[:500])
        state.pop("ticket", None)
        save_state(conn, state)
        conn.commit()


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".full-csv-", suffix=".tmp", delete=False) as temp:
            temp_path = Path(temp.name)
            temp.write(content)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


def apply_snapshot(database, ticket, content, verified_links):
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_CSV_BYTES:
        raise ValueError("GGAB 全量 CSV 超过大小限制")
    digest = hashlib.sha256(encoded).hexdigest()
    with LOCK, closing(sqlite3.connect(database, timeout=30)) as conn:
        columns = database_columns(conn)
        rows = validate_text(content, columns)
        match_index = columns.index("match_id")
        match_ids = {row[match_index] for row in rows}
        state = read_state(conn)
        check_ticket(state, ticket)
        if state["trigger"] not in match_ids:
            raise ValueError("全量 CSV 尚未包含刚完成的比赛，保留旧数据等待下次导出")
        mappings = sorted((remote, value["session_id"], value["project_id"])
                          for remote, value in verified_links.items() if remote in match_ids)
        existing_mappings = sorted(conn.execute("SELECT match_id,session_id,project_id FROM match_video_links"))
        names = ",".join('"' + name + '"' for name in columns)
        current_rows = conn.execute(f"SELECT {names} FROM shots ORDER BY shot_id").fetchall()
        shot_index = columns.index("shot_id")
        missing = sorted({r[match_index] for r in current_rows} - match_ids)
        if accounts.ENFORCE_AUTH:
            # Absence from one download is not proof of remote deletion.
            rows.extend(r for r in current_rows if r[match_index] not in match_ids)
            if len({r[shot_index] for r in rows}) != len(rows):
                raise ValueError("新旧比赛逐拍 ID 冲突；保留旧快照待核查")
            retained = [tuple(r) for r in existing_mappings if r[0] not in match_ids]
            mappings = sorted(mappings + retained)
            for remote, value in verified_links.items():
                if remote in match_ids:
                    accounts.bind_match(remote, value["project_id"])
        changed = state.get("sha256") != digest or current_rows != sorted(rows, key=lambda row: row[shot_index])
        mapping_changed = mappings != existing_mappings
        previous_path = Path(state.get("path", CSV_PATH)) if accounts.ENFORCE_AUTH else CSV_PATH
        old_csv = previous_path.read_bytes() if previous_path.is_file() else None
        output_path = CSV_PATH.parent / "snapshots" / f"{digest}.csv" if accounts.ENFORCE_AUTH else CSV_PATH
        backup = state.get("backup")
        csv_backup = state.get("csv_backup")
        if changed:
            backup = str(backup_database(conn, Path(database)))
            if old_csv is not None:
                csv_backup_path = Path(backup).with_suffix(".csv")
                shutil.copy2(previous_path, csv_backup_path)
                csv_backup = str(csv_backup_path)
        conn.execute("BEGIN IMMEDIATE")
        # Recheck inside the write transaction; late workers may not publish.
        check_ticket(read_state(conn), ticket)
        file_written = False
        try:
            if changed:
                write_shots(conn, columns, rows)
            conn.execute("DELETE FROM match_video_links")
            conn.executemany("INSERT INTO match_video_links VALUES (?,?,?)", mappings)
            now = stamp()
            state.update(state="complete", sha256=digest, path=str(output_path), error=None,
                         rows=len(rows), matches=len(match_ids), match_ids=sorted(match_ids),
                         checked_at=now, checked_epoch=time.time(), backup=backup, csv_backup=csv_backup)
            state["missing_match_ids"] = missing
            if changed or mapping_changed or not state.get("revision"):
                state.update(imported_at=now, revision=secrets.token_hex(16))
            state.pop("ticket", None)
            save_state(conn, state)
            if old_csv != encoded:
                atomic_write(output_path, encoded)
                file_written = True
            conn.commit()
        except Exception:
            conn.rollback()
            if file_written and not accounts.ENFORCE_AUTH:
                if old_csv is None:
                    CSV_PATH.unlink(missing_ok=True)
                else:
                    atomic_write(CSV_PATH, old_csv)
            raise
        return {"changed": changed, "rows": len(rows), "matches": len(match_ids),
                "sha256": digest, "path": str(output_path), "backup": backup,
                "imported_at": state["imported_at"], "revision": state["revision"],
                "match_ids": sorted(match_ids)}
