# Deployment and Configuration Guide (English)

## 1. Requirements

- Python 3.9 or newer (3.10+ recommended).
- This project only uses the Python standard library. No `pip install` is required.
- The chatroom runs on Windows / macOS / Linux.
- The optional 3-AI dispatcher (`scripts/dispatch.ps1`) requires Windows PowerShell 5.1 or PowerShell 7.

## 2. One-Click Lazy Start (Windows)

After cloning or extracting the project, double-click:

```text
scripts/one-click-start.vbs
```

The launcher:

- creates `config.json` from `config.example.json` if needed;
- uses the Python path from `config.json`, or `pythonw.exe` from PATH when not configured;
- starts the chatroom server;
- starts the workload and automatic-support watcher;
- opens the browser on the `main` channel.

No package installation or third-party dependency is needed. The launcher reads the host, port, and Python path from `config.json`. To test another channel, run:

```bat
cscript //nologo scripts\one-click-start.vbs /project:demo /nobrowser
```

You can also override the port:

```bat
cscript //nologo scripts\one-click-start.vbs /project:demo /port:9000 /nobrowser
```

If antivirus software asks about it, review the script first: it only starts Python locally and does not download or execute remote code.

## 3. Manual Startup

### Step 1: Get the project

Download and extract the ZIP, or:

```bash
git clone <repository-url>
cd <project-directory>
```

### Step 2 (optional): Create a config file

Copy `config.example.json` to `config.json`:

```bash
cp config.example.json config.json
```

The defaults work out of the box. Common fields you may edit:

```json
{
  "host": "127.0.0.1",
  "port": 8787,
  "python": "python",
  "zcode_app": "C:/Path/To/ZCode.exe",
  "opencode_app": "C:/Path/To/OpenCode.exe",
  "idle_minutes": 15
}
```

- `host`: bind address; default is localhost only.
- `port`: web port; default is 8787.
- `python`: interpreter used by the dispatcher. Use a full path if `python` is not on PATH.
- `zcode_app` / `opencode_app`: optional. When set, the dispatcher launches these AI apps on start; leave empty to skip.
- `idle_minutes`: minutes of silence before auto-releasing a project; default 15.

### Step 3: Start the chatroom

Windows: double-click `scripts/start-chatroom.bat`, or run:

```bash
python chatroom/chatroom.py server
```

macOS / Linux:

```bash
bash scripts/start-chatroom.sh
```

Then open:

```text
http://127.0.0.1:8787
```

The status dot should show "online".

## 4. Common Operations

### Send a message from the web UI

Type in the box at the bottom and choose an identity: You / Codex / ZCode / OpenCode.

### Send a message from the command line

```bash
python chatroom/chatroom.py send --name Codex --project main --text "Hello"
```

Check collaboration state:

```bash
python chatroom/workload.py status --project main
```

If the chatroom uses a custom port, keep the workload watcher on the same endpoint:

```bash
python chatroom/workload.py watch --project main --host 127.0.0.1 --port 9000
```

### Send a message over HTTP

```bash
curl -X POST "http://127.0.0.1:8787/api/send?project=main" \
  -H "Content-Type: application/json" \
  -d '{"name":"Codex","text":"Hello"}'
```

### Use multiple project channels

Open in the browser:

```text
http://127.0.0.1:8787/?project=demo
```

Or from the CLI:

```bash
python chatroom/chatroom.py send --name Codex --project demo --text "demo channel"
```

Each project keeps its own messages, transcripts, and watermarks.

## 5. Optional: 3-AI Collaboration Dispatcher (Windows)

The dispatcher provides one-click start / stop for multi-AI collaboration:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dispatch.ps1 -Action start -Project demo
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dispatch.ps1 -Action status -Project demo
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dispatch.ps1 -Action stop -Project demo
```

Or double-click:

```text
scripts/dispatch-start.bat       # default project: main
scripts/dispatch-start.bat demo
scripts/dispatch-status.bat demo
scripts/dispatch-stop.bat demo
```

Behavior:

- `start`: ensures the chatroom is online, starts one monitor process for the project, launches the external AI apps configured in `zcode_app` / `opencode_app`, and posts a "gather" message.
- `stop`: stops only this project's monitor. The shared chatroom stays online and other projects are untouched.
- `status`: shows chatroom, monitor, and app status.
- Auto-release: after `idle_minutes` (default 15) of silence, the monitor posts a notice and runs stop. Any new message resets the timer.
- To disable auto-release: `-NoAutoRelease`.

## 6. Data and Privacy

- All data lives in `chatroom/data/`: message JSONL, plain-text transcript, watermark files, and dispatch state.
- `config.json` and `chatroom/data/` are ignored by `.gitignore`, so they are not pushed to GitHub. Also keep API keys, tokens, personal endpoints, and absolute local paths out of commits.
- The server binds to `127.0.0.1` by default and is not exposed to the internet.

## 7. Resource Usage

- Chatroom + one monitor process: about 48 MB total on Windows, near-zero idle CPU.
- All message reads are incremental; nothing re-reads the whole message file every few seconds.
- Browser polling uses the `since` delta API and only receives new messages.
