import hashlib
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

import accounts
import app as server
import security_config
import upload_limits
import video_tools as videos
from test_accounts import environment, post, PASSWORD


def test_registration_closed_and_only_admin_can_invite(environment, monkeypatch):
    monkeypatch.setenv("YUXU_SELF_REGISTRATION", "0")
    admin, alice, _, guest = environment["clients"]
    monkeypatch.setattr(accounts, "hash_password", lambda _: pytest.fail("closed registration must not hash"))
    assert post(guest, "/api/auth/register", {"email": "intruder@example.test", "password": PASSWORD}).status_code == 403
    assert post(guest, "/api/admin/users", {"email": "intruder@example.test"}).status_code == 401
    assert post(alice, "/api/admin/users", {"email": "intruder@example.test"}).status_code == 403
    assert guest.get("/api/auth/status").json()["registration_open"] is False


def test_invitation_is_one_use_and_does_not_change_admin_session(environment):
    admin, _, _, guest = environment["clients"]
    original = admin.get("/api/me").json()
    created = post(admin, "/api/admin/users", {"email": "Invited@Example.test"})
    assert created.status_code == 201
    invite = created.json()
    assert invite["role"] == "user" and invite["email"] == "invited@example.test"
    assert admin.get("/api/me").json() == original
    assert post(guest, "/api/auth/login", {"email": invite["email"], "password": PASSWORD}).status_code == 401
    payload = {"token": invite["token"], "password": PASSWORD}
    assert post(guest, "/api/auth/password-reset/complete", payload).status_code == 200
    assert post(guest, "/api/auth/password-reset/complete", payload).status_code == 400
    assert post(guest, "/api/auth/login", {"email": invite["email"], "password": PASSWORD}).status_code == 200
    with accounts.database() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit WHERE action='create_user' AND target_user=?", (invite["id"],)).fetchone()[0] == 1


def test_invalid_or_disabled_reset_is_rejected_before_password_hash(environment, monkeypatch):
    admin, _, _, guest = environment["clients"]
    invited = post(admin, "/api/admin/users", {"email": "disabled@example.test"}).json()
    assert post(admin, f"/api/admin/users/{invited['id']}/state", {"enabled": False}).status_code == 200
    monkeypatch.setattr(accounts, "hash_password", lambda _: pytest.fail("invalid code must not hash"))
    for value in ("x" * 32, invited["token"]):
        assert post(guest, "/api/auth/password-reset/complete", {"token": value, "password": PASSWORD}).status_code == 400
    assert post(guest, "/api/auth/setup", {"email": "x@example.test", "password": PASSWORD}).status_code == 409


def test_login_throttles_account_across_addresses_and_persists(environment):
    for index in range(15):
        request = Request({"type": "http", "client": (f"192.0.2.{index}", 1234)})
        accounts.rate_limit(request, "login-test", "target@example.test")
    with accounts.database() as conn:
        assert conn.execute("SELECT COUNT(*) FROM auth_limits").fetchone()[0] >= 16
    with pytest.raises(HTTPException) as error:
        accounts.rate_limit(Request({"type": "http", "client": ("192.0.2.200", 1)}), "login-test", "target@example.test")
    assert error.value.status_code == 429 and error.value.headers["Retry-After"] == "300"


def test_auth_body_limits_and_headers_apply_to_rejections(environment):
    guest = environment["clients"][-1]
    for body in (b"x" * 16385, iter([b"x" * 8000, b"y" * 9000])):
        response = guest.post("/api/auth/login", content=body, headers={"X-Yuxu-Local": "1"})
        assert response.status_code == 413
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert guest.get("/api/matches").headers["x-frame-options"] == "DENY"
    page = guest.get("/login").text
    assert 'data-mode="register"' in page and 'data-mode="reset"' in page


def test_https_cookie_origin_and_local_bootstrap(environment, monkeypatch):
    monkeypatch.setenv("YUXU_PUBLIC_ORIGIN", "https://video.example.test")
    with TestClient(server.app, base_url="https://video.example.test", client=("192.0.2.8", 1)) as client:
        login = post(client, "/api/auth/login", {"email": "alice@example.test", "password": PASSWORD})
        assert login.status_code == 200, login.text
        assert "Secure" in login.headers["set-cookie"] and "HttpOnly" in login.headers["set-cookie"]
        assert login.headers["strict-transport-security"] == "max-age=31536000"
        bad_origin = client.post("/api/auth/login", json={"email": "alice@example.test", "password": PASSWORD},
                                 headers={"X-Yuxu-Local": "1", "Origin": "http://video.example.test"})
        assert bad_origin.status_code == 403
    with TestClient(server.app, base_url="http://video.example.test") as client:
        assert client.get("/health").status_code == 403
    with TestClient(server.app, base_url="https://other.example.test") as client:
        assert client.get("/health").status_code == 403
    with pytest.raises(RuntimeError):
        monkeypatch.setenv("YUXU_PUBLIC_ORIGIN", "http://unsafe.example.test")
        security_config.public_origin()


def test_bootstrap_cannot_be_taken_over_remotely(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "ENFORCE_AUTH", True)
    with TestClient(server.app, base_url="http://127.0.0.1:8000", client=("192.0.2.9", 1)) as client:
        assert post(client, "/api/auth/setup", {"email": "x@example.test", "password": PASSWORD}).status_code == 403
    assert not accounts.initialized()


def test_private_proxy_requires_vpn_identity_and_preserves_local_extension_login(environment, monkeypatch):
    monkeypatch.setenv("YUXU_PUBLIC_ORIGIN", "https://video.example.test")
    monkeypatch.setenv("YUXU_REQUIRE_VPN_IDENTITY", "1")
    with TestClient(server.app, base_url="https://video.example.test") as client:
        assert client.get("/health").status_code == 403
        client.headers["Tailscale-User-Login"] = "vpn@example.test"
        assert client.get("/health").status_code == 200
        login = post(client, "/api/auth/login", {"email": "alice@example.test", "password": PASSWORD})
        assert login.status_code == 200 and "Secure" in login.headers["set-cookie"]
        assert client.get("/api/me").json()["email"] == "alice@example.test"
    with TestClient(server.app, base_url="http://127.0.0.1:8000") as client:
        login = post(client, "/api/auth/login", {"email": "admin@example.test", "password": PASSWORD})
        assert login.status_code == 200 and "Secure" not in login.headers["set-cookie"]
        assert client.get("/api/me").json()["role"] == "admin"


def small_policy(monkeypatch, capacity=100, daily=100, count=20):
    monkeypatch.setattr(upload_limits, "policy", lambda: {"capacity_bytes": capacity, "daily_bytes": daily, "daily_files": count})


def test_capacity_reservations_are_atomic_durable_and_include_history(environment, monkeypatch):
    owner = environment["identities"][1]["id"]
    baseline = upload_limits.historical_usage(owner)
    small_policy(monkeypatch, capacity=baseline + 10)
    def begin(identifier):
        try:
            upload_limits.reserve(identifier, owner, 10)
            return 201
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(begin, ["one", "two"])) == [201, 413]
    summary = upload_limits.summary(owner)
    assert summary["used_capacity_bytes"] == baseline + 10
    assert summary["used_daily_files"] == 1
    with accounts.database() as conn:
        identifier = conn.execute("SELECT id FROM upload_budget WHERE user_id=?", (owner,)).fetchone()[0]
    upload_limits.reserve(identifier, owner, 10)
    assert upload_limits.summary(owner) == summary
    # Another account has an independent allowance.
    upload_limits.reserve("bob", environment["identities"][2]["id"], 10)


def test_daily_cancel_and_expiry_do_not_launder_upload_budget(environment, monkeypatch):
    owner = environment["identities"][1]["id"]
    small_policy(monkeypatch, capacity=100, daily=10, count=1)
    upload_limits.reserve("one", owner, 10)
    upload_limits.release_local("one")
    assert upload_limits.summary(owner)["used_capacity_bytes"] == upload_limits.historical_usage(owner)
    with pytest.raises(HTTPException) as error:
        upload_limits.reserve("two", owner, 1)
    assert error.value.status_code == 429
    with accounts.database() as conn:
        conn.execute("UPDATE upload_budget SET created=?", (time.time() - 86401,))
    upload_limits.reserve("two", owner, 1)


def test_upload_api_enforces_budget_and_blocks_legacy_bypass(environment, monkeypatch):
    alice = environment["clients"][1]
    owner = environment["identities"][1]["id"]
    small_policy(monkeypatch, capacity=upload_limits.historical_usage(owner) + 4)
    assert post(alice, "/api/uploads", {"filename": "too-big.mp4", "size_bytes": 5}).status_code == 413
    state = post(alice, "/api/uploads", {"filename": "allowed.mp4", "size_bytes": 4, "request_id": "a" * 32})
    assert state.status_code == 201
    assert post(alice, "/api/uploads", {"filename": "allowed.mp4", "size_bytes": 4, "request_id": "a" * 32}).json() == state.json()
    assert alice.get("/api/uploads/limits").json()["used_daily_files"] == 1
    assert alice.post("/api/sessions/import?filename=legacy.mp4", content=b"12345", headers={"X-Yuxu-Local": "1"}).status_code == 409
    assert alice.get("/api/admin/upload-limits").status_code == 403
    assert environment["clients"][-1].get("/api/uploads/limits").status_code == 401


def test_drive_cancel_keeps_capacity_reservation(environment, monkeypatch):
    import video_uploads
    owner = environment["identities"][1]
    small_policy(monkeypatch, capacity=100)
    monkeypatch.setenv("YUXU_UPLOAD_STORAGE", "google_drive")
    monkeypatch.setattr(video_uploads.drive_storage, "configuration", lambda: {})
    monkeypatch.setattr(video_uploads.drive_storage, "begin", lambda _: "https://www.googleapis.com/mock")
    context = accounts.principal.set(owner)
    try:
        state = video_uploads.create("upload.mp4", 10, "new", "new", True)
        video_uploads.cancel(state["upload_id"])
        assert upload_limits.summary(owner["id"])["used_capacity_bytes"] == upload_limits.historical_usage(owner["id"]) + 10
    finally:
        accounts.principal.reset(context)
