# -*- mode: python ; coding: utf-8 -*-
# macOS 构建: 产物为 dist/Hello Pinghe! Launcher.app
# 用法(在 Mac 上): python -m PyInstaller --noconfirm --clean HelloPingheLauncher-mac.spec

a = Analysis(
    ['run_hellopinghe.py'],
    pathex=[],
    binaries=[],
    datas=[('ui', 'ui')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='HelloPingheLauncher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='HelloPingheLauncher',
)
app = BUNDLE(
    coll,
    name='Hello Pinghe! Launcher.app',
    icon='logo.icns',   # 由 scripts/macos_build.py 在 Mac 上用 sips+iconutil 生成
    info_plist={
        'CFBundleName': 'Hello Pinghe! Launcher',
        'CFBundleDisplayName': 'Hello Pinghe! Launcher',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
    },
)
