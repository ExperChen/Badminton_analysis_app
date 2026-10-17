from pathlib import Path

import pytest
from pydantic import ValidationError

import feedback
from tools import feedback_queue


def test_development_snapshot_excludes_credentials_and_data(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback_queue,"ROOT",tmp_path)
    monkeypatch.setattr(feedback,"config",lambda:{"admin_email":"review-admin@example.test"})
    (tmp_path/"app.py").write_text("# application source",encoding="utf-8")
    (tmp_path/"ggab.db").write_bytes(b"private-data")
    (tmp_path/"client_secret.json").write_text('{"private":"secret"}',encoding="utf-8")
    frontend=tmp_path/"ui-versions/react/frontend";frontend.mkdir(parents=True)
    (frontend/".env.local").write_text("PRIVATE_KEY=secret",encoding="utf-8")
    (frontend/"package.json").write_text("{}",encoding="utf-8")
    result=Path(feedback_queue.development_copy("a"*32))
    assert (result/"app.py").exists()
    assert (result/"ui-versions/react/frontend/package.json").exists()
    assert not (result/"ggab.db").exists()
    assert not (result/"client_secret.json").exists()
    assert not (result/"ui-versions/react/frontend/.env.local").exists()
    assert "不得自动上线" in (result/"AGENTS.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("title",[None,123,{"object":"not text"},"标题\n多行"])
def test_invalid_title_is_rejected(title):
    with pytest.raises(ValidationError):
        feedback.Submission(request_id="feedback-request-0001",title=title,content="这是用于检查无效标题的反馈内容。")
