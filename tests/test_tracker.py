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
    headers = {'X-Yuxu-Local': '1'}
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
    import json
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
    assert client.get(f'/api/tracker/projects/{pid}/handoff').status_code == 404
    assert client.put(f'/api/tracker/projects/{pid}/migration', json={}, headers=headers).status_code == 404
    project['segments'].append({'start':1,'end':1.8,'first_server':'far'})
    assert client.post('/api/tracker/projects',json=project,headers=headers).status_code==422
    project['segments']=[]
    project['game_type']='doubles'
    assert client.post('/api/tracker/projects',json=project,headers=headers).status_code==422
    project['game_type']='singles'
    vt.register('test',source,'replacement.mp4',vt.probe(source))
    assert client.post('/api/tracker/projects',json=project,headers=headers).status_code==422
    deleted = client.delete(f'/api/tracker/projects/{pid}', headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()['recoverable'] is True
    assert deleted.json()['video_retained'] is True
    assert client.get(f'/api/tracker/projects/{pid}').status_code == 404
    assert client.get(f'/api/tracker/projects/{pid}/migration').status_code == 404
    with tracker.database() as conn:
        trashed = conn.execute('SELECT payload, migration_payload FROM deleted_projects WHERE id=?', (pid,)).fetchone()
    assert json.loads(trashed['payload'])['title'] == 'Edited'
    assert trashed['migration_payload'] is None
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
