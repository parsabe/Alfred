"""Open Alfred's interface in its own app window (Chrome/Edge/Chromium app mode)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

_LINUX = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
    "brave-browser",
    "vivaldi",
]


def _windows_candidates() -> list[str]:
    out = []
    for env, rel in [
        ("ProgramFiles(x86)", r"Microsoft\Edge\Application\msedge.exe"),
        ("ProgramFiles", r"Microsoft\Edge\Application\msedge.exe"),
        ("ProgramFiles", r"Google\Chrome\Application\chrome.exe"),
        ("ProgramFiles(x86)", r"Google\Chrome\Application\chrome.exe"),
        ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"),
    ]:
        base = os.environ.get(env)
        if base:
            out.append(str(Path(base) / rel))
    return out


def find_app_browser() -> str | None:
    if sys.platform == "win32":
        return next((p for p in _windows_candidates() if Path(p).is_file()), None)
    if sys.platform == "darwin":
        for app in ("Google Chrome", "Microsoft Edge", "Chromium", "Brave Browser"):
            path = f"/Applications/{app}.app/Contents/MacOS/{app}"
            if Path(path).is_file():
                return path
        return None
    return next((p for name in _LINUX if (p := shutil.which(name))), None)


def open_app(server: str) -> str:
    """Opens the interface; returns how it was opened."""
    url = server.rstrip("/") + "/"
    browser = find_app_browser()
    if browser:
        subprocess.Popen(
            [browser, f"--app={url}", "--new-window"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(os.name == "posix"),
        )
        return f"app window ({Path(browser).name})"
    webbrowser.open(url)
    return "default browser"
