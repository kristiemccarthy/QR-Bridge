import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse

import cv2
import mss
import numpy as np
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import messagebox

# System-tray support is optional. If pystray is unavailable (or fails to
# import on this platform) the app still runs — it just falls back to a
# normal taskbar-minimised window instead of hiding to the tray.
try:
    import pystray
except Exception:  # pragma: no cover - depends on platform/runtime
    pystray = None


APP_NAME = "QR Bridge"
# Launch flag written into the auto-start shortcut so a login launch comes up
# silently (hidden to the tray) instead of popping the window every time.
MINIMIZED_FLAGS = ("--minimized", "--minimised")


def resource_path(file_name: str) -> Path:
    """Locate a data file that ships alongside the app.

    When packaged by PyInstaller (a frozen --onefile .exe) the bundled data
    files are unpacked into a temporary folder exposed as sys._MEIPASS. When
    run as a normal script they sit next to qr_bridge.py. This returns the
    right path for both cases so the file-serving code never changes.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / file_name
    return Path(__file__).with_name(file_name)


def settings_dir() -> Path:
    """Per-user folder for QR Bridge's small UI-state settings file.

    Kept in %APPDATA% (not next to the .exe) so it stays writable even when
    the app is installed somewhere read-only.
    """
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".qr-bridge"


def settings_path() -> Path:
    return settings_dir() / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(data: dict) -> None:
    directory = settings_dir()
    directory.mkdir(parents=True, exist_ok=True)
    settings_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def startup_shortcut_path() -> Path:
    """The Windows Startup-folder shortcut used for auto-start-on-logon.

    A shortcut in this folder (rather than a registry Run key) is easy for a
    non-technical user to see and delete: it shows up in the Startup Apps list
    and in the Startup folder itself.
    """
    base = os.environ.get("APPDATA", str(Path.home()))
    startup = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return startup / f"{APP_NAME}.lnk"


def _launch_target() -> Tuple[str, str]:
    """Return (target_path, arguments) that a login launch should run.

    Frozen: run the .exe directly with the minimised flag. As a script (dev):
    run pythonw with this file so no console window appears at login.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, MINIMIZED_FLAGS[0]
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    launcher = str(pythonw if pythonw.exists() else sys.executable)
    return launcher, f'"{Path(__file__).resolve()}" {MINIMIZED_FLAGS[0]}'


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def is_autostart_enabled() -> bool:
    return startup_shortcut_path().exists()


def enable_autostart() -> None:
    """Create the Startup-folder shortcut pointing at this app.

    Uses PowerShell's WScript.Shell to write a proper .lnk without pulling in
    an extra Python dependency. Raises on failure so the caller can report it.
    """
    target, arguments = _launch_target()
    shortcut = startup_shortcut_path()
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(%s);"
        "$s.TargetPath = %s;"
        "$s.Arguments = %s;"
        "$s.WorkingDirectory = %s;"
        "$s.Description = 'QR Bridge';"
        "$s.Save()"
    ) % (
        _powershell_quote(str(shortcut)),
        _powershell_quote(target),
        _powershell_quote(arguments),
        _powershell_quote(str(Path(target).parent)),
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        creationflags=creationflags,
    )
    if result.returncode != 0 or not shortcut.exists():
        raise OSError(
            "Could not create the startup shortcut.\n"
            + (result.stderr.strip() or "Unknown error")
        )


def disable_autostart() -> None:
    try:
        startup_shortcut_path().unlink()
    except FileNotFoundError:
        pass


MIN_SELECTION_SIZE = 20
HANDOFF_EXPIRY_SECONDS = 5 * 60
# Fixed port so a phone's saved home-screen link keeps working between runs.
# If another program is using 8765, change it here — but the phone-side
# shortcut only stays valid while this stays the same.
HANDOFF_PORT = 8765
RESULT_PAGE_FILE = resource_path("qr-bridge-result.html")
# Home-screen install assets, served at root paths. iOS probes
# /apple-touch-icon(-precomposed).png at the root by default, so both
# paths point at the same file.
STATIC_FILES = {
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/apple-touch-icon-precomposed.png": ("apple-touch-icon.png", "image/png"),
}
URL_PATTERN = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)
SENSITIVE_QR_TERMS = (
    "login",
    "log-in",
    "signin",
    "sign-in",
    "sign_in",
    "auth",
    "authenticate",
    "authentication",
    "verify",
    "verification",
    "security",
    "secure",
    "pair",
    "pairing",
    "government",
    "mygov",
    "identity",
    "idverify",
    "2fa",
    "mfa",
    "otp",
)


@dataclass
class Selection:
    left: int
    top: int
    right: int
    bottom: int

    def normalized(self) -> "Selection":
        return Selection(
            min(self.left, self.right),
            min(self.top, self.bottom),
            max(self.left, self.right),
            max(self.top, self.bottom),
        )

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


class QRBridgeApp:
    def __init__(self, start_minimized: bool = False) -> None:
        self.root = tk.Tk()
        self.root.title("QR Bridge")
        self.root.geometry("620x380")
        self.root.minsize(520, 320)
        self.root.configure(bg="#f5f5f5")

        self.result_text: Optional[str] = None
        self.preview_window: Optional[tk.Toplevel] = None
        self.select_button: Optional[tk.Button] = None
        self.settings = load_settings()
        self.tray_icon = None
        self._tray_notified = False
        self.handoff_store = HandoffStore()
        self.handoff_server = PhoneHandoffServer(self.handoff_store)

        self._build_main_window()
        # Bind port 8765 first, always — the phone must be able to reach the
        # server even on a silent login launch, before anyone touches the UI.
        self._start_handoff_server()
        self._setup_tray()
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_close)

        if start_minimized:
            # Silent login launch: come up hidden to the tray (or minimised to
            # the taskbar if the tray isn't available). The server is already
            # bound above, so the phone can reach it immediately.
            if self.tray_icon is not None:
                self.root.withdraw()
            else:
                self.root.iconify()

    def _setup_tray(self) -> None:
        if pystray is None:
            return
        try:
            image = Image.open(resource_path("icon-192.png"))
        except Exception:
            self.tray_icon = None
            return
        menu = pystray.Menu(
            pystray.MenuItem("Open QR Bridge", self._tray_open, default=True),
            pystray.MenuItem("Quit QR Bridge", self._tray_quit),
        )
        self.tray_icon = pystray.Icon("qr_bridge", image, "QR Bridge", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _tray_open(self, *_args) -> None:
        self.root.after(0, self.show_window)

    def _tray_quit(self, *_args) -> None:
        self.root.after(0, self.close_app)

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        if self.select_button is not None:
            self.select_button.focus_set()

    def on_window_close(self) -> None:
        # With a tray icon, closing the window hides it and leaves the
        # always-listen server bound so the phone can still reach it. Without a
        # tray, closing quits the app as before.
        if self.tray_icon is not None:
            self.root.withdraw()
            if not self._tray_notified:
                self._tray_notified = True
                try:
                    self.tray_icon.notify(
                        "QR Bridge is still running so your phone can reach it. "
                        "Use the tray icon to open it again or to quit.",
                        "QR Bridge",
                    )
                except Exception:
                    pass
        else:
            self.close_app()

    def _start_handoff_server(self) -> None:
        # Bind port 8765 once, at launch, and keep it up for the whole session
        # so a cold tap on the phone's home-screen icon reaches a live server.
        # This only BINDS the port and serves the static routes plus
        # /api/handoff; it does NOT create a handoff. /api/handoff has nothing
        # to hand out until "Send to phone" makes a one-time code.
        try:
            self.handoff_server.start()
        except OSError as exc:
            messagebox.showerror(
                "QR Bridge",
                (
                    f"Could not start the local phone handoff server on port {HANDOFF_PORT}. "
                    "Another program may be using that port.\n\n"
                    "Send to phone will not be available until you restart QR Bridge.\n\n"
                    f"{exc}"
                ),
            )

    def _build_main_window(self) -> None:
        container = tk.Frame(self.root, bg="#f5f5f5", padx=28, pady=28)
        container.pack(fill="both", expand=True)

        title = tk.Label(
            container,
            text="QR Bridge",
            font=("Segoe UI", 28, "bold"),
            bg="#f5f5f5",
            fg="#111111",
        )
        title.pack(anchor="w")

        intro = tk.Label(
            container,
            text=(
                "Decode a QR code that is already on your screen. "
                "Nothing is uploaded, stored, or opened automatically."
            ),
            font=("Segoe UI", 15),
            bg="#f5f5f5",
            fg="#222222",
            wraplength=540,
            justify="left",
        )
        intro.pack(anchor="w", pady=(14, 24))

        select_button = tk.Button(
            container,
            text="Select area of screen",
            font=("Segoe UI", 18, "bold"),
            command=self.start_capture,
            padx=18,
            pady=12,
            default="active",
        )
        select_button.pack(anchor="w", pady=(0, 20))
        select_button.focus_set()
        self.select_button = select_button

        help_text = tk.Label(
            container,
            text=(
                "Tip: put the QR code on screen, press this button, then drag "
                "a box around the code. Press Esc to cancel selection."
            ),
            font=("Segoe UI", 13),
            bg="#f5f5f5",
            fg="#333333",
            wraplength=540,
            justify="left",
        )
        help_text.pack(anchor="w", pady=(12, 0))

        settings_button = tk.Button(
            container,
            text="Settings",
            font=("Segoe UI", 13),
            command=self.open_settings,
            padx=12,
            pady=6,
            takefocus=True,
        )
        settings_button.pack(anchor="w", pady=(18, 0))

        self.root.bind("<Return>", lambda _event: self.start_capture())
        self.root.bind("<Escape>", lambda _event: self.on_window_close())

    def start_capture(self) -> None:
        self.root.withdraw()
        self.root.after(250, self._capture_and_select)

    def _capture_and_select(self) -> None:
        try:
            screenshot, monitor = self._take_screenshot()
        except Exception as exc:
            self.root.deiconify()
            messagebox.showerror("QR Bridge", f"Could not capture the screen.\n\n{exc}")
            return

        selector = AreaSelector(self.root, screenshot, monitor)
        selection = selector.select()
        self.root.deiconify()

        if selection is None:
            return

        cropped = screenshot.crop(
            (
                selection.left,
                selection.top,
                selection.right,
                selection.bottom,
            )
        )
        self._decode_and_show(cropped)

    def _take_screenshot(self) -> Tuple[Image.Image, dict]:
        with mss.mss() as screen_capture:
            monitor = screen_capture.monitors[0]
            raw = screen_capture.grab(monitor)
            image = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            return image, monitor

    def _decode_and_show(self, image: Image.Image) -> None:
        try:
            decoded = decode_qr_image(image)
        except MultipleQRCodesError:
            self.show_plain_error("Multiple QR codes found")
            return
        except QRUnreadableError:
            self.show_plain_error("QR code could not be read")
            return
        except QRNotFoundError:
            self.show_plain_error("No QR code found")
            return
        except cv2.error:
            self.show_plain_error("QR code could not be read")
            return

        self.result_text = decoded
        self.show_preview(decoded)

    def show_plain_error(self, message: str) -> None:
        messagebox.showinfo("QR Bridge", message)

    def show_preview(self, decoded: str) -> None:
        if self.preview_window is not None and self.preview_window.winfo_exists():
            self.preview_window.destroy()

        window = tk.Toplevel(self.root)
        self.preview_window = window
        window.title("QR Bridge preview")
        window.geometry("780x560")
        window.minsize(620, 460)
        window.configure(bg="#ffffff")
        window.transient(self.root)
        window.grab_set()
        window.protocol("WM_DELETE_WINDOW", self.close_preview)

        container = tk.Frame(window, bg="#ffffff", padx=24, pady=24)
        container.pack(fill="both", expand=True)

        heading = tk.Label(
            container,
            text="Decoded QR code",
            font=("Segoe UI", 24, "bold"),
            bg="#ffffff",
            fg="#111111",
        )
        heading.pack(anchor="w")

        safety_message = tk.Label(
            container,
            text="Review this before opening. QR codes can contain login or security links.",
            font=("Segoe UI", 14, "bold"),
            bg="#ffffff",
            fg="#111111",
            wraplength=700,
            justify="left",
        )
        safety_message.pack(anchor="w", pady=(14, 0))

        if looks_security_sensitive(decoded):
            extra_warning = tk.Label(
                container,
                text=(
                    "This may be a private or security-sensitive QR code. "
                    "Only open it if you trust where it came from."
                ),
                font=("Segoe UI", 13, "bold"),
                bg="#fff3cd",
                fg="#3b2f00",
                wraplength=700,
                justify="left",
                padx=12,
                pady=10,
            )
            extra_warning.pack(fill="x", pady=(12, 0))

        result_box = tk.Text(
            container,
            font=("Segoe UI", 16),
            wrap="char",
            height=7,
            padx=12,
            pady=12,
            bg="#f7f7f7",
            fg="#111111",
            relief="solid",
            borderwidth=1,
        )
        result_box.pack(fill="both", expand=True, pady=(16, 18))
        result_box.insert("1.0", decoded)
        result_box.configure(state="disabled")

        button_row = tk.Frame(container, bg="#ffffff")
        button_row.pack(fill="x")
        secondary_button_row = tk.Frame(container, bg="#ffffff")
        secondary_button_row.pack(fill="x", pady=(12, 0))

        copy_button = tk.Button(
            button_row,
            text="Copy",
            font=("Segoe UI", 15, "bold"),
            command=lambda: self.copy_result(window),
            padx=18,
            pady=10,
            takefocus=True,
        )
        copy_button.pack(side="left", padx=(0, 12))

        open_button = tk.Button(
            button_row,
            text="Open on this computer",
            font=("Segoe UI", 15, "bold"),
            command=lambda: self.open_result(decoded),
            padx=18,
            pady=10,
            takefocus=True,
        )
        open_button.pack(side="left", padx=(0, 12))
        if not URL_PATTERN.match(decoded.strip()):
            open_button.configure(state="disabled")

        send_button = tk.Button(
            button_row,
            text="Send to phone",
            font=("Segoe UI", 15, "bold"),
            command=lambda: self.start_phone_handoff(decoded),
            padx=18,
            pady=10,
            takefocus=True,
        )
        send_button.pack(side="left", padx=(0, 12))

        scan_another_button = tk.Button(
            secondary_button_row,
            text="Scan another QR code",
            font=("Segoe UI", 15, "bold"),
            command=self.close_preview,
            padx=18,
            pady=10,
            takefocus=True,
        )
        scan_another_button.pack(side="left", padx=(0, 12))

        cancel_button = tk.Button(
            secondary_button_row,
            text="Cancel",
            font=("Segoe UI", 15),
            command=self.close_preview,
            padx=18,
            pady=10,
            takefocus=True,
        )
        cancel_button.pack(side="left")

        status = tk.Label(
            container,
            text=(
                "Opening is available only for http:// or https:// links."
                if not URL_PATTERN.match(decoded.strip())
                else "The link will open only if you press the button."
            ),
            font=("Segoe UI", 12),
            bg="#ffffff",
            fg="#333333",
        )
        status.pack(anchor="w", pady=(14, 0))

        window.bind("<Escape>", lambda _event: self.close_preview())
        window.bind("<Control-c>", lambda _event: self.copy_result(window))
        copy_button.focus_set()

    def close_preview(self) -> None:
        self.result_text = None
        if self.preview_window is not None and self.preview_window.winfo_exists():
            self.preview_window.destroy()
        self.preview_window = None
        self.root.deiconify()
        self.root.lift()
        if self.select_button is not None:
            self.select_button.focus_set()

    def start_phone_handoff(self, decoded: str) -> None:
        # The server is already listening (bound at launch). This only creates
        # a fresh one-time code for the current result and shows the window —
        # it never re-binds. Works repeatedly across multiple decodes.
        url = self.handoff_server.url
        if url is None:
            messagebox.showerror(
                "QR Bridge",
                (
                    f"The phone handoff server is not running (port {HANDOFF_PORT} may be "
                    "in use by another program). Restart QR Bridge to try again."
                ),
            )
            return

        code = self.handoff_store.create(decoded)
        link = f"{url}r?code={code}"
        self.show_handoff_window(url, code, link)

    def show_handoff_window(self, url: str, code: str, link: str) -> None:
        window = tk.Toplevel(self.root)
        window.title("Send to phone")
        window.geometry("620x430")
        window.minsize(520, 360)
        window.configure(bg="#ffffff")
        window.transient(self.preview_window or self.root)
        window.grab_set()

        container = tk.Frame(window, bg="#ffffff", padx=24, pady=24)
        container.pack(fill="both", expand=True)

        heading = tk.Label(
            container,
            text="Send to phone",
            font=("Segoe UI", 22, "bold"),
            bg="#ffffff",
            fg="#111111",
        )
        heading.pack(anchor="w")

        instructions = tk.Label(
            container,
            text=(
                "On your phone, open this link. Your result appears on its own — "
                "no code entry needed. Your phone must be on the same Wi-Fi or "
                "local network."
            ),
            font=("Segoe UI", 13),
            bg="#ffffff",
            fg="#222222",
            wraplength=560,
            justify="left",
        )
        instructions.pack(anchor="w", pady=(12, 16))

        tk.Label(
            container,
            text="Phone link",
            font=("Segoe UI", 12, "bold"),
            bg="#ffffff",
            fg="#111111",
        ).pack(anchor="w")
        link_entry = tk.Entry(container, font=("Segoe UI", 14))
        link_entry.pack(fill="x", pady=(4, 14))
        link_entry.insert(0, link)
        link_entry.configure(state="readonly")

        tk.Label(
            container,
            text="One-time code (only needed if you type the address by hand)",
            font=("Segoe UI", 12, "bold"),
            bg="#ffffff",
            fg="#111111",
        ).pack(anchor="w")
        code_entry = tk.Entry(container, font=("Segoe UI", 20, "bold"), justify="center")
        code_entry.pack(fill="x", pady=(4, 14))
        code_entry.insert(0, code)
        code_entry.configure(state="readonly")

        note = tk.Label(
            container,
            text=(
                "This handoff expires after 5 minutes. The decoded QR result is kept "
                "in memory only and is removed after the code is used or expires."
            ),
            font=("Segoe UI", 12),
            bg="#ffffff",
            fg="#333333",
            wraplength=560,
            justify="left",
        )
        note.pack(anchor="w", pady=(0, 18))

        button_row = tk.Frame(container, bg="#ffffff")
        button_row.pack(fill="x")

        copy_details_button = tk.Button(
            button_row,
            text="Copy phone link",
            font=("Segoe UI", 13, "bold"),
            command=lambda: self.copy_handoff_details(link, code, window),
            padx=14,
            pady=9,
            takefocus=True,
        )
        copy_details_button.pack(side="left", padx=(0, 12))

        close_button = tk.Button(
            button_row,
            text="Close",
            font=("Segoe UI", 13),
            command=window.destroy,
            padx=14,
            pady=9,
            takefocus=True,
        )
        close_button.pack(side="right")

        window.bind("<Escape>", lambda _event: window.destroy())
        link_entry.focus_set()

    def copy_handoff_details(self, link: str, code: str, window: tk.Toplevel) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(f"QR Bridge phone link: {link}\nOne-time code: {code}")
        self.root.update()
        window.bell()

    def copy_result(self, window: tk.Toplevel) -> None:
        if self.result_text is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.result_text)
        self.root.update()
        window.bell()

    def open_result(self, decoded: str) -> None:
        text = decoded.strip()
        if URL_PATTERN.match(text):
            webbrowser.open(text, new=2)

    def open_settings(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("QR Bridge settings")
        window.geometry("560x320")
        window.minsize(480, 280)
        window.configure(bg="#ffffff")
        window.transient(self.root)
        window.grab_set()

        container = tk.Frame(window, bg="#ffffff", padx=24, pady=24)
        container.pack(fill="both", expand=True)

        tk.Label(
            container,
            text="Settings",
            font=("Segoe UI", 22, "bold"),
            bg="#ffffff",
            fg="#111111",
        ).pack(anchor="w")

        self.autostart_var = tk.BooleanVar(value=is_autostart_enabled())
        autostart_check = tk.Checkbutton(
            container,
            text="Start QR Bridge when I log in",
            variable=self.autostart_var,
            command=self._on_autostart_toggle,
            font=("Segoe UI", 15),
            bg="#ffffff",
            fg="#111111",
            activebackground="#ffffff",
            selectcolor="#ffffff",
            anchor="w",
            takefocus=True,
        )
        autostart_check.pack(anchor="w", pady=(20, 0))

        tk.Label(
            container,
            text=(
                "When on, QR Bridge starts automatically each time you log in and "
                "opens quietly to the notification tray (bottom-right of the "
                "taskbar) so your phone can reach it without opening a window. It "
                "adds a shortcut to your Windows Startup folder, which you can "
                "remove here or from Startup Apps at any time."
            ),
            font=("Segoe UI", 12),
            bg="#ffffff",
            fg="#333333",
            wraplength=500,
            justify="left",
        ).pack(anchor="w", pady=(10, 0))

        close_button = tk.Button(
            container,
            text="Close",
            font=("Segoe UI", 13, "bold"),
            command=window.destroy,
            padx=16,
            pady=9,
            takefocus=True,
        )
        close_button.pack(anchor="w", pady=(24, 0))

        window.bind("<Escape>", lambda _event: window.destroy())
        autostart_check.focus_set()

    def _on_autostart_toggle(self) -> None:
        want_enabled = self.autostart_var.get()
        try:
            if want_enabled:
                enable_autostart()
            else:
                disable_autostart()
        except Exception as exc:
            messagebox.showerror(
                "QR Bridge",
                f"Could not update the login startup setting.\n\n{exc}",
            )
            # Reflect what actually happened on disk, not the failed intent.
            self.autostart_var.set(is_autostart_enabled())
            return

        self.settings["autostart"] = want_enabled
        try:
            save_settings(self.settings)
        except OSError:
            # The shortcut is the source of truth; a failed settings write is
            # not worth interrupting the user over.
            pass

    def run(self) -> None:
        self.root.mainloop()

    def close_app(self) -> None:
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        self.handoff_server.stop()
        self.root.destroy()


class HandoffStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items = {}

    def create(self, value: str) -> str:
        self.cleanup()
        alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        with self._lock:
            while True:
                code = "".join(secrets.choice(alphabet) for _ in range(6))
                if code not in self._items:
                    break
            self._items[code] = {
                "value": value,
                "expires_at": time.time() + HANDOFF_EXPIRY_SECONDS,
            }

        expiry_timer = threading.Timer(HANDOFF_EXPIRY_SECONDS + 1, self.cleanup)
        expiry_timer.daemon = True
        expiry_timer.start()
        return code

    def consume(self, code: str) -> Optional[str]:
        normalized_code = code.strip().upper().replace(" ", "")
        now = time.time()
        with self._lock:
            item = self._items.pop(normalized_code, None)
            if item is None:
                return None
            if item["expires_at"] < now:
                return None
            return item["value"]

    def cleanup(self) -> None:
        now = time.time()
        with self._lock:
            expired_codes = [
                code
                for code, item in self._items.items()
                if item["expires_at"] < now
            ]
            for code in expired_codes:
                self._items.pop(code, None)


class PhoneHandoffServer:
    def __init__(self, store: HandoffStore) -> None:
        self.store = store
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.url: Optional[str] = None

    def start(self) -> str:
        if self.httpd is not None and self.url is not None:
            return self.url

        handler = build_handoff_handler(self.store)
        self.httpd = ThreadingHTTPServer(("", HANDOFF_PORT), handler)
        self.httpd.daemon_threads = True
        port = self.httpd.server_address[1]
        self.url = f"http://{get_local_network_ip()}:{port}/"

        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self.url

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.httpd = None
        self.thread = None
        self.url = None


def build_handoff_handler(store: HandoffStore):
    class HandoffRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            static = STATIC_FILES.get(path)
            if static is not None:
                file_name, content_type = static
                try:
                    content = resource_path(file_name).read_bytes()
                except OSError:
                    self.send_bytes(b"Not found", "text/plain; charset=utf-8", status=404)
                    return
                self.send_bytes(content, content_type)
                return
            if path.startswith("/cancel"):
                self.send_html(render_message_page("Handoff cancelled."))
                return
            if path == "/r":
                page = load_result_page()
                if page is None:
                    self.send_html(
                        render_message_page(
                            "The result page is missing on this computer. "
                            "You can still use the code form instead.",
                            show_try_again=True,
                        ),
                        status=500,
                    )
                    return
                self.send_html(page)
                return
            self.send_html(render_code_form())

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/handoff":
                self.handle_api_handoff()
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length).decode("utf-8", errors="replace")
            form = parse_qs(raw_body)
            code = form.get("code", [""])[0]
            decoded_value = store.consume(code)

            if decoded_value is None:
                self.send_html(
                    render_message_page(
                        "Code is wrong, expired, or already used.",
                        show_try_again=True,
                    ),
                    status=400,
                )
                return

            self.send_html(render_result_page(decoded_value))

        def handle_api_handoff(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length).decode("utf-8", errors="replace")

            try:
                payload = json.loads(raw_body)
            except json.JSONDecodeError:
                self.send_json(
                    {
                        "ok": False,
                        "error": "malformed_request",
                        "message": "Request could not be read. Send JSON like {\"code\": \"ABC123\"}.",
                    },
                    status=400,
                )
                return

            if not isinstance(payload, dict) or not isinstance(payload.get("code"), str):
                self.send_json(
                    {
                        "ok": False,
                        "error": "malformed_request",
                        "message": "Request must include a one-time code.",
                    },
                    status=400,
                )
                return

            decoded_value = store.consume(payload["code"])
            if decoded_value is None:
                self.send_json(
                    {
                        "ok": False,
                        "error": "wrong_expired_or_used",
                        "message": "Code is wrong, expired, or already used.",
                    },
                    status=400,
                )
                return

            self.send_json({"ok": True, "result": decoded_value})

        def send_html(self, content: str, status: int = 200) -> None:
            encoded = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def send_bytes(self, content: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            # Icons and the manifest may cache: they carry no QR data and
            # caching keeps the home-screen icon durable.
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(content)

        def send_json(self, payload: dict, status: int = 200) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args) -> None:
            return

    return HandoffRequestHandler


def render_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f5f5f5;
      color: #111;
      font-size: 20px;
      line-height: 1.45;
    }}
    main {{
      max-width: 720px;
      margin: 0 auto;
      padding: 24px;
    }}
    h1 {{
      font-size: 32px;
      margin: 0 0 16px;
    }}
    label {{
      display: block;
      font-weight: 700;
      margin: 18px 0 8px;
    }}
    input, textarea {{
      box-sizing: border-box;
      width: 100%;
      font: inherit;
      padding: 14px;
      border: 2px solid #555;
      border-radius: 6px;
      background: #fff;
    }}
    textarea {{
      min-height: 180px;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    .button-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 18px;
    }}
    button, .button {{
      appearance: none;
      border: 2px solid #222;
      border-radius: 6px;
      background: #fff;
      color: #111;
      font: inherit;
      font-weight: 700;
      padding: 12px 16px;
      text-decoration: none;
    }}
    .primary {{
      background: #111;
      color: #fff;
    }}
    .warning {{
      background: #fff3cd;
      border-left: 6px solid #a66d00;
      padding: 12px 14px;
      margin: 16px 0;
    }}
    .disabled {{
      opacity: 0.55;
    }}
  </style>
</head>
<body>
  <main>{body}</main>
</body>
</html>"""


def load_result_page() -> Optional[str]:
    try:
        return RESULT_PAGE_FILE.read_text(encoding="utf-8")
    except OSError:
        return None


def render_code_form() -> str:
    body = """<h1>QR Bridge</h1>
<p>Enter the one-time code shown on your computer.</p>
<p>This handoff expires after 5 minutes and works only on the same Wi-Fi or local network.</p>
<form method="post" action="/">
  <label for="code">One-time code</label>
  <input id="code" name="code" autocomplete="one-time-code" inputmode="text" autofocus required>
  <div class="button-row">
    <button class="primary" type="submit">Show QR result</button>
    <a class="button" href="/cancel">Cancel</a>
  </div>
</form>"""
    return render_page("QR Bridge phone handoff", body)


def render_result_page(decoded_value: str) -> str:
    escaped_value = html.escape(decoded_value)
    is_link = URL_PATTERN.match(decoded_value.strip()) is not None
    open_control = (
        f'<a class="button primary" href="{html.escape(decoded_value.strip())}" target="_blank" rel="noopener noreferrer">Open</a>'
        if is_link
        else '<button class="disabled" type="button" disabled>Open</button>'
    )
    body = f"""<h1>QR result</h1>
<p class="warning">Review this before opening. QR codes can contain login or security links.</p>
<label for="result">Decoded QR result</label>
<textarea id="result" readonly>{escaped_value}</textarea>
<div class="button-row">
  {open_control}
  <button type="button" onclick="copyResult()">Copy</button>
  <a class="button" href="/cancel">Cancel</a>
</div>
<script>
function copyResult() {{
  const result = document.getElementById('result');
  result.focus();
  result.select();
  result.setSelectionRange(0, result.value.length);
  if (navigator.clipboard) {{
    navigator.clipboard.writeText(result.value).catch(function () {{
      document.execCommand('copy');
    }});
  }} else {{
    document.execCommand('copy');
  }}
}}
</script>"""
    return render_page("QR Bridge result", body)


def render_message_page(message: str, show_try_again: bool = False) -> str:
    retry_link = '<a class="button" href="/">Try again</a>' if show_try_again else ""
    body = f"""<h1>QR Bridge</h1>
<p>{html.escape(message)}</p>
<div class="button-row">{retry_link}</div>"""
    return render_page("QR Bridge", body)


def get_local_network_ip() -> str:
    try:
        hostname = socket.gethostname()
        _name, _aliases, addresses = socket.gethostbyname_ex(hostname)
        for address in addresses:
            if not address.startswith("127."):
                return address
    except OSError:
        pass
    return "127.0.0.1"


class AreaSelector:
    def __init__(self, parent: tk.Tk, screenshot: Image.Image, monitor: dict) -> None:
        self.parent = parent
        self.screenshot = screenshot
        self.monitor = monitor
        self.selection: Optional[Selection] = None
        self.start_x = 0
        self.start_y = 0
        self.rect_id: Optional[int] = None

        self.window = tk.Toplevel(parent)
        self.window.title("Select QR code area")
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.geometry(
            format_geometry(
                monitor["width"],
                monitor["height"],
                monitor["left"],
                monitor["top"],
            )
        )

        self.canvas = tk.Canvas(
            self.window,
            width=monitor["width"],
            height=monitor["height"],
            highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True)

        self.photo = ImageTk.PhotoImage(screenshot)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.create_rectangle(
            0,
            0,
            monitor["width"],
            54,
            fill="#111111",
            outline="",
        )
        self.canvas.create_text(
            18,
            27,
            text="Drag a box around the QR code. Release to scan. Press Esc to cancel.",
            anchor="w",
            fill="#ffffff",
            font=("Segoe UI", 18, "bold"),
        )

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.window.bind("<Escape>", self._on_cancel)
        self.window.focus_force()

    def select(self) -> Optional[Selection]:
        self.parent.wait_window(self.window)
        return self.selection

    def _on_press(self, event: tk.Event) -> None:
        self.start_x = int(event.x)
        self.start_y = int(event.y)
        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            self.start_x,
            self.start_y,
            self.start_x,
            self.start_y,
            outline="#00a6ff",
            width=4,
        )

    def _on_drag(self, event: tk.Event) -> None:
        if self.rect_id is None:
            return
        x = max(0, min(int(event.x), self.monitor["width"]))
        y = max(0, min(int(event.y), self.monitor["height"]))
        self.canvas.coords(self.rect_id, self.start_x, self.start_y, x, y)

    def _on_release(self, event: tk.Event) -> None:
        x = max(0, min(int(event.x), self.monitor["width"]))
        y = max(0, min(int(event.y), self.monitor["height"]))
        selection = Selection(self.start_x, self.start_y, x, y).normalized()

        if selection.width < MIN_SELECTION_SIZE or selection.height < MIN_SELECTION_SIZE:
            self.selection = None
        else:
            self.selection = selection
        self.window.destroy()

    def _on_cancel(self, _event: tk.Event) -> None:
        self.selection = None
        self.window.destroy()


class QRNotFoundError(Exception):
    pass


class QRUnreadableError(Exception):
    pass


class MultipleQRCodesError(Exception):
    pass


def decode_qr_image(image: Image.Image) -> str:
    detector = cv2.QRCodeDetector()
    rgb = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    decoded_values = []
    saw_unreadable_qr = False

    for candidate in build_decode_candidates(rgb, gray):
        try:
            values, saw_qr = try_decode_candidate(detector, candidate)
        except cv2.error:
            saw_unreadable_qr = True
            continue
        saw_unreadable_qr = saw_unreadable_qr or saw_qr

        for value in values:
            if value not in decoded_values:
                decoded_values.append(value)
            if len(decoded_values) > 1:
                raise MultipleQRCodesError()

    if len(decoded_values) == 1:
        return decoded_values[0]
    if saw_unreadable_qr:
        raise QRUnreadableError()
    raise QRNotFoundError()


def build_decode_candidates(rgb: np.ndarray, gray: np.ndarray) -> list:
    candidates = []

    def add_candidate(candidate: np.ndarray) -> None:
        candidates.append(candidate)
        candidates.append(add_white_border(candidate))

    add_candidate(rgb)
    add_candidate(gray)

    reduced = shrink_if_large(gray)
    if reduced is not gray:
        add_candidate(reduced)

    for scale in (2, 3, 4):
        enlarged = upscale_if_reasonable(gray, scale)
        if enlarged is not None:
            add_candidate(enlarged)

    add_candidate(cv2.equalizeHist(gray))

    _unused_threshold, otsu = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )
    add_candidate(otsu)

    adaptive = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        2,
    )
    add_candidate(adaptive)

    inverted = cv2.bitwise_not(otsu)
    add_candidate(inverted)

    return candidates


def try_decode_candidate(detector: cv2.QRCodeDetector, candidate: np.ndarray) -> tuple:
    decoded_values = []
    saw_qr = False

    found, decoded_info, _points, _straight = detector.detectAndDecodeMulti(candidate)
    if found:
        saw_qr = True
        if len(decoded_info) > 1:
            raise MultipleQRCodesError()
        decoded_values.extend(value for value in decoded_info if value)

    decoded, points, _straight = detector.detectAndDecode(candidate)
    if decoded:
        decoded_values.append(decoded)
    elif points is not None:
        saw_qr = True

    unique_values = []
    for value in decoded_values:
        if value not in unique_values:
            unique_values.append(value)

    return unique_values, saw_qr


def upscale_if_reasonable(image: np.ndarray, scale: int) -> Optional[np.ndarray]:
    if max(image.shape[0], image.shape[1]) * scale > 3000:
        return None
    width = image.shape[1] * scale
    height = image.shape[0] * scale
    return cv2.resize(image, (width, height), interpolation=cv2.INTER_CUBIC)


def shrink_if_large(image: np.ndarray) -> np.ndarray:
    largest_side = max(image.shape[0], image.shape[1])
    if largest_side <= 1200:
        return image

    scale = 1200 / largest_side
    width = max(1, int(image.shape[1] * scale))
    height = max(1, int(image.shape[0] * scale))
    return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)


def add_white_border(image: np.ndarray) -> np.ndarray:
    shortest_side = min(image.shape[0], image.shape[1])
    border = max(16, shortest_side // 8)
    border_color = [255, 255, 255] if image.ndim == 3 else 255
    return cv2.copyMakeBorder(
        image,
        border,
        border,
        border,
        border,
        cv2.BORDER_CONSTANT,
        value=border_color,
    )


def looks_security_sensitive(text: str) -> bool:
    lowered = text.strip().lower()
    if any(term in lowered for term in SENSITIVE_QR_TERMS):
        return True
    if any(marker in lowered for marker in (".gov.", ".gov/", ".gov?", ".gov.au", ".gov.uk", ".govt.nz")):
        return True

    parsed_url = urlparse(lowered)
    hostname = parsed_url.hostname or ""
    return (
        hostname.endswith(".gov")
        or ".gov." in hostname
        or hostname.endswith(".gov.au")
        or hostname.endswith(".gov.uk")
        or hostname.endswith(".govt.nz")
    )


def format_geometry(width: int, height: int, left: int, top: int) -> str:
    x_position = f"+{left}" if left >= 0 else str(left)
    y_position = f"+{top}" if top >= 0 else str(top)
    return f"{width}x{height}{x_position}{y_position}"


def main() -> None:
    start_minimized = any(flag in sys.argv[1:] for flag in MINIMIZED_FLAGS)
    try:
        app = QRBridgeApp(start_minimized=start_minimized)
        app.run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
