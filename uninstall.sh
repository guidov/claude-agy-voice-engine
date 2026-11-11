#!/usr/bin/env bash
# ==============================================================================
# Claude & Antigravity (AGY) Voice Engine Uninstaller
# ==============================================================================

set -uo pipefail

USER_BIN="$HOME/.local/bin"
VOICEMODE_DIR="$HOME/.voicemode"
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

uninstall_agy() {
    log "Removing Voice Engine integration from Antigravity (AGY)..."
    # Remove from mcp_config.json
    if [ -f "$GEMINI_CONFIG_DIR/mcp_config.json" ]; then
        python3 -c "
import json
p = '$GEMINI_CONFIG_DIR/mcp_config.json'
try:
    with open(p, 'r') as f:
        data = json.load(f)
    if 'mcpServers' in data and 'voicemode' in data['mcpServers']:
        del data['mcpServers']['voicemode']
        with open(p, 'w') as f:
            json.dump(data, f, indent=2)
        print('Removed voicemode from mcp_config.json')
except Exception as e:
    print('Error:', e)
"
    fi

    # Remove from hooks.json
    if [ -f "$GEMINI_CONFIG_DIR/hooks.json" ]; then
        python3 -c "
import json
p = '$GEMINI_CONFIG_DIR/hooks.json'
try:
    with open(p, 'r') as f:
        data = json.load(f)
    if 'voicemode-summary-speaker' in data:
        del data['voicemode-summary-speaker']
    with open(p, 'w') as f:
        json.dump(data, f, indent=2)
    print('Removed voicemode hook from hooks.json')
except Exception as e:
    print('Error:', e)
"
    fi

    rm -rf "$GEMINI_CONFIG_DIR/skills/talk"
    rm -rf "$GEMINI_CONFIG_DIR/skills/code"
    rm -rf "$GEMINI_CONFIG_DIR/skills/voicemode"
    rm -rf "$HOME/.gemini/antigravity-cli/mcp/voicemode"
    success "Antigravity integration uninstalled cleanly."
}

uninstall_claude() {
    log "Removing Voice Engine integration from Claude Code..."
    rm -f "$CLAUDE_CONFIG_DIR/commands/talk.md"
    rm -f "$CLAUDE_CONFIG_DIR/commands/code.md"
    rm -f "$CLAUDE_CONFIG_DIR/commands/localvoice.md"
    rm -rf "$CLAUDE_CONFIG_DIR/skills/talk"
    rm -rf "$CLAUDE_CONFIG_DIR/skills/code"
    rm -rf "$CLAUDE_CONFIG_DIR/skills/voicemode"

    if [ -f "$CLAUDE_CONFIG_DIR/settings.json" ]; then
        python3 -c "
import json
p = '$CLAUDE_CONFIG_DIR/settings.json'
try:
    with open(p, 'r') as f:
        data = json.load(f)
    if 'Stop' in data.get('hooks', {}):
        del data['hooks']['Stop']
    with open(p, 'w') as f:
        json.dump(data, f, indent=2)
    print('Cleaned hooks from Claude settings.json')
except Exception as e:
    print('Error:', e)
"
    fi
    success "Claude Code integration uninstalled cleanly."
}

stop_and_remove_services() {
    log "Stopping and disabling systemd user services..."
    systemctl --user stop claude-voice-bridge.service whisper-server.service dictate.service 2>/dev/null || true
    systemctl --user disable claude-voice-bridge.service whisper-server.service dictate.service 2>/dev/null || true

    rm -f "$SYSTEMD_USER_DIR/claude-voice-bridge.service"
    rm -f "$SYSTEMD_USER_DIR/whisper-server.service"
    rm -f "$SYSTEMD_USER_DIR/dictate.service"

    systemctl --user daemon-reload
    success "Systemd user services removed."
}

remove_binaries() {
    log "Removing CLI launcher tools..."
    rm -f "$USER_BIN/voice-engine"
    rm -f "$USER_BIN/voicemode"
    rm -f "$USER_BIN/voicemode-stop"
    rm -f "$USER_BIN/voicemode-summary"
    success "CLI tools removed from $USER_BIN."
}

case "${1:-all}" in
    --agy|agy)
        uninstall_agy
        ;;
    --claude|claude)
        uninstall_claude
        ;;
    --services|services)
        stop_and_remove_services
        ;;
    --all|all)
        uninstall_agy
        uninstall_claude
        stop_and_remove_services
        remove_binaries
        success "Voice engine completely uninstalled."
        ;;
    -h|--help)
        echo "Usage: ./uninstall.sh [--agy | --claude | --services | --all]"
        exit 0
        ;;
    *)
        echo "Usage: ./uninstall.sh [--agy | --claude | --services | --all]"
        exit 1
        ;;
esac
