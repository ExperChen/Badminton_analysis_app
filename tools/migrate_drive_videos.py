"""Organize Drive videos and resumably migrate registered local originals."""
import argparse
import hashlib
import json
import socket
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import accounts
import drive_storage as drive
import video_tools as videos
from compute_resources import CPU_THREADS, ENCODING_LOCK
from fastapi import HTTPException

CHUNK = 8 * 1024 * 1024


def inventory(include_local=False):
    items, skipped = [], []
    with accounts.database() as connection:
        owners = {r['resource_id']: r['user_id'] for r in connection.execute(
            "SELECT resource_id,user_id FROM ownership WHERE kind='session'")}
        users = {r['id']: r['email'] for r in connection.execute('SELECT id,email FROM users')}
    for sid, record in videos.registry().items():
        owner = owners.get(sid)
        if owner not in users:
            skipped.append({'session_id': sid, 'reason': '缺少有效账号归属'})
            continue
        if record.get('storage') != 'google_drive' and not include_local:
            continue
        items.append({'session_id': sid, 'record': record, 'owner_id': owner, 'email': users[owner]})
    return items, skipped


def fingerprint(item):
    source = Path(item['record']['path']).resolve()
    if not source.is_relative_to(videos.MEDIA_ROOT.resolve()):
        raise ValueError('历史原视频不在媒体目录内，未迁移')
    before = videos.file_signature(source)
    md5, sha, chunks = hashlib.md5(), hashlib.sha256(), []
    with source.open('rb') as stream:
        while content := stream.read(CHUNK):
            md5.update(content)
            sha.update(content)
            chunks.append({'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
    if videos.file_signature(source) != before:
        raise ValueError('原视频在校验时发生变化，未迁移')
    if not chunks:
        raise ValueError('原视频为空，未迁移')
    return {**item, 'signature': list(before), 'md5': md5.hexdigest(), 'sha256': sha.hexdigest(),
            'size_bytes': before[2], 'chunks': chunks}


def job_path(sid):
    return videos.MEDIA_ROOT / 'drive-migrations' / (hashlib.sha256(sid.encode()).hexdigest() + '.json')


def retry(operation):
    for attempt in range(5):
        try:
            return operation()
        except HTTPException as error:
            if error.status_code not in (429, 502, 503, 504) or attempt == 4:
                raise
            time.sleep(min(2 ** attempt, 16))


def upload_local(item, notify):
    root = drive.configuration()['folder_id']
    source = Path(item['record']['path']).resolve()
    destination = job_path(item['session_id'])
    identity = {'session_id': item['session_id'], 'owner_id': item['owner_id'], 'root_folder_id': root,
                'source_path': str(source), 'signature': item['signature'], 'sha256': item['sha256']}
    data = json.loads(destination.read_text(encoding='utf-8')) if destination.exists() else None
    if data and any(data.get(key) != value for key, value in identity.items()):
        raise ValueError('历史迁移记录与当前原视频或归属不同，未覆盖旧记录')
    if data is None:
        data = {**identity, 'upload_id': uuid.uuid4().hex, 'filename': item['record']['filename'],
                'size_bytes': item['size_bytes'], 'md5': item['md5'], 'chunks': item['chunks'],
                'source_record': item['record'], 'received_bytes': 0, 'remote_ready': False}
        videos.save_json(destination, data)
    if data.get('remote_ready'):
        meta = retry(lambda: drive.metadata(data['drive_file']['id']))
        drive.verify_metadata(meta, data)
        if meta['md5Checksum'] != item['md5']:
            raise ValueError('已上传历史视频校验不一致')
        return data
    if not data.get('drive_session'):
        data['drive_session'] = retry(lambda: drive.begin(data))
        videos.save_json(destination, data)
    try:
        offset, meta = retry(lambda: drive.progress(data))
    except HTTPException as error:
        if error.status_code != 410:
            raise
        data['drive_session'] = retry(lambda: drive.begin(data))
        videos.save_json(destination, data)
        offset, meta = 0, None
    last_report = time.monotonic()
    notify(item, '上传', offset, data['size_bytes'])
    with source.open('rb') as stream:
        while offset < data['size_bytes']:
            start = offset // CHUNK * CHUNK
            expected = data['chunks'][start // CHUNK]
            stream.seek(start)
            content = stream.read(expected['size'])
            if hashlib.sha256(content).hexdigest() != expected['sha256']:
                raise ValueError('历史原视频片段发生变化，停止上传')
            try:
                received, meta = drive.progress(data, content[offset - start:], offset)
            except HTTPException as error:
                if error.status_code not in (429, 502, 503, 504):
                    raise
                received, meta = retry(lambda: drive.progress(data))
            if received < offset or received > start + len(content):
                raise ValueError('Drive 返回无效的历史视频续传位置')
            if received == offset:
                # The query after a lost response may show that no bytes arrived.
                received, meta = retry(lambda: drive.progress(data, content[offset - start:], offset))
                if received <= offset or received > start + len(content):
                    raise ValueError('Drive 上传未推进，请稍后重新运行迁移')
            offset = received
            data['received_bytes'] = offset
            if meta:
                data['drive_file'] = meta
            videos.save_json(destination, data)
            if time.monotonic() - last_report >= 20 or offset == data['size_bytes']:
                notify(item, '上传', offset, data['size_bytes'])
                last_report = time.monotonic()
    if videos.file_signature(source) != tuple(data['signature']):
        raise ValueError('历史原视频在上传期间发生变化，未切换网站记录')
    meta = retry(lambda: drive.metadata((meta or data.get('drive_file', {}))['id']))
    drive.verify_metadata(meta, data)
    if meta['md5Checksum'] != data['md5']:
        raise ValueError('Drive 历史视频完整校验失败，未切换网站记录')
    meta = retry(lambda: drive.organize_file(meta, data['owner_id']))
    data.update(drive_file=meta, received_bytes=data['size_bytes'], remote_ready=True)
    videos.save_json(destination, data)
    notify(item, '已核验', data['size_bytes'], data['size_bytes'])
    return data


def publish(data):
    """Preserve local path and version so existing screenshots and jobs remain valid."""
    meta = drive.metadata(data['drive_file']['id'])
    drive.verify_metadata(meta, data)
    if meta['md5Checksum'] != data['md5']:
        raise ValueError('Drive 视频在切换前发生变化')
    source = Path(data['source_path'])
    if not source.resolve().is_relative_to(videos.MEDIA_ROOT.resolve()):
        raise ValueError('原视频不在媒体目录内')
    with videos.LOCK, accounts.database() as connection:
        connection.execute('BEGIN IMMEDIATE')
        owner = connection.execute("SELECT user_id FROM ownership WHERE kind='session' AND resource_id=?",
                                   (data['session_id'],)).fetchone()
        if not owner or owner[0] != data['owner_id']:
            raise ValueError('账号归属在迁移期间发生变化，未切换网站记录')
        records = videos.registry()
        old = records.get(data['session_id'])
        if old and old.get('storage') == 'google_drive' and old.get('drive_file_id') == meta['id']:
            return False
        if old != data['source_record'] or videos.file_signature(source) != tuple(data['signature']):
            raise ValueError('网站原视频关联在迁移期间发生变化，未切换网站记录')
        marker = source.with_suffix(source.suffix + '.drive-cache.json')
        videos.save_json(marker, {'md5': data['md5'], 'size': data['size_bytes'], 'mtime_ns': data['signature'][3]})
        records[data['session_id']] = {**old, 'storage': 'google_drive', 'drive_file_id': meta['id'],
            'drive_md5': data['md5'], 'drive_size': data['size_bytes'], 'sha256': data['sha256'],
            'retained_local_path': str(source), 'cache_mtime_ns': data['signature'][3]}
        videos.save_json(videos.MEDIA_ROOT / 'sessions.json', records)
    return True


def migrate(include_local, workers, notify, defer_link=False):
    items, skipped = inventory(include_local)
    local = [item for item in items if item['record'].get('storage') != 'google_drive']
    quota = drive.read_json(drive.open_api('https://www.googleapis.com/drive/v3/about?fields=storageQuota'))['storageQuota']
    needed = sum(Path(item['record']['path']).stat().st_size for item in local)
    if quota.get('limit') and needed > int(quota['limit']) - int(quota.get('usage', 0)):
        raise ValueError('Drive 剩余空间不足以保存全部历史视频')
    for owner in sorted({item['owner_id'] for item in items}):
        drive.account_folder(owner)
    moved = 0
    for item in items:
        if item['record'].get('storage') == 'google_drive':
            meta = drive.verify_source(item['record'])
            drive.organize_file(meta, item['owner_id'])
            moved += 1
            notify(item, '已整理', 1, 1)
    ready = []
    # Hash files concurrently within the same CPU budget used by video encoders.
    with ENCODING_LOCK, ThreadPoolExecutor(max_workers=min(CPU_THREADS, max(1, len(local)))) as pool:
        tasks = {pool.submit(fingerprint, item): item for item in local}
        for future in as_completed(tasks):
            item = future.result()
            ready.append(item)
            notify(item, '本机校验完成', 1, 1)
    failures, jobs = [], []
    def remaining_bytes(item):
        path = job_path(item['session_id'])
        previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        return max(0, item['size_bytes'] - previous.get('received_bytes', 0))
    # Start larger remaining transfers first to avoid a single large file at the end.
    ready.sort(key=remaining_bytes, reverse=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        tasks = {pool.submit(upload_local, item, notify): item for item in ready}
        for future in as_completed(tasks):
            item = tasks[future]
            try:
                jobs.append(future.result())
            except (HTTPException, OSError, ValueError, KeyError) as error:
                failures.append({'session_id': item['session_id'], 'reason': getattr(error, 'detail', str(error))})
    linked = 0
    if not defer_link:
        for data in jobs:
            linked += bool(publish(data))
    return {'organized': moved, 'uploaded': len(jobs), 'linked': linked, 'failed': failures, 'skipped': skipped}


def console(item, stage, done, total):
    print(f"[{stage}] {item['record']['filename']} {done / total:.1%}", flush=True)


def website_running(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description='按账号整理 Drive 历史视频，保留本机原件和 Session 关联')
    parser.add_argument('--apply', action='store_true', help='实际执行；默认只列出计划')
    parser.add_argument('--include-local', action='store_true', help='包含原先只在本机的视频')
    parser.add_argument('--defer-link', action='store_true', help='先上传，稍后停站再切换网站记录')
    parser.add_argument('--publish', action='store_true', help='切换所有已核验迁移记录；须先停站')
    parser.add_argument('--workers', type=int, choices=range(1, min(CPU_THREADS, 8) + 1), default=3)
    parser.add_argument('--port', type=int, default=8000, help='停站检查使用的网站端口')
    args = parser.parse_args()
    if args.publish:
        if not args.apply:
            raise ValueError('--publish 必须同时使用 --apply')
        if website_running(args.port):
            raise ValueError('请先停止网站服务，再切换视频关联，完成后重启网站')
        path = videos.MEDIA_ROOT / 'sessions.json'
        backup = videos.MEDIA_ROOT / 'drive-migrations' / ('sessions-backup-' + time.strftime('%Y%m%d-%H%M%S') + '.json')
        videos.save_json(backup, videos.registry())
        linked = 0
        for job in sorted((videos.MEDIA_ROOT / 'drive-migrations').glob('*.json')):
            if job.name.startswith('sessions-backup-'):
                continue
            data = json.loads(job.read_text(encoding='utf-8'))
            if data.get('remote_ready'):
                linked += bool(publish(data))
        print(json.dumps({'linked': linked, 'backup': str(backup)}, ensure_ascii=False), flush=True)
    elif args.apply:
        if not args.defer_link and website_running(args.port):
            raise ValueError('网站运行中，请使用 --defer-link，上传完成后停站并执行 --publish')
        result = migrate(args.include_local, args.workers, console, args.defer_link)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 1 if result['failed'] or result['skipped'] else 0
    else:
        items, skipped = inventory(args.include_local)
        print(json.dumps({'videos': [{'session_id': i['session_id'], 'account': i['email'],
            'filename': i['record']['filename'], 'storage': i['record'].get('storage', 'local')} for i in items],
            'skipped': skipped}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (HTTPException, OSError, ValueError, KeyError) as error:
        print(getattr(error, 'detail', str(error)), file=sys.stderr)
        sys.exit(1)
