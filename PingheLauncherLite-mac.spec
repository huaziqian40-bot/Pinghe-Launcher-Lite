# -*- mode: python ; coding: utf-8 -*-
# macOS 构建: 产物为 dist/Pinghe Launcher Lite.app
# 用法(在 Mac 上): python -m PyInstaller --noconfirm --clean PingheLauncherLite-mac.spec

# 版本号从源码读，不在 spec 里硬编码 —— 之前写死成 '1.1.0'，
# 之后每次 bump 都忘了改，导致包里的版本号一直停在 1.1.0。
# （更新判断用的是 hellopinghe/updater.py 的 APP_VERSION，但关于面板/系统
#   显示的是 Info.plist，两者不一致会让用户以为没更新成功。）
def _app_version() -> str:
    import re
    from pathlib import Path

    src = Path(SPECPATH) / 'hellopinghe' / 'updater.py'
    m = re.search(r'APP_VERSION\s*=\s*"([\d.]+)"', src.read_text(encoding='utf-8'))
    if not m:
        raise SystemExit('✗ 读不到 hellopinghe/updater.py 里的 APP_VERSION')
    return m.group(1)


APP_VERSION = _app_version()
print(f'[spec] 打包版本号 = {APP_VERSION}')

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
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
    },
)
