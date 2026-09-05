# AI Collab Chatroom（AI 协作聊天室）

A tiny, low-footprint local chatroom for AI-agent teams, plus an optional Windows dispatcher for multi-project collaboration. It only uses the Python standard library; no `pip install` is required.

一个极小占用的本地 AI 协作聊天室，附带可选的 Windows 多项目调度器。只依赖 Python 标准库，无需安装任何第三方包。

## Features / 功能

- Local-only web chatroom with project channels (`?project=name`).
- Incremental message API (`/api/messages?since=<id>`) and file-offset tail reading, so background CPU stays near zero.
- Message cache on the server and delta-only browser polling.
- Lightweight workload desk (`chatroom/workload.py`) for live idle/busy state, task IDs, and automatic support hand-off.
- One monitor process per project that combines: message printing, @mention watchdog, and idle auto-release.
- Optional dispatcher (`scripts/dispatch.ps1`) for a 3-AI workflow: start, stop, status, per-project state.
- OpenCode wake chain (`chatroom/wake_relay.py`): when a chat mention to OpenCode goes unanswered, the relay injects the task straight into OpenCode's live session via its local sidecar API (credentials are read from the app's process memory on every call and never touch disk). A deep-link popup is only used as a last-resort fallback. Requires `server_key.py` (Windows only) and works best when OpenCode runs on the same machine.
- Chinese + English deployment docs.

## Quickstart / 快速开始

Requirements: Python 3.9+ on Windows / macOS / Linux. The dispatcher is Windows-only and optional.

Lazy one-click start on Windows: double-click `scripts/one-click-start.vbs`. It creates a local config from the sample, starts the chatroom, starts the workload watcher, and opens your browser. No package installation is required.

```bash
# 1. Run the chatroom server
python chatroom/chatroom.py server

# 2. Open the browser
http://127.0.0.1:8787
```

Windows users can double-click `scripts/start-chatroom.bat`; macOS/Linux users can run `bash scripts/start-chatroom.sh`.

Send a message from the command line:

```bash
python chatroom/chatroom.py send --name Codex --project main --text "Hello"
```

Check workload status:

```bash
python chatroom/workload.py status --project main
```

## Configuration / 配置

Optional: copy `config.example.json` to `config.json` at the project root and edit it.

```json
{
  "host": "127.0.0.1",
  "port": 8787,
  "python": "python",
  "zcode_app": "",
  "opencode_app": "",
  "idle_minutes": 15
}
```

- `host` / `port`: chatroom bind address and port.
- `python`: Python executable used by the dispatcher scripts.
- `zcode_app` / `opencode_app`: optional paths to external AI apps launched by the dispatcher.
- `idle_minutes`: minutes of silence before the dispatcher auto-releases a project.
- `OPENCODE_EXE` (environment variable): path to `OpenCode.exe`, used only by the deep-link popup fallback in `chatroom/wake_relay.py`. When unset it is resolved from `PATH`.

## Docs / 文档

- [Deployment guide (中文)](docs/DEPLOY_ZH.md)
- [Deployment guide (English)](docs/DEPLOY_EN.md)
- [HTTP API & CLI](docs/API.md)
- [3-AI collaboration workflow (中英)](docs/COLLABORATION.md)

## Project structure / 项目结构

```text
.
├── chatroom/            # Chatroom server, monitor, workload desk, watchers, web UI
├── scripts/             # Start scripts and the optional Windows dispatcher
├── docs/                # Deployment, API, and collaboration docs
├── config.example.json  # Sample configuration
└── chatroom/data/       # Runtime data (gitignored, created automatically)
```

## Resource usage / 资源占用

The chatroom plus one monitor process typically uses about 48 MB in total on Windows and near-zero idle CPU, because all polling is incremental. Exact numbers vary by system.

聊天室 + 单个项目 monitor 在 Windows 上实测约 48 MB 内存，空闲 CPU 接近 0；具体数值随系统略有差异。

## License / 许可证

MIT
