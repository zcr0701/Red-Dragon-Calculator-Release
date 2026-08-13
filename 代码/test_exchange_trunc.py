# -*- coding: utf-8 -*-
"""场面交换回归：多场面对比不能因精确截断漏掉更高伤线。

场景 red_dragon_all_paths_20260813_132720.txt：场上牛(4/4)占 1 格，
牛→暗鳞治愈者(4/5) 交换牺牲牛腾格后，5 费可打出 鱼-刀-刀-龙-暗-龙-龙
48 伤/3 龙；若每个交换场面都按 伤害≥32 精确截断，会停在第一条 32 斩
杀线（34336 展开），永远搜不到 48（288513 展开）。框3 默认应不勾选。
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine  # noqa: E402


LOGDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG = os.path.join(LOGDIR, "red_dragon_all_paths_20260813_132720.txt")


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

    # 不截断：牛->治愈者(1,4) 交换应搜出 48 伤/3 龙
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
        exchanges=[(1, 4)],
        lethal_threshold=-1,
    )
    dmg = res.get("max_damage") or 0
    dragons = res.get("max_dragons") or 0
    print("不截断 + 牛->治愈者:", dmg, "伤 /", dragons, "龙")

    if dmg < 48:
        print("FAIL: 场面交换后应搜出 48 伤/3 龙（鱼-刀-刀-龙-暗-龙-龙）")
        return 1

    # 截断（框3 勾选时的旧行为）：会被第一条 32 斩杀线截断
    res2 = engine.compute(
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
        exchanges=[(1, 4)],
        lethal_threshold=32,
    )
    print("截断32 + 牛->治愈者:", res2.get("max_damage"), "伤")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
