# 羽序 · Badminton Analysis App

[简体中文](README_cn.md) · English

Updated 2026-10-25: local project workflow, existing-data review, and `YUXU_` configuration. The previous companion, remote synchronization, and token billing modules have been removed.

A local badminton review workspace by **ExperChen**. Organize match videos, prepare player annotations, inspect shot and rally statistics, and export rally-only video cuts. A FastAPI backend serves a React interface and keeps local accounts, ownership, video sessions, and analysis metadata on disk.

This repository contains application source and tests. It does not include match databases, personal videos, exported shot data, user accounts, mailbox addresses, OAuth credentials, or a preconfigured cloud service account. A new installation starts empty.

## What it does

| Area | Capabilities |
| --- | --- |
| Match Breakdown | Match/game selection, observation side, match comparison, score progression, rally/shot tables, shot patterns, contact height, and error distributions |
| Match Tracker | Five-step workflow: video, singles/doubles, players, segments, and review; frame selection, rotation, player crops, and reusable account-scoped player names |
| Video workspace | Resumable uploads in 8 MiB chunks, persistent video sessions, byte-range playback, local source association, and optional playback previews |
| Video Cutter | Merge annotated rally intervals, add optional padding, export H.264/AAC MP4 with animated scores, and download the original/exported timeline mapping |
| Accounts | First-run administrator setup, ordinary accounts, scoped login contexts in one browser, resource ownership, activation/reset codes, and upload limits |
| Optional integrations | Administrator-owned Google Drive storage and a feedback notification/review queue |

Shot analysis requires compatible shot annotations. Uploading a video alone does not run a bundled recognition model. The current version uses local projects and existing annotations; it does not connect to an external match-analysis service or charge for tracking.

## Technology stack

| Layer | Technology and purpose |
| --- | --- |
| Backend | Python 3.12+, FastAPI, Uvicorn, Pydantic request validation |
| Persistence | Python `sqlite3`, a shot database, separate account/tracker/feedback databases, and JSON video-session/upload manifests |
| Authentication | Argon2 password hashing, HttpOnly session cookies, CSRF checks, ownership checks, and rate limits |
| Frontend | React 19, Vite 8, Tailwind CSS 4, Motion, GSAP, and Lucide icons |
| Existing page controllers | Jinja2 templates, browser JavaScript, and locally served ECharts/fonts; the React host retains these controllers for account management; Match Tracker and Video Cutter use dedicated React components |
| Media | FFmpeg/ffprobe, CPU encoding, background worker pools, and a shared encoding lock |
| Browser integration | Chromium Manifest V3 service worker/content scripts |
| Tests | pytest, HTTPX, and Node's built-in test runner |

Exact Python versions are pinned in `requirements.txt` and `requirements-dev.txt`. Frontend versions are pinned by `ui-versions/react/frontend/package-lock.json`. Node is needed to build the frontend, not to serve an already built release.

## Quick start: Windows and PowerShell 7

Install Python 3.12 or later, Git, Node.js compatible with the locked Vite version, and FFmpeg with `ffmpeg` and `ffprobe` on `PATH`. Node.js 22.12+ is a suitable baseline. Open **PowerShell 7** in the location where you want the project.

```powershell
git clone https://github.com/ExperChen/Badminton_analysis_app.git
Set-Location Badminton_analysis_app

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Push-Location ui-versions/react/frontend
npm ci
Pop-Location

.\.venv\Scripts\python.exe tools/build_release.py
pwsh -File .\start.ps1
```

Open [http://127.0.0.1:8102/login](http://127.0.0.1:8102/login). Keep the terminal running; press `Ctrl+C` to stop.

The launcher creates an empty `ggab.db` only when the database does not already exist. It defaults to local uploads and the project's `media/` directory. It starts one Python process on **8102** and the companion compatibility port **8000**, both bound to `127.0.0.1`. These two ports expose the same application and data.

At first launch, create your administrator on the local login page. Choose your own email-shaped login name and a password of 12–128 characters. No demo login or default password is included. The login name is not an email verification service; registration does not send a verification email.

### Alternative interface and ports

The original Jinja2 interface can run without a Node build:

```powershell
pwsh -File .\start.ps1 -OriginalUI
```

To use another interface port:

```powershell
pwsh -File .\start.ps1 -Port 8104
```

Port 8000 remains an optional compatibility listener. For a separate test instance, select a separate database and media directory and disable the compatibility listener with `-CompatPort 0`. Changing the port alone does not isolate data.

### Linux/macOS manual startup

Use the equivalent Python/Node/FFmpeg installation for your system. After cloning:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
(cd ui-versions/react/frontend && npm ci)
.venv/bin/python tools/build_release.py

export YUXU_MEDIA_ROOT="$PWD/media"
export YUXU_DB_PATH="$PWD/ggab.db"
export YUXU_UPLOAD_STORAGE=local
export YUXU_CPU_BUDGET=20
.venv/bin/python tools/init_db.py
.venv/bin/python run_site.py --port 8102 --compat-port 8000
```

Windows/PowerShell is the primary launcher path. These manual commands do not provision a remote server, HTTPS, a VPN, or a service manager.

## First-use workflow

### 1. Set up local users

Create the initial administrator from loopback access. The account page provides ordinary-user management, activation/password reset codes, resource ownership, upload summaries.

Self-registration is enabled by default after a unique active administrator exists. It is permitted from loopback, or from an explicitly configured private HTTPS origin with VPN identity enforcement. Set `YUXU_SELF_REGISTRATION=0` to close it. New users receive no historical resources automatically.

Use “log in to another account” (`/login?new=1`) to create a separate login context in the same browser. Keep navigation inside that context. Signing out of one context does not sign out the others. Application isolation does not prevent a person with operating-system access to the files or the shared upstream account from accessing those resources.

### 2. Upload and prepare a video

Open Match Tracker and complete its five steps:

1. Upload a video or select an existing video session; enter a project title and optional date.
2. Select singles or doubles.
3. Enter player names, handedness, and your observation position. Select a frame, rotate if necessary, and draw each player crop.
4. Optionally define non-overlapping match segments, starting scores, and serving side. An empty segment list represents the full video.
5. Review and save the project.

Supported filename extensions are `.mp4`, `.mov`, `.mkv`, `.webm`, `.avi`, and `.m4v`; actual decoding depends on FFmpeg. Uploads have checksums, retry/resume state, a 50 GiB single-file limit, and account limits. Browser file selection cannot provide a trustworthy absolute path. Local file association is available for eligible files already in the configured media library; it does not make arbitrary server paths browsable.

In local storage mode, video content stays on the application host until an external workflow is explicitly configured and used. Google Drive mode stores uploads remotely.

### 3. Supply shot annotations

You can use compatible CSV exports you are authorized to access, or configure the optional companion integration below. The application does not ship personal match examples.

Initialize a new shot database if needed, then validate a CSV before importing it:

```powershell
.\.venv\Scripts\python.exe tools/init_db.py
.\.venv\Scripts\python.exe refresh_shots.py path/to/shots.csv --dry-run
.\.venv\Scripts\python.exe refresh_shots.py path/to/shots.csv
```

CSV headers must match the `shots` table in `schema.sql`; each row represents one shot. The validator checks required IDs, unique `shot_id`, complete columns, numeric values, and nonempty content. A successful import backs up the current database, then replaces the entire `shots` table in one transaction. It is a full snapshot import, not an append operation. `--database path/to/other.db` selects a different target; when using `YUXU_DB_PATH`, pass the same path explicitly to this import tool.

The administrator must verify and assign historical/imported resources to local accounts. An imported row does not automatically grant an ordinary user ownership. Importing shot records also does not generate or copy a video.

### 4. Review a match

Open Match Breakdown, choose a match and game, then select the observation side. Use score charts, rally lists, shot patterns, and height/error filters to inspect the annotated data. Two-match comparison uses complete matches. Link the corresponding uncut video for timestamp navigation and rally playback.

The statistics have specific meanings:

- A rally is grouped by `match_id + rally_id`; incomplete/unknown results are excluded from the relevant win-rate denominator.
- Average shot count uses recorded rows; average duration uses annotated durations.
- Shot “win source” distributions describe the last-shot technique in winning rallies, rather than the conditional probability of winning after any use of that technique.
- Opening-pattern statistics require at least the chosen 3 or 5 shots.
- Unknown contact height is not inferred. Return-height transitions use consecutive opposing-side shots in the same rally.
- The observation side comes from local player/project settings when available; an upstream `you` field is not treated as the identity of every local user.

### 5. Export a rally cut

Open Video Cutter, select an annotated match and its original video, inspect the cut plan, and optionally add 0–5 seconds before/after each rally. Overlapping intervals are merged. Generate the MP4, then download the output and optional timeline mapping.

Exports re-encode with FFmpeg for accurate cuts. Animated scores use the annotated post-rally A/B score. Originals and shot data remain intact. Missing, inconsistent, or out-of-range timestamps block the cut. Unannotated videos cannot be automatically trimmed into rallies.

Optional smooth-playback previews create a separate lower-resolution cache; they are not substituted for originals during upstream upload or cutting. Preview and export encoding share one execution lock and the CPU thread budget. Restarting the service interrupts in-progress jobs; regenerate interrupted work after restarting.

## Optional integrations

### Google Drive storage

Local storage is the public launcher's default. To use administrator-owned Drive storage, enable Drive API, create a desktop OAuth client, and keep its JSON file outside version control. Then run:

```powershell
.\.venv\Scripts\python.exe tools/connect_drive.py --client path/to/oauth-desktop-client.json
$env:YUXU_UPLOAD_STORAGE = 'google_drive'
pwsh -File .\start.ps1
```

The connection tool uses a loopback OAuth/PKCE flow and requests `drive.file` access. It creates a private application folder and saves authorization locally under `.secrets/` unless overridden. Reauthorization uses `--reconnect` and preserves the original account/folder. Do not commit OAuth files or refresh tokens.

Existing Drive files retain their historical `ggab_*` private metadata keys so ownership checks and interrupted uploads can reuse the original files. These compatibility fields do not enable a connection to an external analysis service. Existing local databases keep the `ggab.db` filename and stored match/video associations.

Drive mode does not silently fall back to local uploads when unavailable. Playback uses authenticated byte-range access; validation, screenshots, previews, and cutting may still require a complete local cache. Budget disk space for both originals/caches and derived media. Removing a local association does not prove a remote file was deleted.

### Feedback and email review

Authenticated users can submit feedback and inspect their own feedback history. The repository includes a durable notification/review/development queue and `tools/feedback_queue.py`. The public repository does not bind a personal mailbox, install an external connector, schedule recurring automation, or enable approved-development execution.

For an operator-managed deployment, start with `feedback-config.example.json` and configure a private `feedback-config.json`. A connected Outlook integration or separately configured Graph credentials and an external scheduler/executor are additional requirements. Verify the exact account before accepting approval mail; use the queue's validation commands rather than treating user feedback or quoted messages as executable instructions. Development approval is separate from deployment approval.

To inspect the local queue without starting development:

```powershell
.\.venv\Scripts\python.exe tools/feedback_queue.py status
```

## Configuration

Set environment variables before starting the process. The application does not automatically load an arbitrary `.env` file.

| Variable | Meaning |
| --- | --- |
| `YUXU_MEDIA_ROOT` | Media, sessions, and account/tracker/feedback metadata root; public default is project `media/` |
| `YUXU_DB_PATH` | Shot SQLite database; defaults to project `ggab.db` |
| `YUXU_UPLOAD_STORAGE` | `local` or `google_drive`; public default is `local` |
| `YUXU_CPU_BUDGET` | Maximum FFmpeg CPU threads, clamped to detected availability; Windows default 14, non-Windows default 20 |
| `YUXU_FFMPEG`, `YUXU_FFPROBE` | Optional full paths to executables, otherwise resolved from `PATH` |
| `YUXU_SELF_REGISTRATION` | `1` permits registration under the supported local/private-network conditions; `0` disables it |
| `YUXU_ACCOUNT_UPLOAD_GIB` | Per-account original-video capacity; default 200 GiB |
| `YUXU_DAILY_UPLOAD_GIB` | Rolling 24-hour upload-byte allowance; default 50 GiB |
| `YUXU_DAILY_UPLOAD_FILES` | Rolling 24-hour upload-count allowance; default 20 |
| `YUXU_DRIVE_CREDENTIALS_PATH` | Private Drive authorization file location |
| `YUXU_PUBLIC_ORIGIN` | Optional complete HTTPS origin for a trusted private reverse proxy |
| `YUXU_REQUIRE_VPN_IDENTITY` | `1` requires the VPN identity header for non-loopback access |
| `YUXU_UI_RELEASE` | Optional release directory name under `deployments/`; otherwise use `active.json` |
| `YUXU_FEEDBACK_ROOT` | Optional feedback database directory |
| `YUXU_FEEDBACK_WORKER_DISABLED` | `1` prevents the built-in mail worker from starting |
| `YUXU_FEEDBACK_NO_MAIL` | `1` disables direct-mail configuration checks for isolated testing |

Capacity reservations and upload quotas are persistent. Canceling an upload does not always refund daily allowances; remote-storage cleanup must be verified separately. Derived files are not included in the original-video quota, so monitor free space as well.

Use one Uvicorn process. The launcher intentionally uses `workers=1`; adding web workers does not create a safe distributed task queue. CPU parallelism happens inside media encoders. This repository does not provide local model training.

For private remote use, terminate HTTPS at a trusted local reverse proxy/VPN, configure the exact origin and identity enforcement, and keep the application bound to loopback. Only trusted proxies may supply identity headers. This repository does not configure a public Internet deployment for you.

## Project layout

```text
app.py / production_app.py       API/backend and built React release hosting
accounts.py                     Authentication and resource ownership
analytics.py / schema.sql       Statistics and one-row-per-shot schema
tracker.py / local_data.py       Project annotations and existing local data bindings
video_tools.py                  Video sessions, FFmpeg cuts, media streaming
video_uploads.py                Durable chunk upload state
playback_preview.py             Optional playback transcodes
drive_storage.py                Optional Drive storage adapter
feedback.py / feedback_mail.py  Feedback queue and optional mail transport
templates/ / static/            Existing templates, controllers, charts, vendor assets
ui-versions/react/frontend/     React/Vite source and lockfile
ui-versions/react/templates/    React host and embedded workspace templates
ui-versions/react/page-config.json
tools/init_db.py                Create an empty database without altering an existing one
tools/build_release.py          Build and assemble a local UI release
tools/connect_drive.py          Optional administrator Drive OAuth setup
tools/feedback_queue.py         Feedback operator CLI
tests/                         Backend and browser-controller unit tests
start.ps1 / run_site.py         PowerShell launcher and loopback ASGI runner
```

Generated `media/`, `deployments/`, `exports/`, `backups/`, credentials, databases, and logs are local runtime state and are excluded from the published source. Back up the shot database and the entire media metadata tree together; retaining a video file alone does not retain its ownership, annotations, or session association.

## Development and checks

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
node --test --test-concurrency=14 tests/*.cjs
.\.venv\Scripts\python.exe tools/build_release.py
```

The tests use temporary media/test databases and mocked network behavior. FFmpeg-dependent checks require working FFmpeg/ffprobe. Unit tests do not submit real analysis jobs or validate live Drive/Outlook connectivity. Personal browser-preview test fixtures are not distributed.


For frontend changes, rebuild and assemble a new release, then restart the Python service. If you already ran `npm run build` in the frontend directory, `tools/build_release.py --skip-build` assembles those assets without rebuilding. The release tool copies UI files only; it does not migrate or replace user databases.

Use disposable database/media paths when experimenting. The manual CSV import command intentionally replaces data; run `--dry-run` and retain backups before using it on a populated installation. Administrator API diagnostics are at `/docs`; ordinary users can use `/help`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| “Build the React UI first” / missing release manifest | Run `npm ci` in the frontend directory, then `tools/build_release.py` from the repository root |
| Port already in use | Stop the existing instance or choose a free interface port; 8000 must also be free when compatibility is enabled |
| Empty analysis page | A clean clone has no matches; import compatible shots and verify ownership/video association |
| Database/table error | Check `YUXU_DB_PATH`; `init_db.py` preserves existing files and does not repair an incorrect schema |
| Frame extraction or export fails | Check `ffmpeg -version`, `ffprobe -version`, input codec support, timestamps, and free disk space |
| Upload says Drive is unavailable | Use explicit local mode, or complete administrator Drive authorization before choosing Drive mode |
| Feedback mail does not arrive | A new clone has no mail connector/credentials/scheduler; inspect the queue and configure the operator integration |
| HTTP 401/403 | Check the selected login context, resource ownership, CSRF/session state, origin, and configured private-network identity |

## Scope and license

The application currently uses Chinese interface labels. It is intended for a single application host with local persistence. Live third-party services, remote cancellation, bidirectional annotation editing, a bundled recognition model, and public hosting are outside the standalone local workflow.

Author: **[ExperChen](https://github.com/ExperChen)**.

No project-wide open-source license has been specified. Third-party dependencies and bundled vendor assets retain their respective licenses.
