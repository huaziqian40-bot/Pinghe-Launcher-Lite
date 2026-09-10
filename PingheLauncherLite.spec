# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run_hellopinghe.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('ui', 'ui'),
        ('tools/WebView2/MicrosoftEdgeWebview2Setup.exe', '.'),
    ],
    hiddenimports=[
        # 托盘图标: pystray 按平台动态导入后端, PyInstaller 静态分析看不到
        'pystray', 'pystray._win32', 'pystray._base',
        # settings.yaml: PyYAML 在函数体内延迟导入, 显式声明更保险
        'yaml',
    ],
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
    name='PingheLauncherLite',
    icon='logo.ico',
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
)
