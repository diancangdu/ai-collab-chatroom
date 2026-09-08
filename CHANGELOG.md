# Changelog

## 2026-09-08

### Added

- Hidden background supervisor for the chatroom, monitor, wake relay, bridge
  watchers, and workload desk.
- Front-end console for service status, app paths, live roster, commander,
  sessions, tasks, and force shutdown.
- Live roster control. Excluded agents are skipped by wakeups; their tasks are
  reassigned to the least-loaded active agent. Empty roster pauses tasks and
  preserves progress/handoff history.
- Commander selection per project. Any of Codex, ZCode, or OpenCode can be the
  commander, and workload notices use the current commander identity.
- Work-start contract: workers locate the local communication server first and
  wait for it before entering their loops.
- Live session registry for Codex, ZCode, and OpenCode.
- Per-project startup communication gate. ZCode and OpenCode must return a
  gate marker with their real local time before project tasks are dispatched.

### Changed

- Removed the 60-minute automatic release from the collaboration service.
- Added APIs for roster, commander, workload, service state, and session
  refresh.
- Project dispatch now launches both sibling bridge watchers and the workload
  desk. Mention routing accepts `@ZCode` / `@OpenCode` anywhere in the message.
- Added a shared OpenCode rate limiter, longer bridge cooldowns, and disabled
  non-essential digest calls to reduce TPM/RPM failures without changing model.

### Privacy audit

- The private `runtime/` directory and live data/logs remain outside version
  control.
- Machine-specific application paths moved behind config values or environment
  variables in the public code.
- No API keys, tokens, passwords, chat transcripts, session IDs, or local data
  files are intentionally committed.
- Reviewers should still run their own secret scan before deployment.

### Deployment warning

This is a high-token workflow: it keeps multiple AI agents and bridges active.
If you care strongly about saving tokens, this project is not recommended.
## v2.0.0 - 2026-09-08

### Added
- `/desktop` PWA-style desktop UI with project switching, live agents/tasks, image upload, SSE, and offline send queue.
- `/api/health` public liveness endpoint and `/api/stream` incremental SSE endpoint.
- Local API token support (`X-Chatroom-Token` or `Authorization: Bearer`), with browser cookie support.
- One-click Windows lazy deploy that resolves the real Desktop folder and creates a shortcut.
- Privacy audit script for staged files.

### Fixed
- Prevented OpenCode credential probes from opening repeated console windows.
- Fixed ZCode bridge watermark overflow and added UI-reply backflow to chatroom.
- Unified runtime-data documentation and dispatch process deduplication.
