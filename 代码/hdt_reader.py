"""HDT 插件状态读取器。

读取 HDT 插件 RedDragonStateExport 导出的 red_dragon_state.json
（默认 %APPDATA%\\HearthstoneDeckTracker\\red_dragon_state.json，
可用环境变量 RED_DRAGON_STATE_PATH 覆盖），归一化成与
powerlog_reader.snapshot() 兼容的 dict，直接进入
snapshot_to_rebuild_result -> state_from_rebuild_result 计算链路。

依赖：python 代码/hdt_reader.py --once          # 打印最新快照（JSON）
      python 代码/hdt_reader.py --watch         # 持续跟随
      python 代码/hdt_reader.py --once --text   # 输出 rebuild_hand 文本格式
      python 代码/hdt_reader.py --state-file <路径> --once
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from powerlog_reader import card_name, snapshot_to_rebuild_result


BASE_DIR = Path(__file__).resolve().parent


def default_state_file() -> Path:
    """插件默认写入路径，与 C# 插件保持一致。"""
    env_path = os.environ.get("RED_DRAGON_STATE_PATH")

    if env_path:
        return Path(env_path)

    return (
        Path(os.environ.get("APPDATA", ""))
        / "HearthstoneDeckTracker"
        / "red_dragon_state.json"
    )


def _not_in_game(reason: str, state_file: Optional[Path] = None) -> dict:
    return {
        "in_game": False,
        "reason": reason,
        "player_controller": None,
        "player_name": None,
        "opponent_name": None,
        "game_state": "NONE",
        "game_over": False,
        "crystals": None,
        "mana": None,
        "hand": [],
        "board": [],
        "deck": [],
        "secrets": [],
        "weapon": None,
        "current_effects": [],
        "deadly_shadow_hand_indexes": [],
        "parser": "hdt",
        "state_path": str(state_file) if state_file else None,
        "timestamp": None,
    }


def _item(item: dict) -> dict:
    """把插件导出的单卡条目归一化：CardID -> 中文名，补齐缺失字段。"""
    card_id = item.get("card_id") or ""
    return {
        "card_id": card_id,
        "name": card_name(card_id) if card_id else (item.get("name") or card_id),
        "cost": item.get("cost"),
        "attack": item.get("attack"),
        "health": item.get("health"),
        "zone_position": item.get("zone_position"),
        "ghostly": bool(item.get("ghostly")),
        "created": bool(item.get("created")),
    }


def _expand_deck(entries: List[dict]) -> List[dict]:
    """把 [{card_id, count}] 展开成每张卡一条，保持与日志路径一致。"""
    items: List[dict] = []

    for entry in entries:
        card_id = entry.get("card_id")

        if not card_id:
            continue

        count = max(1, int(entry.get("count", 1) or 1))

        for _ in range(count):
            items.append(
                {
                    "card_id": card_id,
                    "name": card_name(card_id),
                    "cost": entry.get("cost"),
                    "attack": None,
                    "health": None,
                    "zone_position": None,
                    "ghostly": False,
                    "created": False,
                }
            )

    return items


def _remaining_from_original(data: dict) -> List[dict]:
    """兜底：没有 remaining_deck 字段时，用起始卡组减去已见卡牌估算剩余牌库。"""
    original: Dict[str, int] = {}

    for entry in data.get("original_deck", []):
        card_id = entry.get("card_id")

        if card_id:
            original[card_id] = original.get(card_id, 0) + max(1, int(entry.get("count", 1) or 1))

    if not original:
        return []

    seen: List[str] = []

    for zone in ("hand", "board", "secrets"):
        seen.extend(item.get("card_id") for item in data.get(zone, []) if item.get("card_id"))

    weapon = data.get("weapon") or {}

    if weapon.get("card_id"):
        seen.append(weapon["card_id"])

    seen.extend(
        card_id
        for card_id in data.get("local_graveyard", [])
        if card_id
    )
    seen.extend(card_id for card_id in data.get("cards_played", []) if card_id)

    remaining: Dict[str, int] = dict(original)

    for card_id in seen:
        if card_id in remaining and remaining[card_id] > 0:
            remaining[card_id] -= 1

    return [
        {"card_id": card_id, "count": count}
        for card_id, count in sorted(remaining.items())
        if count > 0
    ]


class HdtStateReader:
    """读取 HDT 插件导出的状态 JSON，输出与日志快照兼容的 dict。"""

    def __init__(self, state_file: Optional[str] = None):
        self.state_file = Path(state_file) if state_file else default_state_file()

    def snapshot(self) -> dict:
        if not self.state_file.exists():
            return _not_in_game(
                f"未找到 HDT 状态文件：{self.state_file}（请确认 HDT 已启动且红龙插件已启用）",
                self.state_file,
            )

        try:
            with open(self.state_file, encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception as exc:
            return _not_in_game(
                f"HDT 状态文件读取失败：{exc}",
                self.state_file,
            )

        game_over = bool(data.get("game_over"))
        in_game = bool(data.get("in_game")) and not game_over

        if not in_game:
            snap = _not_in_game(data.get("reason") or "对局未开始", self.state_file)
            snap["game_state"] = data.get("game_state") or "NONE"
            snap["game_over"] = game_over
            snap["timestamp"] = data.get("timestamp")
            snap["player_name"] = data.get("player_name")
            snap["opponent_name"] = data.get("opponent_name")
            return snap

        hand = [_item(x) for x in data.get("hand", [])]
        board = [_item(x) for x in data.get("board", [])]
        secrets = [_item(x) for x in data.get("secrets", [])]
        weapon = data.get("weapon")
        weapon = _item(weapon) if weapon else None

        remaining = data.get("remaining_deck") or _remaining_from_original(data)
        deck = _expand_deck(remaining) if remaining else [_item(x) for x in data.get("deck", [])]

        deadly_shadow_hand_indexes = [
            index
            for index, item in enumerate(hand, start=1)
            if item["ghostly"]
        ]

        return {
            "in_game": True,
            "reason": "对局进行中",
            "player_controller": data.get("player_controller"),
            "player_name": data.get("player_name"),
            "opponent_name": data.get("opponent_name"),
            "player_class": data.get("player_class"),
            "opponent_class": data.get("opponent_class"),
            "player_hero": data.get("player_hero"),
            "opponent_hero": data.get("opponent_hero"),
            "turn": data.get("turn"),
            "current_player": data.get("current_player"),
            "game_state": data.get("game_state") or "RUNNING",
            "game_over": False,
            "crystals": data.get("crystals"),
            "mana": data.get("mana"),
            "hand": hand,
            "board": board,
            "deck": deck,
            "deck_revealed": [_item(x) for x in data.get("deck", [])],
            "secrets": secrets,
            "weapon": weapon,
            "current_effects": data.get("current_effects", []),
            "deadly_shadow_hand_indexes": deadly_shadow_hand_indexes,
            "original_deck": data.get("original_deck", []),
            "remaining_deck": remaining,
            "opponent": data.get("opponent"),
            "local_graveyard": data.get("local_graveyard", []),
            "cards_played": data.get("cards_played", []),
            "cards_played_this_turn": data.get("cards_played_this_turn", []),
            "fatigue": data.get("fatigue"),
            "parser": "hdt",
            "state_path": str(self.state_file),
            "timestamp": data.get("timestamp"),
        }


def _snapshot_key(snap: dict) -> tuple:
    return (
        snap.get("state_path") or snap.get("log_path"),
        snap.get("in_game"),
        json.dumps(snap.get("hand", []), ensure_ascii=False),
        json.dumps(snap.get("board", []), ensure_ascii=False),
        snap.get("crystals"),
        snap.get("mana"),
    )


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="HDT 插件状态读取器")
    parser.add_argument("--once", action="store_true", help="只打印一次快照")
    parser.add_argument("--watch", action="store_true", help="持续跟随状态变化")
    parser.add_argument("--text", action="store_true", help="输出 rebuild_hand 文本格式")
    parser.add_argument("--state-file", default=None, help="指定 HDT 状态 JSON 路径")
    parser.add_argument("--interval", type=float, default=0.5, help="watch 轮询间隔")
    args = parser.parse_args(argv)

    reader = HdtStateReader(state_file=args.state_file)

    if args.watch:
        last_key = None

        while True:
            snap = reader.snapshot()
            key = _snapshot_key(snap)

            if key != last_key:
                last_key = key

                if args.text:
                    from rebuild_hand import format_result

                    if snap.get("in_game"):
                        print(format_result(snapshot_to_rebuild_result(snap)))
                    else:
                        print(snap.get("reason", "等待对局开始…"))

                    print("---")
                else:
                    print(json.dumps(snap, ensure_ascii=False, indent=2))

                sys.stdout.flush()

            time.sleep(max(0.1, args.interval))

    snap = reader.snapshot()

    if args.text:
        from rebuild_hand import format_result

        if snap.get("in_game"):
            print(format_result(snapshot_to_rebuild_result(snap)))
        else:
            print(snap.get("reason", "等待对局开始…"))
    else:
        print(json.dumps(snap, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
