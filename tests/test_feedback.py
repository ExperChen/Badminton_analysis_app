import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import accounts
import app as server
import feedback
import feedback_mail
from test_accounts import environment


def submission(key="feedback-request-0001"):
    return {"request_id": key, "title": "两场技术图表对比", "content": "希望能对比两场比赛的杀球和网前球使用比例。", "category": "suggestion", "page": "/insights/match-breakdown"}


def send(client, body=None):
    return client.post("/api/feedback", json=body or submission(), headers={"X-Yuxu-Local": "1"})


def approval_code(fid):
    with feedback.database() as conn:
        return json.loads(conn.execute("SELECT payload FROM feedback_outbox WHERE feedback_id=?", (fid,)).fetchone()[0])["approval_code"]


@pytest.fixture
def feedback_config(tmp_path, monkeypatch):
    monkeypatch.setenv("YUXU_FEEDBACK_ROOT", str(tmp_path / "feedback"))
    file = tmp_path / "feedback-config.json"
    file.write_text(json.dumps({"admin_email":"review-admin@example.test","execution_enabled":True,"mail_transport":"graph"}), encoding="utf-8")
    monkeypatch.setattr(feedback, "CONFIG_PATH", file)
    monkeypatch.setattr(feedback_mail, "CONFIG_PATH", tmp_path / "mail.json")


def test_identity_isolation_and_admin_review(environment, feedback_config):
    admin, alice, bob, guest = environment["clients"]
    assert send(guest).status_code == 401
    item = send(alice).json()
    assert alice.get("/api/feedback").json()["items"][0]["id"] == item["id"]
    assert bob.get("/api/feedback").json()["items"] == []
    assert alice.get("/api/feedback?scope=all").status_code == 403
    assert admin.get("/api/feedback?scope=all").json()["items"][0]["owner_email"] == "alice@example.test"
    assert "approval_digest" not in item and "approval_code" not in json.dumps(item)
    route = "/api/feedback/"+item["id"]+"/decision"
    assert alice.patch(route,json={"decision":"approved"},headers={"X-Yuxu-Local":"1"}).status_code == 403
    assert admin.patch(route,json={"decision":"approved"},headers={"X-Yuxu-Local":"1","X-CSRF-Token":"wrong"}).status_code == 403
    result = admin.patch(route,json={"decision":"approved","note":"仅比较两场图表，不改变数据"},headers={"X-Yuxu-Local":"1"})
    assert result.status_code == 200
    assert result.json()["status"] == "approved"
    assert result.json()["admin_note"] == "仅比较两场图表，不改变数据"
    with feedback.database() as conn:
        assert conn.execute("SELECT count(*) FROM feedback_jobs").fetchone()[0] == 1


def test_settings_do_not_expose_notification_mailbox(environment, feedback_config):
    admin, alice, _, _ = environment["clients"]
    for client in (admin, alice):
        response = client.get("/api/feedback/settings")
        assert response.status_code == 200
        assert "admin_email" not in response.json()
        assert "review-admin@example.test" not in response.text


def test_missing_mailbox_configuration_rejects_mail(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "CONFIG_PATH", tmp_path / "missing.json")
    assert feedback.config()["admin_email"] == ""
    assert not feedback.apply_mail_reply("missing-config", "", "批准 " + "a" * 32,
                                         time.time(), verified_sent_mailbox=True)


def test_duplicate_submission_does_not_duplicate_mail(environment, feedback_config):
    alice = environment["clients"][1]
    first = send(alice)
    again = send(alice)
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    changed = submission(); changed["content"] = "同一个请求标识不能提交不同反馈内容。"
    assert send(alice,changed).status_code == 409
    with feedback.database() as conn:
        assert conn.execute("SELECT count(*) FROM feedback_outbox").fetchone()[0] == 1


def test_validation_limits_and_persisted_rate_limit(environment, feedback_config):
    alice = environment["clients"][1]
    invalid = submission(); invalid["title"] = "  "
    assert send(alice,invalid).status_code == 422
    invalid = submission(); invalid["content"] = "short"
    assert send(alice,invalid).status_code == 422
    for i in range(10):
        assert send(alice,submission(f"feedback-request-{i:04d}")).status_code == 201
    assert send(alice,submission("feedback-request-9999")).status_code == 429
    assert send(alice,submission("feedback-request-0000")).status_code == 201


def test_authenticated_email_command_and_replay(environment, feedback_config):
    item = send(environment["clients"][1]).json()
    code = approval_code(item["id"])
    args = {"sent_at":time.time()+1,"verified_sent_mailbox":True}
    assert not feedback.apply_mail_reply("bad","attacker@example.test","批准 "+code,**args)
    assert not feedback.apply_mail_reply("quoted","review-admin@example.test","> 批准 "+code,**args)
    assert not feedback.apply_mail_reply("ambiguous","review-admin@example.test","可以考虑\n批准 "+code,**args)
    assert not feedback.apply_mail_reply("stale","review-admin@example.test","批准 "+code,0,verified_sent_mailbox=True)
    assert not feedback.apply_mail_reply("unverified","review-admin@example.test","批准 "+code,time.time()+1,verified_sent_mailbox=False)
    assert feedback.apply_mail_reply("approved","review-admin@example.test","批准 "+code+"\n仅修改图表，不修改原始数据。\n\n原始邮件：拒绝其他内容",**args)
    assert not feedback.apply_mail_reply("approved","review-admin@example.test","批准 "+code,**args)
    assert not feedback.apply_mail_reply("another","review-admin@example.test","批准 "+code,**args)
    with feedback.database() as conn:
        assert conn.execute("SELECT count(*) FROM feedback_jobs").fetchone()[0] == 1
        assert conn.execute("SELECT admin_note FROM feedback WHERE id=?",(item["id"],)).fetchone()[0] == "仅修改图表，不修改原始数据。"


def test_rejection_does_not_create_execution_job(environment, feedback_config):
    item = send(environment["clients"][1]).json()
    assert feedback.apply_mail_reply("rejected","review-admin@example.test","拒绝 "+approval_code(item["id"]),time.time()+1,verified_sent_mailbox=True)
    with feedback.database() as conn:
        assert conn.execute("SELECT count(*) FROM feedback_jobs").fetchone()[0] == 0


def test_notification_retry_and_recovery(environment, feedback_config, monkeypatch):
    send(environment["clients"][1])
    monkeypatch.setattr(feedback_mail,"configured",lambda:False)
    feedback_mail.process_notifications()
    with feedback.database() as conn:
        assert conn.execute("SELECT status FROM feedback_outbox").fetchone()[0] == "waiting_configuration"
    monkeypatch.setattr(feedback_mail,"configured",lambda:True)
    def unavailable(*args):
        raise feedback_mail.MailUnavailable("邮件服务暂时不可用")
    monkeypatch.setattr(feedback_mail,"send_notice",unavailable)
    feedback_mail.process_notifications()
    with feedback.database() as conn:
        assert conn.execute("SELECT status FROM feedback_outbox").fetchone()[0] == "retry"
        conn.execute("UPDATE feedback_outbox SET next_attempt=0")
    sent=[]
    monkeypatch.setattr(feedback_mail,"send_notice",lambda *args:sent.append(args))
    feedback_mail.process_notifications(); feedback_mail.process_notifications()
    assert len(sent) == 1
    with feedback.database() as conn:
        assert conn.execute("SELECT status FROM feedback_outbox").fetchone()[0] == "sent"


def test_job_claim_is_atomic_and_completion_notifies(environment, feedback_config):
    item = send(environment["clients"][1]).json()
    feedback.apply_mail_reply("approved","review-admin@example.test","批准 "+approval_code(item["id"]),time.time()+1,verified_sent_mailbox=True)
    with feedback.database() as conn:
        job_id = conn.execute("SELECT id FROM feedback_jobs").fetchone()[0]
    def claim():
        try:
            return feedback.claim_job(job_id)["status"]
        except ValueError:
            return "already_claimed"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _:claim(),range(2)))
    assert sorted(results) == ["already_claimed","in_progress"]
    feedback.finish_job(job_id,"completed","已完成开发，相关测试通过；尚未上线。")
    with pytest.raises(ValueError):
        feedback.finish_job(job_id,"completed","重复完成")
    with feedback.database() as conn:
        assert conn.execute("SELECT status FROM feedback WHERE id=?",(item["id"],)).fetchone()[0] == "completed"
        assert conn.execute("SELECT count(*) FROM feedback_outbox WHERE kind='result'").fetchone()[0] == 1


def test_html_quoted_approval_is_not_a_new_decision():
    parser=feedback_mail.TextBody();parser.feed('<blockquote>批准 abc</blockquote>')
    assert "".join(parser.parts).strip() == ""
    parser=feedback_mail.TextBody();parser.feed('<div>收到，谢谢</div><div id="divRplyFwdMsg">批准 abc</div>')
    assert "".join(parser.parts).strip() == "收到，谢谢"


def test_connector_relay_and_job_disable(environment, feedback_config, monkeypatch):
    from tools import feedback_queue
    item=send(environment["clients"][1]).json()
    mail=feedback_queue.next_mail()
    assert mail["recipient"] == "review-admin@example.test"
    assert item["number"] in mail["subject"]
    assert feedback_queue.next_mail() is None
    feedback_queue.mark_mail(mail["outbox_id"],True)
    with pytest.raises(ValueError):
        feedback_queue.mark_mail(mail["outbox_id"],True)
    with pytest.raises(ValueError):
        feedback_queue.relay_file("C:/untrusted/request.json")
    feedback.apply_mail_reply("approved","review-admin@example.test","批准 "+approval_code(item["id"]),time.time()+1,verified_sent_mailbox=True)
    with feedback.database() as conn:
        job_id=conn.execute("SELECT id FROM feedback_jobs").fetchone()[0]
    monkeypatch.setattr(feedback,"config",lambda:{"admin_email":"review-admin@example.test","execution_enabled":False})
    with pytest.raises(ValueError,match="尚未启用"):
        feedback.claim_job(job_id)
