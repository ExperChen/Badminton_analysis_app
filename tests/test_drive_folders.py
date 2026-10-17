import io
import json
import hashlib
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest
from fastapi import HTTPException
import accounts
import drive_storage as drive
import video_tools as videos
from tools import migrate_drive_videos as migration


class Response(io.BytesIO):
    def __init__(self, value, code=200, headers=None):
        super().__init__(json.dumps(value).encode())
        self.code, self.headers = code, headers or {}


@pytest.fixture
def folders(monkeypatch):
    monkeypatch.setattr(accounts, 'ENFORCE_AUTH', True)
    owner = 'a' * 32
    with accounts.database() as connection:
        connection.execute('INSERT INTO users VALUES(?,?,?,?,?,?)',
                           (owner, 'player@example.test', 'test', 'user', 1, 0))
    root = 'root-folder-123'
    monkeypatch.setattr(drive, 'configuration', lambda: {'folder_id': root})
    remote, calls = {}, []

    def api(url, method='GET', body=None, headers=None):
        parsed = urlsplit(url)
        params = parse_qs(parsed.query)
        calls.append((method, params, json.loads(body) if body else None))
        if method == 'POST' and parsed.path == '/drive/v3/files':
            meta = {'id': f'account-folder-{len(remote)}', **json.loads(body)}
            remote[meta['id']] = meta
            return Response(meta)
        if method == 'POST':
            return Response({}, headers={'Location': 'https://www.googleapis.com/upload/session-123'})
        if method == 'PATCH':
            meta = remote[parsed.path.split('/')[-1]]
            meta.update(json.loads(body))
            meta['parents'] = params.get('addParents', meta.get('parents', []))
            return Response(meta)
        if parsed.path == '/drive/v3/files':
            return Response({'files': [m for m in remote.values() if m.get('mimeType') == drive.FOLDER_MIME]})
        identifier = parsed.path.split('/')[-1]
        return Response(remote[identifier]) if identifier in remote else Response({}, 404)

    monkeypatch.setattr(drive, 'open_api', api)
    return owner, root, remote, calls


def test_folder_reused_by_id_and_survives_local_mapping_loss(folders):
    owner, root, remote, calls = folders
    folder = drive.account_folder(owner)
    assert remote[folder]['name'] == 'player@example.test__aaaaaaaa'
    assert remote[folder]['parents'] == [root]
    assert drive.account_folder(owner) == folder
    with accounts.database() as c:
        c.execute("UPDATE users SET email='renamed@example.test' WHERE id=?", (owner,))
        c.execute("DELETE FROM settings")
    assert drive.account_folder(owner) == folder
    assert sum(method == 'POST' for method, _, _ in calls) == 1


def test_foreign_or_moved_folder_rejected_and_new_upload_uses_child(folders):
    owner, root, remote, calls = folders
    data = {'filename': 'video.mp4', 'owner_id': owner, 'upload_id': 'b' * 32, 'size_bytes': 10}
    assert drive.begin(data).startswith('https://www.googleapis.com/upload/')
    upload = calls[-1][2]
    assert upload['parents'] == [data['drive_folder_id']]
    assert upload['appProperties']['ggab_owner'] == owner
    remote[data['drive_folder_id']]['appProperties']['ggab_owner'] = 'other'
    with pytest.raises(HTTPException) as error:
        drive.account_folder(owner)
    assert error.value.status_code == 409


def test_move_keeps_file_id_and_checksum_and_is_idempotent(folders):
    owner, root, remote, calls = folders
    meta = {'id': 'existing-video-123', 'parents': [root], 'md5Checksum': 'c' * 32,
            'appProperties': {'ggab_owner': owner, 'ggab_upload_id': 'd' * 32}}
    remote[meta['id']] = meta
    result = drive.organize_file(dict(meta), owner)
    assert result['id'] == meta['id'] and result['md5Checksum'] == 'c' * 32
    patch = next(call for call in calls if call[0] == 'PATCH')
    assert patch[1]['removeParents'] == [root]
    assert result['parents'] == patch[1]['addParents']
    drive.organize_file(result, owner)
    assert sum(method == 'PATCH' for method, _, _ in calls) == 1


def test_completed_upload_recovery_finds_file_after_folder_move(monkeypatch):
    observed = []
    def api(url, *args, **kwargs):
        observed.append(url)
        if '/upload/' in url:
            return Response({}, 404)
        return Response({'files': [{'id': 'completed-file-123', 'size': '10'}]})
    monkeypatch.setattr(drive, 'open_api', api)
    offset, meta = drive.progress({'drive_session': 'https://www.googleapis.com/upload/old-session',
                                  'size_bytes': 10, 'upload_id': 'b' * 32, 'owner_id': 'a' * 32})
    assert offset == 10
    query = parse_qs(urlsplit(observed[-1]).query)['q'][0]
    assert 'in parents' not in query and "key='ggab_owner'" in query


def local_job(folders):
    owner = folders[0]
    source = videos.MEDIA_ROOT / 'originals' / 'old.mp4'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'abcdefghijk')
    videos.register('old-session', source, source.name, {'duration': 10, 'has_audio': False})
    accounts.claim('session', 'old-session', owner)
    record = videos.registry()['old-session']
    item = migration.fingerprint({'session_id': 'old-session', 'owner_id': owner, 'record': record})
    data = {**item, 'source_path': str(source), 'source_record': record, 'upload_id': 'b' * 32,
            'filename': source.name, 'drive_file': {'id': 'migrated-file-123'}, 'remote_ready': True}
    meta = {'id': 'migrated-file-123', 'size': str(item['size_bytes']), 'md5Checksum': item['md5'],
            'appProperties': {'ggab_owner': owner, 'ggab_upload_id': data['upload_id']}}
    return source, data, meta


def test_publish_preserves_original_path_version_and_cached_file(folders, monkeypatch):
    source, data, meta = local_job(folders)
    monkeypatch.setattr(drive, 'metadata', lambda _: meta)
    before = videos.file_signature(source)
    assert migration.publish(data)
    record = videos.registry()['old-session']
    assert record['path'] == data['source_record']['path']
    assert record['updated_at'] == data['source_record']['updated_at']
    assert record['storage'] == 'google_drive'
    assert drive.cached(record) == source
    assert videos.file_signature(source) == before
    assert not migration.publish(data)


def test_publish_refuses_changed_ownership_or_remote_content(folders, monkeypatch):
    source, data, meta = local_job(folders)
    monkeypatch.setattr(drive, 'metadata', lambda _: meta)
    with accounts.database() as c:
        c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)', ('z' * 32, 'other@example.test', 'test', 'user', 1, 0))
        c.execute('UPDATE ownership SET user_id=? WHERE resource_id=?', ('z' * 32, 'old-session'))
    with pytest.raises(ValueError, match='账号归属'):
        migration.publish(data)
    assert videos.registry()['old-session'] == data['source_record']
    with accounts.database() as c:
        c.execute('UPDATE ownership SET user_id=? WHERE resource_id=?', (data['owner_id'], 'old-session'))
    meta['md5Checksum'] = '0' * 32
    with pytest.raises(ValueError, match='发生变化'):
        migration.publish(data)
    assert source.read_bytes() == b'abcdefghijk'


def test_retained_original_changes_are_never_overwritten_and_missing_copy_can_recover(folders, monkeypatch):
    source, data, meta = local_job(folders)
    monkeypatch.setattr(drive, 'metadata', lambda _: meta)
    migration.publish(data)
    record = videos.registry()['old-session']
    source.write_bytes(b'user edited original')
    with pytest.raises(HTTPException) as error:
        drive.cached(record)
    assert error.value.status_code == 409 and source.read_bytes() == b'user edited original'
    source.unlink()
    monkeypatch.setattr(drive, 'media_response', lambda *args: io.BytesIO(b'abcdefghijk'))
    assert drive.cached(record) == source
    assert source.read_bytes() == b'abcdefghijk' and source.stat().st_mtime_ns == data['signature'][3]
    source.unlink()
    def race(*args):
        source.write_bytes(b'new user file')
        return io.BytesIO(b'abcdefghijk')
    monkeypatch.setattr(drive, 'media_response', race)
    with pytest.raises(HTTPException) as error:
        drive.cached(record)
    assert error.value.status_code == 409 and source.read_bytes() == b'new user file'


def test_local_migration_resumes_partial_lost_ack_and_does_not_upload_twice(folders, monkeypatch):
    monkeypatch.setattr(migration, 'CHUNK', 4)
    source, data, _ = local_job(folders)
    item = {key: data[key] for key in ('session_id', 'owner_id', 'record', 'signature', 'md5', 'sha256', 'size_bytes', 'chunks')}
    folder = drive.account_folder(data['owner_id'])
    received = bytearray()
    starts = []
    session_data = {}
    lost = False
    def begin(job):
        starts.append(job['upload_id'])
        session_data.update(job)
        return 'https://www.googleapis.com/upload/migration-123'
    def meta():
        return {'id': 'migrated-video-123', 'size': str(len(received)), 'md5Checksum': hashlib.md5(received).hexdigest(),
                'parents': [folder], 'appProperties': {'ggab_owner': data['owner_id'], 'ggab_upload_id': session_data['upload_id']}}
    def progress(job, content=None, start=None):
        nonlocal lost
        if content is not None:
            assert start == len(received)
            if not lost:
                lost = True
                received.extend(content[:2])
                raise HTTPException(503, 'lost ack')
            received.extend(content)
        return len(received), meta() if len(received) == item['size_bytes'] else None
    monkeypatch.setattr(drive, 'begin', begin)
    monkeypatch.setattr(drive, 'progress', progress)
    monkeypatch.setattr(drive, 'metadata', lambda _: meta())
    monkeypatch.setattr(drive, 'organize_file', lambda value, owner: value)
    first = migration.upload_local(item, lambda *args: None)
    assert first['remote_ready']
    assert bytes(received) == source.read_bytes()
    assert videos.registry()['old-session'].get('storage') != 'google_drive'
    second = migration.upload_local(item, lambda *args: None)
    assert second['drive_file']['id'] == first['drive_file']['id'] and len(starts) == 1
