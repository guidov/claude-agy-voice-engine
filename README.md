# Local STT / TTS Voice Engine for Claude Code & Antigravity (AGY)

> High-performance, fully local voice interaction, push-to-talk dictation, spoken task summaries, and instant barge-in for **Claude Code (`claude`)** and **Google Antigravity (`agy`)**.

Powered by:
- **Speech-to-Text (STT)**: `whisper.cpp` running `large-v3-turbo` with sub-0.5s latency (accelerated via Vulkan on an Intel Arc B580 GPU / XPU, or any GPU/CPU).
- **Text-to-Speech (TTS)**: `kokoro-onnx` (54 natural voices) with sub-second time-to-first-audio (TTFA ~0.4s).
- **Push-to-Talk & Interruption**: Global keyboard listener (`KEY_RIGHTCTRL` / `evdev`) delivering instant dictation injection and sub-50ms TTS interruption.
- **Agent Lifecycle Hooks**: Silences intermediate tool runs and reads a clean spoken summary aloud when tasks finish.
- **In-Session Mode Switching**: Switch between **Conversation Mode** (`/talk`) and **Coding Mode** (`/code`) in the same terminal with 100% shared context.

---

## 🏛️ Architecture

```mermaid
flowchart TD
    subgraph Input["User Controls"]
        MIC["Microphone (USB / Desktop)"]
        PTT["Right-Ctrl Button (dictate.py)"]
        TUI["Terminal Input (agy / claude)"]
    end

    subgraph Hardware["Local Inference Hardware"]
        WHISPER["whisper.cpp (large-v3-turbo)<br/>Vulkan / GPU (:8910)"]
        KOKORO["Kokoro-ONNX (54 voices)<br/>CPU / ONNX Runtime"]
    end

    subgraph Bridge["Unified Dual-Port Bridge (:2022 / :8880)"]
        STT_API["STT: /v1/audio/transcriptions<br/>(OpenAI multipart format)"]
        TTS_API["TTS: /v1/audio/speech<br/>(Streaming PCM / WAV / MP3)"]
        CTRL["Control Channel (~/.voicemode/control.sock)<br/>Instant Barge-in / Stop"]
    end

    subgraph Agents["Agent Runtimes"]
        CLAUDE["Claude Code CLI<br/>(~/.claude/settings.json)"]
        AGY["Antigravity CLI<br/>(~/.gemini/config/hooks.json)"]
        VM_MCP["VoiceMode MCP Engine<br/>(converse tool)"]
    end

    subgraph Output["Audio Output"]
        PW["PipeWire / ALSA / PulseAudio"]
        SPK["Desktop Speakers / Headphones"]
    end

    MIC --> WHISPER
    PTT -->|"Interrupt in 50ms"| CTRL
    PTT -->|"Transcribe and paste"| WHISPER
    WHISPER --> STT_API
    STT_API --> VM_MCP

    CLAUDE -->|"Stop Hook"| TTS_API
    AGY -->|"Stop Hook"| TTS_API
    TTS_API --> KOKORO
    KOKORO --> PW
    PW --> SPK

    VM_MCP --> STT_API
    VM_MCP --> TTS_API
    CTRL -->|"Abort playback"| PW
```

---

## ✨ Key Features

### 1. Unified Dual-Port Voice Bridge (`server.py`)
Provides OpenAI-compatible endpoints on both standard VoiceMode ports:
- **Port 2022**: Whisper STT endpoint (`/v1/audio/transcriptions`), bridging requests to the resident `whisper.cpp` server.
- **Port 8880**: Kokoro TTS endpoint (`/v1/audio/speech`), delivering low-latency audio generation.
- Both ports handle health checks (`/health`) and failover.

### 2. Spoken Task Completion Summaries
During complex code building, refactoring, and test runs:
- Intermediate tool use (`Bash`, `read_file`, `edit_file`) remains **completely silent** (no annoying repetitive tool beeps).
- When the execution loop finishes, the `Stop` lifecycle hook triggers `voicemode-speak-summary.py`.
- It automatically parses the session transcript (Claude JSONL or AGY JSONL), strips code blocks and Markdown markup into speakable text, and reads the outcome aloud via Kokoro TTS.

### 3. Master Engine Switch (`voice-engine`)
Control all voice services, push-to-talk, summaries, and agent integrations with unified or client-specific switches:

```bash
# ----------------------------------------------------
# 1. Antigravity (AGY) Specific Switches
# ----------------------------------------------------
voice-engine agy on             # Turn voice engine ON for Antigravity (MCP + skills)
voice-engine agy off            # Turn voice engine OFF for Antigravity (disables MCP)
voice-engine agy toggle         # Toggle Antigravity voice integration ON/OFF
voice-engine agy status         # Check Antigravity voice status
voice-engine agy summary on     # Turn spoken summaries ON for Antigravity
voice-engine agy summary off    # Turn spoken summaries OFF for Antigravity (silent)
voice-engine agy uninstall      # Cleanly remove all custom voice files from AGY

# Or use the direct shortcut:
voice-engine-agy [on|off|toggle|status|summary on|summary off]

# ----------------------------------------------------
# 2. Claude Code Specific Switches
# ----------------------------------------------------
voice-engine claude on          # Turn voice engine ON for Claude (/talk, /localvoice)
voice-engine claude off         # Turn voice engine OFF for Claude (removes commands)
voice-engine claude toggle      # Toggle Claude voice integration ON/OFF
voice-engine claude status      # Check Claude Code voice status & hook cleanliness
voice-engine claude summary on  # Turn spoken summaries ON for Claude Code
voice-engine claude summary off # Turn spoken summaries OFF (leaves /voice 100% clean)
voice-engine claude hooks-off   # Ensure Stop hook is removed so native /voice is clean

# Or use the direct shortcut:
voice-engine-claude [on|off|toggle|status|summary on|summary off]

# ----------------------------------------------------
# 3. Global Power & Hardware Service Switches
# ----------------------------------------------------
voice-engine on                 # Start Whisper (GPU), Voice Bridge & Dictate daemons
voice-engine off                # Stop all background services and kill active audio
voice-engine toggle             # Toggle all services ON / OFF
voice-engine status             # Display unified diagnostics dashboard
voice-engine summary on         # Turn spoken summaries ON globally
voice-engine summary off        # Turn spoken summaries OFF globally (silent)

# Push-to-Talk Dictation Switch
voice-engine dictate on         # Start Right-Ctrl dictate daemon
voice-engine dictate off        # Stop Right-Ctrl dictate daemon
```

### 4. Running Cleanly Without Interfering with `/voice`
Claude Code comes with a built-in `/voice` feature. To prevent the local Whisper/Kokoro engine from talking over or interfering with `/voice`:
- **Native Claude Voice**: Use `/voice` (runs Claude's native cloud voice cleanly, without any hooks or local TTS collisions).
- **Custom Local Engine**: Use `/localvoice` or `/talk` (activates the local Intel Arc Whisper STT and Kokoro TTS engine on demand).
- **Turn Summaries**: Kept **OFF** by default so Kokoro never speaks unsolicited audio when a turn finishes.

### 5. Deterministic Barge-in & Interruption
Stop long-winded speech immediately through multiple interfaces:
- **Push-to-Talk Button (`KEY_RIGHTCTRL`)**: Pressing your dictation key aborts active TTS playback in under **50ms** via the control socket.
- **Acoustic Voice Barge-in**: Enabled for all audio devices (`VOICEMODE_MOUTH_BARGE_DEVICES=.*`).
- **CLI / Shortcut**: Run `voicemode-stop` or `voicemode control stop` to silence audio instantly.

### 6. In-Session Mode Switching with 100% Shared Context
No need to run separate terminals for chat and coding:
- **Conversation Mode (`talk` / `/talk`)**: Enters an interactive, hands-free verbal loop. Whisper listens, Kokoro answers, and the mic remains open for your reply.
- **Coding Mode (`code` / `/code`)**: Ends the voice loop and immediately begins hands-on coding using the **exact conversation context** from your discussion.

---

## 🛠️ Repository Layout

```text
claude-agy-voice-engine/
├── README.md                      # Comprehensive guide & architecture
├── LICENSE                        # MIT License
├── install.sh                     # One-click installer & configurator
├── uninstall.sh                   # Modular uninstaller (--agy, --claude, --services, --all)
├── voice-engine                   # Master switch CLI symlink -> bin/voice-engine
├── bin/
│   └── voice-engine               # Master control CLI (on/off/status/toggle/summary/agy)
├── bridge/
│   ├── server.py                  # Dual-port FastAPI bridge (:2022, :8880)
│   ├── test_bridge.py             # Latency & endpoint test suite
│   ├── start.sh                   # Manual bridge starter
│   ├── stop.sh                    # Manual bridge stopper
│   └── systemd/
│       ├── claude-voice-bridge.service
│       ├── whisper-server.service
│       └── dictate.service
├── hooks/
│   ├── voicemode-speak-summary.py # Transcript parser & summary speaker
│   ├── voicemode-hook-receiver.sh # Fast PipeWire-aware hook receiver
│   ├── voicemode-summary          # Summary toggle CLI
│   └── voicemode-stop             # Instant barge-in stop CLI
└── config/
    ├── voicemode.env.template     # VoiceMode configuration template
    ├── agy/
    │   ├── hooks.json             # AGY Stop hook configuration
    │   ├── mcp_config.json        # AGY VoiceMode MCP registration
    │   └── skills/                # AGY skills: talk, code, voicemode
    ├── claude/
    │   ├── commands/              # Claude slash commands (/talk, /code, /localvoice)
    │   └── skills/                # Claude skills: talk, code, voicemode
    └── dictate/
        ├── dictate.py             # evdev push-to-talk daemon with barge-in
        └── dictate.toml.example   # Configuration for audio & hotkeys
```

---

## 🚀 Quick Start (Automated Installation)

Clone this repository and run the installer:

```bash
git clone https://github.com/<your-username>/claude-agy-voice-engine.git
cd claude-agy-voice-engine
./install.sh
```

The installer will:
1. Place hooks and CLI commands into `~/.local/bin` and `~/.voicemode/bin`.
2. Configure `~/.voicemode/voicemode.env` with local endpoints and control channels.
3. Install skills and hooks for **Antigravity** (`~/.gemini/config`) and **Claude Code** (`~/.claude`).
4. Register user systemd services for continuous background operation.

---

## ⚙️ Hardware & Environment Setup

This configuration was built and tested on **Ubuntu Linux (x86_64)** with an **Intel Arc B580 GPU (XPU)** using Vulkan for Whisper and ONNX Runtime for Kokoro. It can be easily adapted to any GPU or CPU setup.

### 1. Whisper Backend (`whisper.cpp`)
- Build `whisper-server` with your preferred backend:
  - **Intel Arc (Vulkan)**: `cmake -B build -DGGML_VULKAN=1 && cmake --build build --config Release -j`
  - **NVIDIA (CUDA)**: `cmake -B build -DGGML_CUDA=1 && cmake --build build --config Release -j`
  - **CPU**: `cmake -B build && cmake --build build --config Release -j`
- Download the model:
  ```bash
  wget -P /path/to/models/ https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q8_0.bin
  ```

### 2. Kokoro TTS Backend
Install `kokoro-onnx` and download model weights:
```bash
pip install kokoro-onnx soundfile fastapi uvicorn httpx
wget -P /path/to/models/ https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
wget -P /path/to/models/ https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

### 3. Environment Variables (Customizing Paths)
You can configure custom model locations in `server.py` via environment variables:
```bash
export WHISPER_URL="http://127.0.0.1:8910/inference"
export KOKORO_MODEL_PATH="/path/to/kokoro-v1.0.onnx"
export KOKORO_VOICES_PATH="/path/to/voices-v1.0.bin"
```

---

## 🎮 Everyday Usage

### In Antigravity (`agy`):
- Type **`talk`** or **`talk to me`** ➔ Enters continuous hands-free voice mode.
- Type **`code`** or **`let's code`** ➔ Exits voice and starts coding with the shared context.
- Hold **Right-Ctrl** ➔ Dictate prompts directly at the cursor.

### In Claude Code (`claude`):
- Type **`/voice`** ➔ Native Claude Cloud Voice (unhindered, 0% interference).
- Type **`/localvoice`** or **`/talk`** ➔ Enters continuous hands-free voice mode using local Whisper + Kokoro.
- Type **`/code`** ➔ Switches back to coding mode.
- Hold **Right-Ctrl** ➔ Dictate prompts directly at the cursor via `dictate.service`.

### Controlling the Voice Engine (Global & Per-Client):
```bash
# Global Engine Control
voice-engine on                 # Turn entire voice engine ON
voice-engine off                # Turn entire voice engine OFF (stops background services & audio)
voice-engine status             # Check diagnostics dashboard
voice-engine stop               # Halts active audio playback immediately

# Per-Client Controls (or use voice-engine-agy / voice-engine-claude shortcuts)
voice-engine agy on             # Enable voice engine in Antigravity
voice-engine agy off            # Disable voice engine in Antigravity
voice-engine agy summary on|off # Toggle spoken summaries in Antigravity

voice-engine claude on          # Enable voice engine in Claude Code
voice-engine claude off         # Disable voice engine in Claude Code
voice-engine claude summary on|off # Toggle spoken summaries in Claude Code
```

---

## 🗑️ Uninstallation & Modular Removal

You can cleanly uninstall or disable components anytime without affecting other tools:

### Option A: Using the `voice-engine` switch CLI
```bash
# Remove only from Antigravity (AGY)
voice-engine agy uninstall

# Re-enable Antigravity integration later
voice-engine agy enable

# Isolate Claude Code hooks (leaves /voice 100% clean)
voice-engine claude hooks-off
```

### Option B: Using `uninstall.sh`
```bash
# Remove only Antigravity integration (MCP, skills, hooks)
./uninstall.sh --agy

# Remove only Claude Code integration (commands & skills)
./uninstall.sh --claude

# Stop, disable and remove systemd background services
./uninstall.sh --services

# Full uninstallation (removes all services, CLI tools, configs, and hooks)
./uninstall.sh --all
```

---

## 📦 GitHub Repository

Source repository: [guidov/claude-agy-voice-engine](https://github.com/guidov/claude-agy-voice-engine.git)

```bash
git clone git@github.com:guidov/claude-agy-voice-engine.git
cd claude-agy-voice-engine
./install.sh
```

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
