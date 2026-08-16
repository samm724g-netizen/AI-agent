import re
import json
import time
import difflib
import requests
from google import genai
import config

client = genai.Client(api_key=config.GEMINI_API_KEY)

_quota_exhausted_until = 0
QUOTA_COOLDOWN_SECONDS = 60 * 30  # 30 minutes

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:1b"          # for fast intent parsing
OLLAMA_CHAT_MODEL = "llama3.2"        # for friendly chatting (pull this model)

# Conversation memory
chat_history = []

SYSTEM_PROMPT = """You are the intent parser for a voice assistant called Jarvis. 
Given a spoken command, respond with ONLY a JSON object, no other text, no markdown.

The JSON must have this shape:
{"action": "<action_name>", "target": "<string, empty if not applicable>"}

Valid actions:
open_app, close_app, set_volume, screenshot, lock_screen, wifi_toggle, copy, cut, paste,
google_search, minimize_all, snap_window, maximize_window, minimize_window, switch_window,
media_play_pause, media_next, media_previous, media_stop, open_folder, shutdown_pc, restart_pc,
create_pdf, chat, unknown

Rules:
- open_app: target is the app name
- close_app: target is the app name
- set_volume: target is a number 0-100
- screenshot, lock_screen, copy, cut, paste, minimize_all, maximize_window, minimize_window,
  switch_window, media_play_pause, media_next, media_previous, media_stop, shutdown_pc, restart_pc: target is ""
- wifi_toggle: target is "on" or "off"
- snap_window: target is "left" or "right"
- google_search: target is the search query
- open_folder: target is the folder name (downloads, documents, desktop...)
- create_pdf: target should contain the content + optional location (e.g. "solar system on desktop")
- chat: use this for questions, conversation, knowledge, jokes, explanations, opinions etc. Put the full user sentence in target.
- shutdown_pc / restart_pc: ONLY when clearly about the whole computer
- If nothing matches → "unknown"

Respond with ONLY the JSON object."""


CHAT_SYSTEM_PROMPT = """You are Jarvis, a friendly, helpful and slightly witty AI assistant living on the user's computer.
Speak naturally like a good friend. Keep answers concise (1-4 sentences) unless the user asks for more detail.
You have knowledge of the world. Be honest when you don't know something.
Never say you are an AI model unless asked. Just be Jarvis."""


def regex_fast_path(text: str):
    text_lower = text.lower().strip()

    if "screenshot" in text_lower:
        return {"action": "screenshot", "target": ""}

    if "lock" in text_lower and "screen" in text_lower:
        return {"action": "lock_screen", "target": ""}

    mentions_system = any(w in text_lower for w in ["pc", "computer", "system", "laptop"])
    if mentions_system and any(w in text_lower for w in ["shut down", "shutdown", "power off", "turn off"]):
        return {"action": "shutdown_pc", "target": ""}
    if mentions_system and "restart" in text_lower:
        return {"action": "restart_pc", "target": ""}

    volume_match = re.search(r"volume\s+(?:to\s+)?(\d{1,3})", text_lower)
    if volume_match:
        return {"action": "set_volume", "target": volume_match.group(1)}

    if "mute" in text_lower:
        return {"action": "set_volume", "target": "0"}

    if "wifi" in text_lower or "wi-fi" in text_lower or "wi fi" in text_lower:
        if any(w in text_lower for w in ["off", "disable", "disconnect", "turn off"]):
            return {"action": "wifi_toggle", "target": "off"}
        if any(w in text_lower for w in ["on", "enable", "connect", "turn on"]):
            return {"action": "wifi_toggle", "target": "on"}

    if text_lower.strip() in ("copy", "copy that", "copy this"):
        return {"action": "copy", "target": ""}
    if text_lower.strip() in ("cut", "cut that", "cut this"):
        return {"action": "cut", "target": ""}
    if text_lower.strip() in ("paste", "paste that", "paste this"):
        return {"action": "paste", "target": ""}

    if any(p in text_lower for p in ["pause music", "pause song", "play music", "play song"]) or text_lower.strip() in ("play", "pause"):
        return {"action": "media_play_pause", "target": ""}
    if any(p in text_lower for p in ["next song", "next track", "skip song", "skip track", "skip"]):
        return {"action": "media_next", "target": ""}
    if any(p in text_lower for p in ["previous song", "previous track", "last song", "go back a song"]):
        return {"action": "media_previous", "target": ""}
    if any(p in text_lower for p in ["stop music", "stop song", "stop playback"]):
        return {"action": "media_stop", "target": ""}

    if any(p in text_lower for p in ["minimize everything", "minimize all", "show desktop"]):
        return {"action": "minimize_all", "target": ""}

    if "snap" in text_lower or ("move" in text_lower and "window" in text_lower):
        if "left" in text_lower:
            return {"action": "snap_window", "target": "left"}
        if "right" in text_lower:
            return {"action": "snap_window", "target": "right"}

    if "maximize" in text_lower and "window" in text_lower:
        return {"action": "maximize_window", "target": ""}
    if "minimize" in text_lower and "window" in text_lower:
        return {"action": "minimize_window", "target": ""}
    if any(p in text_lower for p in ["switch window", "switch app", "alt tab", "next window"]):
        return {"action": "switch_window", "target": ""}

    folder_match = re.search(
        r"^open[\s,]+(?:my\s+)?(downloads|documents|desktop|pictures|videos|music)(?:\s+folder)?",
        text_lower,
    )
    if folder_match:
        return {"action": "open_folder", "target": folder_match.group(1)}

    # PDF creation detection
    if any(p in text_lower for p in ["create a pdf", "make a pdf", "generate a pdf", "create pdf", "make pdf", "generate pdf"]):
        match = re.search(
            r"(?:create|make|generate)\s+(?:a\s+)?pdf\s+(?:of|about|on|with|for)?\s*(.+)",
            text_lower
        )
        if match:
            return {"action": "create_pdf", "target": match.group(1).strip()}
        return {"action": "create_pdf", "target": text}

    search_match = re.search(r"^(?:google search|search for|search google for|search)[\s,]+(.+)", text_lower)
    if search_match:
        return {"action": "google_search", "target": search_match.group(1).strip()}

    open_match = re.search(r"(?:^please\s+)?(?:open|launch|start)[\s,]+(.+)", text_lower)
    if open_match:
        return {"action": "open_app", "target": open_match.group(1).strip()}

    close_match = re.search(r"(?:^please\s+)?(?:close|shut down|quit|exit)[\s,]+(.+)", text_lower)
    if close_match:
        return {"action": "close_app", "target": close_match.group(1).strip()}

    words = text_lower.strip().split()
    if len(words) >= 2:
        first_word = words[0].strip(",.!?")
        rest = " ".join(words[1:]).strip()
        if difflib.get_close_matches(first_word, ["open", "launch", "start"], n=1, cutoff=0.6):
            return {"action": "open_app", "target": rest}
        if difflib.get_close_matches(first_word, ["close", "quit", "exit"], n=1, cutoff=0.6):
            return {"action": "close_app", "target": rest}

    return None


def _extract_json(raw: str):
    raw = raw.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]
    return json.loads(raw)


def call_gemini(text: str):
    response = client.models.generate_content(
        model="gemini-flash-latest",
        contents=text,
        config={
            "system_instruction": SYSTEM_PROMPT,
            "response_mime_type": "application/json",
        }
    )
    return _extract_json(response.text)


def call_ollama(text: str):
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "prompt": f"{SYSTEM_PROMPT}\n\nCommand: {text}\nJSON:",
            "stream": False,
            "options": {"temperature": 0.1},
        },
        timeout=20,
    )
    response.raise_for_status()
    raw = response.json().get("response", "")
    return _extract_json(raw)


def parse_intent(text: str) -> dict:
    global _quota_exhausted_until

    if not text:
        return {"action": "unknown", "target": ""}

    fast_result = regex_fast_path(text)
    if fast_result:
        return fast_result

    if time.time() >= _quota_exhausted_until:
        try:
            return call_gemini(text)
        except Exception as e:
            error_str = str(e)
            if "RESOURCE_EXHAUSTED" in error_str or "429" in error_str:
                _quota_exhausted_until = time.time() + QUOTA_COOLDOWN_SECONDS
                print("[BRAIN] Gemini quota reached — falling back to Ollama")
            else:
                print(f"[BRAIN ERROR] Gemini failed ({e}), trying Ollama")

    try:
        return call_ollama(text)
    except Exception as e:
        print(f"[BRAIN ERROR] Ollama unavailable ({e})")
        return {"action": "chat", "target": text}


def chat_with_ai(user_text: str) -> str:
    """Friendly conversation with memory"""
    global chat_history

    chat_history.append({"role": "user", "content": user_text})

    if len(chat_history) > 16:
        chat_history = chat_history[-16:]

    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}] + chat_history

    # Try Gemini first
    if time.time() >= _quota_exhausted_until:
        try:
            response = client.models.generate_content(
                model="gemini-flash-latest",
                contents=user_text,
                config={"system_instruction": CHAT_SYSTEM_PROMPT}
            )
            reply = response.text.strip()
            chat_history.append({"role": "assistant", "content": reply})
            return reply
        except Exception as e:
            print(f"[CHAT] Gemini failed: {e}")

    # Fallback to Ollama
    try:
        from ollama import chat as ollama_chat
        response = ollama_chat(model=OLLAMA_CHAT_MODEL, messages=messages)
        reply = response.message.content.strip()
        chat_history.append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        print(f"[CHAT] Ollama failed: {e}")
        return "Sorry, I'm having trouble thinking right now."
