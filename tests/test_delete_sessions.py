import sqlite3

from fastapi.testclient import TestClient

import app as server
import video_tools as vt


def test_delete_only_unmatched_and_keep_recoverable_video(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as conn:
        conn.executescript((server.ROOT / "schema.sql").read_text(encoding="utf-8"))
        conn.execute("INSERT INTO shots(match_id,rally_id,shot_id) VALUES('tagged','r','s')")
    monkeypatch.setattr(server, "DB_PATH", db)
    monkeypatch.setattr(vt, "MEDIA_ROOT", tmp_path / "media")
    originals = vt.MEDIA_ROOT / "originals"
    originals.mkdir(parents=True)
    for sid in ("tagged", "unmatched"):
        path = originals / (sid + ".mp4")
        path.write_bytes(b"test-video")
        vt.register(sid, path, path.name, {"duration": 10, "has_audio": False})
    client = TestClient(server.app)
    headers = {"X-Yuxu-Local": "1"}
    assert client.delete("/api/sessions/unmatched").status_code == 403
    assert client.delete("/api/sessions/tagged", headers=headers).status_code == 409
    assert client.delete("/api/sessions/unmatched", headers=headers).status_code == 200
    assert "unmatched" not in vt.registry()
    assert (originals / "tagged.mp4").is_file()
    assert not (originals / "unmatched.mp4").exists()
    assert next((vt.MEDIA_ROOT / "trash").glob("*/*.mp4")).read_bytes() == b"test-video"
    assert client.delete("/api/sessions/unmatched", headers=headers).status_code == 404
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"keep")
    vt.register("outside", outside, outside.name, {"duration": 10, "has_audio": False})
    assert client.delete("/api/sessions/outside", headers=headers).status_code == 409
    assert outside.read_bytes() == b"keep"


def test_shared_original_remains_until_last_session_is_removed(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as conn:
        conn.executescript((server.ROOT / "schema.sql").read_text(encoding="utf-8"))
    monkeypatch.setattr(server, "DB_PATH", db)
    monkeypatch.setattr(vt, "MEDIA_ROOT", tmp_path / "media")
    original = vt.MEDIA_ROOT / "originals" / "shared.mp4"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"shared-video")
    for sid in ("first", "second"):
        vt.register(sid, original, "shared.mp4", {"duration": 10, "has_audio": False})
    client = TestClient(server.app)
    headers = {"X-Yuxu-Local": "1"}
    first = client.delete("/api/sessions/first", headers=headers)
    assert first.status_code == 200
    assert first.json()["video_retained"] is True
    assert original.read_bytes() == b"shared-video"
    assert list(vt.registry()) == ["second"]
    assert not (vt.MEDIA_ROOT / "trash").exists()
    last = client.delete("/api/sessions/second", headers=headers)
    assert last.status_code == 200
    assert last.json()["video_retained"] is False
    assert not original.exists()
    assert next((vt.MEDIA_ROOT / "trash").glob("*/*.mp4")).read_bytes() == b"shared-video"
