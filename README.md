# QR Bridge

QR Bridge is a small Windows desktop prototype for decoding a QR code that is already on your computer screen.

It is designed for people who cannot easily lift, aim, or steady a phone camera.

## What QR Bridge Does

- Lets you drag a rectangle around part of your screen.
- Looks for a QR code inside that selected area.
- Shows the decoded result in a preview window.
- Lets you choose Copy, Open on this computer, Send to phone, Scan another QR code, or Cancel.
- Can send one decoded QR result to a phone on the same Wi-Fi or local network using a temporary one-time code.

QR Bridge does not upload screenshots or QR contents anywhere. It does not save QR history. It does not open links automatically.

## Install

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

## Run

In PowerShell, with the virtual environment turned on, run:

```powershell
python qr_bridge.py
```

The app window will open.

## How To Use

1. Put a QR code on your screen.
2. In QR Bridge, choose **Select area of screen**.
3. Drag a box around the QR code.
4. Release the mouse button.
5. Review the result in the preview window.
6. Choose:
   - **Copy** to copy the result.
   - **Open on this computer** to open a web link. This button only works for `http://` and `https://` links.
   - **Send to phone** to make a temporary local-network handoff.
   - **Scan another QR code** to close the preview and return to the main app.
   - **Cancel** to close the preview.

QR Bridge will never open a link unless you choose **Open on this computer**.

## Send To Phone

Use this only when your phone and computer are on the same Wi-Fi or local network.

1. Decode a QR code in QR Bridge.
2. In the preview window, choose **Send to phone**.
3. QR Bridge will show:
   - a phone URL
   - a one-time code
   - a note that your phone must be on the same Wi-Fi or local network
   - a note that the handoff expires after 5 minutes
4. On your phone, open the phone URL in a browser.
5. Enter the one-time code.
6. If the code is correct and has not expired, your phone will show the decoded QR result.
7. On your phone, choose:
   - **Open** to open the link on your phone. This only appears as an active button for `http://` and `https://` links.
   - **Copy** to copy the result.
   - **Cancel** to stop.

The phone page does not open links automatically. The phone must show Open, Copy, and Cancel before anything happens.

Each one-time code can be used once. It expires after 5 minutes. After it is used or expired, QR Bridge removes it from memory.

## Developer Note: Handoff API

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

## Keyboard Help

- Press **Enter** on the main window to start selecting an area.
- Press **Esc** during selection to cancel.
- Press **Ctrl+C** in the preview window to copy the decoded result.
- Press **Esc** in the preview window to close it.

## Test The App

You can test QR Bridge with any QR code image shown on your screen.

A simple test is:

1. Open a QR code image in your browser or image viewer.
2. Run QR Bridge.
3. Select the area around the QR code, including the white border around it if possible.
4. Check that the preview window shows the expected text or link.
5. Try selecting an empty part of the screen. You should see **No QR code found**.

Plain black-and-white QR codes are easiest to test first.

Stylised QR codes, QR codes with logos, blurry QR codes, or QR codes with unusual dot shapes may be harder to read.

If the QR code is blurry, too small, partly cut off, or has very low contrast, you may see **QR code could not be read**.

If you select an area that contains more than one readable QR code, you should see **Multiple QR codes found**.

## Privacy Notes

All work happens on your computer. The app captures only your screen locally, decodes locally, and forgets the result when you close the preview.

The app does not have any account, server, database, analytics, cloud storage, or QR history.

Avoid sharing screenshots or decoded links from real login, government, identity verification, authentication, or pairing QR codes. These can be private or security-sensitive.

Phone handoff contents are stored in memory only. QR Bridge does not write decoded QR contents to disk.

When you use **Send to phone**, QR Bridge starts a small temporary web server on your computer. It is meant for your local Wi-Fi or home/work network only. It does not create an internet-hosted page.

## Windows Firewall And Network Notes

Windows may ask whether Python is allowed to accept connections on your network. The phone handoff may not work unless you allow it for your private Wi-Fi or local network.

If your phone cannot open the phone URL:

- Check that the phone and computer are on the same Wi-Fi or local network.
- Check that the computer is not on a public or guest network that blocks device-to-device connections.
- Check whether Windows Firewall, antivirus software, VPN software, or workplace network rules are blocking local connections.
- Try again after closing and reopening QR Bridge, which will create a new temporary local server address.

## Licence

QR Bridge is MIT-licensed with the Commons Clause restriction — you are free to use, modify, and share it, but commercial sale or resale is not permitted (see [LICENSE](LICENSE)).
