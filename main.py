import wave
import time
import winsound
import logging
import os
import subprocess
import numpy as np
import pyaudio
import pythoncom
import pyttsx3

from openwakeword.model import Model
from faster_whisper import WhisperModel
import config
import brain
import actions

# --- Logging setup ---
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis.log")
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)


def log(msg):
    """Print if a console exists, always write to the log file."""
    try:
        print(msg)
    except Exception:
        pass
    logging.info(msg)


log("Loading Whisper model...")
whisper_model = WhisperModel(config.WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")

log("Loading Piper voice...")
piper_voice = None
try:
    from piper import PiperVoice
    piper_model_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), config.PIPER_VOICE + ".onnx"
    )
    if os.path.exists(piper_model_path):
        piper_voice = PiperVoice.load(piper_model_path)
    else:
        log(f"[TTS] Piper model not found at {piper_model_path}, will use fallback voice")
except Exception as e:
    log(f"[TTS] Could not load Piper ({e}), will use fallback voice")

log("Loading wake word detector...")
oww_model = Model(wakeword_models=[config.WAKE_WORD_MODEL])

CHUNK = 1280
RATE = 16000

pa = pyaudio.PyAudio()
mic_stream = pa.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                      input=True, frames_per_buffer=CHUNK)


def get_volume(chunk_data):
    """RMS volume of an audio chunk."""
    audio_np = np.frombuffer(chunk_data, dtype=np.int16).astype(np.float64)
    if len(audio_np) == 0:
        return 0
    return np.sqrt(np.mean(audio_np ** 2))


def record_command(max_seconds=6, silence_threshold=500, silence_duration=0.6, min_speech_seconds=0.4):
    """Record until the user stops talking."""
    winsound.Beep(1000, 150)
    time.sleep(0.25)
    log("Listening for your command...")

    for _ in range(10):
        mic_stream.read(CHUNK, exception_on_overflow=False)

    frames = []
    chunk_duration = CHUNK / RATE
    silence_chunks_needed = int(silence_duration / chunk_duration)
    min_speech_chunks = int(min_speech_seconds / chunk_duration)
    max_chunks = int(max_seconds / chunk_duration)

    silent_chunk_count = 0
    speech_started = False
    total_chunks = 0

    while total_chunks < max_chunks:
        chunk_data = mic_stream.read(CHUNK, exception_on_overflow=False)
        frames.append(chunk_data)
        total_chunks += 1

        volume = get_volume(chunk_data)

        if volume > silence_threshold:
            speech_started = True
            silent_chunk_count = 0
        else:
            silent_chunk_count += 1

        if speech_started and total_chunks > min_speech_chunks and silent_chunk_count >= silence_chunks_needed:
            break

    filename = "temp_command.wav"
    wf = wave.open(filename, "wb")
    wf.setnchannels(1)
    wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
    wf.setframerate(RATE)
    wf.writeframes(b"".join(frames))
    wf.close()
    return filename


def transcribe(filename):
    segments, _ = whisper_model.transcribe(
        filename,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )
    text = " ".join([seg.text for seg in segments]).strip()
    return text


def speak_pyttsx3_fallback(text):
    """Fallback robotic voice."""
    pythoncom.CoInitialize()
    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", 175)
        engine.setProperty("volume", 1.0)
        voices = engine.getProperty("voices")
        if voices:
            engine.setProperty("voice", voices[0].id)
        engine.say(text)
        engine.runAndWait()
        try:
            engine.stop()
        except Exception:
            pass
        del engine
    except Exception as e:
        log(f"[TTS FALLBACK ERROR] {e}")
    finally:
        pythoncom.CoUninitialize()


def speak(text):
    log(f"Jarvis says: {text}")
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_output.wav")
    try:
        if piper_voice is not None:
            with wave.open(output_path, "wb") as wav_file:
                piper_voice.synthesize_wav(text, wav_file)
            winsound.PlaySound(output_path, winsound.SND_FILENAME)
        else:
            speak_pyttsx3_fallback(text)
    except Exception as e:
        log(f"[TTS ERROR] Piper failed ({e}), using fallback voice")
        speak_pyttsx3_fallback(text)


# Destructive action confirmation
pending_confirmation = None

CONFIRMATION_PROMPTS = {
    "shutdown_pc": "Are you sure you want to shut down the computer? Say yes to confirm.",
    "restart_pc": "Are you sure you want to restart the computer? Say yes to confirm.",
}

YES_WORDS = {"yes", "yeah", "yep", "confirm", "do it", "sure", "go ahead"}
NO_WORDS = {"no", "nope", "cancel", "nevermind", "never mind", "stop"}

# Last action memory
last_executed_intent = None
REPEAT_PHRASES = {"do that again", "do it again", "repeat that", "repeat", "again", "same again"}


def handle_command(text: str):
    """Parse intent, execute the action or chat, then speak the result."""
    global pending_confirmation, last_executed_intent

    if not text:
        speak("I didn't catch that.")
        return

    text_clean = text.lower().strip().strip(".,!?")

    # Handle yes/no confirmation for shutdown/restart
    if pending_confirmation is not None:
        if text_clean in YES_WORDS:
            action = pending_confirmation.get("action")
            target = pending_confirmation.get("target", "")
            pending_confirmation = None
            result_message = actions.execute_action(action, target)
            speak(result_message)
            return
        elif text_clean in NO_WORDS:
            pending_confirmation = None
            speak("Cancelled")
            return
        else:
            pending_confirmation = None

    # "Do that again"
    if text_clean in REPEAT_PHRASES:
        if last_executed_intent is None:
            speak("I haven't done anything yet to repeat")
            return
        action = last_executed_intent["action"]
        target = last_executed_intent["target"]
        result_message = actions.execute_action(action, target)
        speak(result_message)
        return

    # Parse the intent
    intent_start = time.time()
    intent = brain.parse_intent(text)
    log(f"[TIMING] Brain (intent parsing) took {time.time() - intent_start:.2f}s")
    log(f"Intent: {intent}")

    action = intent.get("action", "unknown")
    target = intent.get("target", "")

    # ========== FRIENDLY CHAT MODE ==========
    if action in ("chat", "unknown"):
        reply = brain.chat_with_ai(text)
        speak(reply)
        return

    # Destructive actions need confirmation
    if action in actions.DESTRUCTIVE_ACTIONS:
        pending_confirmation = {"action": action, "target": target}
        speak(CONFIRMATION_PROMPTS.get(action, "Are you sure? Say yes to confirm."))
        return

    # Normal actions
    action_start = time.time()
    result_message = actions.execute_action(action, target)
    log(f"[TIMING] Action execution took {time.time() - action_start:.2f}s")

    if action != "unknown":
        last_executed_intent = {"action": action, "target": target}

    speak_start = time.time()
    speak(result_message)
    log(f"[TIMING] Speaking took {time.time() - speak_start:.2f}s")


def flush_mic_buffer(chunks=25, settle_time=0.4):
    """Discard buffered audio after speaking."""
    time.sleep(settle_time)
    for _ in range(chunks):
        try:
            mic_stream.read(CHUNK, exception_on_overflow=False)
        except Exception:
            pass


def main():
    speak("Jarvis online")
    flush_mic_buffer()
    log("Say 'Hey Jarvis' to activate...")

    consecutive_hits = 0
    REQUIRED_HITS = 4
    THRESHOLD = 0.85
    WAKE_COOLDOWN_SECONDS = 1.5
    suppress_until = time.time() + WAKE_COOLDOWN_SECONDS

    try:
        while True:
            audio_chunk = mic_stream.read(CHUNK, exception_on_overflow=False)

            if time.time() < suppress_until:
                consecutive_hits = 0
                continue

            audio_np = np.frombuffer(audio_chunk, dtype=np.int16)
            prediction = oww_model.predict(audio_np)

            triggered = False
            triggered_score = 0
            for wake_word, score in prediction.items():
                if score > THRESHOLD:
                    consecutive_hits += 1
                    if consecutive_hits >= REQUIRED_HITS:
                        triggered = True
                        triggered_score = score
                else:
                    consecutive_hits = 0

            if triggered:
                consecutive_hits = 0
                log(f"Wake word detected! (confidence: {triggered_score:.3f})")

                t0 = time.time()
                audio_file = record_command()
                t1 = time.time()
                log(f"[TIMING] Recording took {t1 - t0:.2f}s")

                text = transcribe(audio_file)
                t2 = time.time()
                log(f"[TIMING] Transcription took {t2 - t1:.2f}s")
                log(f"You said: {text}")

                handle_command(text)
                t3 = time.time()
                log(f"[TIMING] handle_command (brain+action+speak) took {t3 - t2:.2f}s")
                log(f"[TIMING] TOTAL response time: {t3 - t0:.2f}s")

                flush_mic_buffer()
                suppress_until = time.time() + WAKE_COOLDOWN_SECONDS
                log("Say 'Hey Jarvis' to activate...")
    except KeyboardInterrupt:
        log("Stopping...")
    finally:
        mic_stream.stop_stream()
        mic_stream.close()
        pa.terminate()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.exception(f"Jarvis crashed unexpectedly: {e}")
        raise
