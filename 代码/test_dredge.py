# -*- coding: utf-8 -*-
"""垂钓时光探底分叉回归：已知牌库底牌作为垂钓时光的分叉选项。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine  # noqa: E402
import main as app  # noqa: E402


def main():
    snap = {
        "crystals": 7,
        "mana": 7,
        "cards_played_this_turn": 1,
        "hand": [
            {"name": "垂钓时光"},
            {"name": "鲨鱼之灵"},
            {"name": "斯卡布斯·刀油"},
            {"name": "暗影施法者"},
            {"name": "乐队经理精英牛头人酋长"},
            {"name": "晦鳞巢母"},
        ],
        "board": [],
        "enemy_board": [{"name": "敌方随从", "health": 5}],
        "opponent_hero": {"health": 30, "armor": 0},
        "etc_band": ["舞动全场（ft.迦罗娜）", "幻觉药水", "生命的缚誓者阿莱克丝塔萨"],
        "deck_unknown_cards": 20,
        "dredge_bottom": ["斯卡布斯·刀油", "狐人老千"],
    }
    res = engine.compute(
        snap,
        min_alex=1,
        max_alex=10,
        depth=20,
        max_paths=500000,
        threads=4,
        time_budget_sec=3.0,
        heuristic=6,
        etc_band=snap.get("etc_band"),
        branch_expand=True,
    )
    branches = res.get("draw_branches") or []
    keys = {str(b.get("card") or "") for b in branches}
    print("draw_branches keys:", sorted(keys))
    # 垂钓时光分叉池 = 2 张已知底牌 + 1 张未知杂牌
    assert {"斯卡布斯·刀油", "狐人老千", "未知杂牌"} <= keys, keys
    for b in branches:
        pth = [str(s) for s in (b.get("path") or [])]
        if str(b.get("card")) == "斯卡布斯·刀油":
            assert any("垂钓时光（斯卡布斯·刀油）" in s for s in pth), pth

    # 缩写显示
    assert app.abbreviate_step("垂钓时光（斯卡布斯·刀油）") == "垂钓时光(刀)"
    assert app.abbreviate_step("垂钓时光（未知杂牌）") == "垂钓时光(未知杂牌)"

    print("PASS")


if __name__ == "__main__":
    main()
