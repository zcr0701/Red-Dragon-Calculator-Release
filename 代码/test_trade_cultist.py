# -*- coding: utf-8 -*-
"""黑水弯刀交易 + 异教地图发现机制回归。

黑水弯刀(交易)：免费置入牌库 + 抽 1 张 + 手牌中一张 >0 费法术随机 -1 费，
分支数 = 手牌 >0 费法术数 × 牌库剩余卡牌数。
异教地图：发现牌库 3 张（C(3,N)）选择最优 1 张抽上；本回合使用后再抽 1 张。
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine  # noqa: E402


def base_snap():
    return {
        "in_game": True,
        "player_name": "test",
        "crystals": 5,
        "mana": 5,
        "cards_played_this_turn": 1,
        "board": [],
        "enemy_board": [],
        "deck_unknown_cards": 3,
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
    # 1) 黑水弯刀交易分支存在，且手牌 >0 费法术 × 牌库剩余卡牌
    snap = base_snap()
    snap["crystals"] = 1
    snap["mana"] = 1  # 交易本身消耗 1 费
    snap["hand"] = [
        {"name": "黑水弯刀", "cost": 1},
        {"name": "行骗", "cost": 2},
        {"name": "暗影步", "cost": 0},
    ]
    snap["deck"] = [
        {"name": "伪造的幸运币"},
        {"name": "伺机待发"},
        {"name": "暗影步"},
    ]
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
    trade_lines = [
        b
        for b in (res.get("draw_branches") or [])
        if any("黑水弯刀（交易）" in str(s) for s in (b.get("path") or []))
    ]
    print("trade lines:", len(trade_lines))

    if not trade_lines:
        print("FAIL: 未找到 黑水弯刀（交易） 分支")
        return 1

    # 2) 异教地图发现 + 本回合使用后再抽 1 张（重放验证）
    snap2 = base_snap()
    snap2["hand"] = [
        {"name": "异教地图", "cost": 2},
        {"name": "行骗", "cost": 2},
    ]
    snap2["deck"] = [
        {"name": "暗影步"},
        {"name": "闪避"},
        {"name": "行骗"},
    ]
    payload = engine.build_payload(snap2, time_budget_sec=2.0, branch_expand=True)
    payload["replay_path"] = [
        "异教地图（发现：行骗）",
        "行骗（异教地图再抽：暗影步）",
    ]
    exe = engine.find_engine()
    proc = subprocess.run(
        [exe, "--json", "--verify"],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print("异教地图重放 exit:", proc.returncode)

    if proc.returncode != 0:
        print("FAIL: 异教地图发现牌使用后再抽重放失败")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
