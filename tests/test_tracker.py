import pytest
from fastapi.testclient import TestClient
import app as server
import tracker
import video_tools as vt


def test_frame_snapshot_project_roundtrip_and_validation(tmp_path):
    source = vt.MEDIA_ROOT / 'originals' / 'test.mp4'
    source.parent.mkdir(parents=True)
    vt.process([vt.binary('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=red:s=320x180:r=10:d=2', '-vf', 'drawbox=x=160:y=0:w=160:h=180:color=blue:t=fill',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)])
    vt.register('test', source, 'test.mp4', vt.probe(source))
    client = TestClient(server.app)
    headers = {'X-GGAB-Local': '1'}
    body = {'session_id':'test', 'time':.5, 'rotation':0,
            'box':{'x':.5,'y':0,'width':.5,'height':1}}
    assert client.get('/api/tracker/frame/test?time=3').status_code == 422
    assert client.get('/api/tracker/frame/test?rotation=45').status_code == 422
    frame = client.get('/api/tracker/frame/test?time=.5')
    assert frame.status_code == 200 and frame.content.startswith(b'\xff\xd8')
    assert client.post('/api/tracker/snapshots', json=body).status_code == 403
    snap = client.post('/api/tracker/snapshots', json=body, headers=headers)
    assert snap.status_code == 201, snap.text
    sid = snap.json()['id']
    assert client.get(snap.json()['url']).content.startswith(b'\xff\xd8')
    # Crop right half really contains blue, not the red left half.
    result = tracker.frame_bytes(source,.5,0,tracker.Box(**body['box']))
    import subprocess
    rgb = subprocess.run([vt.binary('ffmpeg'),'-v','error','-i','pipe:0','-vf','scale=1:1',
        '-f','rawvideo','-pix_fmt','rgb24','pipe:1'],input=result,capture_output=True,check=True).stdout
    assert rgb[2]>200 and rgb[0]<40
    project = {'title':'Test project','match_date':'2026-09-10','session_id':'test','game_type':'singles',
        'playing':True,'players':[{'slot':s,'name':s,'handedness':'right','is_you':s=='A1',
         'snapshot_id':sid} for s in ['A1','B1']], 'segments':[{'start':0,'end':1.5,'first_server':'near'}]}
    saved = client.post('/api/tracker/projects',json=project,headers=headers)
    assert saved.status_code == 201, saved.text
    pid = saved.json()['id']
    with tracker.database() as conn:
        workflow = conn.execute('SELECT payload FROM migration_workflows WHERE project_id=?', (pid,)).fetchone()
    import json
    assert json.loads(workflow['payload'])['required_extension_version'] == '1.3.5'
    assert client.get('/api/tracker/projects/'+pid).json()['snapshots']['A1']['box']==body['box']
    unchanged = client.put('/api/tracker/projects/'+pid,json=project,headers=headers)
    assert unchanged.status_code == 200
    assert unchanged.json()['updated_at'] == saved.json()['updated_at']
    project['title']='Edited'
    assert client.put('/api/tracker/projects/'+pid,json=project,headers=headers).json()['title']=='Edited'
    spectator = {**project, 'title':'Spectator', 'playing':False,
        'players':[{**p, 'is_you':False} for p in project['players']]}
    assert client.post('/api/tracker/projects',json=spectator,headers=headers).status_code==201
    spectator['players'][0]['is_you']=True
    assert client.post('/api/tracker/projects',json=spectator,headers=headers).status_code==422
    assert len(client.get('/api/tracker/projects').json())==2
    # Handoff preserves original-video timing and every player crop.
    bundle = client.get(f'/api/tracker/projects/{pid}/handoff')
    assert bundle.status_code == 200
    assert bundle.json()['project']['title'] == 'Edited'
    assert bundle.json()['video']['local_path'] == str(source.resolve())
    assert bundle.json()['project']['snapshots']['B1']['time'] == .5
    prompt = bundle.json()['prompt']
    assert '不要提前停在 Review 或 CREATE PROJECT 前' in prompt
    assert '停在 CONFIRM 前' in prompt
    assert '本次不授权点击自动跟踪的 CONFIRM 或扣除 Tokens' in prompt
    assert '最多重试两次' in prompt
    assert f'PUT /api/tracker/projects/{pid}/migration' in prompt
    assert 'VIDEO UNAVAILABLE' in prompt
    assert '只创建一个替代项目' in prompt
    assert '将旧 migration 更新为 failed' in prompt
    import io, json, zipfile
    exported = client.get(f'/api/tracker/projects/{pid}/handoff.zip')
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert set(archive.namelist()) == {'manifest.json','CODEX_HANDOFF.md','snapshots/A1.jpg','snapshots/B1.jpg'}
        assert json.loads(archive.read('manifest.json'))['project']['session_id'] == 'test'
        assert archive.read('snapshots/A1.jpg') == client.get(snap.json()['url']).content
    assert client.get('/api/tracker/projects/missing/handoff').status_code == 404
    assert client.get(f'/api/tracker/projects/{pid}/migration').status_code == 404
    migration = {'remote_project_id':'remote-123',
        'remote_url':'https://app.getgoodatbadminton.com/tracker/remote-123',
        'status':'finalized','source':'reused_existing','estimated_cost':385,
        'balance_before':1000,'balance_after':615}
    linked = client.put(f'/api/tracker/projects/{pid}/migration',json=migration,headers=headers)
    assert linked.status_code == 200
    assert linked.json()['session_id'] == 'test'
    assert linked.json()['estimated_cost'] == 385
    assert linked.json()['balance_after'] == 615
    assert linked.json()['association']['verified'] is False
    assert linked.json()['association']['title_used_for_identity'] is False
    assert len(linked.json()['association']['local_video']['fingerprint']['value']) == 64
    migration['evidence'] = {'remote_video_id':'video-456','filename':'test.mp4',
        'duration':2.0,'size_bytes':source.stat().st_size,'method':'verified_existing_video'}
    linked = client.put(f'/api/tracker/projects/{pid}/migration',json=migration,headers=headers)
    assert linked.status_code == 200
    assert linked.json()['association']['verified'] is True
    assert linked.json()['association']['remote_video']['remote_video_id'] == 'video-456'
    mismatch = {**migration, 'evidence': {**migration['evidence'], 'filename':'different.mp4'}}
    mismatch_result = client.put(f'/api/tracker/projects/{pid}/migration', json=mismatch, headers=headers)
    assert mismatch_result.status_code == 200
    assert mismatch_result.json()['association']['verified'] is False
    assert '文件名' in ''.join(mismatch_result.json()['association']['reasons'])
    linked = client.put(f'/api/tracker/projects/{pid}/migration', json=migration, headers=headers)
    assert linked.json()['association']['verified'] is True
    assert client.get(f'/api/tracker/projects/{pid}/migration').json()['remote_project_id'] == 'remote-123'
    assert client.get(f'/api/tracker/projects/{pid}/handoff').json()['migration']['status'] == 'finalized'
    listed = client.get('/api/tracker/projects').json()
    linked_project = next(item for item in listed if item['id'] == pid)
    assert linked_project['migration']['status'] == 'finalized'
    assert linked_project['migration']['estimated_cost'] == 385
    other = next(item for item in listed if item['id'] != pid)
    assert client.put(f"/api/tracker/projects/{other['id']}/migration", json=migration, headers=headers).status_code == 409
    replacement = {**migration, 'remote_project_id':'remote-999',
        'remote_url':'https://app.getgoodatbadminton.com/tracker/remote-999'}
    assert client.put(f'/api/tracker/projects/{pid}/migration', json=replacement, headers=headers).status_code == 409
    migration['remote_url'] = 'https://evil.example/tracker/remote-123'
    assert client.put(f'/api/tracker/projects/{pid}/migration',json=migration,headers=headers).status_code == 422
    project['segments'].append({'start':1,'end':1.8,'first_server':'far'})
    assert client.post('/api/tracker/projects',json=project,headers=headers).status_code==422
    project['segments']=[]
    project['game_type']='doubles'
    assert client.post('/api/tracker/projects',json=project,headers=headers).status_code==422
    project['game_type']='singles'
    vt.register('test',source,'replacement.mp4',vt.probe(source))
    assert client.post('/api/tracker/projects',json=project,headers=headers).status_code==422
    assert client.get(f'/api/tracker/projects/{pid}/handoff').status_code==422
    assert client.get(f'/api/tracker/projects/{pid}/handoff.zip').status_code==422
    deleted = client.delete(f'/api/tracker/projects/{pid}', headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()['recoverable'] is True
    assert deleted.json()['video_retained'] is True
    assert client.get(f'/api/tracker/projects/{pid}').status_code == 404
    assert client.get(f'/api/tracker/projects/{pid}/migration').status_code == 404
    with tracker.database() as conn:
        trashed = conn.execute('SELECT payload, migration_payload FROM deleted_projects WHERE id=?', (pid,)).fetchone()
    assert json.loads(trashed['payload'])['title'] == 'Edited'
    assert json.loads(trashed['migration_payload'])['remote_project_id'] == 'remote-123'
    assert source.is_file()


def test_box_rejects_out_of_bounds():
    with pytest.raises(ValueError):
        tracker.Box(x=.9,y=0,width=.2,height=1)
    with pytest.raises(ValueError):
        tracker.Box(x=float('nan'),y=0,width=.2,height=1)


def test_local_tracker_project_route_and_assets():
    client = TestClient(server.app)
    page = client.get('/tracker/projects/local-project-id')
    assert page.status_code == 200
    assert '比赛项目 · 羽球分析' in page.text
    script = client.get('/static/tracker.js').text
    assert "function localProjectUrl(id)" in script
    assert "'/insights/match-breakdown?match='" in script
    assert 'target="_blank"' not in script
