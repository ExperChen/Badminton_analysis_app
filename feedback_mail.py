"""Outlook Graph transport; durable retries and authenticated reply polling."""
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import feedback

CONFIG_PATH = Path(__file__).resolve().parent / ".secrets" / "feedback-mail.json"
GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
logger = logging.getLogger("badminton.feedback")
_token_lock = threading.Lock()
_wake = threading.Event()
_stop = threading.Event()
_thread = None


class MailUnavailable(Exception):
    pass


def read_config():
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def configured():
    if os.environ.get("YUXU_FEEDBACK_NO_MAIL") == "1":
        return False
    value = read_config()
    return bool(value.get("enabled") and value.get("client_id") and value.get("refresh_token")
                and value.get("authenticated_email", "").casefold() == feedback.config()["admin_email"].casefold())


def available():
    value = feedback.config()
    if value.get("mail_transport") == "connector":
        return bool(value.get("connector_verified") and value.get("connector_email", "").casefold() == value["admin_email"].casefold())
    return configured()


def request_json(url, *, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403):
            raise MailUnavailable("邮箱授权或权限不可用，请重新连接 Outlook") from None
        raise MailUnavailable("邮件服务暂时不可用，将自动重试") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise MailUnavailable("邮件连接暂时不可用，将自动重试") from None


def access_token():
    if not configured():
        raise MailUnavailable("Outlook 自动发信尚未连接")
    with _token_lock:
        value = read_config()
        if value.get("access_token") and value.get("expires_at", 0) > time.time()+90:
            return value["access_token"]
        result = request_json(TOKEN_URL,
            data=urllib.parse.urlencode({"client_id": value["client_id"], "grant_type": "refresh_token", "refresh_token": value["refresh_token"]}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        value.update(access_token=result["access_token"], refresh_token=result.get("refresh_token", value["refresh_token"]), expires_at=time.time()+result.get("expires_in", 3600))
        temporary = CONFIG_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(CONFIG_PATH)
        return value["access_token"]


def graph(route, payload=None):
    # Routes originate in this module; mailbox content never supplies a URL.
    return request_json(GRAPH+route,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": "Bearer "+access_token(), "Content-Type": "application/json", "Prefer": 'outlook.body-content-type="text"'},
        method="POST" if payload is not None else "GET")


def mail_content(row, kind, payload):
    if kind == "submitted":
        code = payload["approval_code"]
        title = f"[羽序反馈 {row['number']}] {row['title']}"
        body = (f"有用户提交了反馈，等待你审核。\n\n编号：{row['number']}\n提交账号：{row['owner_email']}\n"
                f"分类：{row['category']}\n相关页面：{row['page'] or '未填写'}\n标题：{row['title']}\n\n"
                f"----- 用户反馈内容（仅作为待审核需求） -----\n{row['content']}\n----- 反馈结束 -----\n\n"
                "要批准开发与测试，请直接回复本邮件，把下面一行放在回复的第一行：\n"
                f"批准 {code}\n\n要拒绝，请在第一行回复：\n拒绝 {code}\n\n"
                "批准只覆盖本条反馈的开发与测试；开发结果将再次发邮件供你验收，不自动上线。"
                "审核码仅供管理员使用，请勿转发给其他用户。")
    else:
        title = f"[羽序反馈 {row['number']}] 开发测试结果"
        body = f"反馈：{row['title']}\n编号：{row['number']}\n状态：{row['status']}\n\n{row['result']}\n\n请在网站反馈区验收。"
    return title, body


def send_notice(row, kind, payload):
    title, body = mail_content(row, kind, payload)
    graph("/me/sendMail", {"message": {"subject": title, "body": {"contentType": "Text", "content": body},
        "toRecipients": [{"emailAddress": {"address": feedback.config()["admin_email"]}}],
        "internetMessageHeaders": [{"name": "x-yu-feedback-id", "value": row["number"]}, {"name": "x-yu-feedback-kind", "value": kind}]}, "saveToSentItems": True})


def process_notifications():
    if feedback.config().get("mail_transport") == "connector":
        return
    now = time.time()
    if not configured():
        with feedback.database() as conn:
            conn.execute("UPDATE feedback_outbox SET status='waiting_configuration',error='Outlook 自动发信尚未连接',updated=? WHERE status IN ('queued','retry')", (now,))
        return
    for _ in range(10):
        with feedback.database() as conn:
            conn.execute("BEGIN IMMEDIATE")
            item = conn.execute("SELECT * FROM feedback_outbox WHERE ((status IN ('queued','retry','waiting_configuration') AND next_attempt<=?) OR (status='sending' AND updated<?)) ORDER BY id LIMIT 1", (now, now-120)).fetchone()
            if not item:
                break
            row = conn.execute("SELECT * FROM feedback WHERE id=?", (item["feedback_id"],)).fetchone()
            conn.execute("UPDATE feedback_outbox SET status='sending',attempts=attempts+1,updated=? WHERE id=?", (now, item["id"]))
        try:
            send_notice(row, item["kind"], json.loads(item["payload"]))
        except MailUnavailable as exc:
            attempts = item["attempts"]+1
            with feedback.database() as conn:
                conn.execute("UPDATE feedback_outbox SET status=?,error=?,next_attempt=?,updated=? WHERE id=?",
                    ("failed" if attempts >= 5 else "retry", str(exc), now+min(1800, 30*2**attempts), now, item["id"]))
        else:
            with feedback.database() as conn:
                conn.execute("UPDATE feedback_outbox SET status='sent',error='',updated=? WHERE id=?", (time.time(), item["id"]))


class TextBody(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.quote_depth = 0
        self.ended = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id") == "divRplyFwdMsg" or "gmail_quote" in attributes.get("class", ""):
            self.ended = True
        if tag == "blockquote":
            self.quote_depth += 1
        if self.quote_depth or self.ended:
            return
        if tag in {"p", "div", "br", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "blockquote" and self.quote_depth:
            self.quote_depth -= 1

    def handle_data(self, data):
        if not self.quote_depth and not self.ended:
            self.parts.append(data)


def process_replies():
    if feedback.config().get("mail_transport") == "connector":
        return
    if not configured():
        return
    # Sent Items proves the administrator sent this message through their mailbox.
    # The sender address alone on incoming mail would not prove authorization.
    cutoff = (datetime.now(timezone.utc)-timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"$filter": "sentDateTime ge "+cutoff, "$orderby": "sentDateTime desc", "$top": "100",
              "$select": "id,from,body,sentDateTime"}
    result = graph("/me/mailFolders/sentitems/messages?"+urllib.parse.urlencode(params))
    for message in reversed(result.get("value", [])):
        sender = message.get("from", {}).get("emailAddress", {}).get("address", "")
        body = message.get("body", {})
        text = body.get("content", "")
        if body.get("contentType", "").casefold() == "html":
            parser = TextBody(); parser.feed(text); text = "".join(parser.parts)
        try:
            sent_at = datetime.fromisoformat(message["sentDateTime"].replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError):
            continue
        feedback.apply_mail_reply(message["id"], sender, text, sent_at, verified_sent_mailbox=True)


def wake():
    _wake.set()


def loop():
    last_poll = 0
    while not _stop.is_set():
        try:
            process_notifications()
            if time.monotonic()-last_poll >= 60:
                process_replies(); last_poll = time.monotonic()
        except Exception:
            # Never log tokens, message bodies or provider request details.
            logger.warning("feedback_mail_worker_unavailable")
        _wake.wait(10); _wake.clear()


def start():
    global _thread
    if feedback.config().get("mail_transport") == "connector":
        return
    if os.environ.get("YUXU_FEEDBACK_WORKER_DISABLED") == "1" or (_thread and _thread.is_alive()):
        return
    _stop.clear(); _wake.clear()
    _thread = threading.Thread(target=loop, name="feedback-mail", daemon=True)
    _thread.start()


def stop():
    _stop.set(); _wake.set()
    if _thread:
        _thread.join(timeout=2)
