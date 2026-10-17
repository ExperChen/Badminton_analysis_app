"""Serve the selected React release with the original application and real data."""
import json
import os
import re
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import app as backend

ROOT = Path(__file__).resolve().parent
selected = os.environ.get("GGAB_UI_RELEASE")
if not selected:
    selected = json.loads((ROOT/"deployments/active.json").read_text(encoding="utf-8"))["release"]
RELEASE = (ROOT/"deployments"/selected).resolve()
if not RELEASE.is_relative_to(ROOT/"deployments") or not RELEASE.is_dir():
    raise RuntimeError("React 发布目录无效")
CONFIG = json.loads((RELEASE/"page-config.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((RELEASE/"static/react-ui/.vite/manifest.json").read_text(encoding="utf-8"))
ENTRY = MANIFEST["src/main.jsx"]
templates = Jinja2Templates(directory=RELEASE/"templates")


def page(request, name):
    # Older releases have no React guide view; a UI rollback keeps the
    # authenticated guide available without rolling back business data.
    if name == "help.html" and name not in CONFIG:
        response = backend.templates.TemplateResponse(request=request, name=name, context={})
        prefix = request.scope.get("root_path", "")
        html = re.sub(r'''((?:href|src)\s*=\s*["'])/(?!/)''',
                      lambda match: match[1] + prefix + "/", response.body.decode("utf-8"))
        return HTMLResponse(html)
    config = CONFIG[name]
    response = templates.TemplateResponse(request=request, name="react_host.html", context={
        "view": config["view"], "legacy_template": "legacy/"+name,
        "legacy_scripts": config["scripts"], "legacy_styles": config["styles"],
        "entry_js": "/static/react-ui/"+ENTRY["file"],
        "entry_css": ["/static/react-ui/"+css for css in ENTRY.get("css", [])],
    })
    prefix = request.scope.get("root_path", "")
    if not prefix:
        return response
    html = re.sub(r'''((?:href|src)\s*=\s*["'])/(?!/)''',
                  lambda match:match[1]+prefix+"/", response.body.decode("utf-8"))
    return HTMLResponse(html)


app = backend.app
backend.page = page
for route in app.routes:
    if route.name == "static":
        route.app = StaticFiles(directory=RELEASE/"static")
        break
else:
    raise RuntimeError("静态资源路由不存在")


@app.get("/library", response_class=HTMLResponse)
def library(request: Request):
    return page(request,"library.html")
