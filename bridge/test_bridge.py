import asyncio
import io
import time
import httpx

async def test_bridge():
    print("Testing Bridge Endpoints...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Health checks
        print("\n[1] Testing Health Checks:")
        r_2022 = await client.get("http://127.0.0.1:2022/health")
        print(f"Port 2022 /health: status={r_2022.status_code}, response={r_2022.json()}")
        r_8880 = await client.get("http://127.0.0.1:8880/health")
        print(f"Port 8880 /health: status={r_8880.status_code}, response={r_8880.json()}")

        # 2. Test TTS on port 8880 (Kokoro)
        print("\n[2] Testing TTS (speech synthesis) on port 8880:")
        t0 = time.perf_counter()
        r_tts = await client.post(
            "http://127.0.0.1:8880/v1/audio/speech",
            json={
                "model": "tts-1",
                "input": "Claude STT and TTS engine is now running locally on CPU and Intel Arc GPU.",
                "voice": "af_sky",
                "response_format": "pcm"
            }
        )
        t_tts = time.perf_counter() - t0
        print(f"Port 8880 /v1/audio/speech (PCM): status={r_tts.status_code}, bytes={len(r_tts.content)}, latency={t_tts:.2f}s")
        assert r_tts.status_code == 200 and len(r_tts.content) > 1000

        # Also test WAV format
        r_tts_wav = await client.post(
            "http://127.0.0.1:8880/v1/audio/speech",
            json={
                "model": "tts-1",
                "input": "Testing WAV output.",
                "voice": "af_sky",
                "response_format": "wav"
            }
        )
        print(f"Port 8880 /v1/audio/speech (WAV): status={r_tts_wav.status_code}, bytes={len(r_tts_wav.content)}")
        assert r_tts_wav.status_code == 200

        # 3. Test STT on port 2022 (Whisper forwarding to Intel Arc B580)
        print("\n[3] Testing STT (Whisper on Intel Arc GPU) on port 2022:")
        wav_data = r_tts_wav.content
        t0 = time.perf_counter()
        r_stt = await client.post(
            "http://127.0.0.1:2022/v1/audio/transcriptions",
            files={"file": ("speech.wav", wav_data, "audio/wav")},
            data={"response_format": "json"}
        )
        t_stt = time.perf_counter() - t0
        print(f"Port 2022 /v1/audio/transcriptions: status={r_stt.status_code}, latency={t_stt:.2f}s, response={r_stt.json()}")
        assert r_stt.status_code == 200
        assert "testing" in r_stt.json().get("text", "").lower()

    print("\n ALL BRIDGE TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(test_bridge())
