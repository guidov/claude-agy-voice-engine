#!/usr/bin/env python3
"""
dictate — push-to-talk dictation backed by whisper.cpp on the Intel Arc B580.

Hold the push-to-talk key, speak, release. The audio is POSTed to a
whisper-server instance that keeps the model resident in GPU VRAM, and the
returned text is pasted at the cursor.

Reads the keyboard straight from /dev/input via evdev, so it works under
Wayland where X11-based global hotkeys do not.

    python dictate.py [--config PATH] [--list-keys] [--once FILE]
"""

from __future__ import annotations

import argparse
import glob
import io
import os
import queue
import selectors
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
import wave
from pathlib import Path

import evdev
import numpy as np
import requests
import sounddevice as sd

DEFAULT_CONFIG = Path(__file__).with_name("dictate.toml")

# ydotool speaks raw kernel keycodes; see linux/input-event-codes.h
KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_LEFTALT = 56
KEY_LEFTMETA = 125
KEY_V = 47
KEY_INSERT = 110

# Names accepted in output.paste_key. The modifiers are the left-hand ones
# because ydotool is synthesising them, not you: nothing distinguishes them
# to the application, and the right-hand keys are the ones a push-to-talk
# hotkey is likely to be sitting on.
PASTE_MODIFIERS = {
    "ctrl": KEY_LEFTCTRL,
    "shift": KEY_LEFTSHIFT,
    "alt": KEY_LEFTALT,
    "super": KEY_LEFTMETA,
    "meta": KEY_LEFTMETA,
}

# Final key of the chord. Shift+Insert is worth knowing about: VTE terminals
# accept it as paste just like Ctrl+Shift+V, and it is a two-key chord rather
# than a three-key one, so there is less synthetic modifier state in flight.
PASTE_KEYS = {
    "v": KEY_V,
    "insert": KEY_INSERT,
    "ins": KEY_INSERT,
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config(path: Path) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


# --------------------------------------------------------------------------
# audio
# --------------------------------------------------------------------------

class Recorder:
    """Captures mono audio at Whisper's native rate for as long as it's armed.

    The PortAudio stream stays open for the life of the process. Opening and
    closing it on every hold is what stalled the key loop for minutes when
    PipeWire reconfigured between two capture clients; here the callback only
    buffers while armed, so no audio is kept between holds.
    """

    def __init__(self, samplerate: int, max_duration: float):
        self.samplerate = samplerate
        self.max_frames = int(max_duration * samplerate)
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._armed = False
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            log(f"audio: {status}")
        with self._lock:
            if self._armed and self._frames < self.max_frames:
                self._chunks.append(indata.copy())
                self._frames += frames

    def _open_stream(self) -> None:
        if self._stream is not None:
            return
        # Let PortAudio resample from the device rate; the old implementation
        # captured at 48k and dropped every third sample, which aliases badly.
        self._stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="float32",
            blocksize=0,
            callback=self._callback,
        )
        self._stream.start()

    def warmup(self) -> None:
        """Open the microphone at startup so the first hold isn't delayed."""
        try:
            self._open_stream()
        except Exception as exc:
            log(f"microphone not open yet: {exc}")

    def start(self) -> None:
        with self._lock:
            self._chunks = []
            self._frames = 0
            self._armed = True
        try:
            self._open_stream()
        except Exception as exc:
            log(f"could not open microphone: {exc}")

    def stop(self) -> np.ndarray:
        with self._lock:
            self._armed = False
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._chunks)[:, 0]


def to_wav(audio: np.ndarray, samplerate: int) -> bytes:
    """Pack float32 samples into a 16-bit PCM WAV in memory."""
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


# --------------------------------------------------------------------------
# transcription
# --------------------------------------------------------------------------

class Transcriber:
    def __init__(self, cfg: dict):
        self.url = cfg["server"]["url"]
        self.timeout = cfg["server"].get("timeout", 120.0)
        tcfg = cfg.get("transcribe", {})
        self.language = tcfg.get("language", "")
        self.beam_size = int(tcfg.get("beam_size", 0))
        self.prompt = " ".join(tcfg.get("prompt", "").split())
        self.carry_prompt = bool(tcfg.get("carry_initial_prompt", False))
        self.session = requests.Session()

        # whisper truncates the initial prompt at 224 tokens. We cannot count
        # them without the model's tokenizer, so warn on a rough character
        # estimate rather than let it be silently cut.
        if len(self.prompt) > 850:
            log(f"initial prompt is {len(self.prompt)} chars (~{len(self.prompt)//4} "
                "tokens); whisper truncates past 224 and will drop the front")

    def __call__(self, wav: bytes) -> str:
        data = {
            "response_format": "json",
            "temperature": "0.0",
            "no_timestamps": "true",
        }
        if self.language:
            data["language"] = self.language
        if self.beam_size > 0:
            data["beam_size"] = str(self.beam_size)
        if self.prompt:
            data["prompt"] = self.prompt
            if self.carry_prompt:
                data["carry_initial_prompt"] = "true"
        resp = self.session.post(
            self.url,
            files={"file": ("audio.wav", wav, "audio/wav")},
            data=data,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json().get("text", "").strip()


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

# Clipboard tools, in preference order: name, write command, read command,
# and the environment variable the pair cannot work without.
#
# Which pair is invisible depends on the compositor. GNOME/Mutter advertises
# no data-control protocol -- neither zwlr_data_control_manager_v1 nor
# ext_data_control_manager_v1 -- so a client can neither set nor receive the
# selection without keyboard focus. wl-copy and wl-paste both get it the only
# way left to them: they open a real xdg_toplevel titled "wl-clipboard", take
# focus, do their business, and destroy the window. That is the whole-window
# flash, and note that it is *both* tools -- reading flashes exactly like
# writing, so verifying an xclip write through wl-paste puts the flash right
# back. The reader is therefore paired to the writer, never mixed.
#
# An X11 selection owner needs no focus at all; it owns an unmapped window.
# Mutter mirrors the X CLIPBOARD into the Wayland selection, so on a session
# with Xwayland the xclip pair does the whole job with nothing on screen.
CLIPBOARD_TOOLS = (
    ("xclip",
     ["xclip", "-selection", "clipboard"],
     ["xclip", "-selection", "clipboard", "-o"],
     "DISPLAY"),
    ("wl-copy",
     ["wl-copy"],
     ["wl-paste", "--no-newline"],
     None),
)


class Clipboard:
    """The selection, via whichever tool pair this session can use unseen."""

    def __init__(self, preference: str = "auto"):
        self.tools = [
            (name, write, read)
            for name, write, read, needs in CLIPBOARD_TOOLS
            if preference in ("auto", name)
            and shutil.which(write[0]) and shutil.which(read[0])
            and (needs is None or os.environ.get(needs))
        ]
        if not self.tools:
            log(f"no clipboard tool available (clipboard_tool={preference!r}); "
                "paste mode will fall back to typing")
        else:
            log(f"clipboard via {self.tools[0][0]}")

    def _read(self, timeout: float) -> bytes | None:
        if not self.tools:
            return None
        try:
            return subprocess.run(
                self.tools[0][2], capture_output=True, timeout=timeout
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None

    def get(self) -> bytes | None:
        return self._read(2.0)

    def _settled(self, expected: bytes, read: list[str], deadline: float) -> bool:
        """Wait until the tool that just wrote actually serves the new content.

        wl-copy in particular returns as soon as it owns the selection, which
        can be before the compositor will hand that content to anyone asking
        for it; pasting inside that window delivers the previous dictation.
        """
        while True:
            try:
                got = subprocess.run(
                    read, capture_output=True, timeout=1
                ).stdout
            except (OSError, subprocess.SubprocessError):
                got = None
            if got == expected:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def set(self, data: bytes, verify: bool = True, timeout: float = 1.0) -> bool:
        for index, (name, write, read) in enumerate(list(self.tools)):
            try:
                subprocess.run(write, input=data, timeout=2, check=True)
            except (OSError, subprocess.SubprocessError) as exc:
                log(f"{name} failed: {exc}")
                continue
            if not verify or self._settled(data, read, time.monotonic() + timeout):
                if index:
                    # Whatever works here is what this session supports; stop
                    # paying for the failed one on every later dictation.
                    log(f"clipboard: switching to {name}")
                    self.tools.insert(0, self.tools.pop(index))
                return True
            log(f"clipboard: {name} never served the new text")
        return False


class Typist:
    """Delivers transcribed text to whatever window has focus.

    Default is "type": ydotool synthesises the keystrokes, which lands in
    terminals and browsers alike. "paste" is faster for long text but Ctrl+V
    does not paste in most terminals -- they bind Ctrl+Shift+V, and zsh reads
    Ctrl+V as quoted-insert -- so it is not the default.
    """

    def __init__(self, cfg: dict):
        ocfg = cfg.get("output", {})
        self.mode = ocfg.get("mode", "type")
        self.key_delay = int(ocfg.get("key_delay", 0))
        self.key_hold = int(ocfg.get("key_hold", 0))
        self.restore = ocfg.get("restore_clipboard", True)
        self.settle = int(ocfg.get("paste_settle_ms", 20)) / 1000.0
        self.paste_key = ocfg.get("paste_key", "ctrl+v").lower().strip()
        parts = [part.strip() for part in self.paste_key.split("+")]
        try:
            self.paste_mods = [PASTE_MODIFIERS[m] for m in parts[:-1]]
            self.paste_code = PASTE_KEYS[parts[-1]]
        except (KeyError, IndexError):
            log(f"unknown paste_key {self.paste_key!r} (modifiers: "
                f"{'/'.join(PASTE_MODIFIERS)}; keys: {'/'.join(PASTE_KEYS)}), "
                "using ctrl+v")
            self.paste_mods, self.paste_code = [KEY_LEFTCTRL], KEY_V
        self.env = dict(os.environ)
        self.socket = self.env.setdefault(
            "YDOTOOL_SOCKET", f"/run/user/{os.getuid()}/.ydotool_socket"
        )
        if self.mode in ("type", "paste") and not os.path.exists(self.socket):
            log(f"ydotool socket missing: {self.socket} -- "
                "text cannot be delivered. Is ydotoold running?")
        self.clipboard = (
            Clipboard(ocfg.get("clipboard_tool", "auto"))
            if self.mode in ("paste", "clipboard") else None
        )

    def _ydotool(self, args: list[str]) -> bool:
        try:
            result = subprocess.run(
                ["ydotool", *args], env=self.env, timeout=30, capture_output=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log(f"ydotool failed: {exc}")
            return False
        if result.returncode != 0:
            log(f"ydotool exited {result.returncode}: "
                f"{result.stderr.decode(errors='replace').strip()}")
            return False
        return True

    def _type(self, text: str) -> None:
        self._ydotool([
            "type",
            "--key-delay", str(self.key_delay),
            "--key-hold", str(self.key_hold),
            text,
        ])

    def __call__(self, text: str) -> None:
        if self.mode == "type":
            # No clipboard involved, so nothing of yours gets clobbered.
            self._type(text)
            return

        previous = self.clipboard.get() if self.restore else None
        if not self.clipboard.set(text.encode()):
            # A hung compositor clipboard must not turn dictation into
            # silence; typing is slower but always lands.
            log("clipboard unavailable, typing instead")
            self._type(text)
            return
        if self.mode == "clipboard":
            return

        # The xclip pair verifies the X selection, which is all it can see
        # without opening a window; mutter mirroring that into the Wayland
        # selection is the one link left unverified. It is event-driven and
        # ordered ahead of the keystroke -- spawning ydotool alone costs
        # ~27 ms -- but this is the dial to turn if a paste ever delivers the
        # previous dictation.
        if self.settle:
            time.sleep(self.settle)

        press = [f"{code}:1" for code in self.paste_mods]
        release = [f"{code}:0" for code in reversed(self.paste_mods)]
        if not self._ydotool(
            ["key", *press, f"{self.paste_code}:1", f"{self.paste_code}:0", *release]
        ):
            log("paste keystroke failed, typing instead")
            self._type(text)
            return

        # Empty is not restorable: both writers reject empty input, and there
        # was nothing worth putting back anyway.
        if previous:
            # Give the target app a moment to read the selection back.
            time.sleep(0.25)
            self.clipboard.set(previous, verify=False)


# Ordered by preference. aplay is deliberately absent: it cannot decode the
# .oga files these cues live in.
SOUND_PLAYERS = (
    ("pw-play", lambda path: ["pw-play", path]),
    ("paplay", lambda path: ["paplay", path]),
    ("canberra-gtk-play", lambda path: ["canberra-gtk-play", "-f", path]),
)


# Which cues `feedback.sound` asks for. It accepts a bool for all or nothing,
# or any subset of these by name -- the "done" blip in particular is redundant
# once the text lands in front of you, and it is the one that gets mistaken
# for a terminal bell.
SOUND_EVENTS = ("start", "done", "error")


def sound_events(value) -> set[str]:
    """Read `feedback.sound`: a bool, one event name, or a list of them."""
    if isinstance(value, bool):
        return set(SOUND_EVENTS) if value else set()
    names = [value] if isinstance(value, str) else list(value)
    wanted = {str(name).strip().lower() for name in names}
    unknown = wanted - set(SOUND_EVENTS)
    if unknown:
        log(f"unknown feedback.sound entries {sorted(unknown)}; "
            f"valid names are {', '.join(SOUND_EVENTS)}")
    return wanted & set(SOUND_EVENTS)


class Feedback:
    """Optional audible/visual cues. Never fatal: dictation matters, cues do not."""

    def __init__(self, cfg: dict):
        fcfg = cfg.get("feedback", {})
        self.notify = bool(fcfg.get("notify", False)) and bool(shutil.which("notify-send"))
        self.sounds = sound_events(fcfg.get("sound", True))

        self.build_cmd = None
        if self.sounds:
            for name, build in SOUND_PLAYERS:
                if shutil.which(name):
                    self.build_cmd = build
                    log(f"sound cues via {name} on {', '.join(sorted(self.sounds))}")
                    break
            else:
                log("no audio player found "
                    f"({', '.join(n for n, _ in SOUND_PLAYERS)}); sound cues disabled")

    def _spawn(self, cmd: list[str]) -> None:
        try:
            subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log(f"feedback command {cmd[0]!r} failed: {exc}")

    def _play(self, event: str, name: str) -> None:
        if self.build_cmd is None or event not in self.sounds:
            return
        path = f"/usr/share/sounds/freedesktop/stereo/{name}.oga"
        if os.path.exists(path):
            self._spawn(self.build_cmd(path))

    def _notify(self, body: str) -> None:
        if not self.notify:
            return
        self._spawn(["notify-send", "-t", "1500", "-a", "dictate", "dictate", body])

    def recording(self) -> None:
        self._play("start", "dialog-information")
        self._notify("Recording…")

    def done(self, text: str) -> None:
        self._play("done", "message")
        self._notify(text[:80] if text else "(nothing heard)")

    def error(self) -> None:
        self._play("error", "dialog-error")
        self._notify("Transcription failed")

# --------------------------------------------------------------------------
# systemd watchdog
# --------------------------------------------------------------------------

def sd_notify(message: str) -> None:
    """Best-effort notification to the service manager; never fatal."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(message.encode())
    except OSError:
        pass


def start_watchdog() -> None:
    """Ping systemd every WatchdogSec/3 so a wedged loop is restarted."""
    usec = int(os.environ.get("WATCHDOG_USEC", "0") or "0")
    if usec <= 0:
        return
    interval = usec / 1e6 / 3

    def _ping() -> None:
        while True:
            sd_notify("WATCHDOG=1")
            time.sleep(interval)

    threading.Thread(target=_ping, daemon=True).start()


# --------------------------------------------------------------------------
# hotkey
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# interruption / barge-in
# --------------------------------------------------------------------------

def interrupt_playback() -> None:
    """Interrupt VoiceMode speech and summary TTS playback immediately."""
    # 1. VoiceMode control socket
    sock_path = Path.home() / ".voicemode" / "control.sock"
    if sock_path.exists():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.08)
                s.connect(str(sock_path))
                s.sendall(b'{"command": "stop"}\n')
        except Exception:
            pass

    # 2. Kill summary player PID if active
    pid_path = Path.home() / ".voicemode" / "summary_player.pid"
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, 15)
        except Exception:
            pass
        try:
            pid_path.unlink(missing_ok=True)
        except Exception:
            pass

    # 3. Safeguard: kill pw-play playing summary audio
    try:
        subprocess.run(["pkill", "-f", "voicemode_summary"], timeout=0.1)
    except Exception:
        pass


def device_paths() -> list[str]:
    """All readable event devices.

    Not evdev.list_devices(): that calls is_device() with writable=True, so it
    hides anything we cannot also write to. We only ever read, and the udev
    rule grants read-only on purpose -- write access to an event node would
    let us inject events, which this program has no business doing.
    """
    return sorted(
        path for path in glob.glob("/dev/input/event*")
        if evdev.util.is_device(path, writable=False)
    )


def keyboards(keycode: int) -> list[evdev.InputDevice]:
    """Every readable input device that can emit our push-to-talk key."""
    found = []
    for path in device_paths():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        # Never listen to our own paste injection -- that is a feedback loop
        # waiting for a hotkey that overlaps with what ydotool sends.
        if "ydotoold" in dev.name:
            dev.close()
            continue
        keys = dev.capabilities().get(evdev.ecodes.EV_KEY, [])
        if keycode in keys:
            found.append(dev)
        else:
            dev.close()
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--list-keys", action="store_true",
                    help="print readable keyboards and exit")
    ap.add_argument("--once", metavar="WAV",
                    help="transcribe a WAV file and print it, then exit")
    args = ap.parse_args()

    cfg = load_config(args.config)
    transcribe = Transcriber(cfg)

    if args.once:
        print(transcribe(Path(args.once).read_bytes()))
        return 0

    key_name = cfg["hotkey"]["key"]
    keycode = evdev.ecodes.ecodes[key_name]

    if args.list_keys:
        for dev in keyboards(keycode):
            print(f"{dev.path}  {dev.name}")
        return 0

    devices = keyboards(keycode)
    if not devices:
        log("No readable keyboard exposes "
            f"{key_name}. Is 70-dictate-input.rules installed and have you "
            "logged out and back in? Check `groups` includes 'input'.")
        return 1

    recorder = Recorder(cfg["audio"]["samplerate"], cfg["audio"]["max_duration"])
    typist = Typist(cfg)
    feedback = Feedback(cfg)
    min_duration = cfg["hotkey"]["min_duration"]
    suffix = cfg.get("transcribe", {}).get("suffix", " ")

    sel = selectors.DefaultSelector()
    for dev in devices:
        sel.register(dev, selectors.EVENT_READ)
        log(f"listening on {dev.path} ({dev.name})")

    log(f"hold {key_name} to dictate — Ctrl-C to quit")

    start_watchdog()
    sd_notify("READY=1")
    threading.Thread(target=recorder.warmup, daemon=True).start()

    # Key events only enqueue; every audio and subprocess call happens off
    # the selector loop, so nothing can deafen the listener again.
    events: queue.Queue[tuple[str, float]] = queue.Queue()

    held = False
    started_at = 0.0

    def finish(audio: np.ndarray, duration: float) -> None:
        """Runs off the event loop so a slow transcription can't drop keys."""
        try:
            text = transcribe(to_wav(audio, recorder.samplerate))
        except Exception as exc:
            log(f"transcription failed: {exc}")
            feedback.error()
            return
        if not text:
            log(f"({duration:.1f}s, nothing heard)")
            feedback.done("")
            return
        log(f"({duration:.1f}s) {text}")
        feedback.done(text)
        try:
            typist(text + suffix)
        except Exception as exc:
            log(f"could not deliver text: {exc!r}")

    def audio_worker() -> None:
        while True:
            kind, duration = events.get()
            try:
                if kind == "press":
                    interrupt_playback()
                    recorder.start()
                    feedback.recording()
                else:
                    audio = recorder.stop()
                    if duration < min_duration or audio.size == 0:
                        log(f"too short ({duration:.2f}s), ignored")
                        continue
                    threading.Thread(
                        target=finish, args=(audio, duration), daemon=True
                    ).start()
            except Exception as exc:
                # A dictation that goes wrong must not take the worker with
                # it; the next keypress should still work.
                log(f"error handling keypress: {exc!r}")
                try:
                    recorder.stop()
                except Exception:
                    pass

    threading.Thread(target=audio_worker, daemon=True).start()

    try:
        while True:
            for skey, _ in sel.select():
                for event in skey.fileobj.read():
                    if event.type != evdev.ecodes.EV_KEY or event.code != keycode:
                        continue
                    # value: 1 = press, 0 = release, 2 = autorepeat
                    if event.value == 1 and not held:
                        held = True
                        started_at = time.monotonic()
                        threading.Thread(target=interrupt_playback, daemon=True).start()
                        events.put(("press", 0.0))
                    elif event.value == 0 and held:
                        held = False
                        events.put(("release", time.monotonic() - started_at))
    except KeyboardInterrupt:
        log("bye")
    finally:
        for dev in devices:
            dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
