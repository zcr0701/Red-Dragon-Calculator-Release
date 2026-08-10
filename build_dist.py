# -*- coding: utf-8 -*-
"""打包红龙贼计算器为可分发的单文件副本。

- 内嵌收款码（_embedded_assets.py，随代码一起被打入 PyInstaller 归档）；
- 记录 计算核心/卡牌数据 的 SHA256，运行时校验，防篡改/替换；
- PyInstaller onefile 打包，引擎 exe / 卡名映射 / 收款码全部打进单个 exe，源码编译进归档；
- 计算核心仍是原生 C++ exe，计算性能不受影响。
"""
import base64
import hashlib
import subprocess
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent
ENV_PY = r"D:\Anaconda\python.exe"
NAME = "红龙贼计算器"


def sha256_hex(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    py = Path(ENV_PY) if Path(ENV_PY).is_file() else Path(sys.executable)

    engine_exe = PROJ / "red_dragon_engine.exe"
    card_map = PROJ / "card_id_map.json"
    qr = PROJ / "收款码.png"

    if not engine_exe.is_file() or not card_map.is_file():
        print("错误：缺少 red_dragon_engine.exe 或 card_id_map.json")
        return 1

    engine_hash = sha256_hex(engine_exe)
    card_hash = sha256_hex(card_map)
    qr_b64 = base64.b64encode(qr.read_bytes()).decode("ascii") if qr.is_file() else ""

    assets = PROJ / "_embedded_assets.py"
    assets.write_text(
        "# -*- coding: utf-8 -*-\n"
        "# 打包时自动生成：内嵌收款码与关键文件校验哈希（随代码一起打入归档）。\n"
        f"EMBEDDED_QR_BASE64 = {qr_b64!r}\n"
        f"ENGINE_EXE_SHA256 = {engine_hash!r}\n"
        f"CARD_MAP_SHA256 = {card_hash!r}\n",
        encoding="utf-8",
    )
    print(
        "内嵌资产已生成:",
        assets.name,
        "| 收款码:",
        "已内嵌" if qr_b64 else "未提供（副本将显示占位，放入 收款码.png 后重新打包）",
    )

    cmd = [
        str(py),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--noconsole",
        "--onefile",
        "--name",
        NAME,
        # 引擎 exe 与卡名映射打进单文件，运行时解压到临时目录
        "--add-binary",
        f"{engine_exe};.",
        "--add-data",
        f"{card_map};.",
        str(PROJ / "main.py"),
    ]
    print("运行 PyInstaller（可能需要几分钟）...")
    subprocess.check_call(cmd, cwd=str(PROJ))

    dist_exe = PROJ / "dist" / f"{NAME}.exe"
    (PROJ / "dist" / "logs").mkdir(exist_ok=True)
    print("副本生成完毕:", dist_exe)
    print("说明：引擎 exe / 卡名映射 / 收款码均已内嵌，启动时自动校验完整性。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
