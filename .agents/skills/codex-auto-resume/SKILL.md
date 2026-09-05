---
name: codex-auto-resume
description: Safely inspect, start, stop, or report on the local Codex auto-resume watcher for one explicitly selected task in this repository.
---

# Codex Auto Resume

Use the repository's `./resume` entrypoint on macOS, or `.\resume.cmd` on native Windows. Do not call private modules directly or rewrite the launcher. Do not run the Windows helper inside WSL because it connects to the native App's named pipe.

In the commands below, use `.\resume.cmd` in place of `./resume` when the host is Windows.

## Workflow

- For readiness, run `./resume doctor` on macOS or `.\resume.cmd doctor` on Windows. This is read-only.
- For existing watcher records, run `./resume status`.
- List conversations only when the user asks to find or select one. Treat `./resume list` as discovery, never as authorization to manage every result.
- A start or stop target must be one exact UUID explicitly selected by the user. Do not infer it from “latest”, a fuzzy title, or conversation text.
- Before starting, run `./resume check <UUID>` and require `canMonitor: true`. Starting still requires the user's explicit request; a successful check alone is not consent.
- Start with `./resume start <UUID>` and keep the default budget unless the user explicitly chooses another limit. Never use resets, alternate accounts, approvals, or payment as a workaround.
- Stop only the selected task with `./resume stop <UUID>`. Explain that stopping cancels future continuations and does not interrupt work already running in Codex.

## Safety boundaries

Never auto-enroll multiple tasks, scan cloud ChatGPT conversations, send a custom prompt, approve requests, or retry a start whose result is uncertain. Report command failures without weakening version checks or deduplication. The Codex App must remain open; closing the terminal does not stop an already launched watcher, while restarting the computer requires starting it again.
