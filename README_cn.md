# 羽序 · 羽毛球比赛分析应用

简体中文 · [English](README.md)

更新于 2026-10-25：本地项目、已有数据复盘及 `YUXU_` 配置。旧版后台扩展、远端同步和 Token 计费模块已移除。

兼容已有数据：Drive 原片继续使用历史 `ggab_*` 私有元数据字段，以便复用原文件、核对归属及恢复上传；这些字段不连接外部分析平台。本地数据库保留 `ggab.db` 文件名和已有比赛/原片关联。

技术概览：[技术栈与职责](技术栈方案.md)。安装与启动步骤见下文。

由 **ExperChen** 开发的本地羽毛球复盘工作台。用于管理比赛视频、准备球员标注、查看逐拍与回合统计，并导出只保留回合的精剪视频。FastAPI 后端提供接口与数据访问，React 界面展示工作台，本地磁盘保存账号、资源归属、视频关联和分析元数据。

仓库提供应用源码与测试，不包含真实比赛数据库、个人视频、逐拍导出、已有账号、个人邮箱、OAuth 凭据或已绑定的云服务账号。首次安装从空数据开始。

## 功能概览

| 模块 | 功能 |
| --- | --- |
| 比赛分析 | 比赛与局数选择、观察方切换、两场比赛对比、比分走势、回合与逐拍记录、开局模式、触球高度和失误分布 |
| 比赛项目 | 视频、比赛类型、球员、片段、检查五步向导；选帧、旋转、球员截图和按账号保存的球员名称 |
| 视频工作台 | 8 MiB 分片上传、校验与断点续传、持久化视频 Session、HTTP Range 播放、本地原片关联和可选播放预览 |
| 视频剪辑 | 按回合时间合并片段、设置额外保留时间、导出含动态比分的 H.264/AAC MP4、下载新旧时间轴映射 |
| 账号管理 | 首次管理员初始化、普通账号、同一浏览器多登录上下文、资源归属、激活与重置码、上传限额 |
| 可选集成 | 管理员 Google Drive 存储、反馈通知与审核队列 |

统计分析需要兼容的逐拍标注。单纯上传视频不会运行仓库内的识别模型；仓库没有内置识别模型。当前版本使用本地项目与已有逐拍数据，不再连接外部比赛分析服务，也不会自动扣费。

## 技术栈

| 层级 | 技术及用途 |
| --- | --- |
| 后端 | Python 3.12+、FastAPI、Uvicorn、Pydantic 请求校验 |
| 数据存储 | Python `sqlite3`；逐拍库、独立账号/项目/反馈库，以及视频 Session 和上传状态 JSON |
| 认证与权限 | Argon2 密码哈希、HttpOnly 会话 Cookie、CSRF 检查、归属校验和频率限制 |
| 前端 | React 19、Vite 8、Tailwind CSS 4、Motion、GSAP、Lucide 图标 |
| 既有页面控制器 | Jinja2 模板、浏览器 JavaScript、本地 ECharts 与字体；账号管理在 React 宿主内复用控制器；比赛项目和回合剪辑使用独立 React 组件 |
| 媒体处理 | FFmpeg/ffprobe、CPU 编码、后台任务线程池、共享编码锁 |
| 浏览器集成 | Chromium Manifest V3 后台工作线程与内容脚本 |
| 测试 | pytest、HTTPX、Node 内置测试运行器 |

Python 依赖版本固定在 `requirements.txt`、`requirements-dev.txt` 中；前端依赖通过 `ui-versions/react/frontend/package-lock.json` 锁定。Node 用于构建前端，构建完成后的日常服务由 Python 提供。

## 快速启动：Windows 与 PowerShell 7

先安装 Python 3.12 或更高版本、Git、兼容当前锁定 Vite 版本的 Node.js，以及 FFmpeg。确认 `ffmpeg`、`ffprobe` 在 `PATH` 中。Node.js 22.12+ 可作为安装基线。在准备存放项目的位置打开 **PowerShell 7**：

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

打开 [http://127.0.0.1:8102/login](http://127.0.0.1:8102/login)。保持终端运行，按 `Ctrl+C` 停止。

启动脚本只在数据库不存在时创建空的 `ggab.db`，默认使用本地上传和项目内的 `media/` 目录。一个 Python 进程同时监听 **8102** 默认入口和 **8000** 可选兼容入口，均绑定 `127.0.0.1`。两端口访问的是同一套应用与数据。

首次启动在本机登录页创建管理员，使用自己的邮箱格式登录名和 12–128 字符密码。仓库没有体验账号或默认密码。邮箱格式登录名目前不代表已验证邮箱，注册不会发送验证邮件。

### 原版界面与端口

原版 Jinja2 界面不需要构建 Node 前端：

```powershell
pwsh -File .\start.ps1 -OriginalUI
```

修改界面端口：

```powershell
pwsh -File .\start.ps1 -Port 8104
```

8000 保留为可选兼容端口。独立测试实例应同时指定独立数据库与媒体目录，并通过 `-CompatPort 0` 关闭兼容监听；只换端口不会隔离数据。

### Linux/macOS 手动启动

为所在系统安装 Python、Node 和 FFmpeg。克隆后可运行：

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

PowerShell 启动脚本是主要的使用路径。上述手动命令不会配置远程服务器、HTTPS、VPN 或系统服务。

## 首次使用流程

### 1. 创建与管理本地账号

通过本机回环地址创建首个管理员。管理员可在账号页管理普通账号、签发激活与重置码、分配资源、查看上传概览。

存在唯一启用的管理员后，默认允许符合条件的自助注册：本机回环访问，或配置了私有 HTTPS 网站源并强制 VPN 身份核验的访问。设置 `YUXU_SELF_REGISTRATION=0` 可关闭自助注册。新账号不会自动获得历史资源。

使用“登录其他账号”（`/login?new=1`）在同一浏览器建立独立登录上下文，并在该上下文内导航。退出其中一个上下文不影响其他账号。应用的账号隔离不能阻止拥有操作系统文件权限或共享上游账号权限的人访问相应资源。

### 2. 上传视频并准备球员标注

进入比赛项目，完成五步向导：

1. 上传视频或选择已有视频 Session，填写项目名称和可选日期。
2. 选择单打或双打。
3. 填写球员名称、惯用手、本人观察位置；选择画面、按需旋转，再框选球员截图。
4. 可选设置互不重叠的比赛片段、起始比分与先发球方；不设置片段表示完整视频。
5. 检查汇总并保存项目。

允许的文件扩展名为 `.mp4`、`.mov`、`.mkv`、`.webm`、`.avi`、`.m4v`，实际解码能力取决于 FFmpeg。上传提供校验、重试和续传状态，单文件上限为 50 GiB，另受账号额度限制。浏览器文件选择器不能提供可信绝对路径；本地原片关联针对配置媒体库中符合条件的文件，不开放任意服务器路径浏览。

本地存储模式下，视频保存在应用所在机器，直到配置并使用外部流程。Drive 模式会把上传保存到管理员配置的远程存储。

### 3. 提供逐拍标注

可以导入自己有权使用的兼容 CSV，也可以配置下文的可选后台扩展。仓库不附带个人比赛样例。

必要时创建空逐拍库，再先校验、后导入：

```powershell
.\.venv\Scripts\python.exe tools/init_db.py
.\.venv\Scripts\python.exe refresh_shots.py path/to/shots.csv --dry-run
.\.venv\Scripts\python.exe refresh_shots.py path/to/shots.csv
```

CSV 表头必须与 `schema.sql` 中的 `shots` 表一致，每行代表一拍。校验包括必要标识、唯一 `shot_id`、完整列、有效数值和非空数据。正式导入先备份当前数据库，再在单个事务中替换整个 `shots` 表。这是全量快照导入，不是追加导入。`--database path/to/other.db` 可指定目标库；如果服务设置了 `YUXU_DB_PATH`，导入命令也应显式传入同一路径。

管理员需要核验并把历史/导入资源分配给本地账号。导入记录不会自动使普通账号获得归属权限，也不会生成或复制视频。

### 4. 分析与复盘

进入比赛分析，选择比赛、局数和观察方，使用比分图、回合列表、开局模式、高度与失误筛选查看标注。两场比赛对比使用完整比赛。关联对应的未裁剪原片后，可按时间戳定位和播放回合。

统计口径如下：

- 按 `match_id + rally_id` 汇总回合；胜负不明或结果不完整的回合不进入对应胜率分母。
- 平均拍数使用实际记录行数，平均时长使用已标注的回合时长。
- 赢分来源分布统计赢分回合终结拍的技术，不代表每次使用该技术后的条件胜率。
- 开局模式要求回合至少包含所选的 3 拍或 5 拍。
- 缺失的触球高度不作推测；回球高度只连接同回合内相邻的对方击球。
- 有本地球员/项目设置时据此确定观察方；上游 `you` 字段不自动代表每个本地登录者。

### 5. 生成回合精剪

进入视频剪辑，选择有标注的比赛及对应原片，检查剪辑计划，可在每个回合前后各额外保留 0–5 秒。重叠区间会合并。生成 MP4 后，可下载视频及可选时间轴映射。

剪辑通过 FFmpeg 重新编码，以便准确定位切点。动态比分使用标注中的回合结束后 A/B 比分。原片与逐拍数据保持原有内容。时间戳缺失、不一致或超出视频范围时拒绝剪辑；无回合标注的视频不能自动去掉捡球与等待时间。

可选的流畅播放预览单独生成较低分辨率缓存，不替代上游上传或剪辑时的原片。预览与导出共用编码锁和 CPU 线程预算。服务重启会中断未完成任务，重启后需重新生成未完成结果。

## 可选集成

### Google Drive 存储

公开仓库启动脚本默认使用本地存储。需要管理员 Drive 时，先启用 Drive API、创建桌面 OAuth 客户端，并把客户端 JSON 保存在版本控制之外，再运行：

```powershell
.\.venv\Scripts\python.exe tools/connect_drive.py --client path/to/oauth-desktop-client.json
$env:YUXU_UPLOAD_STORAGE = 'google_drive'
pwsh -File .\start.ps1
```

连接工具使用本机回环 OAuth/PKCE 流程，请求 `drive.file` 权限，创建私有应用文件夹，并在本地 `.secrets/` 下保存授权；可通过配置改变保存位置。重新授权使用 `--reconnect`，继续核验原账号并保留原文件夹。不要提交 OAuth 文件或刷新令牌。

Drive 不可用时不会悄悄退回本地上传。播放使用经认证的字节范围读取，文件核验、截图、预览、精剪和上游提交仍可能需要完整本地缓存。磁盘应为原片/缓存和衍生结果预留空间；移除本地关联不能证明远端文件已删除。

### 用户反馈与邮件审核

已登录用户可以提交反馈并查看自己的反馈历史。仓库包含持久化通知/审核/开发队列和 `tools/feedback_queue.py`。公开仓库没有绑定个人邮箱、安装外部连接器、创建定时检查，或启用批准后的自动开发执行。

需要运维集成时，参考 `feedback-config.example.json` 创建私有 `feedback-config.json`。连接的 Outlook，或另行配置的 Graph 授权，以及外部调度/执行环境均需自行提供。接受批准邮件前必须核验准确账号，并通过队列校验命令处理；用户反馈正文和引用邮件不能当作执行指令。开发批准与上线批准应分别处理。

仅查看本地队列状态：

```powershell
.\.venv\Scripts\python.exe tools/feedback_queue.py status
```

## 配置说明

启动前设置环境变量。应用不会自动读取任意 `.env` 文件。

| 环境变量 | 说明 |
| --- | --- |
| `YUXU_MEDIA_ROOT` | 媒体、Session 及账号/项目/反馈元数据根目录；公开版本默认项目内 `media/` |
| `YUXU_DB_PATH` | 逐拍 SQLite 数据库，默认项目内 `ggab.db` |
| `YUXU_UPLOAD_STORAGE` | `local` 或 `google_drive`，公开版本默认 `local` |
| `YUXU_CPU_BUDGET` | FFmpeg 最大 CPU 线程数，按实际可用资源收缩；Windows 默认 14，非 Windows 默认 20 |
| `YUXU_FFMPEG`、`YUXU_FFPROBE` | 可选可执行文件完整路径；不设置时从 `PATH` 查找 |
| `YUXU_SELF_REGISTRATION` | `1` 允许符合本机/私有网络条件的自助注册，`0` 关闭 |
| `YUXU_ACCOUNT_UPLOAD_GIB` | 每账号原视频容量，默认 200 GiB |
| `YUXU_DAILY_UPLOAD_GIB` | 最近 24 小时上传容量，默认 50 GiB |
| `YUXU_DAILY_UPLOAD_FILES` | 最近 24 小时上传数量，默认 20 |
| `YUXU_DRIVE_CREDENTIALS_PATH` | 私有 Drive 授权文件位置 |
| `YUXU_PUBLIC_ORIGIN` | 私有可信反向代理使用的完整 HTTPS 网站源 |
| `YUXU_REQUIRE_VPN_IDENTITY` | `1` 要求非回环访问携带 VPN 身份头 |
| `YUXU_UI_RELEASE` | 可选 `deployments/` 内发布目录名，未设置时读取 `active.json` |
| `YUXU_FEEDBACK_ROOT` | 可选反馈数据库目录 |
| `YUXU_FEEDBACK_WORKER_DISABLED` | `1` 禁止启动内置邮件工作线程 |
| `YUXU_FEEDBACK_NO_MAIL` | `1` 关闭直接邮件配置检查，适合隔离测试 |

上传容量预留和限额是持久化记录。取消上传不一定退还当天限额，远程空间清理应单独核实。衍生文件不计入原视频额度，仍需监测磁盘剩余空间。

保持单个 Uvicorn 进程。启动器有意使用 `workers=1`，增加 Web 进程不能代替安全的分布式任务队列。CPU 并行发生在媒体编码内部；仓库不提供本地模型训练。

私有远程访问需要可信本地反向代理/VPN 终止 HTTPS，配置准确的网站源与身份校验，应用继续只监听回环地址。身份头只能由可信代理注入。仓库不会自动配置公网部署。

## 项目结构

```text
app.py / production_app.py       接口后端与构建后的 React 发布目录托管
accounts.py                     认证与资源归属
analytics.py / schema.sql       统计计算及一行一拍的数据结构
tracker.py / local_data.py       项目标注与已有数据关联
video_tools.py                  视频 Session、FFmpeg 剪辑和媒体读取
video_uploads.py                持久化分片上传状态
playback_preview.py             可选播放预览转码
drive_storage.py                可选 Drive 存储适配
feedback.py / feedback_mail.py  反馈队列和可选邮件传输
templates/ / static/            既有模板、控制器、图表和第三方本地资源
ui-versions/react/frontend/     React/Vite 源码和依赖锁定文件
ui-versions/react/templates/    React 页面宿主与嵌入工作区模板
ui-versions/react/page-config.json
tools/init_db.py                创建空数据库，不修改已有库
tools/build_release.py          构建并组装本地界面发布目录
tools/connect_drive.py          可选管理员 Drive OAuth 连接
tools/feedback_queue.py         反馈队列运维命令
tests/                         后端和浏览器控制器单元测试
start.ps1 / run_site.py         PowerShell 启动器与回环 ASGI 服务
```

运行时产生的 `media/`、`deployments/`、`exports/`、`backups/`、凭据、数据库和日志均属于本地状态，不包含在发布源码中。备份时应一起保存逐拍库与媒体元数据目录；只保存视频文件不能保留账号归属、标注和 Session 关联。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
node --test --test-concurrency=14 tests/*.cjs
.\.venv\Scripts\python.exe tools/build_release.py
```

测试使用临时媒体/测试数据库和模拟网络。FFmpeg 相关检查要求 `ffmpeg`、`ffprobe` 可用。单元测试不会提交真实分析任务，也不代表已验证真实 Drive/Outlook 连接。个人浏览器预览测试夹具未随仓库发布。


修改前端后应重新构建和组装发布目录，再重启 Python 服务。如已在前端目录执行 `npm run build`，可运行 `tools/build_release.py --skip-build` 直接组装。发布工具只复制界面文件，不迁移或替换用户数据库。

开发实验请使用可丢弃的独立数据库和媒体目录。手动 CSV 导入有意执行全量替换；在已有数据的环境运行前先用 `--dry-run` 校验并保留备份。管理员接口诊断入口为 `/docs`，普通用户使用 `/help`。

## 常见问题

| 现象 | 检查内容 |
| --- | --- |
| 提示先构建 React 或找不到发布清单 | 前端目录执行 `npm ci`，再在项目根目录执行 `tools/build_release.py` |
| 端口被占用 | 停止旧实例或选择空闲界面端口；启用兼容监听时 8000 也必须空闲 |
| 分析页为空 | 全新克隆没有比赛；导入兼容逐拍并核验归属和视频关联 |
| 数据库或表错误 | 核对 `YUXU_DB_PATH`；`init_db.py` 保留已有文件，不修复错误结构 |
| 提帧或剪辑失败 | 检查 `ffmpeg -version`、`ffprobe -version`、编解码支持、时间戳和剩余磁盘空间 |
| 上传提示 Drive 不可用 | 显式使用本地模式，或先完成管理员 Drive 授权 |
| 反馈邮件未到达 | 全新克隆没有邮件连接器、授权或调度器；检查队列并配置运维集成 |
| 返回 401/403 | 核对登录上下文、资源归属、CSRF/会话状态、来源和私有网络身份配置 |

## 适用范围与许可

当前产品界面主要为中文，使用单台应用主机和本地持久化。真实第三方服务、远程取消、双向标注编辑、内置识别模型与公网托管均不属于独立本地使用流程。

作者：**[ExperChen](https://github.com/ExperChen)**。

本项目尚未指定统一的开源许可证。第三方依赖与本地第三方资源继续遵循各自许可证。
