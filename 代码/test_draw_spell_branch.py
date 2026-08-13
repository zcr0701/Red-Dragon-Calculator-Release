# -*- coding: utf-8 -*-
"""暗影之门/行骗 抽法术分支建模回归。

暗影之门与行骗随机抽牌库中的一张法术牌，分支数 = 牌库剩余法术数
（追踪器提供的 remaining_deck 按唯一法术名展开）；抽随从卡（挖掘宝藏/
潜伏帷幕/行骗连击）的分支池同样改用牌库剩余随从池（替代旧的固定勾选池）。
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine  # noqa: E402


def base_snap():
    return {
        "in_game": True,
        "player_name": "test",
        "crystals": 2,
        "mana": 2,
        "cards_played_this_turn": 0,
        "hand": [
            {"name": "暗影之门", "cost": 1},
            {"name": "伪造的幸运币", "cost": 0},
        ],
        "board": [],
        "enemy_board": [],
        "deck_unknown_cards": 5,
        "opponent_hero": {"health": 30, "armor": 0},
        "player_hero": {"health": 30},
        "etc_band": [],
        "secrets": [],
        "weapon": None,
        "current_effects": [],
        "deadly_shadow_hand_indexes": [],
        "dredge_bottom": [],
    }


def main():
    # 1) 暗影之门：分支数 = 剩余法术数（含 双面生意）
    snap = base_snap()
    snap["deck"] = [
        {"name": "双面生意"},
        {"name": "异教地图"},
        {"name": "闪避"},
        {"name": "暗影步"},
        {"name": "行骗"},
    ]
    spells = engine.deck_card_names_by_type(snap["deck"], "SPELL", "SECRET")
    print("deck spells:", spells)

    res = engine.compute(
        snap,
        min_alex=1,
        max_alex=4,
        depth=20,
        max_paths=100000,
        threads=4,
        time_budget_sec=2.0,
        wide_widths=[500],
        heuristics=[6],
        etc_band=[],
        only_best_damage=True,
        branch_expand=True,
    )
    cards = [b.get("card") for b in (res.get("draw_branches") or [])]
    print("暗影之门 draw_branches:", cards)

    if "双面生意" not in cards:
        print("FAIL: 双面生意 未出现在暗影之门法术分支")
        return 1

    if len(cards) != len(spells):
        print(f"FAIL: 分支数应为剩余法术数 {len(spells)}，实际 {len(cards)}")
        return 1

    # 2) 行骗：同样按剩余法术展开
    snap2 = base_snap()
    snap2["hand"] = [
        {"name": "行骗", "cost": 2},
        {"name": "伪造的幸运币", "cost": 0},
    ]
    snap2["deck"] = [
        {"name": "暗影步"},
        {"name": "闪避"},
        {"name": "异教地图"},
    ]
    res2 = engine.compute(
        snap2,
        min_alex=1,
        max_alex=4,
        depth=20,
        max_paths=100000,
        threads=4,
        time_budget_sec=2.0,
        wide_widths=[500],
        heuristics=[6],
        etc_band=[],
        only_best_damage=True,
        branch_expand=True,
    )
    cards2 = [b.get("card") for b in (res2.get("draw_branches") or [])]
    print("行骗 draw_branches:", cards2)

    if len(cards2) != 3:
        print(f"FAIL: 行骗分支数应为剩余法术数 3，实际 {len(cards2)}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
