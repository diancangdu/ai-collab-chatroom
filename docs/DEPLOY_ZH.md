# 部署与配置指南（中文）

## 1. 环境要求

- Python 3.9 或更高版本，建议 3.10+。
- 本项目只使用 Python 标准库，不需要 `pip install` 任何包。
- 聊天室可在 Windows / macOS / Linux 运行。
- 可选的三模型调度器（`scripts/dispatch.ps1`）只支持 Windows PowerShell 5.1 或 PowerShell 7。

## 2. 一键跑起来（Windows 懒人版）

下载或克隆项目后，直接双击：

```text
scripts/one-click-start.vbs
```

它会自动完成这些事：

- 从 `config.example.json` 生成本机 `config.json`（如果还没有）。
- 使用 `config.json` 里的 Python 路径；未配置时使用系统 PATH 里的 `pythonw.exe`。
- 启动聊天室服务。
- 启动工作负载/自动支援监听。
- 打开默认浏览器并进入 `main` 频道。

不需要执行安装命令，也没有第三方依赖。启动器会读取 `config.json` 里的主机、端口和 Python 路径。想用其他频道测试，可以运行：

```bat
cscript //nologo scripts\one-click-start.vbs /project:demo /nobrowser
```

也可以同时覆盖端口：

```bat
cscript //nologo scripts\one-click-start.vbs /project:demo /port:9000 /nobrowser
```

如果杀毒软件询问，请先核对脚本内容：它只会在本机启动 Python，不会从互联网下载或执行远程代码。

## 3. 手动跑起来（三步）

### 第一步：拿到项目

直接下载 ZIP 解压，或者：

```bash
git clone <仓库地址>
cd <项目目录>
```

### 第二步（可选）：创建配置文件

把 `config.example.json` 复制一份为 `config.json`：

```bash
cp config.example.json config.json
```

默认配置已经可以直接跑。需要改的常见项：

```json
{
  "host": "127.0.0.1",
  "port": 8787,
  "python": "python",
  "zcode_app": "C:/Path/To/ZCode.exe",
  "opencode_app": "C:/Path/To/OpenCode.exe",
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

- `host`：监听地址，默认只允许本机访问。
- `port`：网页端口，默认 8787。
- `python`：调度器启动 Python 进程时使用的解释器；如果 `python` 不在 PATH，改成完整路径。
- `zcode_app` / `opencode_app`：可选。填写后，调度器 start 会自动拉起这两个 AI 应用；留空则跳过。
- `idle_minutes`：频道静默多少分钟后自动解除调动，默认 60。
- `commander_rules`：可选的总指挥硬规则。`RULE_012` 要求所有被派任务的兄弟都确认完成、复核意见处理完毕后，总指挥才能宣布完成或收工。

### 第三步：启动聊天室

Windows 双击：

```text
scripts/start-chatroom.bat
```

或者命令行：

```bash
python chatroom/chatroom.py server
```

macOS / Linux：

```bash
bash scripts/start-chatroom.sh
```

启动后浏览器打开：

```text
http://127.0.0.1:8787
```

看到“在线”就说明服务正常。

## 4. 常用操作

### 网页发言

直接在最下面的输入框发言，身份可以切换为“你 / Codex / ZCode / OpenCode”。

### 命令行发言

```bash
python chatroom/chatroom.py send --name Codex --project main --text "你好"
```

查看协作状态：

```bash
python chatroom/workload.py status --project main
```

如果聊天室服务使用了自定义端口，工作负载监听也要保持一致：

```bash
python chatroom/workload.py watch --project main --host 127.0.0.1 --port 9000
```

### HTTP 发言

```bash
curl -X POST "http://127.0.0.1:8787/api/send?project=main" \
  -H "Content-Type: application/json" \
  -d '{"name":"Codex","text":"你好"}'
```

### 多项目频道

网页打开：

```text
http://127.0.0.1:8787/?project=demo
```

命令行：

```bash
python chatroom/chatroom.py send --name Codex --project demo --text "demo 频道消息"
```

每个项目的消息、记录、水位自动分开存放，互不影响。

## 5. 可选：三模型协作调度器（Windows）

调度器用于“一键集合 / 一键解散”多 AI 协作：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dispatch.ps1 -Action start -Project demo
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dispatch.ps1 -Action status -Project demo
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dispatch.ps1 -Action stop -Project demo
```

也可以双击：

```text
scripts/dispatch-start.bat    # 默认项目 main
scripts/dispatch-start.bat demo
scripts/dispatch-status.bat demo
scripts/dispatch-stop.bat demo
```

行为说明：

- `start`：确保聊天室在线，为该项目启动一个 monitor 值守进程，按 `config.json` 里的 `zcode_app` / `opencode_app` 拉起外部 AI 应用，并往频道发集合消息。
- `stop`：只停当前项目的 monitor，聊天室服务保留，其他项目不受影响。
- `status`：查看聊天室、monitor、外部应用运行状态。
- 自动收工：频道静默超过 `idle_minutes`（默认 60 分钟）后，monitor 会自动发通知并执行 stop；任何新发言都会重置计时。
- 想关闭自动收工：`-NoAutoRelease`。

## 6. 数据与隐私

- 所有数据保存在 `chatroom/data/`：消息 JSONL、纯文本 transcript、水位文件、调度状态。
- `config.json` 和 `chatroom/data/` 已被 `.gitignore` 忽略，不会上传到 GitHub。提交前也不要加入本机路径、API key、token 或私人日志。
- 服务器默认只绑定 `127.0.0.1`，不对外网开放。

## 7. 资源占用说明

- 聊天室服务 + 每个项目一个 monitor，Windows 实测合计约 48 MB，空闲 CPU 接近 0。
- 所有消息读取都是增量读取，不会每几秒全量重读整个消息文件。
- 网页轮询使用 `since` 增量接口，只返回新消息。
