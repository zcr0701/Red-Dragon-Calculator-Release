# -*- coding: utf-8 -*-
"""WhatIF 回归：手牌有持枪要挟、主路径分支池为空时，WhatIF 必须显示持枪分支。

场景 red_dragon_all_paths_20260813_123555.txt：鱼狐刀暗牛晦全在手（抽牌池
为空），主束宽线不用持枪要挟；引擎仍返回 quickdraw_branches（补水/脱水/误炸/
袋底藏沙/不许乱动/其他快枪牌），WhatIF 应直接展示这些叶子分叉，而不是被
“筛树策略”收成单条路径导致什么都不显示。
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine  # noqa: E402


LOGDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG = os.path.join(LOGDIR, "red_dragon_all_paths_20260813_123555.txt")


def load_snapshot(path):
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            if "输入JSON：" in line:
                return json.loads(line.split("输入JSON：", 1)[1])
    raise RuntimeError("未找到 输入JSON 行")


def main():
    snap = load_snapshot(LOG)

    for key in (
        "log_path",
        "session_dir",
        "parser",
        "deck_source",
        "deck_name",
        "deck_id",
        "drawn_unknown",
    ):
        snap.pop(key, None)

    res = engine.compute(
        snap,
        min_alex=1,
        max_alex=10,
        depth=30,
        max_paths=1000000,
        threads=4,
        time_budget_sec=3.0,
        wide_widths=None,
        heuristics=None,
        etc_band=snap.get("etc_band"),
        only_best_damage=True,
        branch_expand=True,
    )

    main_qd = list(res.get("quickdraw_branches") or [])
    print("quickdraw branches:", len(main_qd))

    if not main_qd:
        print("FAIL: 引擎未返回持枪要挟分支")
        return 1

    # 复现 worker 的 fb fallback：抽牌池为空 -> whatif_tree 直接由 quickdraw 构建
    from PyQt5.QtCore import QCoreApplication

    _app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    import main as M

    options = {
        "branch_strategy": "kill",
        "beam_width": 0,
        "min_alex": 1,
        "max_alex": 10,
        "depth": 30,
        "max_paths": 1000000,
        "threads": 4,
        "time_budget_sec": 3.0,
        "only_best_damage": True,
        "draw_whatif": True,
    }
    worker = M.CalculationWorker(snap, options)

    fb_branches = []

    for b in main_qd:
        pth = list(b.get("path") or [])
        fb_branches.append(
            {
                "card": str(b.get("card") or ""),
                "outcome": str(b.get("card") or ""),
                "damage": int(b.get("damage") or 0),
                "dragons": int(b.get("dragons") or 0),
                "mana_left": int(b.get("mana_left") or 0),
                "mid": [str(s) for s in pth],
                "path": pth,
            }
        )

    whatif_tree = {
        "root": [],
        "branches": fb_branches,
        "worst": min(int(b.get("damage") or 0) for b in fb_branches),
        "main_outcome": "",
    }
    worker._apply_branch_strategy(whatif_tree)
    after = len(whatif_tree.get("branches") or [])
    print("after strategy branches:", after)

    if after < len(fb_branches):
        print("FAIL: 筛树策略把持枪叶子分叉收掉了")
        return 1

    txt = M._whatif_text_block({"whatif_tree": whatif_tree})

    if not txt.strip():
        print("FAIL: WhatIF 文本为空")
        return 1

    if "持枪要挟(补水)" not in txt and "持枪要挟（补水）" not in txt:
        print("FAIL: WhatIF 未包含持枪要挟分支标注")
        return 1

    # 溢出平局决胜：未勾选精确截断时，kill 同比例优先选溢出伤害高的树
    worker2 = M.CalculationWorker(snap, {**options, "truncate_normal": False})

    def mk_leaf(card, dmg):
        return {
            "card": card,
            "outcome": card,
            "damage": dmg,
            "dragons": 1,
            "mana_left": 0,
            "mid": [card + "（x）"],
            "path": [card + "（x）"],
        }

    tie_tree = {
        "root": ["币"],
        "branches": [
            {
                "card": "牛",
                "outcome": "牛",
                "damage": 48,
                "mid": ["牛"],
                "path": ["牛"],
                "children": [mk_leaf("补水", 48), mk_leaf("脱水", 32)],
            },
            {
                "card": "狐",
                "outcome": "狐",
                "damage": 32,
                "mid": ["狐"],
                "path": ["狐"],
                "children": [mk_leaf("补水", 32), mk_leaf("脱水", 32)],
            },
        ],
    }
    worker2._apply_branch_strategy(tie_tree)
    chosen_root = list(tie_tree.get("root") or [])
    print("tie-break root tail:", chosen_root[-1] if chosen_root else None)

    if not chosen_root or str(chosen_root[-1]) != "牛":
        print("FAIL: 同比例未按溢出伤害决胜（应选 48|32 的牛树而非 32|32 的狐树）")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
