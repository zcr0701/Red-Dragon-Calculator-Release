# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:/Users/22501/PycharmProjects/pythonProject/机器学习/红龙贼计算器/main.py'],
    pathex=[],
    binaries=[('C:/Users/22501/PycharmProjects/pythonProject/机器学习/红龙贼计算器/red_dragon_engine.exe', '.')],
    datas=[('C:/Users/22501/PycharmProjects/pythonProject/机器学习/红龙贼计算器/card_id_map.json', '.'), ('C:/Users/22501/PycharmProjects/pythonProject/机器学习/红龙贼计算器/dbf_id_map.json', '.')],
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
    name='红龙贼计算器',
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
