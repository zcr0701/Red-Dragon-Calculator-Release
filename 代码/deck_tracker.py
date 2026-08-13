# -*- coding: utf-8 -*-
"""记牌器式卡组记录 + 实时重建牌库。

数据源（优先级从高到低）：
  1. HDT 插件状态文件（%APPDATA%\\HearthstoneDeckTracker\\red_dragon_state.json，
     由 hdt_plugin/RedDragonStateExport.cs 实时导出 original_deck / remaining_deck）：
     完整 30 张卡组 + 实时剩余牌库（含 已抽/已出/洗入 的加减）。
  2. 本地卡组档案（deck_profile.json）：记录过该玩家的卡组后，即使没有 HDT，
     也能用档案重建剩余牌库（剩余 = 原卡组 − Power.log 追踪到的已出卡）。
  3. Power.log 兜底：只追踪本局从牌库离开的已知卡（drawn_deck），
     没有完整卡组时也能显示“已抽到过什么”。

重建结果写入 snapshot：
  original_deck  原卡组 [{card_id, name, count}]
  remaining_deck 剩余牌库 [{card_id, name, count}]
  drawn_deck     已离开牌库的卡 [{card_id, name, count}]
  deck           剩余牌库展开成逐张卡（供 GUI/引擎读取，name 可解析）
  deck_source    "hdt" / "profile" / "powerlog" / None
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional


BASE_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    # PyInstaller onedir：档案写到 exe 同目录（可写）
    _APP_DIR = Path(sys.executable).resolve().parent
else:
    _APP_DIR = BASE_DIR
PROFILE_PATH = _APP_DIR / "deck_profile.json"

_map_lock = threading.Lock()
_card_map: Optional[Dict[str, dict]] = None


def hdt_state_path() -> Path:
    env = os.environ.get("RED_DRAGON_STATE_PATH")
    if env:
        return Path(env)
    return (
        Path(os.environ.get("APPDATA", "")) / "HearthstoneDeckTracker" / "red_dragon_state.json"
    )


def _load_card_map() -> Dict[str, dict]:
    global _card_map
    if _card_map is not None:
        return _card_map
    with _map_lock:
        if _card_map is not None:
            return _card_map
        try:
            with open(BASE_DIR / "card_id_map.json", encoding="utf-8") as f:
                _card_map = json.load(f)
        except Exception:
            _card_map = {}
    return _card_map


def card_name(card_id: str) -> str:
    if not card_id:
        return ""
    info = _load_card_map().get(card_id)
    name = (info or {}).get("name")
    return name if name else card_id


def read_hdt_state(max_age: float = 8.0) -> Optional[dict]:
    """读取 HDT 插件导出的状态；文件过旧/不在对局中/无卡组时返回 None。"""
    path = hdt_state_path()

    try:
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > max_age:
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    if not data.get("in_game"):
        return None
    if not data.get("original_deck"):
        return None
    return data


def _expand(items: Optional[List[dict]]) -> List[dict]:
    """[{card_id, count}] → [{card_id, name, count}]（未知卡名兜底用 card_id）。"""
    out: List[dict] = []

    for item in items or []:
        cid = str(item.get("card_id") or "")
        count = int(item.get("count") or 0)

        if not cid or count <= 0:
            continue

        out.append({"card_id": cid, "name": card_name(cid), "count": count})

    return out


def _to_counts(items: List[dict]) -> Counter:
    return Counter({item["card_id"]: item["count"] for item in items})


def _load_profile() -> Dict[str, dict]:
    try:
        with open(PROFILE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_profile(profile: Dict[str, dict]) -> None:
    try:
        with open(PROFILE_PATH, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _profile_key(snap: dict) -> Optional[str]:
    name = str(snap.get("player_name") or "").strip()
    return name or None


def merge_snapshot(snap: dict, drawn_deck: Optional[Dict[str, int]] = None) -> dict:
    """把记牌器重建结果合并进快照（原地修改并返回）。"""
    original: List[dict] = []
    remaining: List[dict] = []
    drawn: List[dict] = []
    source: Optional[str] = None

    hdt = read_hdt_state()

    if hdt is not None:
        original = _expand(hdt.get("original_deck"))
        remaining = _expand(hdt.get("remaining_deck"))
        source = "hdt"

        # 记录该玩家卡组档案：之后没有 HDT 时也能重建
        key = _profile_key(snap)

        if key and original:
            profile = _load_profile()
            new_entry = {
                "original_deck": [
                    {"card_id": o["card_id"], "count": o["count"]}
                    for o in original
                ],
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            }

            if profile.get(key) != new_entry:
                profile[key] = new_entry
                _save_profile(profile)
    else:
        key = _profile_key(snap)

        if key:
            profile = _load_profile()
            saved = profile.get(key) or {}
            saved_original = _expand(saved.get("original_deck"))

            if saved_original:
                original = saved_original
                source = "profile"

        if not original and drawn_deck:
            # 纯 Power.log 兜底：没有卡组档案，只给出已出卡
            source = "powerlog"

    if original and not remaining:
        # 有卡组但 HDT 没给剩余：剩余 = 原卡组 − 已出（档案重建）
        name_to_id = {item["name"]: item["card_id"] for item in original}
        left = _to_counts(original) - Counter(
            {
                name_to_id.get(name, name): cnt
                for name, cnt in (drawn_deck or {}).items()
            }
        )
        remaining = [
            {"card_id": cid, "name": card_name(cid), "count": cnt}
            for cid, cnt in sorted(left.items())
            if cnt > 0
        ]

    # 已出卡：HDT 在场时用 原卡组 − 剩余（该卡组无洗入牌，减法准确）；
    # 否则用 Power.log 实体追踪的已出卡。
    if hdt is not None and original and remaining:
        left_drawn = _to_counts(original) - _to_counts(remaining)
        drawn = [
            {"card_id": cid, "name": card_name(cid), "count": cnt}
            for cid, cnt in sorted(left_drawn.items())
            if cnt > 0
        ]
    elif drawn_deck:
        drawn = [
            {"card_id": "", "name": name, "count": cnt}
            for name, cnt in sorted(drawn_deck.items())
        ]

    if remaining:
        # 展开成逐张卡（名字可解析），供 GUI / 引擎读取
        deck_items: List[dict] = []

        for item in remaining:
            cid = item["card_id"]
            info = _load_card_map().get(cid) or {}
            for _ in range(item["count"]):
                deck_items.append(
                    {
                        "card_id": cid,
                        "name": item["name"],
                        "cost": info.get("cost"),
                        "attack": info.get("attack"),
                        "health": info.get("health"),
                    }
                )

        snap["deck"] = deck_items
        snap["deck_unknown_cards"] = len(deck_items)

    snap["original_deck"] = original
    snap["remaining_deck"] = remaining
    snap["drawn_deck"] = drawn
    snap["deck_source"] = source
    return snap


def deck_summary(snap: dict) -> str:
    """GUI 用一行摘要：剩 N 张（原 M，已出 K，来源）。"""
    remaining = sum(item.get("count") or 0 for item in (snap.get("remaining_deck") or []))
    original = sum(item.get("count") or 0 for item in (snap.get("original_deck") or []))
    drawn = sum(item.get("count") or 0 for item in (snap.get("drawn_deck") or []))
    source = snap.get("deck_source")

    if source is None and not remaining and not original and not drawn:
        # 无记牌器数据：退回显示 hslog 当前牌库张数
        raw = len(snap.get("deck") or [])
        return f"牌库：{raw} 张" if raw else "牌库：-"

    parts = [f"牌库：剩 {remaining} 张"]

    if original:
        parts.append(f"原 {original}")

    if drawn:
        parts.append(f"已出 {drawn}")

    if source == "hdt":
        parts.append("HDT 记牌器")
    elif source == "profile":
        parts.append("卡组档案")
    elif source == "powerlog":
        parts.append("Power.log 追踪")

    if len(parts) > 1:
        return parts[0] + "（" + "；".join(parts[1:]) + "）"
    return parts[0]
