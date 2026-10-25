"""Durable, conservative capacity reservations, including files retained on Drive."""
import json
import time
from pathlib import Path

from fastapi import HTTPException

import accounts
import security_config
import video_tools as videos

DAY = 86400
GIB = 1024 ** 3


def policy():
    return {
        "capacity_bytes": security_config.positive_int("YUXU_ACCOUNT_UPLOAD_GIB", 200) * GIB,
        "daily_bytes": security_config.positive_int("YUXU_DAILY_UPLOAD_GIB", 50) * GIB,
        "daily_files": security_config.positive_int("YUXU_DAILY_UPLOAD_FILES", 20),
    }


def schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS upload_capacity (user_id TEXT PRIMARY KEY, baseline INTEGER NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS upload_budget (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, bytes INTEGER NOT NULL, capacity INTEGER NOT NULL, created REAL NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS upload_budget_owner ON upload_budget(user_id,created)")


def historical_usage(owner_id):
    """Count existing physical sources once per account, without touching any file."""
    with accounts.database() as conn:
        owned = {r[0] for r in conn.execute("SELECT resource_id FROM ownership WHERE kind='session' AND user_id=?", (owner_id,))}
    sources, total = set(), 0
    for sid, record in videos.registry().items():
        if sid not in owned:
            continue
        key = record.get("drive_file_id") or record.get("path") or sid
        if key in sources:
            continue
        sources.add(key)
        if record.get("storage") == "google_drive":
            total += int(record["drive_size"])
        elif record.get("path") and Path(record["path"]).is_file():
            total += Path(record["path"]).stat().st_size
    # Old resumable receipts may refer to unregistered or replaced Drive objects.
    for path in (videos.MEDIA_ROOT / "uploads").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("owner_id") != owner_id:
            continue
        key = (data.get("drive_file") or {}).get("id")
        if key in sources:
            continue
        if data.get("storage") == "google_drive":
            total += data["size_bytes"]
            if key:
                sources.add(key)
        elif data.get("status") != "complete" and data.get("expires_at", 0) > time.time():
            total += data["size_bytes"]
    return total


def prepare(owner_id):
    with accounts.database() as conn:
        schema(conn)
        seeded = conn.execute("SELECT 1 FROM upload_capacity WHERE user_id=?", (owner_id,)).fetchone()
    # Never hold a SQLite write transaction while acquiring the video registry lock.
    return 0 if seeded else historical_usage(owner_id)


def usage(conn, owner_id, now):
    baseline = conn.execute("SELECT baseline FROM upload_capacity WHERE user_id=?", (owner_id,)).fetchone()[0]
    capacity = conn.execute("SELECT COALESCE(SUM(capacity),0) FROM upload_budget WHERE user_id=?", (owner_id,)).fetchone()[0]
    daily, count = conn.execute("SELECT COALESCE(SUM(bytes),0),COUNT(*) FROM upload_budget WHERE user_id=? AND created>?", (owner_id, now - DAY)).fetchone()
    return {"used_capacity_bytes": baseline + capacity, "used_daily_bytes": daily, "used_daily_files": count}


def reserve(upload_id, owner_id, size):
    limits, baseline, now = policy(), prepare(owner_id), time.time()
    with accounts.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR IGNORE INTO upload_capacity VALUES(?,?)", (owner_id, baseline))
        existing = conn.execute("SELECT user_id,bytes FROM upload_budget WHERE id=?", (upload_id,)).fetchone()
        if existing:
            if tuple(existing) != (owner_id, size):
                raise HTTPException(409, "上传额度关联不一致")
            return
        used = usage(conn, owner_id, now)
        if used["used_capacity_bytes"] + size > limits["capacity_bytes"]:
            raise HTTPException(413, "账号上传容量不足，请联系管理员；已有视频不受影响")
        if used["used_daily_bytes"] + size > limits["daily_bytes"] or used["used_daily_files"] >= limits["daily_files"]:
            raise HTTPException(429, "已达到账号最近 24 小时的上传额度，请稍后再试", headers={"Retry-After": "3600"})
        conn.execute("INSERT INTO upload_budget VALUES(?,?,?,?,?)", (upload_id, owner_id, size, size, now))


def release_local(upload_id):
    # Intake remains charged for 24h. Drive reservations remain charged because
    # aborting a local receipt does not prove Google has removed the remote bytes.
    with accounts.database() as conn:
        schema(conn)
        conn.execute("UPDATE upload_budget SET capacity=0 WHERE id=?", (upload_id,))


def summary(owner_id):
    baseline = prepare(owner_id)
    with accounts.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR IGNORE INTO upload_capacity VALUES(?,?)", (owner_id, baseline))
        return {**policy(), **usage(conn, owner_id, time.time())}
