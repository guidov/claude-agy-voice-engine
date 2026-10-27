#!/usr/bin/env bash
# ==============================================================================
# Claude & Antigravity (AGY) Voice Engine Installer
# Local STT (Whisper Vulkan/XPU) + TTS (Kokoro ONNX) + Push-to-Talk + Hooks
# ==============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_BIN="$HOME/.local/bin"
VOICEMODE_BIN="$HOME/.voicemode/bin"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
GEMINI_CONFIG_DIR="$HOME/.gemini/config"
CLAUDE_CONFIG_DIR="$HOME/.claude"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

echo -e "${GREEN}"
echo "=========================================================="
echo "  Claude & Antigravity (AGY) Local Voice Engine Installer "
echo "=========================================================="
echo -e "${NC}"

# 1. Directories setup
log "Creating system directories..."
mkdir -p "$USER_BIN" "$VOICEMODE_BIN" "$SYSTEMD_USER_DIR"
mkdir -p "$GEMINI_CONFIG_DIR/skills" "$CLAUDE_CONFIG_DIR/skills" "$CLAUDE_CONFIG_DIR/commands"
mkdir -p "$HOME/.voicemode/logs" "$HOME/.voicemode/soundfonts/voicemode"

# 2. Install Hook & CLI tools
log "Installing VoiceMode summary speaker & control tools..."
cp "$REPO_DIR/hooks/voicemode-speak-summary.py" "$VOICEMODE_BIN/voicemode-speak-summary.py"
chmod +x "$VOICEMODE_BIN/voicemode-speak-summary.py"

cp "$REPO_DIR/hooks/voicemode-hook-receiver.sh" "$VOICEMODE_BIN/voicemode-hook-receiver"
chmod +x "$VOICEMODE_BIN/voicemode-hook-receiver"

cp "$REPO_DIR/hooks/voicemode-summary" "$USER_BIN/voicemode-summary"
chmod +x "$USER_BIN/voicemode-summary"

cp "$REPO_DIR/hooks/voicemode-stop" "$USER_BIN/voicemode-stop"
chmod +x "$USER_BIN/voicemode-stop"

# Ensure voicemode CLI launcher exists in ~/.local/bin
cat << 'EOF' > "$USER_BIN/voicemode"
#!/usr/bin/env bash
exec uvx voice-mode "$@"
EOF
chmod +x "$USER_BIN/voicemode"
success "Installed hooks and CLI utilities into $USER_BIN and $VOICEMODE_BIN"

# 3. Configure VoiceMode environment
VOICEMODE_ENV="$HOME/.voicemode/voicemode.env"
if [ ! -f "$VOICEMODE_ENV" ]; then
    log "Creating new $VOICEMODE_ENV from template..."
    cp "$REPO_DIR/config/voicemode.env.template" "$VOICEMODE_ENV"
else
    log "Ensuring required control channel settings in $VOICEMODE_ENV..."
    grep -q "VOICEMODE_CONTROL_CHANNEL_ENABLED=true" "$VOICEMODE_ENV" || cat << 'EOF' >> "$VOICEMODE_ENV"

#############
# Control Channel & Barge-in Configuration
#############
VOICEMODE_CONTROL_CHANNEL_ENABLED=true
VOICEMODE_MOUTH_BARGE=partial
VOICEMODE_MOUTH_BARGE_DEVICES=.*
VOICEMODE_MOUTH_BARGE_WORDS=2
VOICEMODE_TTS_SUMMARY_ENABLED=true
VOICEMODE_PREFER_LOCAL=true
VOICEMODE_ALWAYS_TRY_LOCAL=true
VOICEMODE_TTS_BASE_URLS=http://127.0.0.1:8880/v1
VOICEMODE_STT_BASE_URLS=http://127.0.0.1:2022/v1
EOF
fi
success "VoiceMode environment configured"

# 4. Antigravity (AGY) Integration
log "Configuring Antigravity (AGY) skills, hooks, and MCP..."
mkdir -p "$GEMINI_CONFIG_DIR/skills/talk" "$GEMINI_CONFIG_DIR/skills/code" "$GEMINI_CONFIG_DIR/skills/voicemode"
cp "$REPO_DIR/config/agy/skills/talk/SKILL.md" "$GEMINI_CONFIG_DIR/skills/talk/SKILL.md"
cp "$REPO_DIR/config/agy/skills/code/SKILL.md" "$GEMINI_CONFIG_DIR/skills/code/SKILL.md"
cp "$REPO_DIR/config/agy/skills/voicemode/SKILL.md" "$GEMINI_CONFIG_DIR/skills/voicemode/SKILL.md"

# Safely merge hooks.json without clobbering existing user hooks
if [ -f "$GEMINI_CONFIG_DIR/hooks.json" ]; then
    python3 -c "
import json
with open('$GEMINI_CONFIG_DIR/hooks.json', 'r') as f:
    dst = json.load(f)
with open('$REPO_DIR/config/agy/hooks.json', 'r') as f:
    src = json.load(f)
dst.update(src)
with open('$GEMINI_CONFIG_DIR/hooks.json', 'w') as f:
    json.dump(dst, f, indent=2)
"
else
    cp "$REPO_DIR/config/agy/hooks.json" "$GEMINI_CONFIG_DIR/hooks.json"
fi

# Safely merge voicemode into mcp_config.json without clobbering other MCP servers
if [ -f "$GEMINI_CONFIG_DIR/mcp_config.json" ]; then
    python3 -c "
import json
with open('$GEMINI_CONFIG_DIR/mcp_config.json', 'r') as f:
    dst = json.load(f)
with open('$REPO_DIR/config/agy/mcp_config.json', 'r') as f:
    src = json.load(f)
dst.setdefault('mcpServers', {})
dst['mcpServers']['voicemode'] = src['mcpServers']['voicemode']
with open('$GEMINI_CONFIG_DIR/mcp_config.json', 'w') as f:
    json.dump(dst, f, indent=2)
"
else
    cp "$REPO_DIR/config/agy/mcp_config.json" "$GEMINI_CONFIG_DIR/mcp_config.json"
fi
success "Antigravity (AGY) configured"

# 5. Claude Code Integration
log "Configuring Claude Code skills, slash commands, and hooks..."
mkdir -p "$CLAUDE_CONFIG_DIR/skills/talk" "$CLAUDE_CONFIG_DIR/skills/code" "$CLAUDE_CONFIG_DIR/skills/voicemode"
cp "$REPO_DIR/config/claude/skills/talk/SKILL.md" "$CLAUDE_CONFIG_DIR/skills/talk/SKILL.md"
cp "$REPO_DIR/config/claude/skills/code/SKILL.md" "$CLAUDE_CONFIG_DIR/skills/code/SKILL.md"
cp "$REPO_DIR/config/claude/skills/voicemode/SKILL.md" "$CLAUDE_CONFIG_DIR/skills/voicemode/SKILL.md"
cp "$REPO_DIR/config/claude/commands/talk.md" "$CLAUDE_CONFIG_DIR/commands/talk.md"
cp "$REPO_DIR/config/claude/commands/code.md" "$CLAUDE_CONFIG_DIR/commands/code.md"
success "Claude Code configured"

# 6. Systemd user services
log "Installing Systemd user services..."
cp "$REPO_DIR/bridge/systemd/claude-voice-bridge.service" "$SYSTEMD_USER_DIR/"
if [ -f "$REPO_DIR/bridge/systemd/whisper-server.service" ]; then
    cp "$REPO_DIR/bridge/systemd/whisper-server.service" "$SYSTEMD_USER_DIR/"
fi
if [ -f "$REPO_DIR/bridge/systemd/dictate.service" ]; then
    cp "$REPO_DIR/bridge/systemd/dictate.service" "$SYSTEMD_USER_DIR/"
fi

systemctl --user daemon-reload
success "Systemd user services installed and reloaded"

# 7. Final instructions
echo ""
echo -e "${GREEN}=========================================================="
echo "              Installation Complete!                     "
echo "==========================================================${NC}"
echo ""
echo "To manage your services:"
echo "  systemctl --user restart claude-voice-bridge.service"
echo "  systemctl --user restart whisper-server.service"
echo "  systemctl --user restart dictate.service"
echo ""
echo "To verify:"
echo "  uvx voice-mode status"
echo "  voicemode-summary status"
echo ""
echo "In Claude Code or Antigravity (agy):"
echo "  Type 'talk' or /talk ➔ Enter continuous hands-free voice mode"
echo "  Type 'code' or /code ➔ Return to silent coding mode with spoken summary at the end"
echo "  Hold Right-Ctrl      ➔ Push-to-talk dictation + instant speech cutoff"
echo ""
