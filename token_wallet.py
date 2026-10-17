"""Account quotas and project holds; quotes never become actual charges."""
import json
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import accounts

router = APIRouter()
MAX_TOKENS = 1_000_000_000


def ensure_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS token_wallets (
            user_id TEXT PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS token_ledger (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, amount INTEGER NOT NULL,
            kind TEXT NOT NULL, project_id TEXT, actor_id TEXT, note TEXT NOT NULL,
            created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS token_holds (
            project_id TEXT PRIMARY KEY, remote_id TEXT UNIQUE NOT NULL,
            user_id TEXT NOT NULL, estimated INTEGER NOT NULL,
            state TEXT NOT NULL, actual INTEGER, receipt_id TEXT UNIQUE,
            evidence TEXT, created REAL NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS ggab_token_balance (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), subject TEXT NOT NULL,
            total INTEGER, observed REAL, error TEXT);
        CREATE TABLE IF NOT EXISTS ggab_token_activity (
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, observed REAL NOT NULL);
    """)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(token_holds)")}
    if "remote_receipt_id" not in columns:
        conn.execute("ALTER TABLE token_holds ADD COLUMN remote_receipt_id TEXT")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(ggab_token_balance)")}
    for name, kind in (("activity_error", "TEXT"), ("activity_observed", "REAL")):
        if name not in columns:
            conn.execute(f"ALTER TABLE ggab_token_balance ADD COLUMN {name} {kind}")


def wallet(conn, user_id):
    row = conn.execute("SELECT balance FROM token_wallets WHERE user_id=?", (user_id,)).fetchone()
    reserved = conn.execute("SELECT COALESCE(SUM(estimated),0) FROM token_holds WHERE user_id=? AND state IN ('held','submitted')", (user_id,)).fetchone()[0]
    balance = row[0] if row else 0
    consumed, settled_projects = conn.execute("SELECT COALESCE(-SUM(amount),0),COUNT(*) FROM token_ledger WHERE user_id=? AND kind='settlement'", (user_id,)).fetchone()
    return {"balance": balance, "reserved": reserved, "available": balance - reserved,
            "consumed": consumed, "settled_projects": settled_projects}


def project_owner(pid):
    if not accounts.ENFORCE_AUTH:
        return None
    ownership = accounts.owner("project", pid)
    if not ownership:
        raise HTTPException(409, "请先核验并认领项目，再分配 Token 额度")
    with accounts.database() as conn:
        row = conn.execute("SELECT id,role FROM users WHERE id=?", (ownership["user_id"],)).fetchone()
    if not row:
        raise HTTPException(409, "项目所属账号不存在")
    return dict(row)


def project_status(conn, pid, estimate=None):
    if not accounts.ENFORCE_AUTH:
        return None
    if not accounts.owner("project", pid):
        return {"uses_local_quota": True, "requires_assignment": True, "needs_recharge": False,
                "settlement": None, "error": "请先认领项目，再分配 Token 额度"}
    owner = project_owner(pid)
    row = conn.execute("SELECT * FROM token_holds WHERE project_id=?", (pid,)).fetchone()
    result = {"uses_local_quota": owner["role"] != "admin", "settlement": None}
    if owner["role"] != "admin":
        result.update(wallet(conn, owner["id"]))
        if row and row["state"] in {"held", "submitted"}:
            result["available_for_project"] = result["available"] + row["estimated"]
        else:
            result["available_for_project"] = result["available"]
        result["needs_recharge"] = type(estimate) is int and estimate > result["available_for_project"]
    if row:
        result["settlement"] = {"state": row["state"], "estimated": row["estimated"], "actual": row["actual"]}
    return result


def remember_balance(conn, total=None, error=None):
    subject = accounts.source_subject()
    if accounts.ENFORCE_AUTH and not subject:
        raise HTTPException(409, "请先核验共享 GGAB 账号")
    if type(total) is int and 0 <= total <= MAX_TOKENS:
        conn.execute("INSERT INTO ggab_token_balance(singleton,subject,total,observed,error) VALUES(1,?,?,?,NULL) ON CONFLICT(singleton) DO UPDATE SET subject=excluded.subject,total=excluded.total,observed=excluded.observed,error=NULL",
                     (subject or "local", total, time.time()))
    elif error:
        conn.execute("INSERT INTO ggab_token_balance(singleton,subject,total,observed,error) VALUES(1,?,NULL,NULL,?) ON CONFLICT(singleton) DO UPDATE SET error=excluded.error",
                     (subject or "local", error))


def remote_balance(conn):
    row = conn.execute("SELECT * FROM ggab_token_balance WHERE singleton=1").fetchone()
    if not row or (accounts.ENFORCE_AUTH and row["subject"] != accounts.source_subject()):
        return {"total": None, "observed_at": None, "error": "等待后台扩展读取 GGAB 余额", "stale": True,
                "activity_error": None, "activity_observed_at": None}
    return {"total": row["total"], "observed_at": row["observed"], "error": row["error"],
            "stale": bool(row["error"]) or row["observed"] is None or time.time() - row["observed"] > 180,
            "activity_error": row["activity_error"], "activity_observed_at": row["activity_observed"]}


def remember_activity_sync(conn, error=None):
    if error:
        conn.execute("UPDATE ggab_token_balance SET activity_error=? WHERE singleton=1", (error,))
    else:
        conn.execute("UPDATE ggab_token_balance SET activity_error=NULL,activity_observed=? WHERE singleton=1", (time.time(),))


def remember_activity(conn, items):
    for item in items:
        if isinstance(item.get("id"), str) and len(item["id"]) <= 200:
            conn.execute("INSERT OR REPLACE INTO ggab_token_activity VALUES(?,?,?)",
                         (item["id"], json.dumps(item), time.time()))


def activity_receipt(conn, remote_id, job_id=None, transaction_id=None):
    """Fail closed unless the official activity explicitly identifies this job."""
    matches = []
    for row in conn.execute("SELECT payload FROM ggab_token_activity"):
        item = json.loads(row[0])
        if item.get("label") != "Track Match Automatically" or item.get("pending") is not False:
            continue
        amount = item.get("amount")
        if type(amount) is not int or not -MAX_TOKENS <= amount <= 0:
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        project_refs = [item.get("projectId"), item.get("project_id"), metadata.get("projectId"), metadata.get("project_id")]
        job_refs = [item.get("jobId"), item.get("job_id"), metadata.get("jobId"), metadata.get("job_id")]
        if (transaction_id and item["id"] == transaction_id) or remote_id in project_refs or (job_id and job_id in job_refs):
            matches.append(item)
    if len(matches) != 1:
        return None
    item = matches[0]
    return {"remote_project_id": remote_id, "actual_tokens": -item["amount"],
            "receipt_id": item["id"], "source": "ggab_token_ledger"}


def settle_ready(conn):
    rows = conn.execute("SELECT h.project_id,h.remote_id,h.remote_receipt_id,m.payload AS migration,w.payload AS workflow FROM token_holds h JOIN migration_links m ON m.project_id=h.project_id LEFT JOIN migration_workflows w ON w.project_id=h.project_id WHERE h.state IN ('held','submitted')").fetchall()
    for row in rows:
        link = json.loads(row["migration"])
        if link.get("remote_project_id") != row["remote_id"] or link.get("status") not in {"finalized", "failed"} or not link.get("association", {}).get("verified"):
            continue
        data = json.loads(row["workflow"]) if row["workflow"] else {}
        receipt = activity_receipt(conn, row["remote_id"], data.get("remote_job_id"), row["remote_receipt_id"])
        if receipt:
            settle(conn, row["project_id"], row["remote_id"], receipt["actual_tokens"], receipt["receipt_id"], receipt)


def bind_receipt(conn, pid, remote_id, receipt_id):
    row = conn.execute("SELECT remote_id,remote_receipt_id FROM token_holds WHERE project_id=?", (pid,)).fetchone()
    if not row:
        return
    if row["remote_id"] != remote_id or (row["remote_receipt_id"] and row["remote_receipt_id"] != receipt_id):
        raise HTTPException(409, "GGAB 项目扣费凭据不匹配，请核查")
    if conn.execute("SELECT 1 FROM token_holds WHERE remote_receipt_id=? AND project_id<>?", (receipt_id, pid)).fetchone():
        raise HTTPException(409, "GGAB 扣费凭据已关联其他项目")
    conn.execute("UPDATE token_holds SET remote_receipt_id=? WHERE project_id=?", (receipt_id, pid))


def reserve(conn, pid, remote_id, estimate):
    owner = project_owner(pid)
    if not owner:
        return
    old = conn.execute("SELECT * FROM token_holds WHERE project_id=?", (pid,)).fetchone()
    if old and (old["remote_id"] != remote_id or old["user_id"] != owner["id"] or old["state"] in {"submitted", "settled"}):
        raise HTTPException(409, "项目额度已提交或已结算，不能重复提交")
    if owner["role"] != "admin":
        data = wallet(conn, owner["id"])
        available = data["available"] + (old["estimated"] if old and old["state"] == "held" else 0)
        if estimate > available:
            raise HTTPException(402, f"Token 额度不足，需要充值：预计 {estimate:,} Tokens，可用 {available:,} Tokens，请联系管理员")
    at = time.time()
    conn.execute("INSERT INTO token_holds(project_id,remote_id,user_id,estimated,state,created,updated) VALUES(?,?,?,?,'held',?,?) ON CONFLICT(project_id) DO UPDATE SET estimated=excluded.estimated,state='held',updated=excluded.updated",
                 (pid, remote_id, owner["id"], estimate, at, at))


def release_unsubmitted(conn, pid):
    # Unknown remote writes retain their hold until a verified outcome arrives.
    conn.execute("UPDATE token_holds SET state='released',updated=? WHERE project_id=? AND state='held'", (time.time(), pid))


def submitted(conn, pid):
    conn.execute("UPDATE token_holds SET state='submitted',updated=? WHERE project_id=? AND state='held'", (time.time(), pid))


def settle(conn, pid, remote_id, actual, receipt_id, evidence):
    """Called only for verified terminal worker results, using a project receipt."""
    hold = conn.execute("SELECT * FROM token_holds WHERE project_id=?", (pid,)).fetchone()
    if not hold:
        return False  # Old matches must never be charged retroactively.
    if hold["remote_id"] != remote_id:
        raise HTTPException(409, "真实费用的远端项目与额度预留不匹配")
    if hold["remote_receipt_id"] and hold["remote_receipt_id"] != receipt_id:
        raise HTTPException(409, "真实扣费凭据与已核验的 GGAB 交易不匹配")
    if hold["state"] == "settled":
        if hold["actual"] != actual or hold["receipt_id"] != receipt_id:
            raise HTTPException(409, "已结算项目的真实费用发生变化，请管理员核查")
        return True
    if hold["state"] not in {"held", "submitted"}:
        raise HTTPException(409, "项目没有有效额度预留，不能结算")
    existing = conn.execute("SELECT project_id FROM token_holds WHERE receipt_id=?", (receipt_id,)).fetchone()
    if existing and existing[0] != pid:
        raise HTTPException(409, "扣费凭据已用于其他项目")
    with accounts.database() as identity_db:
        owner = identity_db.execute("SELECT role FROM users WHERE id=?", (hold["user_id"],)).fetchone()
    at = time.time()
    if owner and owner[0] != "admin":
        conn.execute("INSERT OR IGNORE INTO token_wallets VALUES(?,0)", (hold["user_id"],))
        # An authoritative actual may exceed its quote: record the debt, never
        # hide an incurred charge or replace it with the estimate.
        conn.execute("UPDATE token_wallets SET balance=balance-? WHERE user_id=?", (actual, hold["user_id"]))
        conn.execute("INSERT INTO token_ledger VALUES(?,?,?,'settlement',?,NULL,?,?)",
                     ("settle:" + pid, hold["user_id"], -actual, pid, "GGAB 真实费用结算", at))
    conn.execute("UPDATE token_holds SET state='settled',actual=?,receipt_id=?,evidence=?,updated=? WHERE project_id=?",
                 (actual, receipt_id, json.dumps(evidence), at, pid))
    return True


@router.get("/api/tokens/me")
def my_tokens():
    from tracker import database
    user = accounts.current()
    if not user:
        raise HTTPException(401, "请登录账号")
    with database() as conn:
        result = {"uses_local_quota": user["role"] != "admin", **wallet(conn, user["id"])}
        result["transactions"] = [dict(r) for r in conn.execute("SELECT amount,kind,project_id,note,created FROM token_ledger WHERE user_id=? ORDER BY created DESC LIMIT 100", (user["id"],))]
        if user["role"] == "admin":
            result["ggab"] = remote_balance(conn)
    return result


@router.get("/api/admin/tokens")
def admin_tokens():
    accounts.admin_required()
    from tracker import database
    with accounts.database() as conn:
        users = [dict(r) for r in conn.execute("SELECT id,email,enabled FROM users WHERE role='user' ORDER BY created")]
    with database() as conn:
        for user in users:
            user.update(wallet(conn, user["id"]))
        return {"ggab": remote_balance(conn), "users": users,
                "summary": {key: sum(user[key] for user in users) for key in ("balance", "reserved", "available", "consumed", "settled_projects")},
                "transactions": [dict(r) for r in conn.execute("SELECT * FROM token_ledger ORDER BY created DESC LIMIT 100")]}


class Allocation(BaseModel):
    amount: int = Field(gt=0, le=MAX_TOKENS, strict=True)
    request_id: str = Field(min_length=16, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    note: str = Field(default="管理员分配", min_length=1, max_length=300)


@router.post("/api/admin/users/{user_id}/tokens")
def allocate_tokens(user_id: str, body: Allocation):
    accounts.admin_required()
    with accounts.database() as conn:
        user = conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
    if not user or user[0] != "user":
        raise HTTPException(404, "子账号不存在")
    from tracker import database
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        key = "grant:" + body.request_id
        old = conn.execute("SELECT user_id,amount,note FROM token_ledger WHERE id=?", (key,)).fetchone()
        if old:
            if (old["user_id"], old["amount"], old["note"]) != (user_id, body.amount, body.note):
                raise HTTPException(409, "分配请求已使用，金额或账号不匹配")
        else:
            conn.execute("INSERT OR IGNORE INTO token_wallets VALUES(?,0)", (user_id,))
            if wallet(conn, user_id)["balance"] + body.amount > MAX_TOKENS:
                raise HTTPException(422, "账号额度超过上限")
            conn.execute("UPDATE token_wallets SET balance=balance+? WHERE user_id=?", (body.amount, user_id))
            conn.execute("INSERT INTO token_ledger VALUES(?,?,?,'allocation',NULL,?,?,?)",
                         (key, user_id, body.amount, accounts.current()["id"], body.note, time.time()))
        return {"user_id": user_id, **wallet(conn, user_id)}
