"""静默云端公式上报。

每次计算完成后，将 场面数据 + 最高伤路径（含 场面交换处理 / 可能预处理 /
可能分支 的完整记录）上传到阿里云函数计算后端。上传在后台线程进行，不阻塞
界面；成功无提示，失败仅静默写入本地错误日志。

鉴权地址做了简单混淆，避免源码明文直读（真正防篡改依赖打包混淆）。
"""

from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


def _b64(s: str) -> str:
    return base64.b64decode(s.encode("ascii")).decode("utf-8")


# 阿里云函数计算公网域名 + 鉴权 Token（base64 混淆，非明文）
_DOMAIN = _b64("aHR0cHM6Ly9mb3JtdWxhYm1pdC1hcGktcGN6dGlueHFhcS5jbi1oYW5nemhvdS5mY2FwcC5ydW4=")
_SECRET_TOKEN = _b64("UmVkRHJhZ29uIzU4NTQ=")

UPLOAD_URL = f"{_DOMAIN}/api/report"
_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {_SECRET_TOKEN}",
}

# 失败静默记录（不弹窗）
_ERROR_LOG = Path(__file__).resolve().parent / "logs" / "upload_errors.log"


def _card_text(card: Dict[str, object]) -> str:
    name = str(card.get("name") or "")
    cost = card.get("cost")
    if cost is None:
        return name
    return f"{name}[{cost}费]"


def build_payload(
    snapshot: Dict[str, object],
    result: Dict[str, object],
    best_exchange: Optional[List[tuple]] = None,
) -> Dict[str, object]:
    """组装上报记录：params + 手牌 + 最高伤路径（含交换/预处理/分支）+ 场面数据。"""
    hand = snapshot.get("hand") or []
    best = (result.get("results") or [{}])[0]

    exchanges: List[str] = []
    for fi, ei in (best_exchange or []):
        target = "敌方英雄" if ei == 0 else f"敌方随从{ei}"
        exchanges.append(f"我方随从{fi}->{target}")

    return {
        "params": {
            "crystal": snapshot.get("crystals"),
            "mana": snapshot.get("mana"),
        },
        "initial_hand": [_card_text(h) for h in hand],
        "best_solution": {
            "dragon_num": int(result.get("max_dragons") or 0),
            "damage": int(result.get("max_damage") or 0),
            "remain_cost": int(best.get("mana") or 0),
            "play_sequence": [str(step) for step in (best.get("path") or [])],
            # 场面交换处理：最优解使用的交换计划（我方随从->敌方随从/英雄）
            "exchanges": exchanges,
            # 可能预处理：如果机制（抽随从卡→凑齐组合→预计伤害）
            "draw_whatif": result.get("draw_whatif"),
            # 可能分支：持枪要挟各发现牌分支的完整路径
            "quickdraw_branches": result.get("quickdraw_branches"),
        },
        # 场面数据：敌我随从/英雄/牛池/效果等完整局面
        "scene": {
            "board": snapshot.get("board") or [],
            "enemy_board": snapshot.get("enemy_board") or [],
            "opponent_hero": snapshot.get("opponent_hero") or {},
            "etc_band": snapshot.get("etc_band") or [],
            "current_effects": snapshot.get("current_effects") or [],
            "deadly_shadow_hand_indexes": snapshot.get("deadly_shadow_hand_indexes") or [],
            "weapon": snapshot.get("weapon"),
            "secrets": snapshot.get("secrets") or [],
            "deck_unknown_cards": snapshot.get("deck_unknown_cards"),
        },
    }


def _log_failure(message: str) -> None:
    try:
        _ERROR_LOG.parent.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _ERROR_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except OSError:
        pass


def upload_report(payload: Optional[Dict[str, object]]) -> bool:
    """同步上报（由后台线程调用）。成功返回 True，失败静默记录并返回 False。"""
    if not payload:
        return False

    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            UPLOAD_URL,
            data=body,
            headers=_HEADERS,
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")

        try:
            code = json.loads(resp_body).get("code")
        except Exception:
            code = None

        ok = code in (None, 200)

        if not ok:
            _log_failure(
                f"上报失败 HTTP={resp.status} code={code} "
                f"resp={resp_body[:300]}"
            )
        return ok

    except (urllib.error.URLError, OSError, ValueError) as exc:
        _log_failure(f"上报网络异常 {type(exc).__name__}: {exc}")
        return False


def upload_async(payload: Optional[Dict[str, object]]) -> None:
    """后台静默上传，不阻塞界面。"""
    if not payload:
        return

    threading.Thread(
        target=upload_report,
        args=(payload,),
        daemon=True,
        name="cloud-report",
    ).start()
