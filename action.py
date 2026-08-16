import subprocess
import os
import ctypes
import difflib
import re
import json
from datetime import datetime
from pathlib import Path

try:
    from pycaw.pycaw import AudioUtilities
    PYCAW_AVAILABLE = True
    PYCAW_ERROR = None
except Exception as e:
    PYCAW_AVAILABLE = False
    PYCAW_ERROR = str(e)

import pyautogui
from fpdf import FPDF

# Map of common app names to their launch command
APP_MAP = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "blender": "blender",
    "fusion 360": "fusion360://",
    "fusion360": "fusion360://",
    "chatgpt": "https://chat.openai.com",
    "chat gpt": "https://chat.openai.com",
    "notepad": "notepad",
    "scratch": "https://scratch.mit.edu",
    "easyeda": "https://easyeda.com",
    "easy eda": "https://easyeda.com",
    "arduino ide": "Arduino IDE.exe",
    "arduino": "Arduino IDE.exe",
    "vs code": "code",
    "visual studio code": "code",
    "file manager": "explorer",
    "file explorer": "explorer",
    "explorer": "explorer",
    "ppt": "powerpnt",
    "powerpoint": "powerpnt",
    "doc": "winword",
    "word": "winword",
    "excel": "excel",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "settings": "ms-settings:",
}


def clean_target(target: str) -> str:
    return target.strip().strip(".,!?").lower()


def resolve_app_name(target: str):
    target = clean_target(target)
    if target in APP_MAP:
        return target
    matches = difflib.get_close_matches(target, APP_MAP.keys(), n=1, cutoff=0.5)
    if matches:
        return matches[0]
    return None


def find_installed_exe(exe_name: str):
    search_roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
    ]
    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            for f in filenames:
                if f.lower() == exe_name.lower():
                    return os.path.join(dirpath, f)
            if dirpath.count(os.sep) - root.count(os.sep) >= 3:
                dirnames[:] = []
    return None


CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_path_cache.json")


def load_path_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_path_cache(cache):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass


PATH_CACHE = load_path_cache()
last_app = None
PRONOUNS = {"it", "that", "this", "the same thing", "same"}


def open_app(target: str) -> str:
    global last_app

    if target.strip().lower().strip(",.!?") in PRONOUNS:
        if last_app is None:
            return "I'm not sure what you mean — you haven't opened anything yet"
        target = last_app

    resolved = resolve_app_name(target)
    if resolved is None:
        return f"I don't know an app called {clean_target(target)}"

    command = APP_MAP.get(resolved, resolved)
    try:
        if command.startswith("http") or (":" in command and not command.endswith(".exe")):
            os.startfile(command)
            last_app = resolved
            return f"Opening {resolved}"

        if resolved in PATH_CACHE and os.path.exists(PATH_CACHE[resolved]):
            os.startfile(PATH_CACHE[resolved])
            last_app = resolved
            return f"Opening {resolved}"

        try:
            os.startfile(command)
            last_app = resolved
            return f"Opening {resolved}"
        except OSError:
            pass

        exe_name = command if command.endswith(".exe") else command + ".exe"
        found_path = find_installed_exe(exe_name)
        if found_path:
            PATH_CACHE[resolved] = found_path
            save_path_cache(PATH_CACHE)
            os.startfile(found_path)
            last_app = resolved
            return f"Opening {resolved}"

        return f"I found {resolved} in my app list, but it's not installed or isn't in a location I can find"
    except Exception as e:
        return f"I couldn't open {resolved}: {e}"


CLOSE_PROCESS_MAP = {
    "fusion 360": "Fusion360.exe",
    "fusion360": "Fusion360.exe",
    "chatgpt": "chrome.exe",
    "chat gpt": "chrome.exe",
    "scratch": "chrome.exe",
    "easyeda": "chrome.exe",
    "easy eda": "chrome.exe",
}


def close_app(target: str) -> str:
    global last_app

    if target.strip().lower().strip(",.!?") in PRONOUNS:
        if last_app is None:
            return "I'm not sure what you mean — you haven't opened anything yet"
        target = last_app

    resolved = resolve_app_name(target)
    if resolved is None:
        return f"I don't know an app called {clean_target(target)}"

    process_name = CLOSE_PROCESS_MAP.get(resolved, APP_MAP.get(resolved, resolved))
    guess = process_name if process_name.endswith(".exe") else process_name + ".exe"
    try:
        result = subprocess.run(
            ["taskkill", "/IM", guess, "/F"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            last_app = resolved
            return f"Closed {resolved}"
        else:
            return f"I couldn't find {resolved} running"
    except Exception as e:
        return f"Error closing {resolved}: {e}"


def set_volume(level: int) -> str:
    if not PYCAW_AVAILABLE:
        print(f"[VOLUME ERROR] pycaw failed to load: {PYCAW_ERROR}")
        return "Volume control isn't available on this system"
    try:
        level = max(0, min(100, int(level)))
        device = AudioUtilities.GetSpeakers()
        device.volume_percent = level
        return f"Volume set to {level} percent"
    except Exception as e:
        print(f"[VOLUME ERROR] {e}")
        return f"Couldn't change volume: {e}"


def take_screenshot() -> str:
    try:
        folder = os.path.join(os.path.expanduser("~"), "Pictures", "JarvisScreenshots")
        os.makedirs(folder, exist_ok=True)
        filename = os.path.join(folder, f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
        img = pyautogui.screenshot()
        img.save(filename)
        return "Screenshot taken"
    except Exception as e:
        return f"Couldn't take screenshot: {e}"


def lock_screen() -> str:
    try:
        ctypes.windll.user32.LockWorkStation()
        return "Locking screen"
    except Exception as e:
        return f"Couldn't lock screen: {e}"


def wifi_toggle(state: str) -> str:
    state = state.lower().strip()
    try:
        if state in ("on", "enable", "connect"):
            subprocess.run(
                ["netsh", "interface", "set", "interface", "Wi-Fi", "enabled"],
                capture_output=True, text=True
            )
            return "Wi-Fi turned on"
        elif state in ("off", "disable", "disconnect"):
            subprocess.run(
                ["netsh", "interface", "set", "interface", "Wi-Fi", "disabled"],
                capture_output=True, text=True
            )
            return "Wi-Fi turned off"
        else:
            return "I need to know whether to turn Wi-Fi on or off"
    except Exception as e:
        return f"Couldn't change Wi-Fi: {e}"


def copy_text() -> str:
    try:
        pyautogui.hotkey("ctrl", "c")
        return "Copied"
    except Exception as e:
        return f"Couldn't copy: {e}"


def cut_text() -> str:
    try:
        pyautogui.hotkey("ctrl", "x")
        return "Cut"
    except Exception as e:
        return f"Couldn't cut: {e}"


def paste_text() -> str:
    try:
        pyautogui.hotkey("ctrl", "v")
        return "Pasted"
    except Exception as e:
        return f"Couldn't paste: {e}"


def google_search(query: str) -> str:
    if not query.strip():
        return "What should I search for?"
    try:
        import urllib.parse
        url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
        os.startfile(url)
        return f"Searching Google for {query}"
    except Exception as e:
        return f"Couldn't search: {e}"


def minimize_all() -> str:
    try:
        pyautogui.hotkey("win", "d")
        return "Minimized everything"
    except Exception as e:
        return f"Couldn't minimize windows: {e}"


def snap_window(direction: str) -> str:
    direction = direction.lower().strip()
    try:
        if "left" in direction:
            pyautogui.hotkey("win", "left")
            return "Snapped window to the left"
        elif "right" in direction:
            pyautogui.hotkey("win", "right")
            return "Snapped window to the right"
        else:
            return "I need to know left or right"
    except Exception as e:
        return f"Couldn't snap window: {e}"


def maximize_window() -> str:
    try:
        pyautogui.hotkey("win", "up")
        return "Maximized window"
    except Exception as e:
        return f"Couldn't maximize window: {e}"


def minimize_window() -> str:
    try:
        pyautogui.hotkey("win", "down")
        return "Minimized window"
    except Exception as e:
        return f"Couldn't minimize window: {e}"


def switch_window() -> str:
    try:
        pyautogui.hotkey("alt", "tab")
        return "Switching window"
    except Exception as e:
        return f"Couldn't switch window: {e}"


def media_play_pause() -> str:
    try:
        pyautogui.press("playpause")
        return "Toggled play/pause"
    except Exception as e:
        return f"Couldn't control media: {e}"


def media_next() -> str:
    try:
        pyautogui.press("nexttrack")
        return "Skipped to next track"
    except Exception as e:
        return f"Couldn't skip track: {e}"


def media_previous() -> str:
    try:
        pyautogui.press("prevtrack")
        return "Went back a track"
    except Exception as e:
        return f"Couldn't go to previous track: {e}"


def media_stop() -> str:
    try:
        pyautogui.press("stop")
        return "Stopped media"
    except Exception as e:
        return f"Couldn't stop media: {e}"


FOLDER_MAP = {
    "downloads": os.path.join(os.path.expanduser("~"), "Downloads"),
    "documents": os.path.join(os.path.expanduser("~"), "Documents"),
    "desktop": os.path.join(os.path.expanduser("~"), "Desktop"),
    "pictures": os.path.join(os.path.expanduser("~"), "Pictures"),
    "videos": os.path.join(os.path.expanduser("~"), "Videos"),
    "music": os.path.join(os.path.expanduser("~"), "Music"),
}


def open_folder(name: str) -> str:
    key = name.lower().strip()
    matches = difflib.get_close_matches(key, FOLDER_MAP.keys(), n=1, cutoff=0.5)
    if not matches:
        return f"I don't know a folder called {name}"
    resolved = matches[0]
    try:
        os.startfile(FOLDER_MAP[resolved])
        return f"Opening {resolved} folder"
    except Exception as e:
        return f"Couldn't open {resolved} folder: {e}"


DESTRUCTIVE_ACTIONS = {"shutdown_pc", "restart_pc"}


def shutdown_pc() -> str:
    try:
        subprocess.run(["shutdown", "/s", "/t", "5"])
        return "Shutting down in 5 seconds"
    except Exception as e:
        return f"Couldn't shut down: {e}"


def restart_pc() -> str:
    try:
        subprocess.run(["shutdown", "/r", "/t", "5"])
        return "Restarting in 5 seconds"
    except Exception as e:
        return f"Couldn't restart: {e}"


def create_pdf(target: str) -> str:
    """Create a PDF from a description. Fixed version (no special characters)."""
    target = target.strip()
    if not target:
        return "What should the PDF be about?"

    location = "Desktop"
    content = target

    lower = target.lower()
    if "on desktop" in lower or "on the desktop" in lower:
        location = "Desktop"
        content = re.sub(r"\s+on\s+(the\s+)?desktop", "", target, flags=re.IGNORECASE).strip()
    elif "on documents" in lower or "in documents" in lower:
        location = "Documents"
        content = re.sub(r"\s+(on|in)\s+(the\s+)?documents", "", target, flags=re.IGNORECASE).strip()
    elif "on downloads" in lower:
        location = "Downloads"
        content = re.sub(r"\s+on\s+(the\s+)?downloads", "", target, flags=re.IGNORECASE).strip()

    if not content:
        content = "Untitled Document"

    try:
        # Try to expand content using AI
        expanded = None
        try:
            from brain import chat_with_ai
            expanded = chat_with_ai(
                f"Write a short, clear, well-structured document (3-6 paragraphs) about: {content}. "
                f"Just write the content, no introduction like 'Here is the document'."
            )
        except Exception:
            expanded = None

        if not expanded or "trouble thinking" in expanded.lower() or len(expanded) < 30:
            expanded = (
                f"Topic: {content}\n\n"
                f"This document was automatically generated by Jarvis.\n\n"
                f"You can ask Jarvis to create more detailed PDFs once the AI models are properly configured."
            )

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        # Title
        pdf.set_font("Helvetica", "B", 18)
        pdf.multi_cell(0, 12, text=content[:90], align="C")
        pdf.ln(10)

        # Body
        pdf.set_font("Helvetica", size=12)
        pdf.multi_cell(0, 8, text=expanded)

        # Footer (no special characters that cause errors)
        pdf.ln(12)
        pdf.set_font("Helvetica", "I", 9)
        pdf.cell(0, 8, text=f"Generated by Jarvis - {datetime.now().strftime('%Y-%m-%d %H:%M')}", align="C")

        folder = Path.home() / location
        folder.mkdir(parents=True, exist_ok=True)

        safe_name = re.sub(r'[\\/*?:"<>|]', "", content)[:50].strip() or "document"
        filename = folder / f"{safe_name}.pdf"

        pdf.output(str(filename))
        return f"Created PDF '{filename.name}' on your {location}"

    except Exception as e:
        return f"Couldn't create the PDF: {e}"


def execute_action(action: str, target: str = "") -> str:
    action = (action or "").lower().strip()

    if action == "open_app":
        return open_app(target)
    elif action == "close_app":
        return close_app(target)
    elif action == "set_volume":
        try:
            return set_volume(int(target))
        except ValueError:
            return "I need a volume number between 0 and 100"
    elif action == "screenshot":
        return take_screenshot()
    elif action == "lock_screen":
        return lock_screen()
    elif action == "wifi_toggle":
        return wifi_toggle(target)
    elif action == "copy":
        return copy_text()
    elif action == "cut":
        return cut_text()
    elif action == "paste":
        return paste_text()
    elif action == "google_search":
        return google_search(target)
    elif action == "minimize_all":
        return minimize_all()
    elif action == "snap_window":
        return snap_window(target)
    elif action == "maximize_window":
        return maximize_window()
    elif action == "minimize_window":
        return minimize_window()
    elif action == "switch_window":
        return switch_window()
    elif action == "media_play_pause":
        return media_play_pause()
    elif action == "media_next":
        return media_next()
    elif action == "media_previous":
        return media_previous()
    elif action == "media_stop":
        return media_stop()
    elif action == "open_folder":
        return open_folder(target)
    elif action == "shutdown_pc":
        return shutdown_pc()
    elif action == "restart_pc":
        return restart_pc()
    elif action == "create_pdf":
        return create_pdf(target)
    elif action == "unknown":
        return "I'm not sure how to do that yet"
    else:
        return f"I don't know how to handle '{action}' yet"
