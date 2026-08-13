# -*- coding: utf-8 -*-
"""记牌器式卡组记录 + 实时重建牌库。

数据源（优先级从高到低）：
  1. HDT 插件状态文件（%APPDATA%\\HearthstoneDeckTracker\\red_dragon_state.json，
     由 hdt_plugin/RedDragonStateExport.cs 实时导出 original_deck / remaining_deck）：
     完整 30 张卡组 + 实时剩余牌库（含 已抽/已出/洗入 的加减）。
  2. 收藏读取（炉石 Logs/会话目录/Decks.log）：游戏在进入收藏/选卡组/排到对局时
     会把完整卡组码（AAEB…）写进 Decks.log，解码后即得 30 张完整卡组，
     不需要 HDT，也不需要手动录入；剩余 = 原卡组 − Power.log 追踪到的已出卡。
  3. 本地卡组档案（deck_profile.json）：记录过该玩家的卡组后，即使没有 HDT，
     也能用档案重建剩余牌库（剩余 = 原卡组 − Power.log 追踪到的已出卡）。
  4. Power.log 兜底：只追踪本局从牌库离开的已知卡（drawn_deck），
     没有完整卡组时也能显示“已抽到过什么”。

所有匹配（剩余 = 原卡组 − 已出、卡组识别、档案累积）一律按唯一 card_id
（如 EX1_145）操作，不依赖本地化卡名；仅在核心/经典同名不同版本
（EX1_145 vs CORE_EX1_145）漂移时按卡名兜底扣减。

重建结果写入 snapshot：
  original_deck  原卡组 [{card_id, name, count}]
  remaining_deck 剩余牌库 [{card_id, name, count}]
  drawn_deck     已离开牌库的卡 [{card_id, name, count}]（drawn_deck_raw 为
                 {card_id: count}，不依赖卡名）
  deck           剩余牌库展开成逐张卡（供 GUI/引擎读取，name 可解析）
  deck_source    "hdt" / "profile" / "powerlog" / None
"""

from __future__ import annotations

import json
import os
import re
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
DBF_MAP_PATH = BASE_DIR / "dbf_id_map.json"

_map_lock = threading.Lock()
_card_map: Optional[Dict[str, dict]] = None
_dbf_map: Optional[Dict[str, dict]] = None


_DECK_CODE_RE = re.compile(r"\b(AAE[A-Za-z0-9+/=]+)")
_TS_RE = re.compile(r"^[IDW] (\d{2}:\d{2}:\d{2}\.\d+) ")


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


def _load_dbf_map() -> Dict[str, dict]:
    """dbfId → {card_id, name}（收藏卡组码解码用，静态文件 dbf_id_map.json）。"""
    global _dbf_map
    if _dbf_map is not None:
        return _dbf_map
    with _map_lock:
        if _dbf_map is not None:
            return _dbf_map
        try:
            with open(DBF_MAP_PATH, encoding="utf-8") as f:
                _dbf_map = json.load(f)
        except Exception:
            _dbf_map = {}
    return _dbf_map


def _discover_session_dir(session_dir: Optional[str]) -> Optional[Path]:
    """拿到炉石当前会话目录（Decks.log 所在目录）。"""
    if session_dir:
        return Path(session_dir)

    try:
        # 延迟 import，避免 powerlog_reader ↔ deck_tracker 循环依赖
        import powerlog_reader as _plr

        game_dir = _plr.detect_game_dir()
        return _plr.find_latest_session(game_dir)
    except Exception:
        return None


def _read_decks_log(path: Optional[Path]) -> dict:
    """解析 Decks.log：
      decks   所有被收藏读取到的卡组 [{name, deck_id, code, ts}]
      finding 排对局时“Finding Game With Deck”选中的卡组（时间顺序）
    """
    out: Dict[str, list] = {"decks": [], "finding": []}

    if path is None or not path.exists():
        return out

    cur: dict = {}
    finding_next = False

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return out

    for raw in lines:
        # 去掉行首 日志级别+时间戳 前缀（时间戳里的冒号不能参与内容解析）
        line = _TS_RE.sub("", raw).strip()

        if not line:
            continue

        ts_m = _TS_RE.match(raw)
        ts = ts_m.group(1) if ts_m else ""

        if "Finding Game With Deck:" in line:
            finding_next = True
            cur = {}
            continue

        if "Deck Contents Received:" in line:
            finding_next = False
            cur = {}
            continue

        if "### " in line:
            cur = {"name": line.split("###", 1)[1].strip(), "ts": ts}
            continue

        if "# Deck ID:" in line:
            cur["deck_id"] = line.split("# Deck ID:", 1)[1].strip()
            continue

        m = _DECK_CODE_RE.search(line)

        if m and cur.get("code") is None:
            cur["code"] = m.group(1)

            if cur.get("name") or cur.get("deck_id"):
                entry = dict(cur)
                out["decks"].append(entry)

                if finding_next:
                    out["finding"].append(entry)
                    finding_next = False

            cur = {}

    return out


def decode_deck_code(code: str) -> Optional[List[dict]]:
    """卡组码 → [{card_id, name, count}]；解码失败返回 None。"""
    try:
        from hearthstone.deckstrings import parse_deckstring

        cards, _hero, _fmt, _sides = parse_deckstring(code.strip())
    except Exception:
        return None

    dbf_map = _load_dbf_map()
    out: List[dict] = []

    for dbf_id, count in cards:
        info = dbf_map.get(str(dbf_id)) or {}
        cid = info.get("card_id") or ""

        if not cid:
            continue

        name = card_name(cid) or info.get("name") or cid
        out.append({"card_id": cid, "name": name, "count": int(count)})

    return out if out else None


def _hero_class_of(card_id: Optional[str]) -> Optional[str]:
    info = _load_card_map().get(card_id or "")
    return (info or {}).get("cardClass")


def _code_class(code: str) -> Optional[str]:
    """卡组码对应英雄职业（取卡组码里的英雄 dbf → 卡牌职业）。"""
    try:
        from hearthstone.deckstrings import parse_deckstring

        _cards, hero, _fmt, _sides = parse_deckstring(code.strip())
        info = _load_dbf_map().get(str(hero)) or {}
        return _hero_class_of(info.get("card_id"))
    except Exception:
        return None


def _match_deck_by_drawn(
    decks: List[dict], snap: dict, drawn: Optional[Dict[str, int]]
) -> Optional[dict]:
    """无“Finding Game With Deck”时：按 英雄职业 + 已抽卡重叠度 匹配卡组。
    已抽卡与卡组都按 card_id 匹配（不依赖卡名），至少命中 2 张才认为可靠。"""
    player_hero = (snap.get("player_hero") or {}).get("card_id")
    player_class = _hero_class_of(player_hero)
    drawn = drawn or {}

    best: Optional[dict] = None
    best_score = 0

    for deck in decks:
        code_class = _code_class(deck.get("code") or "")

        if player_class and code_class and code_class != player_class:
            continue

        deck_ids = {
            item["card_id"]
            for item in (decode_deck_code(deck.get("code") or "") or [])
        }

        score = sum(
            min(cnt, 3)
            for cid, cnt in drawn.items()
            if cid in deck_ids
        )

        if score > best_score:
            best = deck
            best_score = score

    return best if best_score >= 2 else None


def read_collection_deck(
    snap: dict,
    session_dir: Optional[str] = None,
    drawn: Optional[Dict[str, int]] = None,
) -> Optional[dict]:
    """从 Decks.log 读取收藏卡组（无需 HDT）。
    返回 {"original_deck": [...], "deck_name": str, "deck_id": str} 或 None。"""
    session = _discover_session_dir(session_dir)

    if session is None:
        return None

    data = _read_decks_log(session / "Decks.log")

    # 当前会话没有收藏数据时，再搜索历史会话（排到对局时通常已写入）
    if not data["decks"]:
        try:
            for older in sorted(
                session.parent.glob("*_*/Decks.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            ):
                if older == session / "Decks.log":
                    continue

                data = _read_decks_log(older)

                if data["decks"]:
                    break
        except Exception:
            pass

    if not data["decks"]:
        return None

    # 优先用当前会话排到对局时的卡组；当前会话没排到（如直接进练习/收藏），
    # 再用历史会话最近一次排到的卡组，最后按 职业+已抽卡 匹配
    deck = data["finding"][-1] if data["finding"] else None

    if deck is None and session_dir is None:
        player_class = _hero_class_of((snap.get("player_hero") or {}).get("card_id"))

        try:
            for older in sorted(
                session.parent.glob("*_*/Decks.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            ):
                older_data = _read_decks_log(older)

                if older_data["finding"]:
                    candidate = older_data["finding"][-1]
                    code_class = _code_class(candidate.get("code") or "")

                    if not player_class or not code_class or code_class == player_class:
                        deck = candidate
                        break
        except Exception:
            pass

    if deck is None:
        deck = _match_deck_by_drawn(data["decks"], snap, drawn)

    if deck is None:
        return None

    cards = decode_deck_code(deck.get("code") or "")

    if not cards:
        return None

    return {
        "original_deck": cards,
        "deck_name": deck.get("name") or "",
        "deck_id": deck.get("deck_id") or "",
    }


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


def _accumulate_profile(
    key: str, drawn_deck: Optional[Dict[str, int]]
) -> None:
    """把本局从牌库抽到的卡并入该玩家卡组档案（按 card_id 记最大张数）。

    没有 HDT 时靠多局累积逼近完整 30 张卡组：每局都会揭示一部分牌库卡，
    跨局合并后 unknown 逐渐变小；HDT 的精确卡组会整体覆盖档案。
    同名不同版本（如 EX1_145 / CORE_EX1_145）按最新见过的 card_id 覆盖，
    避免档案里同一张卡出现两份。
    """
    if not drawn_deck:
        return

    profile = _load_profile()
    entry = profile.get(key)

    if not entry or not isinstance(entry, dict):
        entry = {"original_deck": [], "updated": ""}
        profile[key] = entry

    known: Dict[str, dict] = {}

    for item in entry.get("original_deck") or []:
        cid = str(item.get("card_id") or "")
        name = str(item.get("name") or (card_name(cid) if cid else ""))
        count = int(item.get("count") or 0)

        if name and count > 0:
            cur = known.get(name)

            if cur is None:
                known[name] = {"card_id": cid, "count": count}
            else:
                known[name]["count"] = max(cur["count"], count)

    changed = False

    for cid, cnt in (drawn_deck or {}).items():
        name = card_name(cid)

        if not name or cnt <= 0:
            continue

        cur = known.get(name)

        if cur is None or cnt > cur["count"]:
            known[name] = {"card_id": cid, "count": cnt}
            changed = True

    if not changed:
        return

    entry["original_deck"] = [
        {
            "card_id": info["card_id"],
            "name": name,
            "count": info["count"],
        }
        for name, info in sorted(known.items())
    ]
    entry["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save_profile(profile)


def set_manual_deck(player_name: str, items: List[dict]) -> None:
    """手动录入/覆盖某玩家的卡组（GUI 卡组录入用，无需 HDT）。"""
    key = str(player_name or "").strip()

    if not key or not items:
        return

    profile = _load_profile()
    profile[key] = {
        "original_deck": [
            {
                "card_id": str(i.get("card_id") or ""),
                "name": str(i.get("name") or ""),
                "count": int(i.get("count") or 0),
            }
            for i in items
            if i.get("name")
        ],
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_profile(profile)


def _state_drawn(snap: dict, original: List[dict]) -> Dict[str, int]:
    """按当前区域状态统计已抽离的原卡组卡牌（不依赖日志事件顺序）。

    只统计 手牌/战场/墓地/奥秘/武器 中、card_id 属于原卡组、且非生成物
    （creator/ghostly）的本机卡。重连/观战恢复时已抽到的牌会直接以当前
    区域（HAND/PLAY/GRAVEYARD）创建，同样能被识别；偷来的/衍生的牌因
    不在原卡组而被排除。每张卡最多按原卡组张数计，避免复制体重复计数。
    """
    original_ids = {item.get("card_id") for item in original}
    original_counts = _to_counts(original)
    out: Dict[str, int] = {}

    for key in ("hand", "board", "local_graveyard", "secrets"):
        for item in snap.get(key) or []:
            cid = item.get("card_id") or ""

            if not cid or cid not in original_ids:
                continue

            if item.get("creator") or item.get("ghostly"):
                continue

            out[cid] = out.get(cid, 0) + 1

    weapon = snap.get("weapon") or {}
    wcid = weapon.get("card_id") or ""

    if wcid in original_ids and not (
        weapon.get("creator") or weapon.get("ghostly")
    ):
        out[wcid] = out.get(wcid, 0) + 1

    for cid, cnt in out.items():
        out[cid] = min(cnt, original_counts.get(cid, cnt))

    return out


def merge_snapshot(
    snap: dict,
    drawn_deck: Optional[Dict[str, int]] = None,
    session_dir: Optional[str] = None,
) -> dict:
    """把记牌器重建结果合并进快照（原地修改并返回）。

    session_dir 为当前炉石会话目录（Decks.log 所在目录），
    缺省时按 detect_game_dir() 自动发现。
    """
    original: List[dict] = []
    remaining: List[dict] = []
    drawn: List[dict] = []
    source: Optional[str] = None
    key = _profile_key(snap)

    hdt = read_hdt_state()

    if hdt is not None:
        original = _expand(hdt.get("original_deck"))
        remaining = _expand(hdt.get("remaining_deck"))
        source = "hdt"

        # 记录该玩家卡组档案（HDT 精确卡组整体覆盖），之后没有 HDT 时也能重建
        if key and original:
            set_manual_deck(key, original)
    else:
        # 无 HDT：优先收藏读取（Decks.log 卡组码），其次卡组档案（历史累积/手动录入）
        if key:
            _accumulate_profile(key, drawn_deck)

        collection = read_collection_deck(snap, session_dir, drawn_deck)

        if collection:
            original = collection.get("original_deck") or []
            source = "collection"
            snap["deck_name"] = collection.get("deck_name") or ""
            snap["deck_id"] = collection.get("deck_id") or ""

            # 记录完整卡组到档案：Decks.log 暂时读不到时也能用档案重建
            if key and original:
                set_manual_deck(key, original)

        if key:
            profile = _load_profile()
            saved = profile.get(key) or {}
            saved_original = _expand(saved.get("original_deck"))

            if not original and saved_original:
                original = saved_original
                source = "profile"

        if not original and drawn_deck:
            # 纯 Power.log 兜底：没有卡组档案，只给出已出卡
            source = "powerlog"

    # 总剩余张数：HDT 用 remaining 总数；无 HDT 用 hslog 当前牌库实体数
    if hdt is not None:
        total_remaining = sum(item.get("count") or 0 for item in remaining)
    else:
        total_remaining = len(snap.get("deck") or [])

    # 已出卡与剩余牌库：HDT 在场时用 HDT 的权威剩余（原卡组 − 剩余）；
    # 否则按当前区域状态统计已抽离的原卡组卡牌（_state_drawn），
    # 剩余 = 原卡组 − 已出，再按 card_id 扣减、同名不同版本按卡名兜底。
    if original:
        if hdt is not None and remaining:
            left_drawn = _to_counts(original) - _to_counts(remaining)
            drawn = [
                {"card_id": cid, "name": card_name(cid), "count": cnt}
                for cid, cnt in sorted(left_drawn.items())
                if cnt > 0
            ]
        else:
            drawn_state = _state_drawn(snap, original)
            drawn = [
                {"card_id": cid, "name": card_name(cid), "count": cnt}
                for cid, cnt in sorted(drawn_state.items())
                if cnt > 0
            ]

            if not remaining:
                counts = _to_counts(original)
                name_to_id: Dict[str, str] = {}

                for item in original:
                    cid = item.get("card_id") or ""
                    name = item.get("name") or ""

                    if cid and name and name not in name_to_id:
                        name_to_id[name] = cid

                for cid, cnt in drawn_state.items():
                    if not cid:
                        continue

                    if cid in counts:
                        counts[cid] -= cnt
                    else:
                        alt = name_to_id.get(card_name(cid))

                        if alt:
                            counts[alt] -= cnt

                left = Counter({cid: c for cid, c in counts.items() if c > 0})
                remaining = [
                    {"card_id": cid, "name": card_name(cid), "count": cnt}
                    for cid, cnt in sorted(left.items())
                    if cnt > 0
                ]
    elif drawn_deck:
        # 纯 Power.log 兜底（无原卡组）：沿用解析器追踪的已出卡
        drawn = [
            {"card_id": cid, "name": card_name(cid), "count": cnt}
            for cid, cnt in sorted(drawn_deck.items())
            if cid
        ]

    known_remaining = sum(item.get("count") or 0 for item in remaining)
    unknown_remaining = max(0, total_remaining - known_remaining)

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
    elif unknown_remaining > 0:
        # 没有已知剩余时保留 hslog 的牌库实体（数量即总剩余）
        snap.setdefault("deck", [])

    snap["original_deck"] = original
    snap["remaining_deck"] = remaining
    snap["drawn_deck"] = drawn
    snap["deck_source"] = source
    snap["remaining_unknown"] = unknown_remaining
    snap["remaining_total"] = total_remaining
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
    elif source == "collection":
        deck_name = snap.get("deck_name") or ""
        parts.append(f"收藏读取{('：' + deck_name) if deck_name else ''}")
    elif source == "profile":
        parts.append("卡组档案")
    elif source == "powerlog":
        parts.append("Power.log 追踪")

    if len(parts) > 1:
        return parts[0] + "（" + "；".join(parts[1:]) + "）"
    return parts[0]
