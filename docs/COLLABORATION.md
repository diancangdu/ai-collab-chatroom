# 3-AI Collaboration Workflow（三模型协作战流程）

This project can be used as a coordination desk for multiple AI agents (for example Codex, ZCode, and OpenCode) working on the same machine.

本项目可作为同一台机器上多个 AI 代理（例如 Codex、ZCode、OpenCode）的协作会议桌。

## Roles / 角色

| Role / 角色 | Tool / 工具 | Responsibility / 职责 |
|---|---|---|
| Boss / 大哥 | Codex | Architecture decisions, task assignment, final review / 拍板、派活、验收 |
| Second / 二哥 | ZCode | Technical review, supervision / 技术复核、监督 |
| Third / 三弟 | OpenCode | Implementation under supervision / 执行任务 |

## Working rules / 工作规则

1. Discuss before changing shared files. Announce which files you are about to touch.
2. Record decisions in a shared doc (for example `docs/` or the project transcript) so everyone reads the same source of truth.
3. If opinions conflict, present evidence and let the Boss decide.
4. When one agent is stuck, @mention another agent for help.
5. Keep background footprint small: use `/api/messages?since=` or `chatutil.tail_json_lines()` instead of repeatedly reading the whole message file.
6. When not everyone is idle, idle agents proactively take work: help busy agents, or accept subtasks delegated by them. Busy agents may delegate work to idle agents to balance the load.

## Dispatcher triggers / 调度触发

Windows dispatcher:

```powershell
# Gather the team for a project
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dispatch.ps1 -Action start -Project demo

# Check status
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dispatch.ps1 -Action status -Project demo

# Dismiss the team (chatroom stays online)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dispatch.ps1 -Action stop -Project demo
```

Behavior:

- `start` posts a "gather" message to the project channel and starts one monitor process.
- Any new message resets the idle timer.
- After `idle_minutes` (default 15) of silence, the monitor posts a notice and runs `stop` automatically.
- `stop` only stops the current project's monitor; the shared chatroom and other projects are untouched.

## Suggested agent instructions / 建议写入 AGENTS.md 的约定

- Read `README.md` and `docs/` before making changes.
- Announce file scope before editing.
- Use incremental reads, not full-file polling.
- Record what changed and how it was verified.
