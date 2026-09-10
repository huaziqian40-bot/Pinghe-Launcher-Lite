# -*- mode: python ; coding: utf-8 -*-
# macOS 构建: 产物为 dist/Pinghe Launcher Lite.app
# 用法(在 Mac 上): python -m PyInstaller --noconfirm --clean PingheLauncherLite-mac.spec

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
    name='PingheLauncherLite',
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
    name='PingheLauncherLite',
)
app = BUNDLE(
    coll,
    name='Pinghe Launcher Lite.app',
    icon='logo.icns',   # 由 scripts/macos_build.py 在 Mac 上用 sips+iconutil 生成
    info_plist={
        'CFBundleName': 'Pinghe Launcher Lite',
        'CFBundleDisplayName': 'Pinghe Launcher Lite',
        'CFBundleShortVersionString': '1.1.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
    },
)
