#!/usr/bin/env python3
"""
voicemode-speak-summary.py

Extracts the final summary from Claude Code or Antigravity (agy) transcripts
at the end of a turn/task (triggered by the Stop hook), synthesizes it via
local Kokoro TTS, and plays it with full barge-in / interruption support.

Interruption methods:
1. Push-to-talk button (dictate.py on KEY_RIGHTCTRL)
2. Control socket (~/.voicemode/control.sock)
3. PID / process signal (~/.voicemode/summary_player.pid)
4. CLI: voicemode control stop / voicemode-stop
"""

import sys
import os
import json
import re
import socket
import subprocess
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

KOKORO_TTS_URL = "http://127.0.0.1:8880/v1/audio/speech"
TTS_VOICE = os.environ.get("VOICEMODE_VOICE", "af_sky")
CONTROL_SOCK_PATH = Path.home() / ".voicemode" / "control.sock"
PID_FILE_PATH = Path.home() / ".voicemode" / "summary_player.pid"
SUMMARY_WAV_PATH = Path("/tmp/voicemode_summary.wav")
FALLBACK_MP3_PATH = Path.home() / ".voicemode" / "soundfonts" / "voicemode" / "fallback.mp3"
DISABLED_SENTINEL = Path.home() / ".voicemode" / "summary-disabled"
LOG_PATH = Path.home() / ".voicemode" / "logs" / "summary-speaker.log"

def log(msg: str):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")

def clean_for_speech(text: str) -> str:
    """Clean markdown and technical markup into natural speakable English."""
    # Remove code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)
    # Remove inline code
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Remove images and markdown links [text](url) -> text
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
    # Remove raw URLs
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'file://\S+', '', text)
    # Remove markdown headers
    text = re.sub(r'^\s*#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Remove bold/italic markers
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Remove bullet markers but keep text
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    # Remove blockquotes
    text = re.sub(r'^\s*>\s+', '', text, flags=re.MULTILINE)
    # Remove emoji / non-ascii decoration that TTS might stumble on
    text = re.sub(r'[\u25CF\u2713\u2714\u2716\u2717\u25B6\u25BC\u2728\uD83D\uD83E\uD83C][\uDC00-\uDFFF]?', '', text)
    # Collapse extra whitespace
    text = re.sub(r'\n{2,}', '\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

def pick_summary_text(raw_text: str) -> str:
    """Extract a concise summary of the outcome from the assistant's message."""
    cleaned = clean_for_speech(raw_text)
    if not cleaned:
        return ""

    # Check if there is an explicit Summary or Outcome section
    summary_match = re.search(r'(?:Summary|Outcome|Status|Done|Conclusion|What was done)[:\s]+([\s\S]+)', cleaned, re.IGNORECASE)
    if summary_match:
        section = summary_match.group(1).strip()
        paragraphs = [p.strip() for p in section.split('\n') if p.strip()]
        if paragraphs:
            summary_candidate = paragraphs[0]
            if len(summary_candidate) > 20:
                return summary_candidate[:400]

    # Otherwise take the first 1-2 paragraphs of the response
    paragraphs = [p.strip() for p in cleaned.split('\n') if p.strip()]
    if not paragraphs:
        return ""

    first_para = paragraphs[0]
    # If first para is very short and there is a second, combine
    if len(first_para) < 120 and len(paragraphs) > 1:
        combined = f"{first_para} {paragraphs[1]}"
        return combined[:400]
    return first_para[:400]

def extract_from_transcript(path_str: str) -> str:
    """Extract final message from a Claude Code (.jsonl) or AGY (.jsonl) transcript."""
    p = Path(path_str)
    if not p.is_file():
        return ""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except Exception as e:
        log(f"Error reading transcript {path_str}: {e}")
        return ""

    # Reverse scan
    for line in reversed(lines):
        try:
            rec = json.loads(line)
            # Claude Code format
            if rec.get("type") == "assistant":
                msg = rec.get("message", {})
                content = msg.get("content", [])
                texts = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        texts.append(b.get("text", ""))
                    elif isinstance(b, str):
                        texts.append(b)
                if texts:
                    return "\n".join(texts).strip()

            # AGY format
            if rec.get("type") == "PLANNER_RESPONSE" and not rec.get("tool_calls"):
                content = rec.get("content", "").strip()
                if content:
                    return content
        except Exception:
            continue
    return ""

def synthesize_kokoro(text: str) -> bool:
    """Call local Kokoro TTS to generate WAV audio."""
    payload = json.dumps({
        "input": text,
        "voice": TTS_VOICE,
        "model": "tts-1",
        "response_format": "wav"
    }).encode("utf-8")

    req = urllib.request.Request(
        KOKORO_TTS_URL,
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            SUMMARY_WAV_PATH.write_bytes(data)
            return True
    except Exception as exc:
        log(f"TTS synthesis failed: {exc}")
        return False

def play_audio_with_barge_in(wav_path: Path):
    """Play audio via pw-play while listening for control socket or stop commands."""
    stop_event = threading.Event()
    player_proc = None

    # Write our PID so dictate.py or stop helper can kill us
    try:
        PID_FILE_PATH.write_text(str(os.getpid()))
    except Exception:
        pass

    # Setup control socket server if not in use
    server_sock = None
    if not CONTROL_SOCK_PATH.exists():
        try:
            server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server_sock.bind(str(CONTROL_SOCK_PATH))
            server_sock.listen(2)
            server_sock.settimeout(0.5)

            def socket_listener():
                while not stop_event.is_set():
                    try:
                        conn, _ = server_sock.accept()
                        with conn:
                            data = conn.recv(1024).decode(errors="ignore")
                            if "stop" in data or "skip_forward" in data or "pause" in data:
                                log(f"Received stop/barge-in command via socket: {data.strip()}")
                                stop_event.set()
                                if player_proc and player_proc.poll() is None:
                                    player_proc.terminate()
                                break
                    except socket.timeout:
                        continue
                    except Exception:
                        break

            threading.Thread(target=socket_listener, daemon=True).start()
        except Exception as exc:
            log(f"Could not bind control socket: {exc}")
            server_sock = None

    try:
        # Start playback via pw-play or paplay
        player_cmd = ["pw-play", str(wav_path)]
        log(f"Starting playback: {' '.join(player_cmd)}")
        player_proc = subprocess.Popen(player_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        while player_proc.poll() is None:
            if stop_event.is_set():
                player_proc.terminate()
                try:
                    player_proc.wait(timeout=0.2)
                except Exception:
                    player_proc.kill()
                break
            time.sleep(0.05)
    except Exception as exc:
        log(f"Playback error: {exc}")
    finally:
        if server_sock:
            try:
                server_sock.close()
            except Exception:
                pass
            CONTROL_SOCK_PATH.unlink(missing_ok=True)
        PID_FILE_PATH.unlink(missing_ok=True)

def main():
    if DISABLED_SENTINEL.exists():
        log("Summary TTS disabled via sentinel (~/.voicemode/summary-disabled) - playing completion chime")
        if FALLBACK_MP3_PATH.exists():
            subprocess.run(["pw-play", str(FALLBACK_MP3_PATH)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    # Read stdin from Stop hook
    json_str = ""
    if not sys.stdin.isatty():
        try:
            json_str = sys.stdin.read()
        except Exception:
            pass

    log(f"Invoked with input length={len(json_str)}")
    data = {}
    if json_str:
        try:
            data = json.loads(json_str)
        except Exception:
            pass

    raw_text = ""

    # 1. Claude Code direct message field
    if "last_assistant_message" in data and data["last_assistant_message"]:
        raw_text = data["last_assistant_message"]

    # 2. Transcript path from hook
    if not raw_text:
        t_path = data.get("transcript_path") or data.get("transcriptPath")
        if t_path:
            raw_text = extract_from_transcript(t_path)

    # 3. Fallback: recent transcripts
    if not raw_text:
        # Check AGY current brain directory
        brain_transcripts = sorted(Path.home().glob(".gemini/antigravity-cli/brain/*/.system_generated/logs/transcript.jsonl"), key=os.path.getmtime, reverse=True)
        if brain_transcripts:
            raw_text = extract_from_transcript(str(brain_transcripts[0]))

    if not raw_text:
        log("No text extracted, falling back to chime")
        if FALLBACK_MP3_PATH.exists():
            subprocess.run(["pw-play", str(FALLBACK_MP3_PATH)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    summary_text = pick_summary_text(raw_text)
    if not summary_text:
        summary_text = clean_for_speech(raw_text)[:300]

    log(f"Extracted summary ({len(summary_text)} chars): {summary_text[:100]}...")

    if synthesize_kokoro(summary_text):
        play_audio_with_barge_in(SUMMARY_WAV_PATH)
    else:
        # Fallback chime if TTS unavailable
        if FALLBACK_MP3_PATH.exists():
            subprocess.run(["pw-play", str(FALLBACK_MP3_PATH)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if __name__ == "__main__":
    main()
