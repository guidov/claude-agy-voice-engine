---
name: voicemode
description: >-
  Enables hands-free two-way voice conversations and speech-to-text / text-to-speech
  workflows in Antigravity (AGY) using local Whisper STT (Intel Arc B580 GPU) and Kokoro TTS.
  Use when the user asks to talk, speak, converse, have a voice chat, listen, transcribe audio,
  or manage voice settings.
---

# VoiceMode Skill for Antigravity (AGY)

Enables hands-free voice interaction in Antigravity using local hardware:
- **STT**: Whisper `large-v3-turbo` running on Intel Arc B580 GPU via Vulkan (`127.0.0.1:2022`).
- **TTS**: Kokoro ONNX running locally with 54 voices (`127.0.0.1:8880`).

## Key Features

### 1. Spoken Final Summary on Task Completion
At the end of a coding task or code-building exercise, the `Stop` hook automatically extracts the final outcome summary from the turn transcript, synthesizes it through local Kokoro TTS, and speaks it aloud.
- Intermediate tool runs remain completely silent.
- No redundant beeps during execution; only the natural spoken summary is read when work finishes.

### 2. Barge-in & Interruption
If the assistant speaks for too long or the user wants to interject:
- **Push-to-Talk Button (`KEY_RIGHTCTRL`)**: Holding the external whisper button (`dictate.service`) immediately terminates any active TTS audio playback within milliseconds.
- **Acoustic Barge-in**: Simply speaking into the microphone during playback detects novel user words and cuts off the speech.
- **Control Socket & CLI**: Run `voicemode-stop` or `voicemode control stop` to halt playback from any script or hotkey.

## Available Tools (via `voicemode` MCP)

1. **`converse`**:
   - Initiates or continues a real-time hands-free voice dialogue with the user.
   - Captures microphone input, transcribes it through the local Intel Arc GPU Whisper server.
   - Synthesizes spoken replies through Kokoro TTS.
   - Set `wait_for_response=false` to speak an utterance without waiting for user input.

2. **`pause_conversation`**:
   - Temporarily pause an ongoing voice conversation.

3. **`service`**:
   - Inspect or manage the local voice services (`whisper`, `kokoro`).
   - Actions: `status`, `health`, `restart`, `logs`.

## Architecture & Endpoints

- **Whisper Server (Intel Arc GPU)**: `whisper-server.service` on port `8910`.
- **Kokoro & Whisper Bridge**: `claude-voice-bridge.service` on ports `2022` (STT) and `8880` (TTS).
- **Default Voice**: `af_sky` (configurable in `~/.voicemode/voicemode.env`).
- **Control Socket**: `~/.voicemode/control.sock`.
- **Dictation Daemon**: `dictate.service` (`KEY_RIGHTCTRL`).

## Commands & Controls

```bash
# Check VoiceMode service health
uvx voice-mode status

# Toggle spoken summary TTS vs quiet chime mode
voicemode-summary on       # spoken summary via Kokoro TTS
voicemode-summary off      # quiet mode: single completion chime only
voicemode-summary toggle   # switch between spoken TTS and chime
voicemode-summary status   # check current summary mode

# Barge-in / Stop playback
voicemode-stop
voicemode control stop
```
