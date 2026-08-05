"""重建 card_id_map.json（CardID -> 中文卡牌信息）。

数据源：HearthstoneJSON 的 zhCN 卡库（http://hearthstonejson.com/）。
已下载的全量数据存在 card_data_zhCN.json；缺失或想更新时可用 --download 重新拉取。

用法：
    python 代码/update_card_map.py            # 从本地 card_data_zhCN.json 重建映射
    python 代码/update_card_map.py --download # 先下载最新 zhCN 卡库再重建
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from typing import Dict


BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "card_data_zhCN.json"
MAP_PATH = BASE_DIR / "card_id_map.json"
DOWNLOAD_URL = "https://api.hearthstonejson.com/v1/latest/zhCN/cards.json"

# 映射中保留的字段（不含卡牌文本，控制体积）
KEEP_FIELDS = ("name", "cost", "type", "health", "attack", "cardClass", "set")


def download() -> None:
    print(f"下载 {DOWNLOAD_URL} ...")
    urllib.request.urlretrieve(DOWNLOAD_URL, DATA_PATH)
    print(f"已保存 {DATA_PATH}（{DATA_PATH.stat().st_size} 字节）")


def build() -> Dict[str, dict]:
    with open(DATA_PATH, encoding="utf-8") as f:
        cards = json.load(f)

    mapping: Dict[str, dict] = {}

    for card in cards:
        card_id = card.get("id")
        name = card.get("name")

        if not card_id or not name:
            continue

        mapping[card_id] = {
            key: card.get(key)
            for key in KEEP_FIELDS
        }

    with open(MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, separators=(",", ":"))

    return mapping


def main() -> int:
    if "--download" in sys.argv:
        download()

    if not DATA_PATH.exists():
        print(f"缺少 {DATA_PATH}，请先运行：python {Path(__file__).name} --download")
        return 1

    mapping = build()
    print(f"已生成 {MAP_PATH}：{len(mapping)} 张卡，{MAP_PATH.stat().st_size} 字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
