"""Exercise real session, ownership and CSV boundaries across three clients."""
import csv
import io
import json
import sqlite3
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import accounts
import app as server
import tracker
import video_tools as vt

PASSWORD = "local-password-2026"
SUBJECT = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def post(client, path, body):
    return client.post(path, json=body, headers={"X-Yuxu-Local":"1"})


def activate_user(admin, client, email):
    invitation = post(admin, "/api/admin/users", {"email": email})
    assert invitation.status_code == 201, invitation.text
    result = post(client, "/api/auth/password-reset/complete", {"token": invitation.json()["token"], "password": PASSWORD})
    assert result.status_code == 200, result.text
    return post(client, "/api/auth/login", {"email": email, "password": PASSWORD})


def upload_video(client, filename, content):
    import hashlib
    state = post(client, "/api/uploads", {"filename": filename, "size_bytes": len(content)})
    assert state.status_code == 201, state.text
    identifier = state.json()["upload_id"]
    uploaded = client.put("/api/uploads/" + identifier, content=content, headers={"X-Yuxu-Local": "1",
        "Content-Range": f"bytes 0-{len(content)-1}/{len(content)}", "X-Chunk-SHA256": hashlib.sha256(content).hexdigest()})
    assert uploaded.status_code == 200, uploaded.text
    response = post(client, "/api/uploads/" + identifier + "/complete", {})
    assert response.status_code == 200, response.text
    return response


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "ENFORCE_AUTH", True)
    db = tmp_path / "shots.db"
    monkeypatch.setattr(server, "DB_PATH", db)
    with sqlite3.connect(db) as conn:
        conn.executescript((server.ROOT / "schema.sql").read_text(encoding="utf-8"))
        for match, own in [("A",1),("A",0),("B",1),("B",0),("X",1)]:
            conn.execute("INSERT INTO shots(match_id,rally_id,shot_id,match_title,game_number,rally_number_in_game,shot_number_in_rally,striker,striker_name,striker_is_you,is_final_shot,rally_won_by_you) VALUES(?,?,?,?,1,1,?,?,?,?,1,1)",
                         (match,"r-"+match,match+str(own),"same-title.mp4",2-own,"A1" if own else "B1","own" if own else "opponent",own))
    with ExitStack() as stack:
        clients = [stack.enter_context(TestClient(server.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))) for _ in range(4)]
        admin, alice, bob, guest = clients
        identities = []
        for client, email, route in [(admin,"admin@example.test","setup"),(alice,"alice@example.test","activate"),(bob,"bob@example.test","activate")]:
            response = (post(client, "/api/auth/setup", {"email":email,"password":PASSWORD}) if route == "setup"
                        else activate_user(admin, client, email))
            assert response.status_code == 200, response.text
            identity = response.json()
            client.headers["X-CSRF-Token"] = identity["csrf"]
            identities.append(identity)
        for index, label in [(1,"A"),(2,"B")]:
            user = identities[index]
            accounts.claim("match",label,user["id"])
            token = accounts.principal.set(user)
            try:
                source = tmp_path / f"{label}.mp4"
                source.write_bytes((label+" video").encode())
                vt.register("video-"+label,source,source.name,{"duration":10,"has_audio":False})
                accounts.claim("snapshot","snapshot-"+label)
                accounts.claim("project","project-"+label)
                accounts.claim("clip",label.lower()*32)
                with tracker.database() as conn:
                    players = [{"slot":"A1","name":label+" Saved Self","handedness":"left","is_you":True},
                               {"slot":"B1","name":label+" Saved Opponent","handedness":"right","is_you":False}]
                    conn.execute("INSERT INTO projects VALUES(?,?,?)", ("project-"+label,json.dumps({"id":"project-"+label,"title":label,"session_id":"video-"+label,"players":players}),"now"))
                    conn.execute("INSERT INTO snapshots VALUES(?,?)", ("snapshot-"+label,json.dumps({"id":"snapshot-"+label,"session_id":"video-"+label})))
                    (vt.MEDIA_ROOT/"tracker"/("snapshot-"+label+".jpg")).write_bytes(b"image")
            finally:
                accounts.principal.reset(token)
        yield {"clients":clients,"identities":identities,"db":db,"tmp":tmp_path}


def test_bootstrap_is_single_admin_and_passwords_are_hashed(environment):
    admin, alice, bob, guest = environment["clients"]
    assert post(guest,"/api/auth/setup",{"email":"other@example.test","password":PASSWORD}).status_code == 409
    with accounts.database() as conn:
        rows = conn.execute("SELECT password_hash,role FROM users").fetchall()
    assert sum(r[1]=="admin" for r in rows) == 1
    assert all(r[0].startswith("$argon2id$") and PASSWORD not in r[0] for r in rows)
    assert guest.get("/api/matches").status_code == 401
    assert guest.get("/health").json() == {"status":"ok"}
    assert guest.get("/tracker",follow_redirects=False).status_code == 303


def test_csv_and_statistics_include_opponents_but_only_owned_matches(environment):
    _, alice, bob, _ = environment["clients"]
    for client, label in [(alice,"A"),(bob,"B")]:
        result = client.get("/api/my/export.csv")
        assert result.status_code == 403, result.text
        assert 'text/csv' not in result.headers.get('content-type', '')
        assert {r["match_id"] for r in client.get("/api/matches").json()} == {label,"video-"+label}
        breakdown = client.get(f"/api/matches/{label}/breakdown")
        assert breakdown.status_code == 200, breakdown.text
        assert breakdown.json()["summary"]["shots"] == 2
        options = {item["name"]: item for item in client.get("/api/tracker/player-options").json()}
        assert options[label+" Saved Self"] == {"name":label+" Saved Self","handedness":"left","is_you":1}
        assert options[label+" Saved Opponent"]["is_you"] == 0
        assert ("B" if label == "A" else "A")+" Saved Self" not in options
        assert options["own"]["is_you"] == 0


def test_player_options_are_personal_even_for_admin_and_unknown_matches(environment):
    admin, alice, bob, guest = environment["clients"]
    with sqlite3.connect(environment["db"]) as conn:
        conn.execute("UPDATE shots SET striker_name=match_id || ' Match Player'")
    assert admin.get("/api/tracker/player-options").json() == []
    assert guest.get("/api/tracker/player-options").status_code == 401
    accounts.claim("match", "X", environment["identities"][0]["id"])
    for client, label in [(admin, "X"), (alice, "A"), (bob, "B")]:
        names = {item["name"] for item in client.get("/api/tracker/player-options").json()}
        assert label + " Match Player" in names
        for other in {"A", "B", "X"} - {label}:
            assert other + " Match Player" not in names
            assert other + " Saved Self" not in names


def test_admin_project_edits_remember_names_for_owner_and_same_names_stay_independent(environment, monkeypatch):
    admin, alice, bob, _ = environment["clients"]
    monkeypatch.setattr(tracker, "frame_bytes", lambda *args: b"test jpeg")
    for client, label, hand in [(alice, "A", "left"), (bob, "B", "right")]:
        snap = post(client, "/api/tracker/snapshots", {"session_id": "video-" + label, "time": 0,
                    "rotation": 0, "box": {"x": 0, "y": 0, "width": 1, "height": 1}})
        assert snap.status_code == 201, snap.text
        body = {"title": "Admin edited", "session_id": "video-" + label, "game_type": "singles",
                "playing": False, "players": [{"slot": slot, "name": "Shared Name" if slot == "A1" else label + " New Opponent",
                "handedness": hand, "is_you": False, "snapshot_id": snap.json()["id"]} for slot in ("A1", "B1")], "segments": []}
        edited = admin.put("/api/tracker/projects/project-" + label, json=body, headers={"X-Yuxu-Local": "1"})
        assert edited.status_code == 200, edited.text
        # Persistence must survive removal of the project source for suggestions.
        with tracker.database() as conn:
            conn.execute("DELETE FROM projects WHERE id=?", ("project-" + label,))
        options = {item["name"]: item for item in client.get("/api/tracker/player-options").json()}
        assert options["Shared Name"]["handedness"] == hand
    assert admin.get("/api/tracker/player-options").json() == []
    for client, label, hand in [(alice, "A", "left"), (bob, "B", "right")]:
        options = {item["name"]: item for item in client.get("/api/tracker/player-options").json()}
        assert options["Shared Name"]["handedness"] == hand
        assert ("B" if label == "A" else "A") + " New Opponent" not in options


@pytest.mark.parametrize("path", [
    "/api/matches/B/breakdown", "/api/matches/B/export", "/api/matches/B/video",
    "/api/matches/video-B/video", "/api/matches/video-B/playback-preview",
    "/api/tracker/projects/project-B",
    "/api/tracker/snapshots/snapshot-B", "/api/tracker/frame/video-B",
    "/api/clips/"+"b"*32, "/api/clips/"+"b"*32+"/download", "/api/my/export.csv?match_id=B"
])
def test_every_resource_route_denies_foreign_ids(environment, path):
    alice = environment["clients"][1]
    expected = 403 if path.endswith('/export') or '/export.csv' in path  else 404
    assert alice.get(path, headers={"Range":"bytes=0-1"}).status_code == expected


def test_raw_export_pairing_and_local_paths_require_admin(environment):
    _, alice, _, _ = environment["clients"]
    assert post(alice,"/api/sessions/link",{"relative_path":"other.mp4"}).status_code == 403
    assert alice.get("/api/sessions/local-videos").json() == {"directory":"","videos":[]}
    assert alice.get("/api/admin/users").status_code == 403
    assert [p["id"] for p in alice.get("/api/tracker/projects").json()] == ["project-A"]


def test_csrf_host_and_worker_get_are_checked(environment):
    _, alice, _, guest = environment["clients"]
    assert alice.post("/api/auth/logout",headers={"X-CSRF-Token":"wrong","X-Yuxu-Local":"1"}).status_code == 403
    assert guest.get("/api/auth/status",headers={"Host":"evil.example"}).status_code == 403
    assert guest.get("/api/tracker/snapshots/snapshot-A").status_code == 401


def test_claiming_unknown_match_preserves_all_rows_and_cannot_transfer(environment):
    admin, alice, bob, _ = environment["clients"]
    alice_id = environment["identities"][1]["id"]
    response = post(admin,"/api/admin/resources/match/X/assign",{"user_id":alice_id,"reason":"核对旧项目和上传记录"})
    assert response.status_code == 200, response.text
    assert {m['match_id'] for m in alice.get('/api/matches').json()} == {'A', 'X', 'video-A'}
    with sqlite3.connect(environment['db']) as conn:
        assert conn.execute("SELECT COUNT(*) FROM shots WHERE match_id IN ('A','X')").fetchone()[0] == 3
    assert post(admin,"/api/admin/resources/match/X/assign",{"user_id":environment["identities"][2]["id"],"reason":"不能覆盖原所有者"}).status_code == 409
    assert len([event for event in admin.get("/api/admin/audit").json() if event["action"] == "assign"]) == 1
    assert "X" not in {m["match_id"] for m in bob.get("/api/matches").json()}


def test_claim_project_is_atomic_on_associated_video_conflict(environment):
    admin = environment["clients"][0]
    with tracker.database() as conn:
        conn.execute("INSERT INTO projects VALUES(?,?,?)",("old",json.dumps({"id":"old","title":"old","session_id":"video-B","players":[]}),"now"))
    r=post(admin,"/api/admin/resources/project/old/assign",{"user_id":environment["identities"][1]["id"],"reason":"已有视频归属冲突"})
    assert r.status_code == 409
    assert accounts.owner("project","old") is None


def test_reset_is_single_use_and_revokes_old_sessions(environment):
    admin, alice, _, guest = environment["clients"]
    uid=environment["identities"][1]["id"]
    code=post(admin,f"/api/admin/users/{uid}/reset-token",{}).json()["token"]
    body={"token":code,"password":"new-password-2026"}
    assert post(guest,"/api/auth/password-reset/complete",body).status_code == 200
    assert alice.get("/api/matches").status_code == 401
    assert post(guest,"/api/auth/password-reset/complete",body).status_code == 400
    assert post(guest,"/api/auth/login",{"email":"alice@example.test","password":"new-password-2026"}).status_code == 200


def test_suspension_blocks_login_and_new_tasks(environment):
    admin, alice, _, guest=environment["clients"]
    uid=environment["identities"][1]["id"]
    assert post(admin,f"/api/admin/users/{uid}/state",{"enabled":False}).status_code == 200
    assert alice.get("/api/my/export.csv").status_code == 401
    assert post(guest,"/api/auth/login",{"email":"alice@example.test","password":PASSWORD}).status_code == 401


def test_empty_user_and_admin_personal_export_do_not_receive_the_master_csv(environment):
    admin, _, _, guest = environment["clients"]
    empty = activate_user(admin, guest, "empty@example.test")
    assert empty.status_code == 200
    for client in (guest, admin):
        exported = client.get("/api/my/export.csv")
        assert exported.status_code == 403
        assert 'text/csv' not in exported.headers.get('content-type', '')


def test_local_team_view_does_not_rewrite_original_flags(environment):
    alice = environment["clients"][1]
    with tracker.database() as conn:
        saved={"id":"project-A","title":"A","session_id":"video-A","players":[{"slot":"A1","is_you":True},{"slot":"B1","is_you":False}]}
        conn.execute("UPDATE projects SET payload=? WHERE id='project-A'",(json.dumps(saved),))
    with sqlite3.connect(environment["db"]) as conn:
        conn.execute("CREATE TABLE match_video_links(match_id TEXT PRIMARY KEY, session_id TEXT, project_id TEXT)")
        conn.execute("INSERT INTO match_video_links VALUES(?,?,?)", ("A","video-A","project-A"))
        conn.execute("UPDATE shots SET striker_is_you=0,rally_won_by_you=0,score_a_before=0,score_b_before=0,score_a_after=1,score_b_after=0 WHERE match_id='A'")
    data=alice.get("/api/matches/A/breakdown").json()
    assert data["summary"]["own_shots"] == 1
    assert data["summary"]["wins"] == 1
    assert data["perspective_labels"]["you"] == "A 方"
    with sqlite3.connect(environment['db']) as conn:
        assert {r[0] for r in conn.execute("SELECT striker_is_you FROM shots WHERE match_id='A'")} == {0}


def test_new_upload_and_shared_file_keep_distinct_owned_sessions(environment, monkeypatch):
    _, alice, bob, _ = environment["clients"]
    monkeypatch.setattr(vt, "probe", lambda path: {"duration": 10, "has_audio": False})
    uploads = []
    for client in (alice, bob):
        response = upload_video(client, "shared.mp4", b"same original video")
        uploads.append(response.json()["match_id"])
    a, b = uploads
    assert a != b
    assert accounts.owner("session", a)["user_id"] == environment["identities"][1]["id"]
    assert accounts.owner("session", b)["user_id"] == environment["identities"][2]["id"]
    assert vt.registry()[a]["path"] == vt.registry()[b]["path"]
    assert alice.get(f"/api/matches/{b}/video").status_code == 404
    assert bob.get(f"/api/matches/{a}/video").status_code == 404
    assert post(alice, "/api/uploads", {"filename": "shared.mp4", "match_id": b, "size_bytes": 7}).status_code == 404
    deleted = alice.delete(f"/api/sessions/{a}", headers={"X-Yuxu-Local": "1"})
    assert deleted.status_code == 200 and deleted.json()["video_retained"]
    assert bob.get(f"/api/matches/{b}/video").content == b"same original video"


def test_delete_project_preserves_hidden_ownership_for_local_bindings(environment):
    alice = environment["clients"][1]
    accounts.bind_match("A","project-A")
    r=alice.delete("/api/tracker/projects/project-A",headers={"X-Yuxu-Local":"1"})
    assert r.status_code == 200, r.text
    accounts.bind_match("A","project-A")
    assert accounts.owner("match","A")["hidden"] == 1
    assert alice.get("/api/matches/A/breakdown").status_code == 404
    assert alice.get('/api/my/export.csv').status_code == 403
    assert 'A' not in {m['match_id'] for m in alice.get('/api/matches').json()}


def test_one_browser_keeps_two_contexts_and_logout_independent(environment):
    browser = environment["clients"][3]
    a, b = "/s/" + "1" * 32, "/s/" + "2" * 32
    identities = []
    for prefix, email in [(a, "alice@example.test"), (b, "bob@example.test")]:
        response = post(browser, prefix + "/api/auth/login", {"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        assert f"Path={prefix}/" in response.headers["set-cookie"]
        assert "HttpOnly" in response.headers["set-cookie"]
        identities.append(response.json())
    for prefix, identity, match in [(a, identities[0], "A"), (b, identities[1], "B")]:
        assert browser.get(prefix + "/api/me").json()["id"] == identity["id"]
        assert browser.get(prefix + '/api/my/export.csv').status_code == 403
        assert {m['match_id'] for m in browser.get(prefix + '/api/matches').json()} == {match, 'video-' + match}
        video = browser.get(prefix + f"/api/matches/video-{match}/video", headers={"Range": "bytes=0-1"})
        assert video.status_code == 206 and video.content == (match + " ").encode()
        assert browser.get(prefix + f"/api/tracker/snapshots/snapshot-{match}").content == b"image"
        names = {item["name"] for item in browser.get(prefix + "/api/tracker/player-options").json()}
        assert match + " Saved Self" in names
        assert ("B" if match == "A" else "A") + " Saved Self" not in names
        assert browser.get(prefix + f"/api/matches/{'B' if match == 'A' else 'A'}/video").status_code == 404
    page = browser.get(a + "/tracker")
    assert page.status_code == 200
    assert f'src="{a}/static/account-context.js"' in page.text
    assert f'href="{a}/insights/match-breakdown"' in page.text
    assert browser.get(a + "/static/account-context.js").status_code == 200
    assert browser.get(a + "/account/tokens").status_code == 404
    account_page = browser.get(a + "/account")
    assert account_page.status_code == 200
    assert f'src="{a}/static/accounts.js?' in account_page.text
    # Another tab's CSRF cannot act as this context.
    assert browser.post(a + "/api/auth/logout", headers={"X-Yuxu-Local": "1", "X-CSRF-Token": identities[1]["csrf"]}).status_code == 403
    assert browser.post(a + "/api/auth/logout", headers={"X-Yuxu-Local": "1", "X-CSRF-Token": identities[0]["csrf"]}).status_code == 200
    assert browser.get(a + "/api/me").status_code == 401
    assert browser.get(b + "/api/me").json()["id"] == identities[1]["id"]
    assert browser.get(b + "/api/my/export.csv").status_code == 403


def test_logged_in_login_pages_open_match_breakdown_for_both_roles(environment):
    browser = environment['clients'][3]
    for digit, email in [('8', 'admin@example.test'), ('9', 'alice@example.test')]:
        prefix = '/s/' + digit * 32
        assert post(browser, prefix + '/api/auth/login', {'email': email, 'password': PASSWORD}).status_code == 200
        response = browser.get(prefix + '/login', follow_redirects=False)
        assert response.status_code == 303
        assert response.headers['location'] == prefix + '/insights/match-breakdown'
        assert browser.get(response.headers['location']).status_code == 200
        other = browser.get(prefix + '/login?new=1', follow_redirects=False)
        assert other.status_code == 303 and other.headers['location'] != response.headers['location']


def test_context_selector_is_not_a_credential_and_never_falls_back_to_legacy_admin(environment):
    admin = environment["clients"][0]
    a, wrong = "/s/" + "3" * 32, "/s/" + "4" * 32
    response = post(admin, a + "/api/auth/login", {"email": "alice@example.test", "password": PASSWORD})
    assert response.status_code == 200
    assert admin.get(a + "/api/me").json()["role"] == "user"
    assert admin.get(a + "/api/admin/users").status_code == 403
    assert admin.get(wrong + "/api/me").status_code == 401
    scoped_token = next(cookie.value for cookie in admin.cookies.jar if cookie.name == accounts.SCOPED_COOKIE)
    admin.cookies.set(accounts.SCOPED_COOKIE, scoped_token, path=wrong + "/", domain="127.0.0.1")
    assert admin.get(wrong + "/api/me").status_code == 401
    assert admin.get("/api/me").json()["role"] == "admin"
    new = admin.get(a + "/login?new=1", follow_redirects=False)
    assert new.status_code == 303 and new.headers["location"] not in (a + "/login", wrong + "/login")
    assert admin.get(new.headers["location"].removesuffix("/login") + "/api/me").status_code == 401


def test_password_reset_revokes_all_target_contexts_without_logging_out_other_user(environment):
    admin, _, _, browser = environment["clients"]
    prefixes = ["/s/" + str(i) * 32 for i in (5, 6, 7)]
    for prefix, email in zip(prefixes, ["alice@example.test", "alice@example.test", "bob@example.test"]):
        assert post(browser, prefix + "/api/auth/login", {"email": email, "password": PASSWORD}).status_code == 200
    code = post(admin, f'/api/admin/users/{environment["identities"][1]["id"]}/reset-token', {}).json()["token"]
    assert post(browser, prefixes[0] + "/api/auth/password-reset/complete", {"token": code, "password": "reset-password-2026"}).status_code == 200
    assert browser.get(prefixes[0] + "/api/me").status_code == 401
    assert browser.get(prefixes[1] + "/api/me").status_code == 401
    assert browser.get(prefixes[2] + "/api/me").json()["email"] == "bob@example.test"


def test_admin_upload_inventory_groups_by_owner_even_for_shared_file(environment):
    admin, alice, bob, guest = environment["clients"]
    records = vt.registry()
    for record in records.values():
        record.update(path=records["video-A"]["path"], title="same-title.mp4", filename="same-title.mp4")
    vt.save_json(vt.MEDIA_ROOT / "sessions.json", records)
    result = admin.get("/api/admin/uploads")
    assert result.status_code == 200, result.text
    data = result.json()
    groups = {group["email"]: group for group in data["accounts"]}
    assert data["total_videos"] == 2
    assert data["unassigned"] == {"video_count": 0, "videos": []}
    assert groups["admin@example.test"]["video_count"] == 0
    for label, email in [("A", "alice@example.test"), ("B", "bob@example.test")]:
        assert groups[email]["video_count"] == 1
        video = groups[email]["videos"][0]
        assert video["session_id"] == "video-" + label
        assert video["filename"] == "same-title.mp4"
        assert video["file_available"] is True
        assert video["uploaded_at"] == records["video-" + label]["created_at"]
        assert [project["id"] for project in video["projects"]] == ["project-" + label]
        assert admin.get("/api/matches/" + video["session_id"] + "/video").content == b"A video"
    assert str(environment["tmp"]) not in result.text
    assert "password_hash" not in result.text
    assert "csrf" not in result.text
    for client in (alice, bob):
        denied = client.get("/api/admin/uploads")
        assert denied.status_code == 403
        assert "same-title.mp4" not in denied.text
    assert guest.get("/api/admin/uploads").status_code == 401


def test_unassigned_video_moves_only_after_explicit_claim_and_missing_file_is_listed(environment):
    admin = environment["clients"][0]
    records = vt.registry()
    records["legacy"] = {"title": "A", "filename": "A.mp4", "path": str(environment["tmp"] / "missing.mp4"), "duration": 35, "updated_at": "2026-01-01T00:00:00Z"}
    vt.save_json(vt.MEDIA_ROOT / "sessions.json", records)
    data = admin.get("/api/admin/uploads").json()
    assert data["total_videos"] == 3
    video = data["unassigned"]["videos"][0]
    assert video["session_id"] == "legacy"
    assert video["file_available"] is False
    assert video["uploaded_at"] is None
    assert video["projects"] == []
    alice_id = environment["identities"][1]["id"]
    assert post(admin, "/api/admin/resources/session/legacy/assign", {"user_id": alice_id, "reason": "核对上传记录"}).status_code == 200
    data = admin.get("/api/admin/uploads").json()
    assert data["unassigned"]["video_count"] == 0
    group = next(group for group in data["accounts"] if group["id"] == alice_id)
    assert {video["session_id"] for video in group["videos"]} == {"legacy", "video-A"}


def test_admin_inventory_preserves_suspended_uploads_and_omits_deleted_resources(environment):
    admin = environment["clients"][0]
    bob_id = environment["identities"][2]["id"]
    assert post(admin, f"/api/admin/users/{bob_id}/state", {"enabled": False}).status_code == 200
    with accounts.database() as conn:
        conn.execute("UPDATE ownership SET hidden=1 WHERE kind='project' AND resource_id='project-B'")
        conn.execute("UPDATE ownership SET hidden=1 WHERE kind='session' AND resource_id='video-A'")
    data = admin.get("/api/admin/uploads").json()
    assert data["total_videos"] == 1
    groups = {group["id"]: group for group in data["accounts"]}
    assert groups[environment["identities"][1]["id"]]["video_count"] == 0
    assert groups[bob_id]["enabled"] == 0
    assert groups[bob_id]["video_count"] == 1
    assert groups[bob_id]["videos"][0]["projects"] == []
