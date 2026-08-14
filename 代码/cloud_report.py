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


# 上传数据压缩：卡名缩写（保持可读）+ 精简字段，降低 KB
_CARD_ABBREV = {
    "伪造的幸运币": "币", "幸运币": "币", "伺机待发": "伺", "暗影步": "步",
    "殒命暗影": "殒", "狐人老千": "狐", "斯卡布斯·刀油": "刀",
    "暗影施法者": "暗", "乐队经理精英牛头人酋长": "牛", "晦鳞巢母": "晦",
    "鲨鱼之灵": "鱼", "生命的缚誓者阿莱克丝塔萨": "龙", "赤烟·腾武": "腾",
    "幻觉药水": "幻", "舞动全场（ft.迦罗娜）": "舞", "战略转移": "转",
    "锯齿骨刺": "骨", "持枪要挟": "持", "垂钓时光": "垂", "挖掘宝藏": "挖",
    "潜伏帷幕": "幕", "行骗": "骗", "疯狂之灾祸": "疯", "暗影之门": "门",
    "异教地图": "图", "双面生意": "双", "黑水弯刀": "弯", "闪避": "闪",
    "补水": "水", "脱水": "脱", "误炸": "炸", "袋底藏沙": "袋",
    "不许乱动": "乱", "其他快枪牌·随从": "随", "其他快枪牌·法术": "法",
    "未知快枪牌随从": "随", "未知快枪牌法术": "法", "未知法术": "法?",
    "未知抽牌": "抽?", "精灵弓箭手": "弓", "绿洲钳嘴龟": "龟",
}


def _abbrev(name: str) -> str:
    """把已知卡名替换为缩写（未知卡保留原名）。"""
    for full, ab in _CARD_ABBREV.items():
        if full in name:
            return name.replace(full, ab)
    return name


def _compact_card(card: Dict[str, object]) -> Dict[str, object]:
    """场面卡压缩：只留 名(n)/费(c)/攻(a)/血(h)/耐久(d) 非空字段。"""
    c: Dict[str, object] = {"n": _abbrev(str(card.get("name") or "?"))}
    if card.get("cost") is not None:
        c["c"] = card.get("cost")
    if card.get("attack") is not None:
        c["a"] = card.get("attack")
    if card.get("health") is not None:
        c["h"] = card.get("health")
    if card.get("durability") is not None:
        c["d"] = card.get("durability")
    return c


def _compact_hero(hero: Dict[str, object]) -> Dict[str, object]:
    """英雄压缩：只留 职业/名字/血量/护甲。"""
    c: Dict[str, object] = {}
    if hero.get("card_id"):
        c["cid"] = hero["card_id"]
    if hero.get("name"):
        c["n"] = hero["name"]
    if hero.get("health") is not None:
        c["hp"] = hero["health"]
    if hero.get("armor") is not None:
        c["ar"] = hero["armor"]
    return c


HERO_CLASS = {
    "HERO_01": "法师",
    "HERO_02": "猎人",
    "HERO_03": "战士",
    "HERO_03b": "潜行者",
    "HERO_04": "圣骑士",
    "HERO_05": "牧师",
    "HERO_06": "德鲁伊",
    "HERO_07": "萨满",
    "HERO_08": "术士",
    "HERO_09": "恶魔猎手",
    "HERO_10": "死亡骑士",
    "HERO_11": "武僧",
}


def _enemy_class(hero: Optional[Dict[str, object]]) -> str:
    """从敌方英雄 card_id 推断职业（HERO_xx 前缀）。"""
    if not hero:
        return "?"

    cid = str(hero.get("card_id") or "")

    for prefix, cls in HERO_CLASS.items():
        if cid.startswith(prefix):
            return cls

    return str(hero.get("name") or "?")


def build_payload(
    snapshot: Dict[str, object],
    result: Dict[str, object],
    best_exchange: Optional[List[tuple]] = None,
) -> Dict[str, object]:
    """组装上报记录：

    用户ID + 场面信息（敌方职业/手牌/场面/法力/水晶/牛池等影响计算的因素）
    + 缩写公式 + 完整对局出牌/操作记录。
    """
    hand = snapshot.get("hand") or []
    best = (result.get("results") or [{}])[0]

    exchanges: List[str] = []
    for fi, ei in (best_exchange or []):
        target = "敌方英雄" if ei == 0 else f"敌方随从{ei}"
        exchanges.append(f"我方随从{fi}->{target}")

    # 压缩：场面卡/英雄/路径/对局记录全部缩写精简（保持可读）
    compact_game_record = []
    for ev in snapshot.get("game_record") or []:
        etype = str(ev.get("type") or "")
        if etype == "play":
            compact_game_record.append(
                {
                    "t": "p",
                    "r": int(ev.get("turn") or 0),
                    "c": _abbrev(str(ev.get("card") or "")),
                }
            )
        elif etype == "turn":
            compact_game_record.append(
                {
                    "t": "s",
                    "r": int(ev.get("turn") or 0),
                    "h": [_abbrev(str(x)) for x in (ev.get("hand") or [])],
                    "b": [_abbrev(str(x)) for x in (ev.get("board") or [])],
                    "m": ev.get("mana"),
                    "x": ev.get("crystal"),
                }
            )
        elif etype == "mulligan":
            compact_game_record.append(
                {
                    "t": "m",
                    "h": [_abbrev(str(x)) for x in (ev.get("initial_hand") or [])],
                    "r": [_abbrev(str(x)) for x in (ev.get("replaced") or [])],
                }
            )
        else:
            compact_game_record.append(ev)

    return {
        # 条件1：正常计算的伤害 > 0 时才上报（由调用方在构造前判断）
        "user_id": str(snapshot.get("player_name") or "?"),
        # 场面信息（压缩）：影响计算的全部因素
        "scene": {
            "enemy_class": _enemy_class(snapshot.get("opponent_hero") or {}),
            "ph": _compact_hero(snapshot.get("player_hero") or {}),
            "oh": _compact_hero(snapshot.get("opponent_hero") or {}),
            "hand": [_compact_card(x) for x in hand],
            "board": [_compact_card(x) for x in (snapshot.get("board") or [])],
            "enemy_board": [
                _compact_card(x) for x in (snapshot.get("enemy_board") or [])
            ],
            "crystal": snapshot.get("crystals"),
            "mana": snapshot.get("mana"),
            "etc_band": snapshot.get("etc_band") or [],
            "current_effects": snapshot.get("current_effects") or [],
            "deadly_shadow_hand_indexes": (
                snapshot.get("deadly_shadow_hand_indexes") or []
            ),
            "weapon": (
                _compact_card(snapshot["weapon"]) if snapshot.get("weapon") else None
            ),
            "secrets": snapshot.get("secrets") or [],
            "deck_unknown_cards": snapshot.get("deck_unknown_cards"),
        },
        # 缩写公式（主窗口缩写格式，如 币-鱼-狐-刀-牛(舞龙)-…）
        "formula": str(result.get("abbr_formula") or ""),
        # 数据内容：完整的对局出牌、操作记录
        "rec": {
            "dragon_num": int(result.get("max_dragons") or 0),
            "damage": int(result.get("max_damage") or 0),
            "remain_cost": int(best.get("mana") or 0),
            "play_seq": [_abbrev(str(step)) for step in (best.get("path") or [])],
            # 完整对局记录：起手牌/换牌 + 每回合出牌 + 每回合场面（reader 自动追踪）
            "game": compact_game_record,
            # 场面交换处理：最优解使用的交换计划（我方随从->敌方随从/英雄）
            "exchanges": exchanges,
            # 统一 W-B 机制分支树（抽随从卡/持枪要挟分支的完整记录）
            "wb": result.get("wb"),
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
