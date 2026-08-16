# -*- mode: python ; coding: utf-8 -*-


block_cipher = pyi_crypto.PyiBlockCipher(key='0b93149847b09854c5e98c28741307de')


a = Analysis(
    ['C:\\Users\\22501\\PycharmProjects\\pythonProject\\机器学习\\红龙贼计算器\\main.py'],
    pathex=[],
    binaries=[('C:\\Users\\22501\\PycharmProjects\\pythonProject\\机器学习\\红龙贼计算器\\red_dragon_engine.exe', '.'), ('D:\\Anaconda\\Library\\bin\\libexpat.dll', '.')],
    datas=[('C:\\Users\\22501\\PycharmProjects\\pythonProject\\机器学习\\红龙贼计算器\\card_id_map.json', '.'), ('C:\\Users\\22501\\PycharmProjects\\pythonProject\\机器学习\\红龙贼计算器\\dbf_id_map.json', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pkg_resources', 'platformdirs'],
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
