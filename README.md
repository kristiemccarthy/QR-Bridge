# QR Bridge

**Scan a QR code without lifting your phone.**

QR Bridge reads a QR code straight off your computer screen and sends the
result to your phone. No camera, no aiming, no steadying your hand. You stay
in control the whole way: you always see what the code says before anything
opens.

This was built for anyone who finds it hard to pick up a phone, aim a camera
at a screen, and hold it steady while it scans. That includes people with
limited arm or hand movement, people who use a wheelchair, people with
tremor, and anyone who is simply tired of asking someone else to scan a code
for them.

## What it does

1. A QR code appears on your screen.
2. You open QR Bridge and select the area with the code in it.
3. QR Bridge reads the code and shows you what it says.
4. You choose: send it to your phone, copy it, open it, or cancel.

That is the whole tool. Nothing is stored. There are no accounts. Nothing is
sent anywhere except to your own phone.

## Download

**[Download QR Bridge for Windows](https://github.com/kristiemccarthy/QR-Bridge/releases/download/v0.1.0/QR.Bridge.exe)**

1. Click the link above.
2. Save the file.
3. Double-click it to open QR Bridge. No installation, no setup.

**The first time you open it**, Windows will show a blue screen that says
"Windows protected your PC." This happens because the app isn't registered
with Microsoft, not because anything is wrong. To open it anyway:
- Click **More info**
- Click **Run anyway**

You only need to do this once.

## Using it on your phone

The first time, open the link QR Bridge gives you in your phone's browser.
**Tap the link rather than typing it from scratch.** If you type the
address by hand and leave out the `http://` part, some browsers (including
Chrome) will search the web for it instead of opening it.

Once the page opens, save it to your home screen so you don't need to do
this again:

- **iPhone (Safari):** tap the Share button, then "Add to Home Screen."
- **Android (Chrome):** tap the three-dot menu, then "Add to Home screen."

After that, just tap the QR Bridge icon on your home screen. You'll only
need to enter the short one-time code from the desktop app each time you
use it. There's nothing else to type.

Each one-time code lasts 5 minutes. If it doesn't work, go back to the
desktop app and choose "Send to phone" again to get a new one.

## If QR Bridge can't read a code

Sometimes a code won't read. QR Bridge tells you what happened in plain
words:

- **No QR code found** — there was no code in the area you selected. Try
  again, and include the whole code plus a little of the white space around
  it.
- **QR code could not be read** — there was a code, but it wasn't clear
  enough. This usually means it's blurry, very small, or low contrast.
  Making it bigger on your screen before you select it often fixes this.
- **Multiple QR codes found** — there was more than one code in the area
  you selected. Select a smaller area with just one code in it.

Plain black-and-white codes read the most reliably. Codes that are
stylised, have a logo in the middle, or use unusual dot shapes are harder
to read, and some won't read at all. That's a limit of the code itself, not
a fault in QR Bridge.

## If sending to your phone doesn't work

This is usually a network issue, not a problem with QR Bridge itself.

- Your computer and phone need to be on the same Wi-Fi network.
- Windows may ask for firewall permission the first time you use "Send to
  phone." Choose **Allow**.
- If it still doesn't work, check that your Wi-Fi network allows devices to
  see each other (some public and guest networks block this on purpose).
- If a saved home-screen shortcut that used to work suddenly says it can't
  reach your computer, your computer's network address may have changed.
  Open QR Bridge on the computer and choose "Send to phone" again, then
  save a fresh shortcut using the new link.

## Keyboard shortcuts

- **Enter** — confirm
- **Esc** — cancel
- **Ctrl+C** — copy the result

## Before you scan certain QR codes

Take extra care with QR codes used for logging in, government services, or
proving your identity. QR Bridge shows you exactly what a code contains
before anything opens, so always read that preview before choosing Open.

## Your privacy

- Nothing you scan is stored after you're done with it. (QR Bridge saves
  one small settings file for your auto-start preference, nothing else.)
- There is no account to make and nothing to sign in to.
- QR Bridge does not track what you scan.
- You always see the decoded result before anything opens.

## Licence

QR Bridge is free to use, change, and share, under MIT with the Commons
Clause. That means anyone can use it or build on it, but nobody can sell it
or turn it into a paid product without contacting the original author
first.

---

## For developers

If you want to run QR Bridge from source or contribute to it, see the
sections below.

### Install

1. Install Python for Windows from [python.org](https://www.python.org/downloads/windows/).
2. During installation, tick the box that says **Add python.exe to PATH**.
3. Open PowerShell in this folder.
4. Create a virtual environment:

```powershell
python -m venv .venv
```

5. Turn it on:

```powershell
.\.venv\Scripts\Activate.ps1
```

6. Install the needed packages:

```powershell
python -m pip install -r requirements.txt
```

### Run

In PowerShell, with the virtual environment turned on, run:

```powershell
python qr_bridge.py
```

The app window will open.

### Developer Note: Handoff API

The local handoff server supports two ways to receive a QR result:

- the existing browser form at the phone URL
- a JSON endpoint for a future mobile companion app

The JSON endpoint is:

```text
POST /api/handoff
```

Example request:

```json
{
  "code": "ABC123"
}
```

Example success response:

```json
{
  "ok": true,
  "result": "https://example.com"
}
```

Example error response:

```json
{
  "ok": false,
  "error": "wrong_expired_or_used",
  "message": "Code is wrong, expired, or already used."
}
```

This endpoint is still local-network only. QR handoff contents stay in memory only and are removed after successful use or expiry.
