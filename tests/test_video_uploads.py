import hashlib
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

import accounts
import app as server
import drive_storage as drive
import video_tools as videos
import video_uploads as uploads
from test_accounts import environment, post

HEADERS = {"X-Yuxu-Local": "1"}


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setattr(uploads, "CHUNK_SIZE", 4)
    monkeypatch.setattr(videos, "probe", lambda _: {"duration": 12, "has_audio": True})


def start(client, content=b"abcdefghij", filename="upload.mp4", **extra):
    response = post(client, "/api/uploads", {"filename": filename, "size_bytes": len(content), **extra})
    assert response.status_code == 201, response.text
    return response.json()


def chunk(client, upload_id, start_at, content, total=10, digest=None):
    return client.put("/api/uploads/" + upload_id, content=content, headers={**HEADERS,
        "Content-Range": f"bytes {start_at}-{start_at + len(content) - 1}/{total}",
        "X-Chunk-SHA256": digest or hashlib.sha256(content).hexdigest()})


def complete(client, identifier):
    return client.post("/api/uploads/" + identifier + "/complete", headers=HEADERS)


def test_resumes_from_disk_and_lost_response_retries_are_idempotent(environment, small_chunks, monkeypatch):
    alice = environment["clients"][1]
    state = start(alice, request_id="a" * 32)
    identifier = state["upload_id"]
    assert start(alice, request_id="a" * 32)["upload_id"] == identifier
    assert chunk(alice, identifier, 0, b"abcd").json()["received_bytes"] == 4
    # Simulate a process restart and an interrupted, uncommitted disk append.
    monkeypatch.setattr(uploads, "UPLOAD_LOCKS", {})
    _, temporary = uploads.paths(identifier)
    with temporary.open("ab") as target:
        target.write(b"uncommitted")
    assert alice.get("/api/uploads/" + identifier).json()["received_bytes"] == 4
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: chunk(alice, identifier, 4, b"efgh"), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert temporary.read_bytes() == b"abcdefgh"
    assert complete(alice, identifier).status_code == 409
    assert chunk(alice, identifier, 8, b"ij").status_code == 200
    first = complete(alice, identifier)
    second = complete(alice, identifier)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    record = videos.registry()[first.json()["match_id"]]
    assert accounts.owner("session", record["session_id"])["user_id"] == environment["identities"][1]["id"]
    assert len(list((videos.MEDIA_ROOT / "originals").glob("*.mp4"))) == 1
    assert not temporary.exists()


def test_upload_ownership_and_csrf_are_enforced_before_reading_bytes(environment, small_chunks):
    _, alice, bob, guest = environment["clients"]
    identifier = start(alice)["upload_id"]
    path = "/api/uploads/" + identifier
    assert guest.get(path).status_code == 401
    assert bob.get(path).status_code == 404
    assert chunk(bob, identifier, 0, b"abcd").status_code == 404
    assert complete(bob, identifier).status_code == 404
    assert bob.delete(path, headers=HEADERS).status_code == 404
    assert chunk(alice, identifier, 0, b"abcd", digest="0" * 64).status_code == 422
    assert alice.put(path, content=b"abcd", headers={"X-Yuxu-Local": "1", "X-CSRF-Token": "wrong"}).status_code == 403
    assert post(alice, "/api/uploads", {"filename": "x.mp4", "size_bytes": 10, "match_id": "video-B"}).status_code == 404
    assert alice.get(path).json()["received_bytes"] == 0


def test_bad_ranges_mismatched_retries_expiry_and_cancellation(environment, small_chunks):
    alice = environment["clients"][1]
    identifier = start(alice)["upload_id"]
    path = "/api/uploads/" + identifier
    assert chunk(alice, identifier, 4, b"efgh").status_code == 409
    assert chunk(alice, identifier, 0, b"abcd", total=11).status_code == 422
    assert chunk(alice, identifier, 0, b"abcde").status_code == 413
    assert chunk(alice, identifier, 0, b"abcd").status_code == 200
    assert chunk(alice, identifier, 0, b"wxyz").status_code == 409
    assert alice.delete(path, headers=HEADERS).status_code == 200
    assert not uploads.paths(identifier)[1].exists()
    assert alice.get(path).status_code == 404
    expired = start(alice)["upload_id"]
    manifest, temporary = uploads.paths(expired)
    data = json.loads(manifest.read_text())
    data["expires_at"] = time.time() - 1
    videos.save_json(manifest, data)
    assert alice.get("/api/uploads/" + expired).status_code == 410
    start(alice)
    assert not temporary.exists() and not manifest.exists()


class MediaResponse(io.BytesIO):
    def __init__(self, content, code=200, headers=None):
        super().__init__(content)
        self.code = code
        self.headers = headers or {"Content-Length": str(len(content)), "Content-Type": "video/mp4"}


@pytest.fixture
def fake_drive(monkeypatch, small_chunks):
    monkeypatch.setenv("YUXU_UPLOAD_STORAGE", "google_drive")
    monkeypatch.setattr(drive, "configuration", lambda: {"folder_id": "test-folder-id"})
    records, offsets, sent = {}, {}, []

    def begin(data):
        records[data["upload_id"]] = {"data": dict(data), "content": b""}
        return "https://www.googleapis.com/upload/secret-session-" + data["upload_id"]

    def progress(data, content=None, start=None):
        stored = records[data["upload_id"]]
        if content is not None:
            sent.append((start, bytes(content)))
            assert start == len(stored["content"])
            stored["content"] += bytes(content)
        offset = len(stored["content"])
        offsets[data["upload_id"]] = offset
        meta = None
        if offset == data["size_bytes"]:
            meta = {"id": "drive-" + data["upload_id"], "size": str(offset),
                    "md5Checksum": hashlib.md5(stored["content"]).hexdigest(), "mimeType": "video/mp4",
                    "appProperties": {"ggab_upload_id": data["upload_id"], "ggab_owner": data["owner_id"]},
                    "capabilities": {"canDownload": True}}
            stored["meta"] = meta
        return offset, meta

    def metadata(identifier):
        return records[identifier.removeprefix("drive-")]["meta"]

    def media(record, byte_range=None):
        content = records[record["drive_file_id"].removeprefix("drive-")]["content"]
        if not byte_range:
            return MediaResponse(content)
        begin, end = map(int, byte_range.removeprefix("bytes=").split("-"))
        value = content[begin:end + 1]
        return MediaResponse(value, 206, {"Content-Type": "video/mp4", "Content-Length": str(len(value)),
                             "Content-Range": f"bytes {begin}-{end}/{len(content)}"})

    monkeypatch.setattr(drive, "begin", begin)
    monkeypatch.setattr(drive, "progress", progress)
    monkeypatch.setattr(drive, "metadata", metadata)
    monkeypatch.setattr(drive, "media_response", media)
    return {"records": records, "offsets": offsets, "sent": sent, "progress": progress}


def test_drive_upload_recovers_lost_ack_and_links_private_video(environment, fake_drive, monkeypatch):
    _, alice, bob, _ = environment["clients"]
    state = start(alice)
    identifier = state["upload_id"]
    original = fake_drive["progress"]
    lost = [False]

    def lose_response(data, content=None, start=None):
        result = original(data, content, start)
        if content is not None and not lost[0]:
            lost[0] = True
            raise HTTPException(503, "lost acknowledgement")
        return result

    monkeypatch.setattr(drive, "progress", lose_response)
    assert chunk(alice, identifier, 0, b"abcd").status_code == 503
    resumed = alice.get("/api/uploads/" + identifier)
    assert resumed.json()["received_bytes"] == 4
    assert "secret-session" not in resumed.text
    assert chunk(alice, identifier, 0, b"abcd").status_code == 200
    assert chunk(alice, identifier, 4, b"efgh").status_code == 200
    assert chunk(alice, identifier, 8, b"ij").status_code == 200
    assert not uploads.paths(identifier)[1].exists()
    queued = complete(alice, identifier)
    assert queued.status_code == 202
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = alice.get("/api/uploads/" + identifier).json()
        if state["status"] in {"complete", "failed"}:
            break
        time.sleep(.01)
    assert state["status"] == "complete", state
    session_id = state["result"]["match_id"]
    record = videos.registry()[session_id]
    assert record["storage"] == "google_drive"
    assert accounts.owner("session", session_id)["user_id"] == environment["identities"][1]["id"]
    assert fake_drive["records"][identifier]["content"] == b"abcdefghij"
    assert alice.get(f"/api/matches/{session_id}/video", headers={"Range": "bytes=2-5"}).content == b"cdef"
    assert bob.get(f"/api/matches/{session_id}/video").status_code == 404
    # Playback/listing do not require a local cache; processing rebuilds it with the same timestamp.
    from pathlib import Path
    path = Path(record["path"])
    timestamp = path.stat().st_mtime_ns
    path.unlink()
    assert next(item for item in alice.get("/api/matches").json() if item["match_id"] == session_id)["has_video"]
    token = accounts.principal.set(environment["identities"][1])
    try:
        _, rebuilt = videos.source(session_id)
        assert rebuilt.read_bytes() == b"abcdefghij"
        assert rebuilt.stat().st_mtime_ns == timestamp
    finally:
        accounts.principal.reset(token)
    assert complete(alice, identifier).json() == state["result"]


def test_drive_partial_acceptance_resends_only_missing_bytes(environment, fake_drive, monkeypatch):
    alice = environment["clients"][1]
    identifier = start(alice)["upload_id"]
    original = fake_drive["progress"]
    partial = [True]

    def partial_progress(data, content=None, start=None):
        if content is not None and partial[0]:
            partial[0] = False
            return original(data, content[:2], start)
        return original(data, content, start)

    monkeypatch.setattr(drive, "progress", partial_progress)
    assert chunk(alice, identifier, 0, b"abcd").status_code == 503
    assert alice.get("/api/uploads/" + identifier).json()["received_bytes"] == 0
    assert chunk(alice, identifier, 0, b"wxyz").status_code == 409
    assert chunk(alice, identifier, 0, b"abcd").status_code == 200
    assert fake_drive["sent"] == [(0, b"ab"), (2, b"cd")]


def test_drive_is_required_and_untrusted_hosts_never_receive_credentials(environment, monkeypatch, tmp_path):
    monkeypatch.setenv("YUXU_UPLOAD_STORAGE", "google_drive")
    monkeypatch.setenv("YUXU_DRIVE_CREDENTIALS_PATH", str(tmp_path / "missing.json"))
    alice = environment["clients"][1]
    response = post(alice, "/api/uploads", {"filename": "x.mp4", "size_bytes": 10})
    assert response.status_code == 503
    assert "管理员尚未连接" in response.json()["detail"]
    legacy = alice.post("/api/sessions/import?filename=x.mp4", content=b"local-bypass", headers=HEADERS)
    assert legacy.status_code == 409
    assert not (videos.MEDIA_ROOT / "uploads").exists()
    monkeypatch.setattr(drive, "token", lambda **_: pytest.fail("Credential must not be read for an untrusted URL"))
    with pytest.raises(HTTPException) as error:
        drive.open_api("https://example.test/upload")
    assert error.value.status_code == 502


def test_drive_changes_before_registration_do_not_replace_the_uploaded_video(environment, fake_drive):
    alice = environment["clients"][1]
    identifier = start(alice)["upload_id"]
    for offset, content in [(0, b"abcd"), (4, b"efgh"), (8, b"ij")]:
        assert chunk(alice, identifier, offset, content).status_code == 200
    data = json.loads(uploads.paths(identifier)[0].read_text())
    fake_drive["records"][identifier]["meta"]["md5Checksum"] = "0" * 32
    with pytest.raises(HTTPException) as error:
        drive.register(data)
    assert error.value.status_code == 409
    assert data["session_id"] not in videos.registry()


def test_parallel_verification_checks_original_chunk_hashes(tmp_path):
    path = tmp_path / "cached.mp4"
    path.write_bytes(b"abcdefgh")
    data = {"chunks": [{"size": 4, "sha256": hashlib.sha256(value).hexdigest()} for value in (b"abcd", b"efgh")]}
    assert drive.verify_upload(path, data) == hashlib.sha256(b"abcdefgh").hexdigest()
    path.write_bytes(b"abcdEFGH")
    with pytest.raises(HTTPException) as error:
        drive.verify_upload(path, data)
    assert error.value.status_code == 409
