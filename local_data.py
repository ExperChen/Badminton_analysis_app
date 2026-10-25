"""Read existing local match/video bindings without any remote synchronization."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def aliases(database):
    try:
        with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            return dict(conn.execute("SELECT match_id,session_id FROM match_video_links"))
    except sqlite3.OperationalError:
        return {}


def revision(database):
    """Retain the existing local revision for playback clients; never poll upstream."""
    try:
        with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT payload FROM full_csv_state WHERE id=1").fetchone()
        state = json.loads(row[0]) if row else {}
        return {key: state.get(key) for key in ("revision", "imported_at")}
    except (sqlite3.OperationalError, ValueError):
        return {"revision": None, "imported_at": None}


def project_matches(database):
    """Only expose historical bindings that actually have local shot records."""
    try:
        with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            return dict(conn.execute("SELECT match_id,project_id FROM match_video_links l "
                                     "WHERE EXISTS (SELECT 1 FROM shots s WHERE s.match_id=l.match_id)"))
    except sqlite3.OperationalError:
        return {}
