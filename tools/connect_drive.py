"""Connect the administrator's Drive using desktop OAuth with a loopback + PKCE flow."""
import argparse
import base64
import hashlib
import json
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import drive_storage


def api_json(url, payload=None, bearer=None, form=False):
    headers = {}
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    data = None
    if payload is not None:
        data = urlencode(payload).encode() if form else json.dumps(payload).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    try:
        with urlopen(Request(url, data=data, headers=headers), timeout=45) as response:
            return json.load(response)
    except (HTTPError, URLError, OSError, ValueError):
        raise RuntimeError("Google 请求失败，请检查网络、Drive API 是否启用，以及管理员授权") from None


def connect(client_file, reconnect=False):
    client = json.loads(Path(client_file).read_text(encoding="utf-8")).get("installed")
    if not client or not all(client.get(key) for key in ("client_id", "client_secret")):
        raise RuntimeError("请使用 Google OAuth 的桌面应用客户端 JSON")
    destination = drive_storage.credentials_path()
    previous = None
    if destination.exists():
        if not reconnect:
            raise RuntimeError("已有 Drive 配置；重新授权请加 --reconnect，保留原存储文件夹")
        previous = json.loads(destination.read_text(encoding="utf-8"))
        if previous["client_id"] != client["client_id"]:
            raise RuntimeError("重新授权须使用原 OAuth 客户端")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            params = parse_qs(parsed.query)
            if parsed.path != "/oauth/callback" or not secrets.compare_digest(params.get("state", [""])[0], state):
                self.send_error(400, "Invalid OAuth callback")
                return
            if params.get("code"):
                result["code"] = params["code"][0]
            else:
                result["error"] = True
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("授权已返回，可以关闭此页面并查看 PowerShell 中的连接结果。".encode())

        def log_message(self, *_):
            pass  # OAuth codes must never enter logs.

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        redirect = f"http://127.0.0.1:{server.server_port}/oauth/callback"
        query = {"client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
                 "scope": "https://www.googleapis.com/auth/drive.file", "access_type": "offline",
                 "prompt": "consent", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        print("请在本机 Chrome/Edge 打开下面的链接，用管理员 Google 账号授权：", flush=True)
        print("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(query), flush=True)
        deadline = time.monotonic() + 600
        server.timeout = 1
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if "code" not in result:
        raise RuntimeError("授权被取消或十分钟内未完成，可重新运行脚本")
    tokens = api_json("https://oauth2.googleapis.com/token", {"client_id": client["client_id"],
                      "client_secret": client["client_secret"], "code": result["code"], "redirect_uri": redirect,
                      "grant_type": "authorization_code", "code_verifier": verifier}, form=True)
    if not tokens.get("refresh_token"):
        raise RuntimeError("未获得持久授权，请撤销旧授权后重新连接")
    user = api_json("https://www.googleapis.com/drive/v3/about?fields=user(permissionId,emailAddress)", bearer=tokens["access_token"])["user"]
    if previous and previous.get("owner_permission_id") != user["permissionId"]:
        raise RuntimeError("必须使用原管理员 Google 账号重新授权")
    if previous:
        folder_id = previous["folder_id"]
        api_json(drive_storage.API + "/" + folder_id + "?fields=id", bearer=tokens["access_token"])
    else:
        folder = api_json(drive_storage.API + "?fields=id", {"name": "Yuxu Videos", "mimeType": "application/vnd.google-apps.folder"}, tokens["access_token"])
        folder_id = folder["id"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    saved = {"client_id": client["client_id"], "client_secret": client["client_secret"],
             "refresh_token": tokens["refresh_token"], "folder_id": folder_id,
             "owner_permission_id": user["permissionId"]}
    temporary = destination.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as target:
            json.dump(saved, target, ensure_ascii=False, indent=2)
        temporary.chmod(0o600)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print("Drive 已连接。文件夹：https://drive.google.com/drive/folders/" + folder_id)
    print("授权文件已保存到：" + str(destination.resolve()))
    print("请重启网站服务；子账号的新上传将保存到该文件夹。")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="连接管理员 Google Drive，并新建私有 Yuxu Videos 文件夹")
    parser.add_argument("--client", required=True, help="Google OAuth 桌面客户端 JSON 的路径")
    parser.add_argument("--reconnect", action="store_true", help="重新授权原管理员账号并保留原文件夹")
    arguments = parser.parse_args()
    try:
        connect(arguments.client, arguments.reconnect)
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
