# -*- coding: utf-8 -*-
"""局面存档：计算前查重、计算后归档，可同步到云端（GitHub 仓库）。

保存格式（每个局面一块）：
    手牌
    战场
    状态
    牌库
    N龙（路径）：N<4 只保留一条，N>=4 全部保留

去重规则：手牌乱序视为一致（多集比较）；不一致只取决于
费用 / 卡牌类型 / 数量（含战场、状态、牌库）。
"""
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "存档"
ARCHIVE_PATH = ARCHIVE_DIR / "红龙贼-情况存档.json"
FORMULA_PATH = ARCHIVE_DIR / "红龙贼-公式表.json"

_ALEX_NAME = "生命的缚誓者阿莱克丝塔萨"
_COIN_CARD_NAMES = frozenset({"幸运币", "伪造的幸运币"})

# 核心牌归纳（其余全部视为杂牌，只计数量）：
# 鱼/狐/刀/牛/晦/暗/殒/步/骨/伺/币/舞/龙/药
CORE_CARD_GROUPS = {
    "鱼": "鲨鱼之灵",
    "狐": "狐人老千",
    "刀": "斯卡布斯·刀油",
    "牛": "乐队经理精英牛头人酋长",
    "晦": "晦鳞巢母",
    "暗": "暗影施法者",
    "殒": "殒命暗影",
    "步": "暗影步",
    "骨": "锯齿骨刺",
    "伺": "伺机待发",
    "币": None,  # 幸运币 / 伪造的幸运币 合并
    "舞": "舞动全场（ft.迦罗娜）",
    "龙": "生命的缚誓者阿莱克丝塔萨",
    "药": "幻觉药水",
}
_CORE_NAME_TO_KEY = {
    name: key for key, name in CORE_CARD_GROUPS.items() if name
}


def _card_key(card) -> Tuple:
    name = "幸运币" if card.name in _COIN_CARD_NAMES else card.name
    return (name, card.current_cost(), card.card_type)


def hand_signature(state) -> Tuple:
    """手牌：乱序也一致，按 (名称, 费用, 类型) 排序后的多集。"""
    return tuple(sorted(_card_key(card) for card in state.hand))


def board_signature(state) -> Tuple:
    """战场：按 (名称, 费用, 类型, 血量) 保持入场顺序。"""
    return tuple(
        _card_key(card) + (card.health,)
        for card in state.board
    )


def state_effect_signature(state) -> Tuple:
    """状态：水晶/法力/各类减费/当前效果/武器/奥秘/殒命暗影标记等。"""
    return (
        state.mana_crystals,
        state.mana,
        state.next_spell_discount,
        state.next_combo_discount,
        state.next_card_discount,
        tuple(state.active_card_discounts),
        tuple(state.etc_band_remaining),
        state.weapon.name if state.weapon else None,
        tuple(sorted(card.name for card in state.secrets)),
        tuple(sorted(card.name for card in state.hand if card.is_deadly_shadow)),
        state.cards_played_this_turn,
    )


def deck_signature(state) -> Tuple:
    return tuple(sorted(_card_key(card) for card in state.deck))


def situation_signature(state) -> Tuple:
    return (
        hand_signature(state),
        board_signature(state),
        state_effect_signature(state),
        deck_signature(state),
    )


def signature_key(signature: Tuple) -> str:
    return json.dumps(signature, ensure_ascii=False, sort_keys=True)


def _card_label(card) -> str:
    cost = card.current_cost()
    cost_text = "*费" if cost is None else f"{cost}费"
    health_text = (
        f",{card.health}血"
        if card.card_type == "minion" and card.health is not None
        else ""
    )
    shadow_text = "[殒命暗影]" if card.is_deadly_shadow else ""
    return f"{card.name}{shadow_text}[{cost_text}{health_text}]"


def render_situation_text(state, results: List[Any], params: Optional[Dict[str, Any]] = None) -> str:
    """按用户指定格式渲染局面存档块。"""
    lines = []
    lines.append("手牌")
    lines.append(
        "，".join(f"{index}.{_card_label(card)}" for index, card in enumerate(state.hand, start=1))
        or "空"
    )
    lines.append("战场")
    lines.append(
        "，".join(f"{index}.{_card_label(card)}" for index, card in enumerate(state.board, start=1))
        or "空"
    )
    lines.append("状态")
    state_parts = [f"{state.mana}法力/{state.mana_crystals}水晶"]

    if state.next_spell_discount > 0:
        state_parts.append(f"法术减{state.next_spell_discount}")

    if state.next_combo_discount > 0:
        state_parts.append(f"连击减{state.next_combo_discount}")

    if state.next_card_discount > 0:
        state_parts.append(f"下张减{state.next_card_discount}")

    if state.active_card_discounts:
        state_parts.append(f"刀油减费{list(state.active_card_discounts)}")

    if state.weapon:
        state_parts.append(f"武器：{state.weapon.name}")

    if state.etc_band_remaining:
        state_parts.append(f"牛池：{'/'.join(state.etc_band_remaining)}")

    lines.append("；".join(state_parts) or "无")
    lines.append("牌库")
    lines.append(
        "，".join(f"{index}.{_card_label(card)}" for index, card in enumerate(state.deck, start=1))
        or "空（未知）"
    )

    best_count = max((item.alex_play_count for item in results), default=0)
    lines.append(f"{best_count}龙")

    for result in sorted(
        results,
        key=lambda item: (-item.alex_play_count, -item.alex_damage, item.mana),
    ):
        lines.append("  " + " -> ".join(result.path))

    return "\n".join(lines)


def _retain_paths(paths: List[str], dragon_count: int) -> List[str]:
    """N<4 只保留一条路径；N>=4 全部保留。"""
    if dragon_count < 4:
        return paths[:1]

    return paths


def load_archive(archive_path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(archive_path) if archive_path else ARCHIVE_PATH

    if not path.exists():
        return {"version": 1, "situations": {}}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"version": 1, "situations": {}}


def save_archive(archive: Dict[str, Any], archive_path: Optional[Path] = None) -> Path:
    path = Path(archive_path) if archive_path else ARCHIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)

    tmp_path.replace(path)
    return path


def lookup_situation(
    state,
    archive_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """计算前查重：同一局面（手牌乱序/费用/类型/数量一致）直接返回缓存。"""
    archive = load_archive(archive_path)
    key = signature_key(situation_signature(state))
    entry = archive.get("situations", {}).get(key)
    return entry if entry else None


def remember_situation(
    state,
    results: List[Any],
    params: Optional[Dict[str, Any]] = None,
    archive_path: Optional[Path] = None,
) -> Path:
    """计算后归档：按龙数分组保存路径，N<4 一条、N>=4 全部。"""
    archive = load_archive(archive_path)
    key = signature_key(situation_signature(state))
    situations = archive.setdefault("situations", {})
    existing = situations.get(key)
    paths_by_count: Dict[str, List[str]] = {}

    for item in results:
        count = item.alex_play_count
        paths_by_count.setdefault(str(count), []).append(" -> ".join(item.path))

    if existing is None:
        entry = {
            "签名": signature_key(situation_signature(state)),
            "手牌": "，".join(
                f"{index}.{_card_label(card)}"
                for index, card in enumerate(state.hand, start=1)
            )
            or "空",
            "战场": "，".join(
                f"{index}.{_card_label(card)}"
                for index, card in enumerate(state.board, start=1)
            )
            or "空",
            "状态": "；".join(
                part
                for part in (
                    f"{state.mana}法力/{state.mana_crystals}水晶",
                    f"法术减{state.next_spell_discount}" if state.next_spell_discount > 0 else "",
                    f"连击减{state.next_combo_discount}" if state.next_combo_discount > 0 else "",
                    f"下张减{state.next_card_discount}" if state.next_card_discount > 0 else "",
                    f"刀油减费{list(state.active_card_discounts)}" if state.active_card_discounts else "",
                    f"武器：{state.weapon.name}" if state.weapon else "",
                    f"牛池：{'/'.join(state.etc_band_remaining)}" if state.etc_band_remaining else "",
                )
                if part
            )
            or "无",
            "牌库": "，".join(
                f"{index}.{_card_label(card)}"
                for index, card in enumerate(state.deck, start=1)
            )
            or "空（未知）",
            "路径": {},
            "最多龙数": 0,
            "计算参数": params or {},
            "首次存档": time.strftime("%Y-%m-%d %H:%M:%S"),
            "更新时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        situations[key] = entry
        existing = entry

    # 合并规则（针对“错误计算也会被缓存”的修正）：
    # - 新旧龙数取并集，绝不丢旧路径；
    # - 同一龙数下新算出的路径排前面（优先采用刚验证过的结论）；
    # - 若新计算搜出了更高的龙数，整块结果以新计算为准，旧的低龙数结果自动退居其次。
    all_counts = set(paths_by_count) | set(existing.get("路径", {}))
    merged_paths: Dict[str, List[str]] = {}

    for count_text in sorted(all_counts, key=lambda item: -int(item)):
        count = int(count_text)
        new_paths = paths_by_count.get(count_text, [])
        old_paths = existing.get("路径", {}).get(count_text, [])

        if new_paths:
            merged = list(dict.fromkeys(new_paths + old_paths))
        else:
            merged = list(old_paths)

        merged_paths[count_text] = _retain_paths(merged, count)

    existing["路径"] = merged_paths
    existing["最多龙数"] = max(
        (int(count_text) for count_text in merged_paths),
        default=0,
    )
    existing["更新时间"] = time.strftime("%Y-%m-%d %H:%M:%S")

    if params:
        existing["计算参数"].update(params)

    return save_archive(archive, archive_path)


def sync_archive_to_cloud(archive_path: Optional[Path] = None) -> str:
    """把存档提交并推送到云端（GitHub 仓库）。"""
    path = Path(archive_path) if archive_path else ARCHIVE_PATH

    if not path.exists():
        return "存档文件不存在，请先完成一次计算。"

    try:
        add = subprocess.run(
            ["git", "add", "--", str(path.relative_to(Path.cwd()))],
            check=True,
            capture_output=True,
            text=True,
        )
        commit = subprocess.run(
            ["git", "commit", "-m", "存档：更新红龙贼局面存档"],
            capture_output=True,
            text=True,
        )
        push = subprocess.run(
            ["git", "push"],
            capture_output=True,
            text=True,
        )

        if push.returncode != 0:
            return f"已提交，但推送失败：{push.stderr.strip()}"

        return "已同步到云端（GitHub 仓库）。"
    except Exception as error:
        return f"同步失败：{error}"


def format_cached_paths(entry: Dict[str, Any]) -> str:
    lines = []
    lines.append("命中存档（相同局面已计算过，直接输出）：")
    lines.append("")
    lines.append(f"手牌：{entry.get('手牌', '')}")
    lines.append(f"战场：{entry.get('战场', '')}")
    lines.append(f"状态：{entry.get('状态', '')}")
    lines.append(f"牌库：{entry.get('牌库', '')}")
    params = entry.get("计算参数") or {}

    if params:
        lines.append("")
        lines.append("缓存来源参数：" + json.dumps(params, ensure_ascii=False))

    lines.append("")

    paths = entry.get("路径", {})

    if not paths:
        lines.append("该局面此前未搜到任何红龙路径。")
        return "\n".join(lines)

    best = str(max(int(count) for count in paths))
    lines.append(f"最多龙数：{best}龙")

    for count_text in sorted(paths, key=lambda item: -int(item)):
        lines.append("")
        lines.append(f"{count_text}龙：")

        for path_text in paths[count_text]:
            lines.append("  " + path_text)

    return "\n".join(lines)


# =====================================================================
# 公式表：把局面抽象归一化（手牌乱序/杂牌任意变化视为同一情况），
# 记录该初局状态（抽象手牌 + 随从栏 + 水晶/法力）下的
# 最高龙数路径 与 最高伤害路径。
# =====================================================================
def abstract_hand(state) -> Tuple:
    """手牌抽象：核心牌单独计数（币合并），其余全部计为杂牌数量。"""
    counts: Dict[str, int] = {}
    filler = 0

    for card in state.hand:
        if card.name in _COIN_CARD_NAMES:
            counts["币"] = counts.get("币", 0) + 1
        elif card.name in _CORE_NAME_TO_KEY:
            key = _CORE_NAME_TO_KEY[card.name]
            counts[key] = counts.get(key, 0) + 1
        else:
            filler += 1

    if filler:
        counts["杂牌"] = filler

    return tuple(sorted(counts.items()))


def abstract_board(state) -> Tuple:
    """随从栏抽象：核心随从单独计数，其余计为杂牌数量。"""
    counts: Dict[str, int] = {}
    filler = 0

    for card in state.board:
        if card.name in _CORE_NAME_TO_KEY:
            key = _CORE_NAME_TO_KEY[card.name]
            counts[key] = counts.get(key, 0) + 1
        else:
            filler += 1

    if filler:
        counts["杂牌"] = filler

    return tuple(sorted(counts.items()))


def formula_key(state) -> str:
    """初局状态键 =（抽象手牌, 抽象随从栏, 水晶, 法力）。"""
    return json.dumps(
        {
            "手牌": abstract_hand(state),
            "随从栏": abstract_board(state),
            "水晶": state.mana_crystals,
            "法力": state.mana,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def load_formula(formula_path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(formula_path) if formula_path else FORMULA_PATH

    if not path.exists():
        return {"version": 1, "entries": {}}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"version": 1, "entries": {}}


def save_formula(table: Dict[str, Any], formula_path: Optional[Path] = None) -> Path:
    path = Path(formula_path) if formula_path else FORMULA_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, indent=2)

    tmp_path.replace(path)
    return path


def _best_path_record(item) -> Dict[str, Any]:
    return {
        "龙数": item.alex_play_count,
        "伤害": item.alex_damage,
        "剩余法力": item.mana,
        "路径": " -> ".join(item.path),
    }


def lookup_formula(state, formula_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """计算前查公式表：抽象局面（手牌乱序/杂牌变化）一致即命中。"""
    table = load_formula(formula_path)
    return table.get("entries", {}).get(formula_key(state))


def update_formula(
    state,
    results: List[Any],
    formula_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """计算后更新公式表（去重）：按抽象局面合并最高龙数/最高伤害路径。"""
    table = load_formula(formula_path)
    entries = table.setdefault("entries", {})
    key = formula_key(state)
    entry = entries.get(key)
    best_dragons = None
    best_damage = None

    for item in results:
        record = _best_path_record(item)

        if best_dragons is None or (
            record["龙数"],
            record["伤害"],
        ) > (best_dragons["龙数"], best_dragons["伤害"]):
            best_dragons = record

        if best_damage is None or (
            record["伤害"],
            record["龙数"],
        ) > (best_damage["伤害"], best_damage["龙数"]):
            best_damage = record

    if entry is None:
        entry = {
            "抽象手牌": dict(abstract_hand(state)),
            "抽象随从栏": dict(abstract_board(state)),
            "水晶": state.mana_crystals,
            "法力": state.mana,
            "最高龙数路径": None,
            "最高伤害路径": None,
            "示例手牌": "，".join(
                _card_label(card) for card in sorted(
                    state.hand,
                    key=lambda card: (card.current_cost() or 0, card.name),
                )
            ),
        }
        entries[key] = entry

    # 只升不降合并
    if best_dragons is not None and (
        entry["最高龙数路径"] is None
        or (best_dragons["龙数"], best_dragons["伤害"])
        > (
            entry["最高龙数路径"]["龙数"],
            entry["最高龙数路径"]["伤害"],
        )
    ):
        entry["最高龙数路径"] = best_dragons

    if best_damage is not None and (
        entry["最高伤害路径"] is None
        or (best_damage["伤害"], best_damage["龙数"])
        > (
            entry["最高伤害路径"]["伤害"],
            entry["最高伤害路径"]["龙数"],
        )
    ):
        entry["最高伤害路径"] = best_damage

    save_formula(table, formula_path)
    return entry


def format_formula_hit(entry: Dict[str, Any]) -> str:
    """命中公式表的展示文案。"""
    lines = [
        "命中公式表（抽象局面一致：手牌乱序/杂牌变化视为同一种情况）：",
        "",
        f"抽象手牌：{json.dumps(entry.get('抽象手牌', {}), ensure_ascii=False)}",
        f"抽象随从栏：{json.dumps(entry.get('抽象随从栏', {}), ensure_ascii=False)}",
        f"初局：{entry.get('水晶', '?')}水晶 / {entry.get('法力', '?')}法力",
        f"示例手牌：{entry.get('示例手牌', '')}",
        "",
    ]
    best_dragons = entry.get("最高龙数路径")
    best_damage = entry.get("最高伤害路径")

    if best_dragons:
        lines.append(f"最高龙数：{best_dragons['龙数']}龙 / {best_dragons['伤害']}伤 / 剩{best_dragons['剩余法力']}法力")
        lines.append("  " + best_dragons["路径"])

    if best_damage:
        lines.append(f"最高伤害：{best_damage['伤害']}伤 / {best_damage['龙数']}龙 / 剩{best_damage['剩余法力']}法力")
        lines.append("  " + best_damage["路径"])

    return "\n".join(lines)
