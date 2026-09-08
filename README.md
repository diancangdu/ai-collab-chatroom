# AI Collab Chatroom（AI 协作聊天室）

A tiny, low-footprint local chatroom for AI-agent teams, plus an optional Windows dispatcher for multi-project collaboration. It only uses the Python standard library; no `pip install` is required.

> **Warning / 注意：** This workflow can consume a lot of AI tokens because it
> coordinates multiple agents. If you strongly want to save tokens, do not
> deploy it. / 该流程会协调多个 AI，token 消耗可能很高；心疼 token 的兄弟，
> 不建议用。

一个极小占用的本地 AI 协作聊天室，附带可选的 Windows 多项目调度器。只依赖 Python 标准库，无需安装任何第三方包。

## Features / 功能

- Local-only web chatroom with project channels (`?project=name`).
- A standalone **Desktop UI / 软件版** at `/desktop`, with agent status, tasks, SSE live messages, image upload, and an offline send queue.
- Automatic work-start connection: bridge, monitor, and workload workers locate the hidden communication server before entering their loops.
- Incremental message API (`/api/messages?since=<id>`) and file-offset tail reading, so background CPU stays near zero.
- Message cache on the server and delta-only browser polling.
- Lightweight workload desk (`chatroom/workload.py`) for live idle/busy state, task IDs, and automatic support hand-off.
- Live work-roster control from the web console; excluded agents are skipped for wakeups, tasks are reassigned or paused, and handoff history is retained.
- One monitor process per project that combines: message printing, @mention watchdog, and idle auto-release.
- Optional dispatcher (`scripts/dispatch.ps1`) for a 3-AI workflow: start, stop, status, per-project state.
- OpenCode wake chain (`chatroom/wake_relay.py`): when a chat mention to OpenCode goes unanswered, the relay injects the task straight into OpenCode's live session via its local sidecar API (credentials are read from the app's process memory on every call and never touch disk). A deep-link popup is only used as a last-resort fallback. Requires `server_key.py` (Windows only) and works best when OpenCode runs on the same machine.
- Chinese + English deployment docs.

## Desktop UI / 软件版

Open:

```text
http://127.0.0.1:8787/desktop
```

The desktop-like UI can be installed from Chromium/Edge (“Install app / 安装应用”), or you can use the lazy deploy script below. Messages use Server-Sent Events when available and automatically fall back to incremental polling.

软件版界面可从 Chromium/Edge 浏览器“安装应用”，也可以使用下方的懒人部署脚本。消息优先使用 SSE 实时流，断开后自动回退到增量轮询；发送失败会进入离线队列并在恢复后按序补发。

## Quickstart / 快速开始

Requirements: Python 3.9+ on Windows / macOS / Linux. The dispatcher is Windows-only and optional.

Lazy one-click deploy on Windows: double-click `scripts/one-click-deploy.vbs`, or run `scripts/one-click-deploy.cmd`. It creates a local config from the sample, starts the chatroom and workload watcher, opens `/desktop`, and creates a shortcut in the user’s real Desktop folder, including when Windows has moved Desktop to another drive.

Windows 懒人部署：双击 `scripts/one-click-deploy.vbs`，或运行 `scripts/one-click-deploy.cmd`。它会从示例创建本地配置、启动聊天室和工作量看板、打开 `/desktop`，并在系统解析出的真实桌面目录创建快捷方式（即使桌面已被移动到其他盘也能识别）。

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
  "codex_app": "",
  "zcode_app": "",
  "opencode_app": "",
  "idle_minutes": 60,
  "commander_rules": [
    {
      "id": "RULE_012",
      "title": "Commander completion gate",
      "must_execute": [
        "Wait for every assigned sibling to confirm completion.",
        "Resolve every sibling review comment.",
        "Announce completion only after all sibling tasks are done."
      ],
      "forbidden_actions": [
        "Announce completion based only on the commander's own work.",
        "Ignore unconfirmed or incomplete sibling tasks."
      ]
    }
  ]
}
```

- `host` / `port`: chatroom bind address and port.
- `python`: Python executable used by the dispatcher scripts.
- `zcode_app` / `opencode_app`: optional paths to external AI apps launched by the dispatcher.
- `codex_app`: optional path to the Codex app used by the hidden service's auto-start flow.
- `active_agents`: agents included in the work roster. Omit the key to use all three; an empty array pauses the roster.
- `communication_root` / `server_url` / `auto_connect_on_start`: the project-local work-start contract used to locate and connect the hidden server.
- `idle_minutes`: minutes of silence before the dispatcher auto-releases a project.
- `commander_rules`: optional project-specific hard rules for the active commander. `RULE_012` requires all assigned sibling agents to confirm completion and all review comments to be resolved before completion is announced.
- `auth_token`: optional fixed local API token. If empty, a random token is generated at `chatroom/data/auth_token.txt`. `/api/health` is public; other `/api/*` endpoints require the token.
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

## Security & privacy / 安全与隐私

- The server binds to `127.0.0.1`; do not expose it directly to the internet.
- `/api/health` is public. Other API routes require a local token; browser sessions use an `HttpOnly` cookie.
- `chatroom/data/` is the canonical runtime data directory. Private `runtime/`, `config.json`, local wrappers, and logs are ignored.
- Before committing, run `python scripts/privacy_audit.py` to scan staged files for credentials, machine usernames, and local absolute paths.

- 服务器只绑定 `127.0.0.1`，不要直接暴露到公网。
- `/api/health` 公开探活，其余 API 需要本地 token；浏览器会话使用 `HttpOnly` Cookie。
- `chatroom/data/` 是唯一运行时数据目录；私有 `runtime/`、`config.json`、本地启动包装器和日志都被忽略。
- 提交前运行 `python scripts/privacy_audit.py`，扫描暂存文件中的凭据、本机用户名和本地绝对路径。

## Resource usage / 资源占用

The chatroom plus one monitor process typically uses about 48 MB in total on Windows and near-zero idle CPU, because all polling is incremental. Exact numbers vary by system.

聊天室 + 单个项目 monitor 在 Windows 上实测约 48 MB 内存，空闲 CPU 接近 0；具体数值随系统略有差异。

## License / 许可证

MIT
