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


_CARD_MAP: Optional[Dict[str, dict]] = None
_CARD_MAP_LOCK = threading.Lock()


def _load_card_map() -> Dict[str, dict]:
    """卡名映射（card_id → {name, type, ...}），惰性加载。"""
    global _CARD_MAP
    if _CARD_MAP is not None:
        return _CARD_MAP
    with _CARD_MAP_LOCK:
        if _CARD_MAP is not None:
            return _CARD_MAP
        try:
            with open(BASE_DIR / "card_id_map.json", encoding="utf-8") as f:
                _CARD_MAP = json.load(f)
        except Exception:
            _CARD_MAP = {}
    return _CARD_MAP


def deck_card_names_by_type(
    deck_items: List[dict],
    *types: str,
) -> List[str]:
    """剩余牌库中属于指定类型（SPELL/SECRET/MINION…）的卡名（唯一名，保序）。

    用于 暗影之门/行骗（抽法术）与 挖掘宝藏/潜伏帷幕/行骗连击（抽随从）
    的分支池——分支数 = 牌库剩余该类型卡数，替代旧的固定勾选池。
    """
    card_map = _load_card_map()
    out: List[str] = []

    for item in deck_items or []:
        name = str(item.get("name") or "")

        if not name or name in out:
            continue

        cid = str(item.get("card_id") or "")
        info = card_map.get(cid) or {}
        card_type = str(info.get("type") or "")

        if card_type in types:
            out.append(name)
        elif not cid and name in KNOWN_SPELL_NAMES and "SPELL" in types:
            out.append(name)
        elif not cid and name in KNOWN_NON_MINION_NAMES and "SPELL" in types:
            out.append(name)

    return out


def default_threads() -> int:
    """按 CPU 逻辑核数自适应线程数（桌面机 8~16 核用满，4 通道并行展开）。"""
    try:
        n = os.cpu_count() or 4
    except Exception:
        n = 4
    return max(4, min(16, n))

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
    "疯狂之灾祸": 1,
    "异教地图": 2,
    "暗影之门": 1,
    "双面生意": 2,
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
    "疯狂之灾祸",
    "异教地图",
    "行骗",
    "暗影之门",
    "双面生意",
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
    "疯狂之灾祸",
    "异教地图",
    "行骗",
    "暗影之门",
    "双面生意",
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
    "暗影之门": {"base": {"随机": 1}, "combo": {}},                       # 随机抽 1 张牌（任意类型）
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
    health: Optional[int] = None,
    fish_on_board: bool = False,
    board: Optional[List[dict]] = None,
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
        # 刀油是减费引擎（下两张牌-2费），连招价值与身材无关；
        # 用户优先级 鱼/刀/牛 最高。085526：保留刀油不交换=48伤，
        # 送刀腾1格反而只有32伤——旧值50被1个空位(+100)盖过。
        return 60.0 if in_hand else 110.0

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

        if "战略转移" in etc_band:
            # 牛内还有战略转移 → 回手引擎（单随从回手并还原），价值≈幻觉药水档。
            # 085526：不交换保留牛=48伤（幻弹牛→牛拿转→转回手），送牛腾1格只有32伤。
            return 110.0

        if set(etc_band) == {"幻觉药水"}:
            if any(str(h.get("name", "")) == "舞动全场（ft.迦罗娜）" for h in (hand or [])):
                # 牛池只剩幻 且 手牌已有舞：舞已覆盖整场回手，牛只能提供幻（被舞替代），
                # 价值降低。180257：保牛不交换=0伤，送牛腾格=32伤。
                return 40.0
            return 110.0  # 只剩幻：牛本体仍可舞动回手/占位，保留价值高

        return 90.0  # 其他组合（如 幻+其他）

    if name == "暗影施法者":
        if not in_hand and (hand or board):
            # 暗影施法者是复制引擎：有回手引擎（舞动全场/战略转移/暗影步在手，
            # 或 赤烟·腾武 在场上）且存在值得复制的目标（手牌/牛池/场上有
            # 阿莱克丝塔萨）时，暗被 舞动全场 弹回后可反复复制龙，价值极高。
            # 依 20260813_015341：交换把 暗 送掉后只剩 16 伤，留着暗 舞 回来
            # 能打 32（暗影施法者（阿莱克丝塔萨5nd）），因此不能被当 30 分
            # 的普通随从拿去换空位。
            has_return_engine = any(
                str(h.get("name", ""))
                in ("舞动全场（ft.迦罗娜）", "战略转移", "暗影步")
                for h in (hand or [])
            ) or any(
                str(b.get("name", "")) == "赤烟·腾武" for b in (board or [])
            )

            if has_return_engine:
                dragon_src = any(
                    str(h.get("name", "")) == "生命的缚誓者阿莱克丝塔萨"
                    for h in (hand or [])
                ) or (
                    etc_band
                    and "生命的缚誓者阿莱克丝塔萨" in etc_band
                ) or any(
                    str(b.get("name", "")) == "生命的缚誓者阿莱克丝塔萨"
                    for b in (board or [])
                )

                if dragon_src:
                    return 110.0

        return 12.0 if in_hand else 30.0

    if name == "赤烟·腾武":
        # 原版腾武（health>1）是回手引擎：回手并设 1 费，保留价值 60/110；
        # 1/1 复制体是牺牲材料，价值 0（腾武不优先回手 1/1 复制）。
        # 依据 081102：80 伤线保留 3/2 腾武+双刀，只牺牲两个 1/1 复制体；
        # 牺牲原版腾武的计划（如 送腾3/2+晦1/1）只有 64 伤；
        # 085526：保留腾武送刀+牛=48伤 > 保牛送腾=32伤，腾武价值须高于 牛(其他组合)=90。
        if health and health > 1:
            return 60.0 if in_hand else 110.0
        return 0.0

    if name == "晦鳞巢母":
        if in_hand:
            if fish_on_board:
                # 手上有晦 且 场上有鱼：晦是伤害引擎（战吼1伤×鱼翻倍×多轮回手）。
                # 215306：保晦送刀=96伤>保刀送晦=80伤（鱼在场时晦价值高于刀）。
                return 70.0
            return 8.0
        # 原版晦（health>1）且场上有鱼：晦是伤害引擎（战吼1伤×鱼翻倍×多轮回手），
        # 保留价值高。173736：保晦送狐=80伤>送晦=64伤；180257：保晦送牛=32伤。
        if health and health > 1 and fish_on_board:
            return 110.0
        return 20.0

    if name == "狐人老千":
        return 0.0

    return 0.0


def exchange_heuristic(
    board: List[dict],
    hand: Optional[List[dict]] = None,
    etc_band: Optional[List[str]] = None,
    free_slot_linear: bool = False,
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
    fish_on_board = any(item.get("name") == "鲨鱼之灵" for item in board)
    free_slots = max(0, MAX_BOARD_SLOTS - len(board))
    # 空位价值是条件式的：
    # - 场面有原版腾武（回手引擎，可弹回随从循环空位）→ 空位饱和：前4格 100/格，
    #   之后边际 15（102544：保留腾武+刀不交换=128伤，多余空位无价值）；
    # - 无腾武（无回手引擎，连招靠堆格子）→ 线性 100/格（213918/185407/051204 等
    #   13 个漏解场面都是“送随从腾第5格换更高伤”，旧饱和值把最优线压出前3）。
    if free_slot_linear:
        free_value = 100.0 * free_slots
    else:
        free_value = 100.0 * min(free_slots, 4) + 15.0 * max(0, free_slots - 4)
    keep_score = 0.0
    position_bonus = 0.0

    for index, item in enumerate(board, start=1):
        name = item.get("name") or ""
        keep = _keep_value(
            name,
            name in hand_names,
            etc_band,
            hand,
            int(item.get("health") or 0),
            fish_on_board,
            board,
        )
        keep_score += keep

        if keep:
            position_bonus += max(0, MAX_BOARD_SLOTS - index) * 0.5

    total = free_value + keep_score + position_bonus
    return (total, free_slots, len(board))


# 场面交换：敌方英雄剩余总血量（血+甲）需要的红龙数（每条 16 伤）
def _hero_dragons_needed(total: int) -> int:
    if total <= 0:
        return 0
    return (total + 15) // 16


# 每少打一条龙的价值（≈ 一个随从栏格子）：血量 31(2龙) > 33(3龙)，46(3龙) > 48+2甲(4龙)
HERO_DRAGON_WEIGHT = 100.0

# 牺牲 1/1 复制体加分：腾手牌时优先送掉 1/1 复制体（殒命暗影/暗影施法者造的复制），
# 保留原版随从。依据 20260807_081102 场面：牺牲两个 1/1 复制体腾 2 格（80伤）的
# 最优交换原评分排第 4，被“牺牲原版随从”的计划挤出前 3。
EXCHANGE_SAC_1_1_BONUS = 18.0


def plan_exchanges_top(
    board: List[dict],
    enemy_board: List[dict],
    hand: Optional[List[dict]] = None,
    etc_band: Optional[List[str]] = None,
    hero: Optional[dict] = None,
    top_n: int = 3,
    diversity: float = 0.0,
    max_trades: int = 2,
    max_plans: int = 4000,
) -> List[Tuple[List[Tuple[int, int]], List[dict], Tuple[float, int, int]]]:
    """场面交换搜索（独立于路径搜索，单独的启发函数），按价值排序返回前 top_n 个场面。

    枚举候选交换方案（每个我方随从最多主动攻击一次；敌方随从只要没死，
    可被多个我方随从选为目标；enemy_index == 0 表示攻击敌方英雄；
    最多 max_trades 个交换；枚举量受 max_plans 硬上限保护，不影响性能），
    评分 = exchange_heuristic（我方随从栏） + 敌方英雄血量评分
    （剩余总血量所需龙数越少越好：31 血(2龙) > 33 血(3龙)，46 血(3龙) > 48血+2甲(4龙)）
    + 送牛且暗在手惩罚。返回 [(交换计划, 交换后随从栏, (分数,空位,随从数))] 按分数降序。
    diversity（temperature，0~1）：>0 时在做 top_n 截取前加入差异化——优先覆盖
    “不同的随从栏空位 / 不同英雄血量档 / 不同存活随从”，让可能产生最优解的
    异质场面也有机会入选（0=纯按分数取前 top_n）。
    """
    # 只有攻击力 >= 1 的我方随从能主动发起交换（0 攻随从不能攻击）
    friend_indices = [
        index
        for index, item in enumerate(board, start=1)
        if int(item.get("attack") or 0) >= 1
    ]
    enemy_indices = [0] + list(range(1, len(enemy_board) + 1))  # 0 = 敌方英雄
    if not friend_indices:
        return []

    # 空位线性/饱和的条件式规则（每条对应 logs 里的具体漏解）：
    # - 场上原版腾武（回手引擎）→ 饱和：保留引擎优先（102544/085526/081102）；
    # - 手牌 < 6 张 → 饱和：牌太少，腾格无牌可打（050857，手牌5张，送刀后0伤）；
    # - 舞动全场+暗影步 都在手 → 饱和：双回手在手，引擎会被弹回重打（173736）；
    # - 其余（手牌≥6 且非双回手）→ 线性：连招靠堆格子，送随从腾第5/6格有价值
    #   （213918/185407/004634/051204 等 13 个漏解场面）。
    hand = hand or []
    tenwu_scene = any(b.get("name") == "赤烟·腾武" for b in board)
    hand_names = {str(h.get("name", "")) for h in hand}
    has_wu = "舞动全场（ft.迦罗娜）" in hand_names
    has_shadowstep = "暗影步" in hand_names
    free_slot_linear = (
        (not tenwu_scene)
        and len(hand) >= 6
        and not (has_wu and has_shadowstep)
    )

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
    ranked: List[Tuple[float, List[Tuple[int, int]], List[dict], tuple]] = []
    seen_boards: set = set()

    for plan in plans:
        pairs = [(plan[k], plan[k + 1]) for k in range(0, len(plan), 2)]
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
            score = exchange_heuristic(
                traded_board, hand=hand, etc_band=etc_band,
                free_slot_linear=free_slot_linear,
            )
            score_cache[board_key] = score

        score_val = score[0]

        # 敌方英雄血量评分：剩余总血量所需龙数越少越好
        # （血量 31(2龙) > 33(3龙)；46(3龙) > 48血+2甲=50(4龙)）
        # 不交换的空场面也承担同样的英雄血量惩罚，使“攻击英雄”与“不交换”可比
        after_total = None

        if _traded_hero is not None:
            after_total = int(_traded_hero.get("health") or 0) + int(_traded_hero.get("armor") or 0)
            score_val -= _hero_dragons_needed(after_total) * HERO_DRAGON_WEIGHT

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

        # 新维度：牺牲 1/1 复制体加分——只对“与敌方随从交换后死亡”的 1/1 随从生效；
        # 打英雄（ei==0）我方不掉血不计；高价值随从（牛/龙等）的保留分仍主导，不会误伤。
        sac_1_1 = 0

        for fi, ei in pairs:
            if ei == 0 or not (1 <= fi <= len(board)) or not (1 <= ei <= len(enemy_board)):
                continue
            f = board[fi - 1]
            e = enemy_board[ei - 1]
            f_health = int(f.get("health") or 0)
            e_atk = int(e.get("attack") or 0)

            if f_health == 1 and f_health <= e_atk:
                sac_1_1 += 1

        if sac_1_1:
            score_val += sac_1_1 * EXCHANGE_SAC_1_1_BONUS

        # 去重键仍以我方随从栏为主，但把英雄剩余血量也纳入：
        # 攻击英雄不改变随从栏，但会把英雄血量打到不同档位，不能被“不交换”去重掉
        dedup_key = (board_key, after_total)

        if dedup_key in seen_boards:
            continue  # 相同随从栏且英雄血量相同的只保留一个（按价值最高的）

        seen_boards.add(dedup_key)
        # 差异化特征：(空位数, 英雄血量档(所需龙数+mod16), 存活随从牌名集合)
        # 存活随从按“牌名”而不是“牌名+血量+攻击”聚类，避免同类型不同身材的
        # 复制体（如 3/3 刀 vs 1/1 刀）被误当成两个完全不同的场面
        free_slots = score[1]
        hero_bucket = None
        if after_total is not None:
            hero_bucket = (
                _hero_dragons_needed(after_total),
                after_total % 16,
            )
        surv_names = tuple(sorted(str(item.get("name", "")) for item in traded_board))
        feat = (free_slots, hero_bucket, surv_names)
        ranked.append((score_val, pairs, traded_board, feat))

    ranked.sort(key=lambda x: x[0], reverse=True)
    if diversity > 0 and top_n > 1:
        ranked = _diversify_ranked(ranked, top_n, diversity)
    return [
        (plan, rboard, (score_val, score[1], score[2]))
        for score_val, plan, rboard, _feat in ranked[:top_n]
    ]


def _diversify_ranked(
    ranked: List[Tuple[float, List[Tuple[int, int]], List[dict], tuple]],
    top_n: int,
    diversity: float,
) -> List[Tuple[float, List[Tuple[int, int]], List[dict], tuple]]:
    """temperature 多样化截取：优先覆盖不同的 (空位数, 英雄血量档, 存活随从)。

    第一个永远取全局最高分；之后按分数降序扫描，只要候选在三个维度中任一与
    已选不同（novel），且分数不低于 best - diversity*分数跨度 就纳入；
    若仍未满 top_n，先按 novel 补足（不限分），再按分数补足。
    """
    if diversity <= 0 or top_n <= 1 or len(ranked) <= top_n:
        return ranked[:top_n]

    best_score = ranked[0][0]
    span = max(1e-9, ranked[0][0] - ranked[-1][0])
    gate = best_score - diversity * span
    selected = [ranked[0]]
    used = {0}
    seen_free = {ranked[0][3][0]}
    seen_hero = {ranked[0][3][1]}
    seen_surv = {ranked[0][3][2]}

    def _accept(item: tuple) -> bool:
        free, hero_b, surv = item[3]
        novel = free not in seen_free or hero_b not in seen_hero or surv not in seen_surv
        if not novel:
            return False
        seen_free.add(free)
        seen_hero.add(hero_b)
        seen_surv.add(surv)
        return True

    # 第一轮：分数过门限的新维度场面
    for i, item in enumerate(ranked[1:], start=1):
        if len(selected) >= top_n:
            break
        if i in used or item[0] < gate:
            continue
        if _accept(item):
            selected.append(item)
            used.add(i)

    # 第二轮：不限分，只补新维度
    for i, item in enumerate(ranked):
        if len(selected) >= top_n:
            break
        if i in used:
            continue
        if _accept(item):
            selected.append(item)
            used.add(i)

    # 第三轮：仍不足则按分数补足
    for i, item in enumerate(ranked):
        if len(selected) >= top_n:
            break
        if i not in used:
            selected.append(item)
            used.add(i)

    return selected


def plan_exchanges(
    board: List[dict],
    enemy_board: List[dict],
    hand: Optional[List[dict]] = None,
    etc_band: Optional[List[str]] = None,
    hero: Optional[dict] = None,
    max_trades: int = 2,
    max_plans: int = 4000,
) -> Tuple[List[Tuple[int, int]], List[dict], Tuple[float, int, int]]:
    """返回最优单个交换计划（plan_exchanges_top 的 top_n=1 便捷包装）。"""
    ranked = plan_exchanges_top(
        board, enemy_board, hand=hand, etc_band=etc_band, hero=hero,
        top_n=1, max_trades=max_trades, max_plans=max_plans,
    )

    if not ranked:
        tenwu_scene = any(b.get("name") == "赤烟·腾武" for b in board)
        base_score = exchange_heuristic(
            board, hand=hand, etc_band=etc_band,
            free_slot_linear=not tenwu_scene,
        )
        return [], list(board), base_score

    plan, rboard, score = ranked[0]
    return plan, rboard, score


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


def build_dredge_options(snapshot: Dict[str, object]) -> List[str]:
    """垂钓时光探底分叉池：只认阅读器已记录的底牌（最多 3 张）。

    垂钓时光是“探底：随机三张里选一张”，没有记录到底牌时不能假设底部有
    某个随从；缺位由 C++ 以“未知杂牌”兜底。
    """
    dredge = []

    for item in snapshot.get("dredge_bottom") or []:
        name = str(item.get("name")) if isinstance(item, dict) else str(item)

        if name and name != "未知杂牌":
            dredge.append(name)

    return dredge[:3]


def build_payload(
    snapshot: Dict[str, object],
    *,
    min_alex: int = 1,
    max_alex: int = 10,
    depth: int = 30,
    max_paths: int = 1000000,
    threads: Optional[int] = None,
    time_budget_sec: float = 3.0,
    # 时间预算（秒）；0 / 负数 = 不限时：按束宽×最大深度跑完，不因时间停止
    heuristic: int = 6,
    wide_widths: Optional[List[int]] = None,
    heuristics: Optional[List[int]] = None,
    etc_band: Optional[List[str]] = None,
    exchanges: Optional[List[tuple]] = None,
    only_best_damage: bool = False,
    discover_quickdraw_choice: Optional[str] = None,
    forced_draw_choice: Optional[str] = None,
    forced_draw_card: Optional[str] = None,
    branch_prefix: Optional[List[str]] = None,
    lethal_threshold: int = -1,
    branch_expand: bool = True,
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
        "threads": threads if threads else default_threads(),
        "time_budget_sec": time_budget_sec,
        "heuristic": heuristic,
        "only_best_damage": 1 if only_best_damage else 0,
        "discover_quickdraw_choice": discover_quickdraw_choice or "",
        "forced_draw_choice": forced_draw_choice or "",
        "forced_draw_card": forced_draw_card or "",
        "branch_prefix": branch_prefix or [],
        "lethal_threshold": int(lethal_threshold),
        "branch_expand": bool(branch_expand),
        # 默认四通道：H6/1100（8水晶十龙深线）、H1/1500（4水晶十龙/紧线）、
        # H2/1100（96 伤线）、H2/3000（6水晶紧 48 伤线）
        "wide_widths": wide_widths or [1100, 1500, 1100, 3000],
        "heuristics": heuristics or [6, 1, 2, 2],
        # 牌库是否“完全已知”：只要还有未揭示的牌库实体就不算完整牌库。
        # （Power.log 只揭示抽到/探明的牌，先前用 bool(deck) 会把残缺牌库当完整，
        #   导致行骗等按“无随从/无法术”误判。）
        "deck_is_known": int(snapshot.get("deck_unknown_cards") or 0) == 0,
        "deck": [{"name": item["name"]} for item in snapshot.get("deck") or []],
        # 垂钓时光探底已知牌（阅读器自动追踪；缺位视为未知杂牌）。
        # 未知底牌可能是缺失的组合随从（如牌库只剩晦）：补进探底分叉，
        # 让搜索/WhatIF 探索“垂钓时光拿到晦回费”等可能；仍以未知杂牌兜底。
        "dredge_bottom": build_dredge_options(snapshot),
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
    threads: Optional[int] = None,
    time_budget_sec: float = 3.0,
    heuristic: int = 6,
    wide_widths: Optional[List[int]] = None,
    heuristics: Optional[List[int]] = None,
    etc_band: Optional[List[str]] = None,
    exchanges: Optional[List[tuple]] = None,
    only_best_damage: bool = False,
    discover_quickdraw_choice: Optional[str] = None,
    forced_draw_choice: Optional[str] = None,
    forced_draw_card: Optional[str] = None,
    branch_prefix: Optional[List[str]] = None,
    lethal_threshold: int = -1,
    branch_expand: bool = True,
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
        forced_draw_choice=forced_draw_choice,
        forced_draw_card=forced_draw_card,
        branch_prefix=branch_prefix,
        lethal_threshold=lethal_threshold,
        branch_expand=branch_expand,
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
# 组合 = {鱼,狐,刀,暗,牛,晦,腾}；主/小窗勾选“牌库剩余随从”可覆盖默认集合。
COMBO_MINION_SETS = [
    ["鲨鱼之灵", "狐人老千", "斯卡布斯·刀油", "暗影施法者",
     "乐队经理精英牛头人酋长", "晦鳞巢母", "赤烟·腾武"],
]
# 抽随从卡：卡名 -> (基础费用, 抽随从张数)
DRAW_MINION_SPELLS = {
    "挖掘宝藏": (1, 1),
    "潜伏帷幕": (3, 2),
    "行骗": (2, 1),  # 连击牌：必须先出过一张牌才能触发抽牌
    "垂钓时光": (1, 1),
}

# “可能抽到”的随从优先级：鱼 > 刀 > 腾 > 牛 > 暗 > 晦 > 狐（其余最低）
DRAW_PRIORITY = {
    "鲨鱼之灵": 6,
    "斯卡布斯·刀油": 5,
    "赤烟·腾武": 4.5,
    "乐队经理精英牛头人酋长": 4,
    "暗影施法者": 3,
    "晦鳞巢母": 2,
    "狐人老千": 1,
}


# ===================== 统一 W-B 机制（WhatIf-Branch） =====================
# 分支卡 = 抽随从卡（潜伏帷幕/挖掘宝藏/行骗/垂钓时光）与 持枪要挟。
# 在分支卡使用处用 [场面变化] 增量评估函数对分支前后打分（复用抽牌优先级、
# 随从保留、空位、法力等既有维度；只按“新增内容/场面需求”增量计算，不重复算两次），
# 按加权得分决定该分支节点是否往下探索；探索的分支用真实搜索回溯得到最终伤害/路径。

# 持枪要挟发现牌池（全部已知）
QUICKDRAW_CHOICES = ("补水", "脱水", "误炸", "袋底藏沙", "不许乱动")
# 其余未建模快枪牌按类型拆成两个杂牌分支（按数量加权计入 WhatIF 平均）：
#   随从杂牌（4 张）：农场小助手/银蛇/和善的银行职员/亮石旋岩虫 → 占随从栏
#   法术杂牌（2 张）：热浪来袭/列车难题 → 不占随从栏
QUICKDRAW_OTHER_MINION = "其他快枪牌·随从"
QUICKDRAW_OTHER_MINION_COUNT = 4
QUICKDRAW_OTHER_SPELL = "其他快枪牌·法术"
QUICKDRAW_OTHER_SPELL_COUNT = 2
QUICKDRAW_WEIGHTS = {name: 1 for name in QUICKDRAW_CHOICES}
QUICKDRAW_WEIGHTS[QUICKDRAW_OTHER_MINION] = QUICKDRAW_OTHER_MINION_COUNT
QUICKDRAW_WEIGHTS[QUICKDRAW_OTHER_SPELL] = QUICKDRAW_OTHER_SPELL_COUNT


def wb_draw_delta(drawn_minions: List[str]) -> float:
    """抽随从卡分支增量分：抽到优先级越高的组合随从，场面提升越大。"""
    return sum(DRAW_PRIORITY.get(mn, 0) for mn in drawn_minions)


def wb_quickdraw_delta(snapshot: Dict[str, object], card: str) -> float:
    """持枪要挟分支增量分：按当前场面需求加权。
    - 需要腾格子（空位少）→ 误炸/脱水/不许乱动 价值高；
    - 需要费用且无法用晦回费 → 补水 价值高。"""
    board = snapshot.get("board") or []
    hand = snapshot.get("hand") or []
    free_slots = max(0, MAX_BOARD_SLOTS - len(board))
    mana = int(snapshot.get("mana") or 0)
    names = {str(h.get("name", "")) for h in hand}
    names |= {str(b.get("name", "")) for b in board}
    need_slots = free_slots <= 2
    need_mana = mana <= 2 and "晦鳞巢母" not in names

    if card == "误炸":
        return (3.0 + (4 - free_slots) * 0.8) if need_slots else 1.0
    if card == "脱水":
        return (2.5 + (4 - free_slots) * 0.5) if need_slots else 1.0
    if card == "补水":
        return 3.5 if need_mana else 1.5
    if card == "不许乱动":
        return (2.0 + (4 - free_slots) * 0.4) if need_slots else 1.0
    if card == "袋底藏沙":
        return 1.0
    return 0.5


def _draw_combinations(missing: List[str], count: int) -> List[List[str]]:
    """牌库剩余随从里取 count 张的所有组合（如 潜伏帷幕 抽 2 张，剩余 3 张 → C(3,2)=3）。"""
    if count <= 0 or count > len(missing):
        return []

    result: List[List[str]] = []

    def rec(start: int, chosen: List[str]) -> None:
        if len(chosen) == count:
            result.append(list(chosen))
            return

        for i in range(start, len(missing)):
            chosen.append(missing[i])
            rec(i + 1, chosen)
            chosen.pop()

    rec(0, [])
    return result


def _draw_play_variants(
    snapshot: Dict[str, object], draw_card: str
) -> List[Tuple[str, List[str], int, bool]]:
    """分支卡的不同打法（= 分支节点）：直接 / 伺机 / 币 / 伺机+币。
    返回 [(打法名, 移除卡, 实付费用, 是否打了幸运币(+1法力))]。"""
    hand_names = [str(h.get("name", "")) for h in snapshot.get("hand") or []]
    cost = 1 if draw_card == "持枪要挟" else DRAW_MINION_SPELLS.get(draw_card, (0, 0))[0]
    variants: List[Tuple[str, List[str], int, bool]] = [("直接", [], cost, False)]

    if "伺机待发" in hand_names:
        variants.append(("伺机", ["伺机待发"], max(0, cost - 2), False))

    if "幸运币" in hand_names:
        variants.append(("币", ["幸运币"], cost, True))

    if "伺机待发" in hand_names and "幸运币" in hand_names:
        variants.append(("伺机+币", ["伺机待发", "幸运币"], max(0, cost - 2), True))

    return variants


def wb_play_delta(mana_after: int) -> float:
    """打法节点增量分：剩余法力越多，后续场面价值越高（增量，不重复评估前后）。"""
    return float(mana_after) * 0.3


def _build_play_variant(
    snapshot: Dict[str, object],
    draw_card: str,
    remove_cards: List[str],
    eff_cost: int,
    coin_played: bool,
) -> Dict[str, object]:
    """分支卡打出后的基础变体：移除分支卡与省费来源，法力按实付扣减（币 +1）。"""
    hand = snapshot.get("hand") or []
    mana = int(snapshot.get("mana") or 0)

    if coin_played:
        mana += 1

    variant = dict(snapshot)
    variant["hand"] = [
        h for h in hand
        if str(h.get("name", "")) not in (set(remove_cards) | {draw_card})
    ]
    variant["mana"] = max(0, mana - eff_cost)
    return variant


def compute_wb_tree(
    snapshot: Dict[str, object],
    options: Optional[Dict[str, object]] = None,
    node_top_k: int = 3,
    draw_top_k: int = 3,
    quickdraw_top_k: int = 5,
    depth: int = 0,
    max_depth: int = 3,
) -> Optional[Dict[str, object]]:
    """递归统一 W-B 机制：分支节点 = 分支卡的不同打法（直接/伺机/币/伺机+币），
    节点内展开其分支（抽随从组合 C（无序）/ 持枪发现牌），按 [场面变化] 增量评分
    取 top-K；每个分支内再递归识别新出现的分支卡并展开子节点（深度受 max_depth 限制）。
    返回 {"kind":"wb", "nodes":[打法节点]} 或 None。

    每个节点：
      {"card": 分支卡, "kind": "draw"|"quickdraw", "play": 打法名,
       "path": 分支前打牌路径（如 伺机待发->潜伏帷幕）,
       "branches": [{"drawn": 抽到的随从组合(无序) | "card": 持枪发现牌,
                     "delta": 增量分, "variant": 打法+抽牌后的变体(仅 draw),
                     "children": 该分支内递归展开的新分支节点}]}
    """
    hand = snapshot.get("hand") or []
    board = snapshot.get("board") or []
    hand_names = [str(h.get("name", "")) for h in hand]
    have = set(hand_names) | {str(b.get("name", "")) for b in board}
    combo = (
        list(options.get("whatif_combo") or COMBO_MINION_SETS[0])
        if options
        else list(COMBO_MINION_SETS[0])
    )
    missing = [n for n in combo if n not in have]
    missing.sort(key=lambda n: -DRAW_PRIORITY.get(n, 0))
    nodes: List[Dict[str, object]] = []

    # 抽随从卡：每个打法（直接/伺机/币/伺机+币）= 一个分支节点；
    # 节点内分支 = 抽到的随从组合（无序 C，如 潜伏帷幕 抽2 从3 剩余 → C(3,2)=3）
    for dcard in DRAW_MINION_SPELLS:
        if dcard not in hand_names:
            continue

        count = DRAW_MINION_SPELLS[dcard][1]
        combos = list(_draw_combinations(missing, count))

        if not combos:
            continue

        # 分支按增量分排序（无序组合，取 top-K）
        combos.sort(key=wb_draw_delta, reverse=True)
        combos = combos[:draw_top_k]

        for play, remove, eff_cost, coin in _draw_play_variants(snapshot, dcard):
            base = _build_play_variant(snapshot, dcard, remove, eff_cost, coin)
            play_score = wb_play_delta(int(base.get("mana") or 0))
            branches = []

            for drawn in combos:
                br_variant = dict(base)
                br_variant["hand"] = list(base["hand"]) + [
                    {"name": mn} for mn in drawn
                ]
                delta = play_score + wb_draw_delta(drawn)
                branches.append(
                    {
                        "drawn": drawn,
                        "delta": round(delta, 2),
                        "variant": br_variant,
                    }
                )

            nodes.append(
                {
                    "card": dcard,
                    "kind": "draw",
                    "play": play,
                    "path": list(remove) + [dcard],
                    "branches": branches,
                }
            )

    # 持枪要挟：不同打法 = 分支节点；分支 = 发现牌（按增量分取 top-K）
    if "持枪要挟" in hand_names:
        choices = sorted(QUICKDRAW_CHOICES, key=lambda c: -wb_quickdraw_delta(snapshot, c))
        choices = choices[:quickdraw_top_k]

        for play, remove, eff_cost, coin in _draw_play_variants(snapshot, "持枪要挟"):
            base = _build_play_variant(snapshot, "持枪要挟", remove, eff_cost, coin)
            play_score = wb_play_delta(int(base.get("mana") or 0))
            branches = []

            for choice in choices:
                # 持枪要挟打出后的变体（发现牌入手），供分支内继续递归展开新分支卡
                q_variant = dict(base)
                q_variant["hand"] = list(base["hand"]) + [{"name": choice}]
                branches.append(
                    {
                        "card": choice,
                        "delta": round(
                            play_score + wb_quickdraw_delta(snapshot, choice), 2
                        ),
                        "recursive_variant": q_variant,
                    }
                )

            nodes.append(
                {
                    "card": "持枪要挟",
                    "kind": "quickdraw",
                    "play": play,
                    "path": list(remove) + ["持枪要挟"],
                    "branches": branches,
                }
            )

    if not nodes:
        return None

    # 分支节点 top-K：按各节点最佳分支增量分排序，取前 node_top_k 个
    nodes.sort(
        key=lambda node: max(
            (b.get("delta") or 0) for b in (node.get("branches") or [])
        ),
        reverse=True,
    )
    nodes = nodes[:node_top_k]

    # 分支内递归：每个分支的变体里若还有新的分支卡，展开为子节点
    if depth < max_depth:
        for node in nodes:
            for br in node.get("branches") or []:
                # draw 分支用 variant，quickdraw 分支用 recursive_variant；任一存在都可继续递归
                variant = br.get("variant") or br.get("recursive_variant")

                if not variant:
                    continue

                children = compute_wb_tree(
                    variant,
                    options,
                    node_top_k=node_top_k,
                    draw_top_k=draw_top_k,
                    quickdraw_top_k=quickdraw_top_k,
                    depth=depth + 1,
                    max_depth=max_depth,
                )
                br["children"] = children

    return {"kind": "wb", "nodes": nodes}
