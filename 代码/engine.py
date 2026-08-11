"""C++ 计算核心（MCTS + 束搜索模拟）的 Python 薄封装。

把 powerlog_reader 的对局快照（或手工局面）转成 C++ 认识的 JSON 局面，
通过子进程调用 red_dragon_engine.exe --json，并把 stdout 的 JSON 结果
解析回 Python dict。实时进度 PROGRESS / FOUND 走 stderr，可回调到 GUI。
瓶颈模型启发函数（可达龙数 ≈ min(龙源数, 回手容量, 法力轮数)），无子链库。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional


BASE_DIR = Path(__file__).resolve().parent

# 随从栏容量（与 C++ MAX_BOARD 一致）
MAX_BOARD_SLOTS = 7

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
    "持枪要挟": 1,
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

EFFECT_NAMES = (
    "狐人老千",
    "伺机待发",
    "斯卡布斯·刀油",
    "锯齿骨刺",
    "幸运彗星",
    "[spcost+1]",
    "[spcost+2]",
)

# 会改变手牌显示费用的效果（日志里的当前费用已含其折扣）：
# 传效果给 C++ 时必须用基础费用，否则双重折扣。
# 幸运彗星不减费（只是下一张连击随从连击双触发），不在此列。
DISCOUNT_EFFECT_NAMES = {"狐人老千", "伺机待发", "斯卡布斯·刀油", "锯齿骨刺"}

# 敌方随从战吼造成的“我方法术增费”（异教低阶牧师 +1 / 音箱践踏者 +2）：
# 日志显示费用已含该增幅，传给 C++ 前需把已知法术回退到基础费用，
# 由 C++ 按 sp_cost_inc 重新加回，避免双重计算；手动输入同理。
SP_COST_EFFECT_NAMES = {"[spcost+1]", "[spcost+2]"}

# 项目里已知的法术/奥秘（spcost 增费只作用于这些；武器/随从不受影响）
KNOWN_SPELL_NAMES = {
    "伪造的幸运币",
    "幸运币",
    "伺机待发",
    "暗影步",
    "殒命暗影",
    "垂钓时光",
    "挖掘宝藏",
    "持枪要挟",
    "锯齿骨刺",
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
    "持枪要挟",
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

# 抽牌属性：{类型: 张数}，类型 ∈ 随机（发现/未知类型）/随从/法术。
# “发现”= 选三张随机牌抽一张，抽到的牌类型不可知，属性定义为“随机”。
# base=无条件抽牌；combo=连击触发时追加抽牌；conditional=有条件才抽。
# 只用于辅助“是否考虑展开该路径”的判断，不模拟具体抽到哪张牌。
CARD_DRAW_ATTRIBUTES = {
    "垂钓时光": {"base": {}, "combo": {"随机": 1}},                       # 探底；连击：抽 1 张
    "挖掘宝藏": {"base": {"随从": 1}, "combo": {}},                       # 抽 1 张随从牌
    "异教地图": {"base": {"随机": 1}, "combo": {}},                       # 从牌库发现 → 随机
    "持枪要挟": {"base": {"随机": 1}, "combo": {}},                       # 发现另一职业快枪牌 → 随机
    "行骗": {"base": {"法术": 1}, "combo": {"随从": 1}},                  # 抽 1 法术；连击再抽 1 随从
    "潜伏帷幕": {"base": {"随从": 2}, "combo": {}},                       # 抽 2 张随从牌
    "可疑交易": {"base": {"随机": 3}, "combo": {}},                       # 抽 3 张随机牌
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


def apply_board_exchanges(
    board: List[dict],
    enemy_board: List[dict],
    exchanges: Optional[List[tuple]] = None,
    hero: Optional[dict] = None,
) -> Tuple[List[dict], List[dict], Optional[dict]]:
    """场面交换结算：我方随从B -= 敌方随从A，敌方随从B -= 我方随从A；
    B <= 0 的随从死亡并从 board 移除。索引按初始 board 顺序（1 起），
    全部交换先按原始攻击力结算，再统一移除死亡随从。

    敌方英雄目标：enemy_index == 0 表示攻击敌方英雄——英雄攻击力视作 0，
    我方随从不掉血；护甲先吸收，再扣血量。返回 (board, enemy_board, hero)。"""
    if not exchanges:
        return board, enemy_board, hero

    board = [dict(item) for item in board]
    enemy_board = [dict(item) for item in enemy_board]
    hero_out = dict(hero) if hero else None
    friend_by_index = {i: item for i, item in enumerate(board, start=1)}
    enemy_by_index = {i: item for i, item in enumerate(enemy_board, start=1)}

    for friend_index, enemy_index in exchanges:
        friend = friend_by_index.get(friend_index)

        if friend is None:
            continue

        friend_attack = int(friend.get("attack") or 0)

        if friend_attack < 1:
            # 0 攻随从无法主动攻击（以场面当前攻击为准），该交换不生效
            continue

        if enemy_index == 0:
            # 攻击敌方英雄：英雄攻击力 0（我方不掉血），护甲先吸收再扣血量
            if hero_out is not None:
                armor = int(hero_out.get("armor") or 0)
                hp = int(hero_out.get("health") or 0)

                if armor >= friend_attack:
                    hero_out["armor"] = armor - friend_attack
                else:
                    hero_out["health"] = max(0, hp - (friend_attack - armor))
                    hero_out["armor"] = 0

            continue

        enemy = enemy_by_index.get(enemy_index)

        if enemy is None:
            continue

        enemy_attack = int(enemy.get("attack") or 0)

        if friend.get("health") is not None:
            friend["health"] = friend["health"] - enemy_attack

        if enemy.get("health") is not None:
            enemy["health"] = enemy["health"] - friend_attack

    board = [
        item for item in board
        if item.get("health") is None or item["health"] > 0
    ]
    enemy_board = [
        item for item in enemy_board
        if item.get("health") is None or item["health"] > 0
    ]
    return board, enemy_board, hero_out


def _keep_value(
    name: str,
    in_hand: bool,
    etc_band: Optional[List[str]],
    hand: Optional[List[dict]] = None,
) -> float:
    """随从保留分：结合手牌与牛池动态判断（场面上的随从价值随持有情况变化）。

    - 手上有的卡，场上的同名牌价值降低；手上没有的卡，场上的价值增高；
    - 手上有鱼 → 场上鱼无价值；手上有牛 → 场上牛无需保留；
    - 牛内按剩余内容评分：阿莱克丝塔萨 > 舞动全场 > 幻觉药水；
    - 牛内只剩余幻觉药水且手上暗影施法者 <= 1 张时，牛价值低
      （不值得暗影施法者选择牛头人酋长作为目标）；
    - 狐人老千：0 价值（无论手上有没有，场上狐不值得保留）。
    """
    if name == "鲨鱼之灵":
        return 0.0 if in_hand else 35.0

    if name == "斯卡布斯·刀油":
        return 20.0 if in_hand else 50.0

    if name == "乐队经理精英牛头人酋长":
        if in_hand:
            return 0.0

        if etc_band is None:
            return 110.0  # 牛池未知：按高价值

        if not etc_band:
            return 0.0  # 牛内无卡 → 0 价值

        if "生命的缚誓者阿莱克丝塔萨" in etc_band:
            return 130.0  # 牛内还有龙 → 巨大价值（可复制/回手拿龙）

        if "舞动全场（ft.迦罗娜）" in etc_band:
            return 120.0  # 牛内还有舞 → 高价值

        if set(etc_band) == {"幻觉药水"}:
            return 110.0  # 只剩幻：牛本体仍可舞动回手/占位，保留价值高

        return 90.0  # 其他组合（如 幻+其他）

    if name == "暗影施法者":
        return 12.0 if in_hand else 30.0

    if name == "晦鳞巢母":
        return 8.0 if in_hand else 20.0

    if name == "狐人老千":
        return 0.0

    return 0.0


def exchange_heuristic(
    board: List[dict],
    hand: Optional[List[dict]] = None,
    etc_band: Optional[List[str]] = None,
) -> Tuple[float, int, int]:
    """场面交换阶段的启发函数：只看我方随从栏（敌方场面完全不参与）。

    - 空位数越多越好（后续连招可用格子越多）；
    - 关键随从保留加分，随手牌/牛池动态调整（见 _keep_value）；
    - 位置影响：序号越靠后，整场回手溢出时越先被烧（舞动全场按进场顺序回手、
      装不下时从后往前烧），因此靠前的关键随从小有加分。
    返回 (综合分, 空位数, 场上随从数)。
    """
    hand = hand or []
    hand_names = {item.get("name") for item in hand}
    free_slots = max(0, MAX_BOARD_SLOTS - len(board))
    keep_score = 0.0
    position_bonus = 0.0

    for index, item in enumerate(board, start=1):
        name = item.get("name") or ""
        keep = _keep_value(name, name in hand_names, etc_band, hand)
        keep_score += keep

        if keep:
            position_bonus += max(0, MAX_BOARD_SLOTS - index) * 0.5

    total = free_slots * 100.0 + keep_score + position_bonus
    return (total, free_slots, len(board))


# 场面交换：把敌方英雄总血量（血量+护甲）扣到 ≤16 的倍数时的加分
# （红龙每条 16 伤，血线对齐到 16 倍数可少打龙）
HERO_ALIGN_BONUS = 80.0


def plan_exchanges(
    board: List[dict],
    enemy_board: List[dict],
    hand: Optional[List[dict]] = None,
    etc_band: Optional[List[str]] = None,
    hero: Optional[dict] = None,
    max_trades: int = 2,
    max_plans: int = 4000,
) -> Tuple[List[Tuple[int, int]], List[dict], Tuple[float, int, int]]:
    """场面交换搜索（独立于路径搜索，单独的启发函数）。

    枚举候选交换方案（每个我方随从最多主动攻击一次；敌方随从只要没死，
    可被多个我方随从选为目标；enemy_index == 0 表示攻击敌方英雄；
    最多 max_trades 个交换；枚举量受 max_plans 硬上限保护，不影响性能），
    把敌方英雄总血量扣到 ≤16 的倍数（(原总血量 mod 16) <= 攻击和）的交换加分，
    按 exchange_heuristic（只看我方随从栏）选最优，返回
    (最优交换计划, 交换后的我方随从栏, 启发分数)。
    """
    # 只有攻击力 >= 1 的我方随从能主动发起交换（0 攻随从不能攻击）
    friend_indices = [
        index
        for index, item in enumerate(board, start=1)
        if int(item.get("attack") or 0) >= 1
    ]
    enemy_indices = [0] + list(range(1, len(enemy_board) + 1))  # 0 = 敌方英雄
    hero_total = 0

    if hero:
        hero_total = int(hero.get("health") or 0) + int(hero.get("armor") or 0)

    base_score = exchange_heuristic(board, hand=hand, etc_band=etc_band)
    best_plan: List[Tuple[int, int]] = []
    best_board = list(board)
    best_score = base_score

    if not friend_indices:
        return best_plan, best_board, best_score

    plans: List[tuple] = [()]

    # 单交换
    plans.extend((fi, ei) for fi in friend_indices for ei in enemy_indices)

    # 双交换（两个我方随从；敌方目标可重复——只要没死就能被多次选为目标）
    if max_trades >= 2:
        for i, fi1 in enumerate(friend_indices):
            for fi2 in friend_indices[i + 1:]:
                for ei1 in enemy_indices:
                    for ei2 in enemy_indices:
                        plans.append((fi1, ei1, fi2, ei2))

    if len(plans) > max_plans:
        plans = plans[:max_plans]

    score_cache: Dict[tuple, Tuple[float, int, int]] = {}

    for plan in plans:
        pairs = [(plan[k], plan[k + 1]) for k in range(0, len(plan), 2)]
        hero_attack = sum(
            int(board[fi - 1].get("attack") or 0)
            for fi, ei in pairs
            if ei == 0 and 1 <= fi <= len(board)
        )
        traded_board, _traded_enemy, _traded_hero = apply_board_exchanges(
            board, enemy_board, exchanges=pairs, hero=hero
        )
        board_key = tuple(
            (item.get("name"), item.get("health"), item.get("attack"))
            for item in traded_board
        )
        score = score_cache.get(board_key)

        if score is None:
            # 大量候选交换会得到相同的我方随从栏，按结果指纹缓存启发分数，
            # 减少复杂交换情况下的重复计算
            score = exchange_heuristic(traded_board, hand=hand, etc_band=etc_band)
            score_cache[board_key] = score

        score_val = score[0]

        # 精确对齐加分：敌方英雄总血量被扣到 ≤16 的倍数（16/32/48/64/…）
        if hero_attack > 0 and hero_total > 0 and hero_total % 16 <= hero_attack:
            score_val += HERO_ALIGN_BONUS

        # 送牛且暗在手：暗影施法者失去牛头人酋长目标
        # （先暗(牛)再送牛 = 复制价值还在；但牛被牺牲后暗无法再选牛为目标）
        if any(
            b.get("name") == "乐队经理精英牛头人酋长" for b in board
        ) and not any(
            b.get("name") == "乐队经理精英牛头人酋长" for b in traded_board
        ) and any(
            str(h.get("name", "")) == "暗影施法者" for h in (hand or [])
        ):
            band = etc_band or []

            if "生命的缚誓者阿莱克丝塔萨" in band:
                score_val -= 60.0  # 失去 暗复制牛拿龙 的线路
            elif "舞动全场（ft.迦罗娜）" in band:
                score_val -= 50.0  # 失去 暗复制牛拿舞 的线路
            else:
                score_val -= 20.0  # 只剩幻：暗复制牛的价值本来就低

        if score_val > best_score[0]:
            best_score = (score_val, score[1], score[2])
            best_plan = pairs
            best_board = traded_board

    return best_plan, best_board, best_score


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

    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            # PyInstaller onefile：引擎 exe 随程序一起解压到临时目录
            candidates.insert(0, Path(meipass) / "red_dragon_engine.exe")
        else:
            # PyInstaller onedir：引擎 exe 与主程序放在同一目录
            candidates.insert(
                0, Path(sys.executable).resolve().parent / "red_dragon_engine.exe"
            )

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
    exchanges: Optional[List[tuple]] = None,
    only_best_damage: bool = False,
    discover_quickdraw_choice: Optional[str] = None,
    branch_prefix: Optional[List[str]] = None,
    lethal_threshold: int = -1,
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
    # 敌方法术增费总额：日志显示费用已含该增幅，需回退基础费后由 C++ 加回
    sp_cost_total = 0

    for effect in effects:
        name = str(effect.get("name", ""))
        count = int(effect.get("count", 1) or 1)

        if name in SP_COST_EFFECT_NAMES:
            sp_cost_total += (1 if name == "[spcost+1]" else 2) * count

    for index, item in enumerate(snapshot.get("hand") or [], start=1):
        cost = item.get("cost")
        name = item["name"]
        base = KNOWN_BASE_COSTS.get(name)

        if use_base_cost and base is not None:
            temp_cost = int(base)
        elif sp_cost_total > 0 and name in KNOWN_SPELL_NAMES and base is not None:
            # 法术显示费用已含 spcost 增幅，回退基础费；C++ 按 sp_cost_inc 加回
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

    board_source = list(snapshot.get("board") or [])
    enemy_source = list(snapshot.get("enemy_board") or [])

    if exchanges:
        # 场面交换：先按 A/B 结算（我方B-=敌方A、敌方B-=我方A，B<=0 死亡移除），
        # 再把结算后的场面交给 C++ 搜索；敌方英雄目标（0）同时结算血量+护甲。
        board_source, enemy_source, _exchanged_hero = apply_board_exchanges(
            board_source,
            enemy_source,
            exchanges=exchanges,
            hero=snapshot.get("opponent_hero"),
        )

    board: List[dict] = []

    for item in board_source:
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

    for item in enemy_source:
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
        "only_best_damage": 1 if only_best_damage else 0,
        "discover_quickdraw_choice": discover_quickdraw_choice or "",
        "branch_prefix": branch_prefix or [],
        "lethal_threshold": int(lethal_threshold),
        # 默认四通道：H6/1100（8水晶十龙深线）、H1/1500（4水晶十龙/紧线）、
        # H2/1100（96 伤线）、H2/3000（6水晶紧 48 伤线）
        "wide_widths": wide_widths or [1100, 1500, 1100, 3000],
        "heuristics": heuristics or [6, 1, 2, 2],
        # 牌库是否“完全已知”：只要还有未揭示的牌库实体就不算完整牌库。
        # （Power.log 只揭示抽到/探明的牌，先前用 bool(deck) 会把残缺牌库当完整，
        #   导致行骗等按“无随从/无法术”误判。）
        "deck_is_known": int(snapshot.get("deck_unknown_cards") or 0) == 0,
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

    popen_kwargs: Dict[str, object] = {}
    if os.name == "nt":
        # 引擎是控制台程序：不创建新窗口，避免计算时弹出黑框
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(
        [exe, "--json"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **popen_kwargs,
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
    exchanges: Optional[List[tuple]] = None,
    only_best_damage: bool = False,
    discover_quickdraw_choice: Optional[str] = None,
    branch_prefix: Optional[List[str]] = None,
    lethal_threshold: int = -1,
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
        exchanges=exchanges,
        only_best_damage=only_best_damage,
        discover_quickdraw_choice=discover_quickdraw_choice,
        branch_prefix=branch_prefix,
        lethal_threshold=lethal_threshold,
    )
    result = run_engine(
        payload,
        exe_path=exe_path,
        progress_callback=progress_callback,
        found_callback=found_callback,
        should_stop=should_stop,
    )
    if only_best_damage and result.get("results"):
        # 只计算最高伤害：结果里只保留最高伤害的路径
        best = result.get("max_damage", 0)
        result["results"] = [r for r in result["results"] if r.get("damage") == best]
    return result


# ===================== 独立的“如果机制” =====================
# 手牌有抽随从卡时，假设以省费方式打出（默认先伺机待发，下一张法术减2费），
# 抽到“用户预写随从组合”里缺失的随从，评估这之后能达到的最高伤害。
COMBO_MINION_SETS = [
    ["鲨鱼之灵", "狐人老千", "斯卡布斯·刀油", "暗影施法者", "乐队经理精英牛头人酋长", "晦鳞巢母"],
    ["鲨鱼之灵", "斯卡布斯·刀油", "晦鳞巢母", "赤烟·腾武", "乐队经理精英牛头人酋长"],
]
# 抽随从卡：卡名 -> (基础费用, 抽随从张数)
DRAW_MINION_SPELLS = {
    "挖掘宝藏": (1, 1),
    "潜伏帷幕": (3, 2),
    "行骗": (2, 1),
    "垂钓时光": (1, 1),
}
# “可能抽到”的随从优先级：鱼 > 刀 > 牛 > 暗 > 晦 > 狐（其余最低）
DRAW_PRIORITY = {
    "鲨鱼之灵": 6,
    "斯卡布斯·刀油": 5,
    "乐队经理精英牛头人酋长": 4,
    "暗影施法者": 3,
    "晦鳞巢母": 2,
    "狐人老千": 1,
}


def _quick_otk_estimate(snapshot: Dict[str, object]) -> Tuple[int, int, int]:
    """毫秒级 OTK 预评估：按资源计数估算（可达龙数、伤害、剩余法力）。

    龙源数 = 手牌/场上龙 + 暗施复制(鱼在场×2) + 牛拿龙 + 药水复制；
    回手容量 = 单体回手 + 整场回手(舞/药水，乐观按 5 个随从)；
    法力轮数 = (法力+水晶)/2 的简化估算；
    可达龙数 = min(龙源, 回手, 法力轮数)；每条龙 16 伤（鱼在场）/ 8 伤。
    """
    hand = [str(h.get("name", "")) for h in snapshot.get("hand") or []]
    board = [str(b.get("name", "")) for b in snapshot.get("board") or []]
    cards = hand + board

    def cnt(n: str) -> int:
        return sum(1 for c in cards if c == n)

    shark = cnt("鲨鱼之灵") > 0
    dragon_hand = cnt("生命的缚誓者阿莱克丝塔萨")
    caster = cnt("暗影施法者")
    etc = cnt("乐队经理精英牛头人酋长")
    band = [str(x) for x in (snapshot.get("etc_band") or [])]
    potion = cnt("幻觉药水")
    shadowstep = cnt("暗影步")
    tenwu = cnt("赤烟·腾武")
    dance = cnt("舞动全场（ft.迦罗娜）")

    sources = (
        dragon_hand
        + caster * (2 if shark else 1)
        + (1 if etc and "生命的缚誓者阿莱克丝塔萨" in band else 0)
        + (potion if dragon_hand > 0 else 0)
    )
    returns = shadowstep + tenwu + 5 * dance + (5 if potion and board else 0)
    mana = int(snapshot.get("mana") or 0)
    crystals = int(snapshot.get("crystals") or 0)
    mana_rounds = max(1, (mana + crystals) // 2)
    dragons = max(0, min(sources, max(1, returns), mana_rounds))
    damage = dragons * (16 if shark else 8)
    return damage, dragons, max(0, mana)


def _combo_completeness(snapshot: Dict[str, object]) -> int:
    """随从齐全度：两个预写组合并集（7 个随从）中手牌/战场已拥有的数量。"""
    have = set(str(h.get("name", "")) for h in snapshot.get("hand") or [])
    have |= set(str(b.get("name", "")) for b in snapshot.get("board") or [])
    all_minions = []
    for combo in COMBO_MINION_SETS:
        for n in combo:
            if n not in all_minions:
                all_minions.append(n)
    return sum(1 for n in all_minions if n in have)


def compute_draw_whatif(
    snapshot: Dict[str, object],
    options: Optional[Dict[str, object]] = None,
) -> Optional[Dict[str, object]]:
    """独立的“如果机制”：毫秒级递归预评估，返回最优假设情形，或 None（无抽随从卡 / 组合已齐）。

    省费打出抽随从卡（默认先伺机待发 -2、狐人老千 -2），按优先级抽缺失随从
    （鱼>刀>牛>暗>晦>狐）；抽到的刀油立即打出成为新的减费状态。

    刀油减费状态模型：
      - 一层刀油状态 = “下两张牌各减 2 费”（一层含 2 个槽位，每个槽位 -2）；
      - 鲨鱼之灵在场时战吼触发两次 = 两层状态（下两张牌各自两层 -2，即各 -4）；
     - 每打出一张随从，每层消耗 1 个槽位；两张随从后该层耗尽移除；
     - 法术（抽随从卡）享受减费但不消耗槽位，可连续低成本打出；
     - 暗(刀)把 1/1 刀油复制加入手牌（不立即补层）；丢晦回 4 法力（鱼在场翻倍）。
    剪枝条件：当前状态没有任何减费状态（层用尽、伺机/狐已消耗、也无刀油可补层）
    即停止递归直接评分——不再按原价继续展开后续可能性（深度 ≤10，毫秒级，不改启发函数与主搜索）。
    返回：{"cards": 用到的抽卡, "drawn": 抽到的随从列表,
          "discounts": 减费来源卡列表, "damage": 预估伤害, "dragons": 龙数, "mana_left": 剩余法力}
    """
    hand = list(snapshot.get("hand") or [])
    hand_names = [str(h.get("name", "")) for h in hand]
    board_names = [str(b.get("name", "")) for b in snapshot.get("board") or []]
    have = set(hand_names) | set(board_names)

    missing: List[str] = []
    for combo in COMBO_MINION_SETS:
        for n in combo:
            if n not in have and n not in missing:
                missing.append(n)
    missing.sort(key=lambda n: -DRAW_PRIORITY.get(n, 0))
    draw_cards = [n for n in hand_names if n in DRAW_MINION_SPELLS]
    if not missing or not draw_cards:
        return None

    mana = int(snapshot.get("mana") or 0)
    crystals = int(snapshot.get("crystals") or 0)
    cards_in_hand = set(hand_names)
    prep_used = "伺机待发" in cards_in_hand
    foxy_used = "狐人老千" in cards_in_hand
    shark = "鲨鱼之灵" in board_names  # 只有场上的鱼才让战吼触发两次
    fish_played = shark

    # 刀油减费状态：一层 = 下两张牌各 -2（槽位 2→1→移除）；鱼在场一次战吼 = 两层
    scabbs_layers: List[int] = []
    scabbs_in_hand = sum(1 for n in hand_names if n == "斯卡布斯·刀油")
    scabbs_played = 0
    scabbs_copies = 0  # 暗(刀)复制的 1/1 刀油（留在手牌，不补层）

    used_cards: List[str] = []
    drawn_minions: List[str] = []
    discounts: List[str] = []
    played_minions: List[str] = []
    draw_cards_in_hand = list(draw_cards)
    hand_left = list(hand)  # 未打出的原始手牌（随出随删，费用取当前显示值）
    shadowcaster_played = False
    mother_played = False

    def _hand_cost(name: str, base: int) -> int:
        # 手牌当前费用（已含旧减费）优先，抽到/缺失牌用基础费用
        for h in hand_left:
            if str(h.get("name", "")) == name:
                c = h.get("cost")
                return int(c) if c is not None else base
        return base

    def _remove_hand_card(name: str) -> None:
        # 打出一张手牌后从“未打出手牌”里移除（同名牌只移除一张）
        for i, h in enumerate(hand_left):
            if str(h.get("name", "")) == name:
                hand_left.pop(i)
                return

    def _add_scabbs() -> None:
        # 一层 = 下两张牌各 -2；鱼在场战吼双触发 = 两层
        if shark:
            scabbs_layers.extend([2, 2])
        else:
            scabbs_layers.append(2)

    def _minion_consumes() -> None:
        # 打出一张随从：每层消耗 1 个槽位；槽位耗尽移除该层
        if scabbs_layers:
            scabbs_layers[:] = [s - 1 for s in scabbs_layers if s > 1]

    def _spell_discount() -> int:
        # 抽随从卡（法术）：伺机/狐一次性 -2，刀油层对法术生效但不消耗槽位
        d = 2 * len(scabbs_layers)
        if prep_used:
            d += 2
        if foxy_used:
            d += 2
        return d

    def _has_discount_state() -> bool:
        # 剪枝条件：当前状态是否仍有减费状态——
        # 刀油层未用尽、或伺机/狐的 -2 未消耗、或手牌还有刀油可打出补层
        return bool(scabbs_layers) or prep_used or foxy_used or scabbs_in_hand > 0

    # 递归：优先打出鱼/刀油补减费状态 → 抽随从卡（省费连抽）→ 暗(刀) → 丢晦；
    # 剪枝：当前状态已无任何减费状态即停止并评分（深度 ≤10，毫秒级）。
    depth = 0
    while depth < 10:
        if not _has_discount_state():
            break
        depth += 1
        progressed = False

        # 0) 手牌/刚抽到的鱼先打出：战吼翻倍来源（随从，消耗每层 1 槽）
        if not fish_played and "鲨鱼之灵" in cards_in_hand:
            eff = max(0, _hand_cost("鲨鱼之灵", 4) - 2 * len(scabbs_layers))
            if eff <= mana:
                mana -= eff
                _minion_consumes()
                _remove_hand_card("鲨鱼之灵")
                cards_in_hand.discard("鲨鱼之灵")
                used_cards.append("鲨鱼之灵")
                played_minions.append("鲨鱼之灵")
                fish_played = True
                shark = True
                progressed = True
        if progressed:
            continue

        # 1) 打出刀油（手牌中原版或抽到的）：一层=下两张牌-2，鱼在场两层
        if scabbs_in_hand > 0:
            eff = max(0, _hand_cost("斯卡布斯·刀油", 4) - 2 * len(scabbs_layers))
            if eff <= mana:
                mana -= eff
                _minion_consumes()
                _remove_hand_card("斯卡布斯·刀油")
                scabbs_in_hand -= 1
                cards_in_hand.discard("斯卡布斯·刀油")
                used_cards.append("斯卡布斯·刀油")
                played_minions.append("斯卡布斯·刀油")
                scabbs_played += 1
                _add_scabbs()
                discounts.append("斯卡布斯·刀油")
                progressed = True
        if progressed:
            continue

        # 2) 抽随从卡（法术）：享受减费、不消耗刀油槽位，优先最省费的
        for dcard in sorted(draw_cards_in_hand, key=lambda n: DRAW_MINION_SPELLS[n][0]):
            eff = max(0, DRAW_MINION_SPELLS[dcard][0] - _spell_discount())
            if eff > mana:
                continue
            mana -= eff
            used_cards.append(dcard)
            _remove_hand_card(dcard)
            if prep_used:
                prep_used = False
                discounts.append("伺机待发")
            if foxy_used:
                foxy_used = False
                discounts.append("狐人老千")
            for _ in range(DRAW_MINION_SPELLS[dcard][1]):
                if not missing:
                    break
                mn = missing.pop(0)
                drawn_minions.append(mn)
                cards_in_hand.add(mn)
                if mn == "斯卡布斯·刀油":
                    scabbs_in_hand += 1
            draw_cards_in_hand.remove(dcard)
            progressed = True
            break
        if progressed:
            continue

        # 3) 暗(刀)：复制刀油进手牌（1/1，留在手牌不补层），随从消耗每层 1 槽
        if not shadowcaster_played and "暗影施法者" in cards_in_hand and scabbs_played > 0:
            eff = max(0, _hand_cost("暗影施法者", 1) - 2 * len(scabbs_layers))
            if eff <= mana:
                mana -= eff
                _minion_consumes()
                _remove_hand_card("暗影施法者")
                cards_in_hand.discard("暗影施法者")
                used_cards.append("暗影施法者")
                played_minions.append("暗影施法者")
                shadowcaster_played = True
                scabbs_copies += 1
                progressed = True
        if progressed:
            continue

        # 4) 丢晦：回 4 法力（鱼在场翻倍，上限水晶），随从消耗每层 1 槽
        if not mother_played and "晦鳞巢母" in cards_in_hand:
            eff = max(0, _hand_cost("晦鳞巢母", 3) - 2 * len(scabbs_layers))
            if eff <= mana:
                mana -= eff
                _minion_consumes()
                _remove_hand_card("晦鳞巢母")
                cards_in_hand.discard("晦鳞巢母")
                used_cards.append("晦鳞巢母")
                played_minions.append("晦鳞巢母")
                mother_played = True
                mana = min(crystals, mana + (4 if shark else 2))
                progressed = True
        if not progressed:
            break

    if not drawn_minions:
        return None

    # 最终变体：移除已打出的卡（抽卡/刀油/暗/晦/鱼/伺机），加入抽到的随从与暗(刀)复制的刀油，
    # 打出的随从放入战场（随从齐全度按 手牌+战场 计算）。
    remove_names = set(used_cards)
    if "伺机待发" in set(hand_names) and "伺机待发" in discounts:
        remove_names.add("伺机待发")
    new_hand = [h for h in hand if str(h.get("name", "")) not in remove_names]
    for mn in drawn_minions:
        if mn not in played_minions:
            new_hand.append({"name": mn})
    for _ in range(scabbs_copies):
        new_hand.append({"name": "斯卡布斯·刀油"})
    new_board = [dict(b) for b in (snapshot.get("board") or [])]
    for mn in played_minions:
        new_board.append({"name": mn})
    variant = dict(snapshot)
    variant["hand"] = new_hand
    variant["board"] = new_board
    variant["mana"] = mana

    dmg, drg, mana_left = _quick_otk_estimate(variant)

    # 精确截断（与框2“可能机制”共享）：预计伤害封顶为 敌方英雄血量+护甲，
    # 超过即视为已斩杀，不再给出更高估值
    if options and bool(options.get("truncate_branch", True)):
        hero = snapshot.get("opponent_hero") or {}
        hp = hero.get("health")

        if isinstance(hp, int) and hp > 0:
            lethal = hp + int(hero.get("armor") or 0)

            if lethal > 0:
                dmg = min(dmg, lethal)

    return {
        "cards": list(used_cards),
        "drawn": drawn_minions,
        "discounts": discounts,
        "completeness": _combo_completeness(variant),
        "completeness_total": 7,
        "crystals": crystals,
        "damage": dmg,
        "dragons": drg,
        "mana_left": mana_left,
    }
