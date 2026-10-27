---
name: talk
description: >-
  Switch to interactive voice conversation mode. Use when the user types /talk,
  or asks to talk, chat, discuss, or enter conversation mode. In this mode, the agent
  converses verbally using VoiceMode (Whisper STT on Intel Arc GPU and Kokoro TTS)
  without executing coding tools until the user asks to switch back to coding.
---

# Interactive Voice Conversation Mode (/talk)

When this skill is triggered:
1. Greet the user verbally using the `converse` tool (e.g., `converse(message="I'm listening. What would you like to discuss?")`).
2. Maintain a fluid, conversational verbal dialogue:
   - Local Whisper STT (Intel Arc B580 GPU) transcribes the user's speech.
   - Local Kokoro TTS speaks your responses.
   - Keep answers conversational, clear, and direct.
   - Do not execute heavy coding tools or disrupt the voice flow while talking.
3. Seamless Transition to Coding:
   - When the user says "let's code", "write the code", "switch to coding", or signals ready:
   - Confirm verbally with `wait_for_response=false` (e.g., `converse(message="Got it! Switching to coding mode now.", wait_for_response=false)`).
   - Immediately transition to hands-on coding using the full context gathered during the discussion.
