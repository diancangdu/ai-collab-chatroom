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

### Changed

- Removed the 60-minute automatic release from the collaboration service.
- Added APIs for roster, commander, workload, service state, and session
  refresh.

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
