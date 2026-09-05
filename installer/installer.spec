# -*- mode: python ; coding: utf-8 -*-
# 安装程序打包: 需要先构建好 dist/HelloPingheLauncher.exe(作为要安装的应用)
# 用法: python -m PyInstaller --noconfirm --clean installer/installer.spec
# 产物: dist/HPHLSetup.exe(双击运行, 自动请求管理员权限)

a = Analysis(
    ['installer.py'],
    pathex=[],
    binaries=[],
    datas=[('../dist/HelloPingheLauncher.exe', '.')],
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
    a.binaries,
    a.datas,
    [],
    name='HPHLSetup',
    icon='..\\logo.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,      # 写 Program Files/注册表需要管理员权限
)
