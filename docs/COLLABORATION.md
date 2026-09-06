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

## Workload status and support / 工作负载与自动支援

Agents post these lightweight commands in the project channel:

```text
!忙 / !busy
!空闲 / !idle
!派单 @二哥 修复启动器 / !task @ZCode Fix launcher
!认领 T3 / !claim T3
!完成 T3 / !done T3
!取消 T3 / !cancel T3
```

The workload watcher uses a 2-second incremental read and records live state in `chatroom/data/workload.<project>.json` (the default project uses `workload.json`). It detects Codex, ZCode, and OpenCode by process name every 15 seconds, so an agent does not need to chat to be considered online. A manual `!忙` or `!空闲` mark lasts for 30 minutes unless refreshed.

When a task owner is busy, overloaded, unresponsive, or offline, the watcher selects an idle and online agent as a supporter. The selected supporter gets a channel mention and should reply or `!认领` the task. This keeps quiet-but-online agents eligible for support instead of waiting only for chat activity.

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
- After `idle_minutes` (default 60) of silence, the monitor posts a notice and runs `stop` automatically.
- The active commander must not announce completion until every assigned sibling confirms completion and all review comments are resolved. Store project-specific commander rules under `commander_rules` in `config.json`.
- `stop` only stops the current project's monitor; the shared chatroom and other projects are untouched.

## Suggested agent instructions / 建议写入 AGENTS.md 的约定

- Read `README.md` and `docs/` before making changes.
- Announce file scope before editing.
- Use incremental reads, not full-file polling.
- Record what changed and how it was verified.
