# PyInstaller build spec for QR Bridge.
#
# Build with:  pyinstaller --noconfirm qr_bridge.spec
# Produces a single-file, windowed (no console) .exe at dist/QR Bridge.exe.
#
# The data files below are the assets the app serves from disk at runtime
# (the phone result page, web manifest, and home-screen icons). They are
# bundled at the root of the PyInstaller temp folder; qr_bridge.resource_path()
# resolves them from sys._MEIPASS when frozen.

block_cipher = None

datas = [
    ("qr-bridge-result.html", "."),
    ("manifest.webmanifest", "."),
    ("icon-192.png", "."),
    ("icon-512.png", "."),
    ("apple-touch-icon.png", "."),
]

a = Analysis(
    ["qr_bridge.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="QR Bridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # --windowed: no console window pops up
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.ico",
)
