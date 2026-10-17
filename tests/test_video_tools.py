import hashlib
import json
import sqlite3
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

import app as server
import tracker as tk
import video_tools as vt


def row(rally, start, end):
    return {"match_id":"match", "rally_id":rally, "rally_start_s":start, "rally_end_s":end}


def test_video_cutter_is_a_separate_local_tool():
    client = TestClient(server.app)
    cutter = client.get("/tools/video-cutter")
    assert cutter.status_code == 200
    assert "视频剪辑" in cutter.text
    assert '/static/video-cutter.js' in cutter.text
    breakdown = client.get("/insights/match-breakdown")
    assert 'id="clip-panel"' not in breakdown.text
    assert 'href="/tools/video-cutter"' in breakdown.text


def test_plan_deduplicates_and_merges_without_repeating_video():
    rows=[row("a",1,3),row("a",1,3),row("b",2.5,5),row("c",8,9)]
    plan=vt.plan_clips(rows,10)
    assert plan["rally_count"]==3
    assert plan["segment_count"]==2
    assert [(x["start"],x["end"]) for x in plan["segments"]]==[(1,5),(8,9)]
    assert plan["kept_duration"]==5
    assert plan["removed_duration"]==5
    padded=vt.plan_clips(rows,10,2,2)
    assert padded["segment_count"]==1
    assert padded["kept_duration"]==10


@pytest.mark.parametrize("rows", [[],[row("a",None,2)],[row("a",-1,2)],
    [row("a",3,2)],[row("a",0,float("nan"))],[row("a",0,11)],
    [row("a",1,3),row("a",1,4)]])
def test_invalid_timestamps_block_instead_of_silently_skipping(rows):
    with pytest.raises(ValueError):vt.plan_clips(rows,10)


def synthetic_video(path, audio=True):
    args=[vt.binary("ffmpeg"),"-v","error","-nostdin","-n"]
    for color in ("red","black","green","black"):
        args += ["-f","lavfi","-i",f"color=c={color}:s=160x120:r=30:d=2"]
    if audio:args += ["-f","lavfi","-i","sine=frequency=880:duration=8"]
    args += ["-filter_complex","[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]","-map","[v]"]
    if audio:args += ["-map","4:a:0","-c:a","aac"]
    args += ["-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",str(path)]
    vt.process(args)
    return path


def make_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript((server.ROOT/"schema.sql").read_text(encoding="utf-8"))
        for i,(start,end) in enumerate([(0,2),(4,6)]):
            conn.execute("INSERT INTO shots(match_id,rally_id,shot_id,match_title,game_number,"
                         "rally_start_s,rally_end_s) VALUES(?,?,?,?,?,?,?)",
                         ("match",f"r{i}",f"s{i}","match.mp4",i+1,start,end))


def wait_complete(client, job_id):
    deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        job=client.get(f"/api/clips/{job_id}").json()
        if job["status"] in ("complete","failed"):
            assert job["status"]=="complete",job
            return job
        time.sleep(.1)
    pytest.fail("Video export did not finish")


def frame_rgb(path, seconds):
    result=subprocess.run([vt.binary("ffmpeg"),"-v","error","-ss",str(seconds),"-i",str(path),
        "-vf","scale=1:1","-frames:v","1","-f","rawvideo","-pix_fmt","rgb24","pipe:1"],
        capture_output=True,check=True,timeout=15)
    return tuple(result.stdout[:3])


@pytest.mark.parametrize("audio",[True,False])
def test_real_export_removes_black_gaps_and_keeps_color_and_audio(tmp_path,monkeypatch,audio):
    db=tmp_path/"shots.db";make_db(db);monkeypatch.setattr(server,"DB_PATH",db)
    path=synthetic_video(tmp_path/"match.mp4",audio)
    original_hash=hashlib.sha256(path.read_bytes()).hexdigest()
    headers={"X-GGAB-Local":"1","Content-Type":"application/octet-stream"}
    with TestClient(server.app) as client:
        uploaded=client.post("/api/sessions/import?filename=match.mp4",content=path.read_bytes(),headers=headers)
        assert uploaded.status_code==201,uploaded.text
        assert uploaded.json()["match_id"]=="match"
        listed=client.get("/api/matches").json()
        assert len(listed)==1 and listed[0]["has_video"]
        assert "path" not in listed[0]["video"]
        assert client.get("/api/matches/match/video",headers={"Range":"bytes=0-99"}).status_code==206
        plan=client.get("/api/matches/match/clip-plan").json()
        assert plan["kept_duration"]==4
        assert client.get("/api/matches/match/clip-plan?game=2").json()["kept_duration"]==2
        started=client.post("/api/matches/match/clips",json={},headers={"X-GGAB-Local":"1"})
        assert started.status_code==202,started.text
        job=wait_complete(client,started.json()["id"])
        assert abs(job["output_duration"]-4)<.2
        download=client.get(job["download_url"])
        assert download.status_code==200 and len(download.content)>1000
        output=vt.job_path(job["id"])/"rallies.mp4"
        assert vt.probe(output)["has_audio"]==audio
        r,g,b=frame_rgb(output,.5);assert r>200 and g<30 and b<30
        r,g,b=frame_rgb(output,2.5);assert g>90 and r<30 and b<30
        assert client.get(job["timeline_url"]).json()["segments"][1]["start"]==4
        assert len(client.get("/api/matches/match/clips").json())==1
    assert hashlib.sha256(path.read_bytes()).hexdigest()==original_hash


def test_untagged_session_and_import_validation(tmp_path,monkeypatch):
    db=tmp_path/"shots.db";make_db(db);monkeypatch.setattr(server,"DB_PATH",db)
    path=synthetic_video(tmp_path/"extra.mp4",False)
    headers={"X-GGAB-Local":"1"}
    with TestClient(server.app) as client:
        assert client.post("/api/sessions/import?filename=extra.mp4",content=b"x").status_code==403
        assert client.post("/api/sessions/import?filename=extra.mp4",content=b"x",headers={**headers,"Origin":"https://example.com"}).status_code==403
        assert client.post("/api/sessions/import?filename=bad.txt",content=b"x",headers=headers).status_code==422
        assert client.post("/api/sessions/import?filename=bad.mp4",content=b"x",headers=headers).status_code==422
        response=client.post("/api/sessions/import?filename=extra.mp4",content=path.read_bytes(),headers=headers)
        sid=response.json()["match_id"]
        assert response.status_code==201
        assert response.json()["reused_local_file"] is False
        original_record=vt.registry()[sid]
        originals=vt.MEDIA_ROOT/"originals"
        assert len(list(originals.glob("*.mp4")))==1
        assert len(client.get("/api/matches").json())==2
        assert client.get(f"/api/matches/{sid}/breakdown").json()["summary"]["shots"]==0
        assert client.get(f"/api/matches/{sid}/clip-plan").status_code==422
        assert client.post("/api/matches/match/clips",json={"before":-1},headers=headers).status_code==422
        assert client.get("/api/matches/match/clip-plan?after=nan").status_code==422
        assert client.get("/api/clips/not-a-job/download").status_code==404
        # Reimporting a same-name video preserves the session instead of duplicating it.
        again=client.post("/api/sessions/import?filename=extra.mp4",content=path.read_bytes(),headers=headers)
        assert again.json()["match_id"]==sid
        assert again.json()["reused_local_file"] is True
        assert vt.registry()[sid]==original_record
        assert len(list(originals.glob("*.mp4")))==1
        assert len(client.get("/api/matches").json())==2

        # An automatically matched same-name but different original cannot
        # silently repoint an existing Session (and invalidate its snapshots).
        changed=synthetic_video(tmp_path/"changed.mp4",True)
        conflict=client.post("/api/sessions/import?filename=extra.mp4",content=changed.read_bytes(),headers=headers)
        assert conflict.status_code==409
        assert vt.registry()[sid]==original_record
        assert len(list(originals.glob("*.mp4")))==1
        assert not list(originals.glob("*.upload"))

        # Explicit replacement remains possible; the previous original stays
        # recoverable instead of being deleted by this import endpoint.
        replace=client.post(f"/api/sessions/import?filename=extra.mp4&match_id={sid}",
                            content=changed.read_bytes(),headers=headers)
        assert replace.status_code==201
        assert replace.json()["reused_local_file"] is False
        assert vt.registry()[sid]["path"]!=original_record["path"]
        assert len(list(originals.glob("*.mp4")))==2
        assert not list(originals.glob("*.upload"))


def test_unregistered_identical_original_in_video_directory_is_linked_without_another_file(tmp_path,monkeypatch):
    db=tmp_path/"shots.db";make_db(db);monkeypatch.setattr(server,"DB_PATH",db)
    source=synthetic_video(tmp_path/"existing.mp4",False)
    folder=vt.MEDIA_ROOT/"originals";folder.mkdir(parents=True)
    existing=folder/"existing.mp4";existing.write_bytes(source.read_bytes())
    with TestClient(server.app) as client:
        result=client.post("/api/sessions/import?filename=existing.mp4",content=source.read_bytes(),
                           headers={"X-GGAB-Local":"1"})
    assert result.status_code==201
    assert result.json()["reused_local_file"] is True
    assert vt.registry()[result.json()["match_id"]]["path"]==str(existing.resolve())
    assert sorted(p.name for p in folder.iterdir())==["existing.mp4"]


def test_direct_link_from_configured_video_directory_keeps_original_in_place(tmp_path,monkeypatch):
    db=tmp_path/"shots.db";make_db(db);monkeypatch.setattr(server,"DB_PATH",db)
    root=vt.MEDIA_ROOT;root.mkdir(parents=True)
    source=synthetic_video(root/"direct.mp4",False)
    original_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    client=TestClient(server.app)
    listing=client.get("/api/sessions/local-videos")
    assert listing.status_code==200
    assert listing.json()["videos"]==[{"relative_path":"direct.mp4","filename":"direct.mp4",
                                       "size_bytes":source.stat().st_size}]
    assert client.post('/api/sessions/link',json={"relative_path":"direct.mp4"}).status_code==403
    assert client.post('/api/sessions/link',json={"relative_path":"../outside.mp4"},
                       headers={"X-GGAB-Local":"1"}).status_code==422
    linked=client.post('/api/sessions/link',json={"relative_path":"direct.mp4"},
                       headers={"X-GGAB-Local":"1"})
    assert linked.status_code==200
    sid=linked.json()["match_id"]
    assert linked.json()["reused_local_file"] is True
    assert vt.registry()[sid]["path"]==str(source.resolve())
    assert not (root/"originals").exists()
    assert client.get('/api/sessions/local-videos').json()["videos"]==[]
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original_hash
    # Even the file-picker fallback finds the disk original by content.
    selected=client.post('/api/sessions/import?filename=direct.mp4',content=source.read_bytes(),
                         headers={"X-GGAB-Local":"1"})
    assert selected.status_code==201
    assert selected.json()["reused_local_file"] is True
    assert vt.registry()[sid]["path"]==str(source.resolve())
    assert list((root/"originals").iterdir())==[]
    removed=client.delete('/api/sessions/'+sid,headers={"X-GGAB-Local":"1"})
    assert removed.status_code==200
    assert removed.json()["video_retained"] is True
    assert source.is_file()


def test_link_same_video_rebinds_path_then_reclaims_uuid_copy(tmp_path, monkeypatch):
    db=tmp_path/"shots.db";make_db(db);monkeypatch.setattr(server,"DB_PATH",db)
    root=vt.MEDIA_ROOT/"originals";root.mkdir(parents=True)
    named=synthetic_video(root/"lim.mp4",False)
    duplicate=root/("a"*32+".mp4");duplicate.write_bytes(named.read_bytes())
    sid="video-lim"
    before=vt.register(sid,duplicate,named.name,vt.probe(duplicate))
    client=TestClient(server.app)
    linked=client.post("/api/sessions/link",json={"relative_path":"originals/lim.mp4"},
                       headers={"X-GGAB-Local":"1"})
    assert linked.status_code==200,linked.text
    assert linked.json()["match_id"]==sid
    after=vt.registry()[sid]
    assert after["path"]==str(named.resolve())
    assert after["updated_at"]==before["updated_at"]
    assert duplicate.is_file()
    assert "originals/lim.mp4" not in [v["relative_path"] for v in
        client.get("/api/sessions/local-videos").json()["videos"]]
    reclaimed=client.post(f"/api/sessions/{sid}/reclaim-duplicate",headers={"X-GGAB-Local":"1"})
    assert reclaimed.status_code==200,reclaimed.text
    assert reclaimed.json()["released_bytes"]==named.stat().st_size
    assert named.is_file() and not duplicate.exists()
    assert vt.registry()[sid]["path"]==str(named.resolve())
    assert vt.registry()[sid]["updated_at"]==before["updated_at"]
    assert client.post(f"/api/sessions/{sid}/reclaim-duplicate",headers={"X-GGAB-Local":"1"}).status_code==409


def test_reclaim_rebinds_registered_uuid_and_protects_tracker_project(tmp_path, monkeypatch):
    db=tmp_path/"shots.db";make_db(db);monkeypatch.setattr(server,"DB_PATH",db)
    root=vt.MEDIA_ROOT/"originals";root.mkdir(parents=True)
    named=root/"lim.mp4";named.write_bytes(b"same-original")
    duplicate=root/("b"*32+".mp4");duplicate.write_bytes(named.read_bytes())
    sid="video-lim";before=vt.register(sid,duplicate,named.name,{"duration":10,"has_audio":False})
    with tk.database() as conn:
        conn.execute("INSERT INTO projects VALUES (?,?,?)",("project-lim",json.dumps({"session_id":sid}),vt.now()))
        conn.execute("INSERT INTO migration_workflows VALUES (?,?,?,?,?)",
                     ("project-lim","upload_video","{}",0,vt.now()))
    client=TestClient(server.app)
    headers={"X-GGAB-Local":"1"}
    match=next(item for item in client.get("/api/matches").json() if item["match_id"]==sid)
    assert match["can_delete"] is False
    assert client.delete(f"/api/sessions/{sid}",headers=headers).status_code==409
    blocked=client.post(f"/api/sessions/{sid}/reclaim-duplicate",headers=headers)
    assert blocked.status_code==409
    assert duplicate.is_file() and vt.registry()[sid]["path"]==str(duplicate.resolve())
    with tk.database() as conn:
        conn.execute("UPDATE migration_workflows SET stage='complete' WHERE project_id='project-lim'")
    done=client.post(f"/api/sessions/{sid}/reclaim-duplicate",headers=headers)
    assert done.status_code==200,done.text
    assert done.json()["rebound"] is True
    assert done.json()["sha256"]==hashlib.sha256(named.read_bytes()).hexdigest()
    assert not duplicate.exists() and named.is_file()
    assert vt.registry()[sid]["path"]==str(named.resolve())
    assert vt.registry()[sid]["updated_at"]==before["updated_at"]
    assert client.delete(f"/api/sessions/{sid}",headers=headers).status_code==409


def test_reclaim_requires_exact_unique_bytes_and_no_shared_source(tmp_path, monkeypatch):
    db=tmp_path/"shots.db";make_db(db);monkeypatch.setattr(server,"DB_PATH",db)
    root=vt.MEDIA_ROOT/"originals";root.mkdir(parents=True)
    duplicate=root/("c"*32+".mp4");duplicate.write_bytes(b"abcd")
    named=root/"lim.mp4";named.write_bytes(b"abce")
    sid="video-lim";vt.register(sid,duplicate,named.name,{"duration":10,"has_audio":False})
    client=TestClient(server.app);headers={"X-GGAB-Local":"1"}
    assert client.post(f"/api/sessions/{sid}/reclaim-duplicate",headers=headers).status_code==409
    assert duplicate.read_bytes()==b"abcd" and named.read_bytes()==b"abce"
    named.write_bytes(b"abcd")
    another=vt.MEDIA_ROOT/"lim.mp4";another.write_bytes(b"abcd")
    assert client.post(f"/api/sessions/{sid}/reclaim-duplicate",headers=headers).status_code==409
    another.unlink()
    vt.register("another-session",duplicate,named.name,{"duration":10,"has_audio":False})
    assert client.post(f"/api/sessions/{sid}/reclaim-duplicate",headers=headers).status_code==409
    assert duplicate.is_file() and named.is_file()


def test_interrupted_export_reports_failure():
    jid="a"*32
    vt.save_json(vt.job_path(jid)/"job.json",{"id":jid,"status":"running","run_id":"previous"})
    assert vt.read_job(jid)["status"]=="failed"


def test_score_timing_survives_padding_merges_and_short_rallies(tmp_path):
    rows = [{**row("a", 1, 3), "score_a_after": 5, "score_b_after": 4, "game_number": 1},
            {**row("b", 4, 4.4), "score_a_after": 0, "score_b_after": 1, "game_number": 2}]
    plan = vt.plan_clips(rows, 7, 1, 1)
    assert plan["segment_count"] == 1
    events = plan["segments"][0]["score_events"]
    assert (events[0]["start"], events[0]["end"]) == (1.75, 3)
    assert (events[1]["start"], events[1]["end"]) == (4, 4.4)
    assert (events[1]["score_a"], events[1]["score_b"]) == ("0", "1")
    subtitles = tmp_path / "score.ass"
    vt.write_score_subtitles(plan["segments"][0], subtitles)
    contents = subtitles.read_text(encoding="utf-8")
    assert "0:00:01.75,0:00:03.00" in contents
    assert "\\fad(250,250)" in contents
    assert "GAME 2" in contents
    assert vt.ass_text("name{\\pos(1,2)}\nnext") == "name（／pos(1,2)） next"


def test_score_fades_on_real_frames_and_disappears_at_rally_end(tmp_path):
    source = tmp_path / "black.mp4"
    vt.process([vt.binary("ffmpeg"), "-v", "error", "-nostdin", "-n", "-f", "lavfi",
                "-i", "color=c=black:s=640x360:r=30:d=4", "-c:v", "libx264", str(source)])
    rows = [{**row("score", .5, 2.5), "score_a_after": 12, "score_b_after": 9,
             "striker": "A1", "striker_name": "PLAYER A", "game_number": 1}]
    plan = vt.plan_clips(rows, 4, .5, 1)
    job = vt.create_job("score-test", source, plan, None, False)
    with TestClient(server.app) as client:
        finished = wait_complete(client, job["id"])
    output = vt.job_path(finished["id"]) / "rallies.mp4"
    def brightness(seconds, crop="320:140:0:0"):
        result = subprocess.run([vt.binary("ffmpeg"), "-v", "error", "-ss", str(seconds),
            "-i", str(output), "-vf", "crop=" + crop, "-frames:v", "1", "-f", "rawvideo",
            "-pix_fmt", "gray", "pipe:1"], capture_output=True, check=True, timeout=15)
        return sum(result.stdout)/len(result.stdout)
    dark = brightness(1.1)
    fade_in = brightness(1.3)
    full = brightness(1.6)
    fade_out = brightness(2.4)
    gone = brightness(2.7)
    assert dark < .1 and gone < .1
    assert .05 < fade_in < full * .8
    assert .05 < fade_out < full * .8
    assert full > .5
    assert brightness(1.6, "200:120:400:220") < .1  # Score stays top-left.
