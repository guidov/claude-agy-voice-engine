---
name: code
description: >-
  Switch to coding mode. Use when the user types /code or asks to switch to coding mode.
  In this mode, the agent focuses on hands-on code development, file edits, and tool execution,
  running tools silently and reading a spoken summary upon task completion.
---

# Coding Mode (/code)

When in coding mode:
1. Review the decisions, requirements, and architecture discussed during the voice session.
2. Execute file edits, code builds, tests, and bash commands silently.
3. Upon task completion, the final summary is automatically read aloud via Kokoro TTS (with instant barge-in support).
4. Whenever the user wants to brainstorm or discuss again, they can type `/talk` or say "talk to me".
