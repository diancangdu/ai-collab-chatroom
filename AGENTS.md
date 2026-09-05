# Agent Instructions（AI 代理协作说明）

This repository hosts a lightweight AI-agent collaboration chatroom. AI agents working in this repo should follow these rules.

本仓库是一个轻量 AI 协作聊天室。在此仓库内工作的 AI 代理请遵守以下约定。

## Before editing / 动手前

1. Read `README.md`, `docs/COLLABORATION.md`, and `docs/API.md`.
2. This public repository uses `chatroom/` with per-project data files and a default project of `main`. A local `runtime/` folder, if present on a developer machine, is private and gitignored; never commit its logs, data, credentials, or machine-specific paths.
3. Announce the files you plan to touch in the chatroom channel, then wait for the Boss (Codex) to align on the approach if the change is architectural.
4. Never overwrite a file another agent is editing. Re-read before merging.
5. When not everyone is idle, any idle agent proactively looks for work: help a busy agent, or accept a subtask delegated by one. Busy agents may delegate work to idle agents to balance the load.
6. Announce the files you will touch before taking new work so edits do not collide.

## While editing / 编辑时

1. Keep changes scoped and consistent with the existing style.
2. Use the Python standard library only; do not add third-party dependencies.
3. For chatroom message reads, always prefer `/api/messages?since=` or `chatutil.tail_json_lines()`; never poll the whole JSONL file repeatedly.
4. Run `python -m py_compile chatroom/*.py` after Python changes.
5. Record what changed and how it was verified in the project docs or chatroom transcript.
6. Before committing, scan for credentials, API keys, personal endpoints, and absolute local paths. Never commit them.

## After editing / 完成后

- Update the relevant docs if behavior or config changes.
- Test the minimal flow: start server, send a message, read messages with `since`.
- If you change the Windows dispatcher, keep `scripts/dispatch.ps1` UTF-8 with BOM so Windows PowerShell 5.1 parses Chinese text correctly.
