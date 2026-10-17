"""Private feedback, durable notifications, and explicitly approved development jobs."""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

import accounts
import video_tools

router = APIRouter(prefix="/api/feedback", tags=["feedback"])
ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "feedback-config.json"


def config():
    defaults = {"admin_email": "", "execution_enabled": False}
    if CONFIG_PATH.exists():
        defaults.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return defaults


@contextmanager
def database():
    folder = Path(os.environ.get("GGAB_FEEDBACK_ROOT", video_tools.MEDIA_ROOT / "feedback"))
    folder.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(folder / "feedback.db", timeout=15) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS feedback (
            id TEXT PRIMARY KEY, number TEXT NOT NULL UNIQUE, owner_id TEXT NOT NULL,
            owner_email TEXT NOT NULL, request_id TEXT NOT NULL, title TEXT NOT NULL,
            category TEXT NOT NULL, content TEXT NOT NULL, page TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', admin_note TEXT NOT NULL DEFAULT '',
            result TEXT NOT NULL DEFAULT '', approval_digest TEXT NOT NULL UNIQUE,
            decision_actor TEXT, created REAL NOT NULL, updated REAL NOT NULL,
            UNIQUE(owner_id, request_id));
        CREATE INDEX IF NOT EXISTS feedback_owner ON feedback(owner_id,created DESC);
        CREATE TABLE IF NOT EXISTS feedback_events (
            id INTEGER PRIMARY KEY, feedback_id TEXT NOT NULL REFERENCES feedback(id),
            kind TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS feedback_outbox (
            id INTEGER PRIMARY KEY, feedback_id TEXT NOT NULL REFERENCES feedback(id),
            kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '', updated REAL NOT NULL,
            UNIQUE(feedback_id,kind));
        CREATE TABLE IF NOT EXISTS feedback_receipts (
            message_id TEXT PRIMARY KEY, feedback_id TEXT NOT NULL REFERENCES feedback(id),
            sender TEXT NOT NULL, decision TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS feedback_jobs (
            id TEXT PRIMARY KEY, feedback_id TEXT NOT NULL UNIQUE REFERENCES feedback(id),
            status TEXT NOT NULL DEFAULT 'queued', created REAL NOT NULL, updated REAL NOT NULL);
        """)
        yield conn


def principal():
    user = accounts.current()
    if not user or user.get("role") not in {"admin", "user"}:
        raise HTTPException(401, "请登录后提交或查看反馈")
    return user


def event(conn, fid, kind, actor, note=""):
    conn.execute("INSERT INTO feedback_events(feedback_id,kind,actor,note,created) VALUES(?,?,?,?,?)",
                 (fid, kind, actor, note, time.time()))


def public_item(conn, row, admin=False):
    result = {key: row[key] for key in ("id", "number", "title", "category", "content", "page",
              "status", "admin_note", "result", "created", "updated")}
    notice = conn.execute("SELECT status,error FROM feedback_outbox WHERE feedback_id=? AND kind='submitted'", (row["id"],)).fetchone()
    result["notification_status"] = notice["status"] if notice else "queued"
    result["events"] = [dict(r) for r in conn.execute("SELECT kind,note,created FROM feedback_events WHERE feedback_id=? ORDER BY id", (row["id"],))]
    if admin:
        result.update(owner_email=row["owner_email"], decision_actor=row["decision_actor"],
                      notification_error=notice["error"] if notice else "")
    return result


class Submission(BaseModel):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,64}$")
    title: str = Field(min_length=2, max_length=160)
    category: Literal["problem", "suggestion", "data", "other"] = "suggestion"
    content: str = Field(min_length=10, max_length=6000)
    page: str = Field(default="", max_length=300)

    @field_validator("title", "content", "page", mode="before")
    @classmethod
    def clean_text(cls, value):
        if not isinstance(value, str):
            raise ValueError("请输入文字")
        return value.strip()

    @field_validator("title", "content", "page")
    @classmethod
    def controls(cls, value):
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value):
            raise ValueError("文字中包含无效字符")
        return value

    @field_validator("title", "page")
    @classmethod
    def single_line(cls, value):
        if "\n" in value or "\r" in value:
            raise ValueError("标题和页面必须为单行")
        return value


@router.get("/settings")
def settings():
    user = principal()
    result = {"is_admin": user["role"] == "admin", "execution_enabled": bool(config()["execution_enabled"]),
              "notification_interval_seconds": config().get("notification_interval_seconds", 0)}
    if user["role"] == "admin":
        import feedback_mail
        result.update(mail_connected=feedback_mail.available())
    return result


@router.get("")
def listing(scope: Literal["mine", "all"] = "mine", offset: int = Query(0, ge=0, le=10000)):
    user = principal()
    if scope == "all":
        accounts.admin_required()
    where, args = ("", ()) if scope == "all" else ("WHERE owner_id=?", (user["id"],))
    with database() as conn:
        rows = conn.execute(f"SELECT * FROM feedback {where} ORDER BY created DESC,id LIMIT 51 OFFSET ?", (*args, offset)).fetchall()
        return {"items": [public_item(conn, r, user["role"] == "admin") for r in rows[:50]],
                "has_more": len(rows) > 50}


@router.post("", status_code=201)
def submit(body: Submission):
    user = principal()
    now = time.time()
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM feedback WHERE owner_id=? AND request_id=?", (user["id"], body.request_id)).fetchone()
        if old:
            if any(old[key] != getattr(body, key) for key in ("title", "category", "content", "page")):
                raise HTTPException(409, "这次提交标识已用于其他内容，请刷新后重试")
            return public_item(conn, old, user["role"] == "admin")
        if conn.execute("SELECT count(*) FROM feedback WHERE owner_id=? AND created>?", (user["id"], now-3600)).fetchone()[0] >= 10:
            raise HTTPException(429, "每个账号每小时最多提交十条反馈，请稍后再试")
        fid, code = uuid.uuid4().hex, secrets.token_urlsafe(24)
        number = "FB-" + time.strftime("%Y%m%d") + "-" + fid[:8].upper()
        conn.execute("INSERT INTO feedback(id,number,owner_id,owner_email,request_id,title,category,content,page,approval_digest,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (fid, number, user["id"], user["email"], body.request_id, body.title, body.category,
                      body.content, body.page, hashlib.sha256(code.encode()).hexdigest(), now, now))
        conn.execute("INSERT INTO feedback_outbox(feedback_id,kind,payload,updated) VALUES(?,'submitted',?,?)",
                     (fid, json.dumps({"approval_code": code}), now))
        event(conn, fid, "submitted", user["id"])
        result = public_item(conn, conn.execute("SELECT * FROM feedback WHERE id=?", (fid,)).fetchone(), user["role"] == "admin")
    import feedback_mail
    feedback_mail.wake()
    return result


class Decision(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=2000)


def decide(conn, row, decision, actor, note=""):
    if row["status"] != "pending":
        if row["status"] == decision:
            return
        raise HTTPException(409, "此反馈已经审核，未重复启动任务")
    now = time.time()
    conn.execute("UPDATE feedback SET status=?,admin_note=?,decision_actor=?,updated=? WHERE id=?",
                 (decision, note.strip(), actor, now, row["id"]))
    event(conn, row["id"], decision, actor, note.strip())
    if decision == "approved":
        conn.execute("INSERT OR IGNORE INTO feedback_jobs(id,feedback_id,created,updated) VALUES(?,?,?,?)", (uuid.uuid4().hex, row["id"], now, now))


@router.patch("/{fid}/decision")
def review(fid: str, body: Decision):
    user = principal()
    accounts.admin_required()
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM feedback WHERE id=?", (fid,)).fetchone()
        if not row:
            raise HTTPException(404, "反馈不存在")
        decide(conn, row, body.decision, user["email"], body.note)
        return public_item(conn, conn.execute("SELECT * FROM feedback WHERE id=?", (fid,)).fetchone(), True)


@router.post("/{fid}/retry-notification")
def retry_notification(fid: str):
    principal()
    accounts.admin_required()
    with database() as conn:
        changed = conn.execute("UPDATE feedback_outbox SET status='queued',attempts=0,next_attempt=0,error='',updated=? WHERE feedback_id=? AND kind='submitted' AND status IN ('retry','failed','waiting_configuration')", (time.time(), fid)).rowcount
        if not changed:
            raise HTTPException(409, "当前通知无需重试")
    import feedback_mail
    feedback_mail.wake()
    return {"queued": True}


def apply_mail_reply(message_id, sender, text, sent_at, *, verified_sent_mailbox):
    """Called only for mail read from the authenticated administrator's Sent Items."""
    administrator = config()["admin_email"].casefold()
    if not administrator or not verified_sent_mailbox or sender.casefold() != administrator:
        return False
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    match = re.fullmatch(r"(批准|拒绝)\s+([A-Za-z0-9_-]{32})", first)
    if not match:
        return False
    digest = hashlib.sha256(match[2].encode()).hexdigest()
    decision = "approved" if match[1] == "批准" else "rejected"
    lines = text.lstrip().splitlines()[1:]
    notes = []
    for line in lines:
        if re.match(r"\s*(>|From:|Sent:|发件人[:：]|发送时间[:：]|原始邮件[:：]|-----|Get Outlook)", line, re.IGNORECASE):
            break
        notes.append(line)
    note = "\n".join(notes).strip()[:2000] or "管理员通过邮件回复审核"
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM feedback_receipts WHERE message_id=?", (message_id,)).fetchone():
            return False
        row = conn.execute("SELECT * FROM feedback WHERE approval_digest=?", (digest,)).fetchone()
        if not row or sent_at < row["created"]:
            return False
        conn.execute("INSERT INTO feedback_receipts VALUES(?,?,?,?,?)", (message_id, row["id"], sender, decision, time.time()))
        if row["status"] != "pending":
            return False
        # The request is immutable; this decision approves exactly the emailed content.
        decide(conn, row, decision, sender, note)
        return True


def claim_job(job_id):
    if not config()["execution_enabled"]:
        raise ValueError("自动开发尚未启用")
    now = time.time()
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        job = conn.execute("SELECT j.*,f.status AS feedback_status FROM feedback_jobs j JOIN feedback f ON f.id=j.feedback_id WHERE j.id=?", (job_id,)).fetchone()
        if not job or job["status"] != "queued" or job["feedback_status"] != "approved":
            raise ValueError("任务未获得批准或已被领取")
        conn.execute("UPDATE feedback_jobs SET status='running',updated=? WHERE id=?", (now, job_id))
        conn.execute("UPDATE feedback SET status='in_progress',updated=? WHERE id=?", (now, job["feedback_id"]))
        event(conn, job["feedback_id"], "in_progress", "codex", "开始开发和测试")
        return dict(conn.execute("SELECT f.* FROM feedback f WHERE id=?", (job["feedback_id"],)).fetchone())


def finish_job(job_id, status, report):
    if status not in {"completed", "blocked", "failed"} or not report.strip():
        raise ValueError("必须提供开发和测试结果")
    now = time.time()
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        job = conn.execute("SELECT * FROM feedback_jobs WHERE id=? AND status='running'", (job_id,)).fetchone()
        if not job:
            raise ValueError("任务不是运行状态")
        conn.execute("UPDATE feedback_jobs SET status=?,updated=? WHERE id=?", (status, now, job_id))
        conn.execute("UPDATE feedback SET status=?,result=?,updated=? WHERE id=?", (status, report[:12000], now, job["feedback_id"]))
        event(conn, job["feedback_id"], status, "codex", "开发测试完成，等待管理员验收" if status == "completed" else "任务需要管理员关注")
        conn.execute("INSERT OR IGNORE INTO feedback_outbox(feedback_id,kind,payload,updated) VALUES(?,'result','{}',?)", (job["feedback_id"], now))
    import feedback_mail
    feedback_mail.wake()
