#!/bin/bash
systemctl --user start whisper-server.service
systemctl --user start claude-voice-bridge.service
echo "Started whisper-server and claude-voice-bridge services."
systemctl --user status claude-voice-bridge.service --no-pager
