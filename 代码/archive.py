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

_ALEX_NAME = "生命的缚誓者阿莱克丝塔萨"


def _card_key(card) -> Tuple:
    return (card.name, card.current_cost(), card.card_type)


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

    for count_text, paths in paths_by_count.items():
        count = int(count_text)
        retained = _retain_paths(paths, count)
        existing["路径"].setdefault(count_text, [])

        for path_text in retained:
            if path_text not in existing["路径"][count_text]:
                existing["路径"][count_text].append(path_text)

        existing["路径"][count_text] = _retain_paths(
            existing["路径"][count_text],
            count,
        )

    existing["最多龙数"] = max(
        int(count_text) for count_text in existing["路径"]
    ) if existing["路径"] else 0
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
