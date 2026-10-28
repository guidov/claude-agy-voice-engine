"""
Claude STT/TTS Bridge for VoiceMode and Jeannie.

Provides OpenAI-compatible endpoints:
  - STT: /v1/audio/transcriptions (forwards to whisper.cpp on Intel Arc GPU/XPU :8910)
  - TTS: /v1/audio/speech (synthesizes using Kokoro-ONNX on CPU/XPU)
  - Health checks: /health, /v1/health

Listens on:
  - Port 2022 (default VoiceMode Whisper STT port)
  - Port 8880 (default VoiceMode Kokoro TTS port)
Both ports serve both STT and TTS for maximum compatibility.
"""

import asyncio
import io
import logging
import wave
from typing import AsyncGenerator, Optional

import httpx
import kokoro_onnx
import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel

import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("claude-voice-bridge")

WHISPER_URL = os.environ.get("WHISPER_URL", "http://127.0.0.1:8910/inference")
KOKORO_MODEL_PATH = os.environ.get("KOKORO_MODEL_PATH", "/home/guido/jeannie/models/kokoro-v1.0.onnx")
KOKORO_VOICES_PATH = os.environ.get("KOKORO_VOICES_PATH", "/home/guido/jeannie/models/voices-v1.0.bin")
DEFAULT_TTS_SPEED = float(os.environ.get("KOKORO_TTS_SPEED", os.environ.get("VOICEMODE_TTS_SPEED", "1.2")))

# Voice alias mapping for common OpenAI voice names
OPENAI_VOICE_MAP = {
    "alloy": "af_alloy",
    "echo": "am_echo",
    "fable": "bm_fable",
    "onyx": "am_onyx",
    "nova": "af_nova",
    "shimmer": "af_bella",
}

# Prefix to language mapping for Kokoro
LANG_MAP = {
    "af": "en-us",
    "am": "en-us",
    "bf": "en-gb",
    "bm": "en-gb",
    "ef": "es",
    "em": "es",
    "ff": "fr-fr",
    "hf": "hi",
    "hm": "hi",
    "if": "it",
    "im": "it",
    "jf": "ja",
    "jm": "ja",
    "pf": "pt-br",
    "pm": "pt-br",
    "zf": "zh",
    "zm": "zh",
}


def get_lang_for_voice(voice: str) -> str:
    prefix = voice[:2].lower()
    return LANG_MAP.get(prefix, "en-us")


# Initialize Kokoro
logger.info(f"Loading Kokoro model from {KOKORO_MODEL_PATH}...")
kokoro = kokoro_onnx.Kokoro(KOKORO_MODEL_PATH, KOKORO_VOICES_PATH)
AVAILABLE_VOICES = set(kokoro.get_voices())
logger.info(f"Kokoro loaded with {len(AVAILABLE_VOICES)} voices.")

app = FastAPI(title="Claude Voice Bridge (Whisper XPU + Kokoro TTS)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
@app.get("/v1/health")
async def health():
    """Health check endpoint expected by VoiceMode."""
    # Check if whisper backend is responding
    whisper_ok = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://127.0.0.1:8910/")
            whisper_ok = resp.status_code == 200
    except Exception:
        whisper_ok = False

    return {
        "status": "ok",
        "whisper_xpu_available": whisper_ok,
        "whisper_backend_url": WHISPER_URL,
        "kokoro_voices_count": len(AVAILABLE_VOICES),
        "default_voice": "af_sky",
    }


@app.get("/v1/models")
async def list_models():
    """OpenAI-compatible models list."""
    return {
        "object": "list",
        "data": [
            {"id": "whisper-1", "object": "model", "owned_by": "openai"},
            {"id": "tts-1", "object": "model", "owned_by": "openai"},
            {"id": "tts-1-hd", "object": "model", "owned_by": "openai"},
            {"id": "kokoro", "object": "model", "owned_by": "hexgrad"},
        ],
    }


@app.get("/v1/audio/voices")
async def list_voices():
    """List available voices for TTS."""
    return {
        "voices": sorted(list(AVAILABLE_VOICES)),
        "aliases": OPENAI_VOICE_MAP,
    }


# ---------------------------------------------------------------------------
# STT (Speech-to-Text) Forwarding to whisper-server on Intel Arc GPU
# ---------------------------------------------------------------------------
@app.post("/v1/audio/transcriptions")
@app.post("/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    response_format: Optional[str] = Form("json"),
    temperature: Optional[float] = Form(None),
    prompt: Optional[str] = Form(None),
):
    """
    OpenAI-compatible STT endpoint.
    Forwards incoming audio file to local whisper.cpp server running on Intel Arc GPU.
    """
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file provided.")

    data = {"response_format": "json"}
    if language and language != "auto":
        data["language"] = language
    if temperature is not None:
        data["temperature"] = str(temperature)

    files = {"file": (file.filename or "chunk.wav", audio_bytes, file.content_type or "audio/wav")}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(WHISPER_URL, files=files, data=data)
            if resp.status_code != 200:
                logger.error(f"Whisper server error ({resp.status_code}): {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=f"Whisper backend error: {resp.text}")

            result = resp.json()
            text = result.get("text", "").strip()
            logger.info(f"Transcribed ({len(audio_bytes)} bytes): {text[:80]}...")

            if response_format == "text":
                return PlainTextResponse(text)
            elif response_format == "verbose_json":
                return JSONResponse(result)
            else:
                return JSONResponse({"text": text})

    except httpx.ConnectError:
        logger.error(f"Cannot connect to Whisper server at {WHISPER_URL}. Is whisper-server.service running?")
        raise HTTPException(
            status_code=503,
            detail=f"Whisper server at {WHISPER_URL} is offline. Please ensure whisper-server.service is running.",
        )
    except Exception as e:
        logger.error(f"Error during transcription: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# TTS (Text-to-Speech) using Kokoro ONNX
# ---------------------------------------------------------------------------
class SpeechRequest(BaseModel):
    model: Optional[str] = "tts-1"
    input: str
    voice: Optional[str] = "af_sky"
    response_format: Optional[str] = "pcm"
    speed: Optional[float] = None


@app.post("/v1/audio/speech")
@app.post("/audio/speech")
async def speech(req: SpeechRequest):
    """
    OpenAI-compatible TTS endpoint.
    Synthesizes speech from text using Kokoro ONNX.
    Supports streaming PCM for VoiceMode, or single-batch WAV/MP3.
    """
    text = req.input.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text cannot be empty.")

    voice = req.voice or "af_sky"
    # Resolve OpenAI voice aliases if provided
    if voice in OPENAI_VOICE_MAP:
        voice = OPENAI_VOICE_MAP[voice]
    if voice not in AVAILABLE_VOICES:
        logger.warning(f"Voice '{voice}' not found in Kokoro. Falling back to 'af_sky'.")
        voice = "af_sky"

    speed = float(req.speed) if req.speed is not None else DEFAULT_TTS_SPEED
    lang = get_lang_for_voice(voice)
    fmt = (req.response_format or "pcm").lower()

    logger.info(f"TTS requested: voice={voice}, lang={lang}, speed={speed}, fmt={fmt}, text='{text[:60]}...'")

    if fmt == "pcm":
        # Stream raw 16-bit PCM (s16le) mono 24000Hz chunks for VoiceMode
        async def pcm_streamer() -> AsyncGenerator[bytes, None]:
            try:
                async for samples, _ in kokoro.create_stream(text, voice=voice, speed=speed, lang=lang):
                    # Convert float32 [-1.0, 1.0] to int16 bytes
                    int16_samples = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
                    yield int16_samples.tobytes()
            except Exception as exc:
                logger.error(f"Error in Kokoro PCM streaming: {exc}")
                raise

        return StreamingResponse(pcm_streamer(), media_type="audio/pcm")

    # For non-streaming formats (wav, mp3), generate full audio off the event loop
    try:
        samples, sr = await asyncio.to_thread(kokoro.create, text, voice=voice, speed=speed, lang=lang)
    except Exception as exc:
        logger.error(f"Error generating speech with Kokoro: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    if fmt == "wav":
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            int16_samples = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
            wf.writeframes(int16_samples.tobytes())
        return Response(content=buf.getvalue(), media_type="audio/wav")

    elif fmt == "mp3":
        buf = io.BytesIO()
        sf.write(buf, samples, sr, format="MP3")
        return Response(content=buf.getvalue(), media_type="audio/mpeg")

    else:
        # Default fallback to wav
        buf = io.BytesIO()
        sf.write(buf, samples, sr, format="WAV")
        return Response(content=buf.getvalue(), media_type="audio/wav")


# ---------------------------------------------------------------------------
# Runner listening on both ports 2022 and 8880
# ---------------------------------------------------------------------------
async def run_servers():
    config_2022 = uvicorn.Config(app, host="127.0.0.1", port=2022, log_level="warning")
    config_8880 = uvicorn.Config(app, host="127.0.0.1", port=8880, log_level="warning")
    server_2022 = uvicorn.Server(config_2022)
    server_8880 = uvicorn.Server(config_8880)

    logger.info("Serving on http://127.0.0.1:2022 (Whisper STT port) and http://127.0.0.1:8880 (Kokoro TTS port)")
    await asyncio.gather(server_2022.serve(), server_8880.serve())


if __name__ == "__main__":
    asyncio.run(run_servers())
