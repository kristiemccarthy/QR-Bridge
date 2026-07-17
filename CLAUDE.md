# Project: QR Bridge (desktop)

## What this is
A local Python desktop app (Tkinter) that lets a user select an area of their
screen, decodes any QR code found in that area entirely on-device, previews
the result, and only opens the link if the user explicitly presses Open.
Built because camera-based QR scanning is inaccessible for some users —
this is a screen-based alternative.

## Core product principle
QR Bridge must not replace one inaccessible action with another inaccessible
action. Every feature decision gets checked against this.

## Architecture
- qr_bridge.py — main app: screen-area selection, local QR decode, preview,
  Copy/Open/Cancel controls, plain-language errors
- qr-bridge-result.html — phone-side web result page, served same-origin by
  the desktop server at GET /r. This is the PRIMARY phone path.
- manifest.webmanifest, icon-192.png, icon-512.png, apple-touch-icon.png —
  add-to-home-screen assets, served by the desktop server at those root paths
  (plus /apple-touch-icon-precomposed.png, which older iOS probes by default;
  it serves the same file)
- requirements.txt — opencv-python, mss, numpy, Pillow, pystray
  (pystray drives the system-tray icon; if it fails to import the app still
  runs and just falls back to a taskbar-minimised window)
- requirements-dev.txt — build-time extras (pulls in requirements.txt +
  pyinstaller); qr_bridge.spec + icon.ico are the packaging inputs
- Decode pipeline tries multiple candidates before failing: original, grayscale,
  downscaled, upscaled, histogram-equalised, Otsu threshold, adaptive threshold,
  inverted threshold, white-border versions of each, via both
  detectAndDecodeMulti and detectAndDecode
- Phone handoff: local web server started on demand on FIXED port 8765
  (HANDOFF_PORT in qr_bridge.py — fixed so a phone's saved home-screen link
  never drifts), one-time 6-character code, 5-minute expiry, in-memory only
  (never written to disk), code removed after use.
  ALWAYS-LISTEN — the server binds 8765 ONCE at app launch
  (_start_handoff_server, called from QRBridgeApp.__init__) and stays up for
  the whole session; it stops on app close (close_app -> stop()). Binding is
  separate from creating a handoff: "Send to phone" no longer binds — it only
  reads the already-live URL and calls handoff_store.create() to mint a fresh
  one-time code for the current result, so it works across repeated decodes
  with no re-bind. A cold tap on the phone's home-screen icon therefore
  reaches a live server and lands on the calm "enter your code" state, not
  "Can't reach your computer". If 8765 is already in use at launch, the plain-
  language port error shows and Send to phone is unavailable until restart.
  Trade-off: a local listener on 8765 is open the whole time the app runs, but
  it only exposes static files (/r, manifest, icons) until an active handoff
  exists — /api/handoff returns the normal invalid-code response for any code
  until "Send to phone" creates one.
  Testing (headless, no GUI): import PhoneHandoffServer + HandoffStore, call
  server.start() once (mirrors launch), then: GET /r, /manifest.webmanifest,
  /icon-192.png all 200 with no handoff; POST /api/handoff before any Send ->
  invalid-code (not a crash); store.create(result) to simulate a Send, POST
  that code -> ok/result, reuse -> invalid; a second store.create -> new code
  works with no re-bind. (See scratchpad harness_alwayslisten.py.)
  Phone paths (scope decision, 16 July 2026 — see the QR Bridge Notion page):
  1. PRIMARY — web result page at GET /r, normally launched from a saved
     home-screen shortcut (the primary entry point). "Send to phone" hands
     out a link of the form http://<desktop-ip>:8765/r?code=XXXXXX (address
     and code in the URL), so the page opens straight into the live state
     with no manual code entry. Opened without ?code= (e.g. from the
     home-screen icon), /r falls back to accessible manual code entry.
     "Address memory" is the home-screen shortcut plus the stable desktop
     address (fixed port; keep the desktop's IP stable, e.g. via a router
     DHCP reservation). localStorage is deliberately NOT used to remember
     the address: if the address has changed, a stored copy of the old one
     cannot rescue the connection — the page instead shows a plain-language
     "Can't reach your computer" state with manual code/address entry as
     the recovery path.
  2. Legacy browser form at GET / — kept for now, not yet removed.
  3. Native Expo app (qr-bridge-mobile) — kept as an OPTIONAL alternative
     path, not retired.
  JSON endpoint used by both the result page and the native app:
  POST /api/handoff with {"code": "ABC123"} →
  {"ok": true, "result": "..."} or {"ok": false, "error": "wrong_expired_or_used"}
  Codes are one-shot and created only after the result exists, so the endpoint
  has no "waiting" state — the first answer is either ready or expired.

## Packaging (Windows double-click .exe)
Build a single-file, windowed (no console) .exe so a non-technical user can
double-click to run QR Bridge without a Python install.

- Build:
  `pip install -r requirements-dev.txt`
  then `pyinstaller --noconfirm qr_bridge.spec`
- Output: `dist/QR Bridge.exe` (~70 MB — it bundles OpenCV/NumPy). `build/`
  and `dist/` are gitignored; the .exe is a local build artefact, not checked in.
- qr_bridge.spec is the build recipe: `console=False` (--windowed),
  `icon="icon.ico"` (generated from icon-512.png via Pillow), and it bundles
  the runtime data files (qr-bridge-result.html, manifest.webmanifest, the
  three icons, apple-touch-icon.png) at the root of the PyInstaller temp folder.
- sys._MEIPASS path handling: the app serves those data files from disk at
  runtime. `resource_path(name)` returns `Path(sys._MEIPASS)/name` when frozen
  (`getattr(sys,"frozen",False)`) and falls back to `Path(__file__).with_name(name)`
  when run as a normal script. Both `RESULT_PAGE_FILE` and the STATIC_FILES
  handler go through it, so `python qr_bridge.py` and the frozen .exe serve /r,
  the manifest, and the icons identically. Verified from the frozen .exe: /r,
  /, /manifest.webmanifest, /icon-192.png, /icon-512.png,
  /apple-touch-icon(-precomposed).png all 200; /api/handoff returns the normal
  invalid-code JSON before any Send. The server still binds 8765 when run frozen.

## Auto-start on logon + tray (packaging, this session)
- Settings dialog (Settings button on the main window) has one checkbox:
  "Start QR Bridge when I log in", off by default.
- ON creates a Startup-folder shortcut; OFF deletes it. Deliberately a
  **Startup-folder .lnk**, not a registry Run key, so a non-technical user can
  see and remove it (Startup Apps list / the Startup folder). Path:
  `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\QR Bridge.lnk`,
  written via PowerShell WScript.Shell (no extra Python dep). The shortcut
  targets the .exe with a `--minimized` argument; in a dev checkout it targets
  pythonw + qr_bridge.py so no console flashes at login.
- The choice is persisted in `%APPDATA%\QR Bridge\settings.json` (UI state
  only — never the handoff/one-time-code logic). The shortcut's existence is
  the source of truth for the checkbox; settings.json just mirrors it.
- `--minimized` (login launch) starts hidden to the system tray (pystray) — or
  iconified to the taskbar if pystray is unavailable — instead of popping a
  window. The always-listen server still binds 8765 on this silent launch
  (server start happens in __init__ before the withdraw), so the phone can
  reach it before anyone touches the desktop. Verified from the frozen .exe.
- Tray behaviour: when a tray icon is present, closing the window (or Esc)
  hides to tray and leaves the server bound; the tray menu has Open / Quit.
  Quit is the only path that stops the server and exits. Without pystray,
  closing quits as before.

## Conventions
- Local-only, always: no upload, no stored QR history, no analytics, no accounts
- Links never auto-open — only after user presses Open
- Plain-language error messages: "No QR code found", "QR code could not be read",
  "Multiple QR codes found"
- Beginner-readable code and README

## Status / where I left off
- Prototype 1: core desktop decode working
- Prototype 2: phone handoff via browser form working
- JSON /api/handoff endpoint added to support the mobile companion app
  without breaking the existing browser form
- Web result page (qr-bridge-result.html) added and served at /r; "Send to
  phone" now hands out the /r?code=XXXXXX link — this is the primary phone path
- Add-to-home-screen path added (16 July 2026): fixed port 8765, manifest +
  icons served from the desktop server, home-screen install is the primary
  entry point on the phone
- Server changed to always-listen (17 July 2026): binds 8765 at launch (not on
  "Send to phone"), so a cold home-screen tap reaches a live server; Send now
  only mints the one-time code
- Packaged as a Windows double-click .exe (17 July 2026): PyInstaller single-
  file --windowed build (qr_bridge.spec), sys._MEIPASS path handling for the
  served data files, Settings checkbox for auto-start-on-logon via a Startup-
  folder shortcut, and silent tray launch on `--minimized`. Frozen .exe route
  test + auto-start on/off both confirmed working (see Packaging sections).
- About to run first live end-to-end test: desktop decodes → Send to phone →
  phone opens the /r?code= link (web page path)

## Known issues / open questions
- Stylised/logo QR codes are harder to decode than plain black-and-white ones
  (expected and documented in the README, not a bug)
- Haven't yet confirmed the full desktop-to-phone handoff works live
- Known friction point: the native app still requires manually typing the
  desktop's local network address — a stepping-stone limitation, not final
  design. The web result page avoids this: the /r?code= link carries both
  address and code.

## Out of scope (explicitly rejected)
Firebase/Supabase, cloud relay, user login, analytics, camera-based scanning,
push notifications, pairing.
