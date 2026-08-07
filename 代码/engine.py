"""C++ 计算核心（MCTS + 束搜索模拟）的 Python 薄封装。

把 powerlog_reader 的对局快照（或手工局面）转成 C++ 认识的 JSON 局面，
通过子进程调用 red_dragon_engine.exe --json，并把 stdout 的 JSON 结果
解析回 Python dict。实时进度 PROGRESS / FOUND 走 stderr，可回调到 GUI。
瓶颈模型启发函数（可达龙数 ≈ min(龙源数, 回手容量, 法力轮数)），无子链库。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional


BASE_DIR = Path(__file__).resolve().parent

DEFAULT_ETC_BAND = ["舞动全场（ft.迦罗娜）", "幻觉药水", "生命的缚誓者阿莱克丝塔萨"]

# 牛头人酋长卡池勾选选项：(卡名, 界面显示名)
ETC_OPTIONS = [
    ("舞动全场（ft.迦罗娜）", "舞动全场（ft.迦罗娜）"),
    ("幻觉药水", "幻觉药水"),
    ("生命的缚誓者阿莱克丝塔萨", "红龙"),
    ("战略转移", "战略转移"),
    ("赤烟·腾武", "赤烟·腾武"),
]

# 项目卡牌基础费用（手动输入/效果折扣换算用）。
# 传效果给 C++ 时必须用基础费用，否则日志里已含折扣的当前费用会双重折扣。
KNOWN_BASE_COSTS = {
    "伪造的幸运币": 0,
    "伺机待发": 0,
    "暗影步": 0,
    "殒命暗影": None,
    "垂钓时光": 1,
    "挖掘宝藏": 1,
    "黑水弯刀": 1,
    "邪恶短刀": 1,
    "异教地图": 2,
    "狐人老千": 2,
    "疾速矿锄": 2,
    "行骗": 2,
    "锯齿骨刺": 2,
    "闪避": 2,
    "晦鳞巢母": 3,
    "潜伏帷幕": 3,
    "乐队经理精英牛头人酋长": 4,
    "舞动全场（ft.迦罗娜）": 3,
    "幻觉药水": 4,
    "生命的缚誓者阿莱克丝塔萨": 9,
    "可疑交易": 4,
    "斯卡布斯·刀油": 4,
    "鲨鱼之灵": 4,
    "暗影施法者": 5,
    "赤烟·腾武": 2,
    "“赤烟”腾武": 2,
    "幸运彗星": 2,
    "战略转移": 1,
    "押注猎手": 3,
    "幸运币": 0,
}

# 手动输入简称 → 全名（与 C++/日志卡名一致）
CARD_ALIASES = {
    "狐": "狐人老千",
    "刀油": "斯卡布斯·刀油",
    "刀": "斯卡布斯·刀油",
    "鱼": "鲨鱼之灵",
    "龙": "生命的缚誓者阿莱克丝塔萨",
    "红龙": "生命的缚誓者阿莱克丝塔萨",
    "阿莱克斯塔萨": "生命的缚誓者阿莱克丝塔萨",          # 常见误写：斯/丝
    "阿莱克丝塔萨": "生命的缚誓者阿莱克丝塔萨",
    "生命的缚誓者阿莱克斯塔萨": "生命的缚誓者阿莱克丝塔萨",
    "牛": "乐队经理精英牛头人酋长",
    "牛头人": "乐队经理精英牛头人酋长",
    "舞": "舞动全场（ft.迦罗娜）",
    "药": "幻觉药水",
    "转": "战略转移",
    "步": "暗影步",
    "暗": "暗影施法者",
    "骨": "锯齿骨刺",
    "伺": "伺机待发",
    "币": "伪造的幸运币",
    "晦": "晦鳞巢母",
    "腾武": "赤烟·腾武",
    "彗星": "幸运彗星",
    "押": "押注猎手",
}

EFFECT_NAMES = ("狐人老千", "伺机待发", "斯卡布斯·刀油", "锯齿骨刺", "幸运彗星")

# 会改变手牌显示费用的效果（日志里的当前费用已含其折扣）：
# 传效果给 C++ 时必须用基础费用，否则双重折扣。
# 幸运彗星不减费（只是下一张连击随从连击双触发），不在此列。
DISCOUNT_EFFECT_NAMES = {"狐人老千", "伺机待发", "斯卡布斯·刀油", "锯齿骨刺"}

# 已知不可能上场的牌（法术/奥秘/武器）：战场解析时直接丢弃，
# 避免把错位数据（如舞动全场出现在战场）当成随从回手/打出。
KNOWN_NON_MINION_NAMES = {
    "伪造的幸运币",
    "幸运币",
    "伺机待发",
    "暗影步",
    "殒命暗影",
    "垂钓时光",
    "挖掘宝藏",
    "黑水弯刀",
    "邪恶短刀",
    "锯齿骨刺",
    "疾速矿锄",
    "异教地图",
    "行骗",
    "闪避",
    "潜伏帷幕",
    "舞动全场（ft.迦罗娜）",
    "幻觉药水",
    "幸运彗星",
    "战略转移",
    "可疑交易",
}


def resolve_card_name(name: str) -> str:
    """简称/别名/子串 → 项目卡名；找不到原样返回（视为杂牌）。"""
    name = (name or "").strip()

    if not name:
        return name

    if name in KNOWN_BASE_COSTS:
        return name

    alias = CARD_ALIASES.get(name)

    if alias:
        return alias

    matches = [candidate for candidate in KNOWN_BASE_COSTS if name in candidate]

    if len(matches) == 1:
        return matches[0]

    return name


def find_engine(exe_path: Optional[str] = None) -> Optional[str]:
    """定位 C++ 计算核心 exe（优先 red_dragon_engine.exe）。"""
    if exe_path:
        p = Path(exe_path)
        if p.is_file():
            return str(p)
        return None

    candidates = [
        BASE_DIR / "red_dragon_engine.exe",
        BASE_DIR / "red_dragon_calculator.exe",
    ]

    for p in candidates:
        if p.is_file():
            return str(p)

    return None


def build_payload(
    snapshot: Dict[str, object],
    *,
    min_alex: int = 1,
    max_alex: int = 10,
    depth: int = 30,
    max_paths: int = 1000000,
    threads: int = 4,
    time_budget_sec: float = 3.0,
    # 时间预算（秒）；0 / 负数 = 不限时：按束宽×最大深度跑完，不因时间停止
    heuristic: int = 6,
    wide_widths: Optional[List[int]] = None,
    heuristics: Optional[List[int]] = None,
    etc_band: Optional[List[str]] = None,
) -> Dict[str, object]:
    """把日志快照转成 C++ JSON 局面（纯束宽搜索）。

    手牌/随从的费用直接取日志里当前显示费用（已含各种减费效果），
    通过 temp_cost 传给 C++；ghostly 手牌标记为 deadly（殒命暗影）。
    """
    hand: List[dict] = []
    deadly_indexes = set(int(i) for i in (snapshot.get("deadly_shadow_hand_indexes") or []))
    effects = snapshot.get("current_effects") or []
    effect_names = {str(effect.get("name", "")) for effect in effects}
    # 只有“真正减费”的待生效效果才需要回退到基础费用；
    # 幸运彗星（连击双触发）不减费，日志显示费用即真实费用。
    use_base_cost = bool(effect_names & DISCOUNT_EFFECT_NAMES)

    for index, item in enumerate(snapshot.get("hand") or [], start=1):
        cost = item.get("cost")
        name = item["name"]
        base = KNOWN_BASE_COSTS.get(name)

        if use_base_cost and base is not None:
            temp_cost = int(base)
        else:
            temp_cost = int(cost) if cost is not None else -1

        hand.append(
            {
                "name": name,
                "temp_cost": temp_cost,
                "locked": False,
                "deadly": index in deadly_indexes,
            }
        )

    board: List[dict] = []

    for item in snapshot.get("board") or []:
        cost = item.get("cost")
        name = item["name"]
        if name in KNOWN_NON_MINION_NAMES:
            continue  # 法术/奥秘/武器不可能在场上，丢弃错位数据
        base = KNOWN_BASE_COSTS.get(name)

        if use_base_cost and base is not None:
            temp_cost = int(base)
        else:
            temp_cost = int(cost) if cost is not None else -1

        board.append(
            {
                "name": name,
                "health": int(item["health"]) if item.get("health") is not None else -1,
                "temp_cost": temp_cost,
            }
        )

    enemy_board: List[dict] = []

    for item in snapshot.get("enemy_board") or []:
        if isinstance(item, dict):
            enemy_board.append(
                {
                    "name": resolve_card_name(item.get("name") or "敌方随从"),
                    "health": int(item["health"]) if item.get("health") is not None else -1,
                }
            )
        else:
            enemy_board.append({"name": "敌方随从", "health": int(item)})

    effect_payload = [
        {"name": effect["name"], "count": int(effect.get("count", 1) or 1)}
        for effect in effects
        if effect.get("name") in EFFECT_NAMES
    ]

    payload: Dict[str, object] = {
        "crystals": int(snapshot["crystals"]) if snapshot.get("crystals") is not None else 10,
        "mana": int(snapshot["mana"]) if snapshot.get("mana") is not None else 10,
        # 本回合已出牌数（连击判定）：阅读器实时统计；手动输入/旧日志缺省为 0
        # ——连击不能是第一张出的牌，首张牌不凭空获得连击状态。
        "cards_played_this_turn": int(snapshot.get("cards_played_this_turn") or 0),
        "min_alex": min_alex,
        "max_alex": max_alex,
        "depth": depth,
        "max_paths": max_paths,
        "threads": threads,
        "time_budget_sec": time_budget_sec,
        "heuristic": heuristic,
        # 默认四通道：H6/1100（8水晶十龙深线）、H1/1500（4水晶十龙/紧线）、
        # H2/1100（96 伤线）、H2/3000（6水晶紧 48 伤线）
        "wide_widths": wide_widths or [1100, 1500, 1100, 3000],
        "heuristics": heuristics or [6, 1, 2, 2],
        "deck_is_known": bool(snapshot.get("deck")),
        "deck": [{"name": item["name"]} for item in snapshot.get("deck") or []],
        "hand": hand,
        "board": board,
        "enemy_board": enemy_board,
        "secrets": [{"name": item["name"]} for item in snapshot.get("secrets") or []],
        "weapon": {"name": snapshot["weapon"]["name"]} if snapshot.get("weapon") else None,
        "current_effects": effect_payload,
    }

    # 显式传空列表 = 牛池已空（全部被选走）；不传才是用 C++ 默认三张。
    if etc_band is not None:
        # 牛池最多三张：防御性截断，避免错误输入把乐队撑大
        payload["etc_band"] = list(etc_band)[:3]

    return payload


def run_engine(
    payload: Dict[str, object],
    exe_path: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, int], None]] = None,
    found_callback: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Dict[str, object]:
    """调用 C++ 核心，返回解析后的 JSON 结果 dict。"""
    exe = find_engine(exe_path)

    if exe is None:
        raise FileNotFoundError(
            "未找到 C++ 计算核心，请先运行 代码/build_engine.bat 编译 red_dragon_engine.exe"
        )

    proc = subprocess.Popen(
        [exe, "--json"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout_chunks: List[str] = []

    def feed_stdin() -> None:
        try:
            proc.stdin.write(json.dumps(payload, ensure_ascii=False))
            proc.stdin.flush()
            proc.stdin.close()
        except Exception:
            pass

    threading.Thread(target=feed_stdin, daemon=True).start()

    def drain_stdout() -> None:
        try:
            for chunk in proc.stdout:
                stdout_chunks.append(chunk)
        except Exception:
            pass

    stdout_thread = threading.Thread(target=drain_stdout, daemon=True)
    stdout_thread.start()

    def drain_stderr() -> None:
        for raw in proc.stderr:
            line = raw.strip()

            if line.startswith("PROGRESS "):
                parts = line.split()

                if len(parts) == 4 and progress_callback is not None:
                    try:
                        progress_callback(int(parts[1]), int(parts[2]), int(parts[3]))
                    except Exception:
                        pass
            elif line.startswith("FOUND ") and found_callback is not None:
                parts = line.split()

                if len(parts) == 3:
                    try:
                        found_callback(int(parts[1]), int(parts[2]))
                    except Exception:
                        pass

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()

    while proc.poll() is None:
        if should_stop is not None and should_stop():
            proc.kill()
            proc.wait()
            raise InterruptedError("计算已中止（用户中断）")

        time.sleep(0.05)

    stderr_thread.join(timeout=2.0)
    stdout_thread.join(timeout=3.0)

    if proc.returncode != 0:
        raise RuntimeError(f"C++ 计算核心退出码 {proc.returncode}")

    stdout_text = "".join(stdout_chunks)

    try:
        return json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"C++ 输出不是合法 JSON：{exc}") from exc


def compute(
    snapshot: Dict[str, object],
    *,
    min_alex: int = 1,
    max_alex: int = 10,
    depth: int = 30,
    max_paths: int = 1000000,
    threads: int = 4,
    time_budget_sec: float = 3.0,
    heuristic: int = 6,
    wide_widths: Optional[List[int]] = None,
    heuristics: Optional[List[int]] = None,
    etc_band: Optional[List[str]] = None,
    exe_path: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, int], None]] = None,
    found_callback: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Dict[str, object]:
    """一步完成：快照 → JSON → C++ 计算 → 返回结果 dict。"""
    payload = build_payload(
        snapshot,
        min_alex=min_alex,
        max_alex=max_alex,
        depth=depth,
        max_paths=max_paths,
        threads=threads,
        time_budget_sec=time_budget_sec,
        heuristic=heuristic,
        wide_widths=wide_widths,
        heuristics=heuristics,
        etc_band=etc_band,
    )
    return run_engine(
        payload,
        exe_path=exe_path,
        progress_callback=progress_callback,
        found_callback=found_callback,
        should_stop=should_stop,
    )
