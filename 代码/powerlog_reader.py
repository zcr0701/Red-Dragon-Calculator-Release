"""Power.log 实时读取器（基于 hslog 库解析）。

读取炉石客户端写出的 Power.log（需在
%LOCALAPPDATA%\\Blizzard\\Hearthstone\\log.config 中开启 [Power] 详细日志），
用 HearthSim/python-hslog 增量解析对局状态，输出快照 dict：
hand/board/deck/secrets/weapon/current_effects/crystals/mana/in_game 等，
供 GUI 展示并把场面传入 C++ 计算核心。

依赖：pip install hslog（自动带 hearthstone、aniso8601）

用法：
    python 代码/powerlog_reader.py --once            # 打印最新对局快照（JSON）
    python 代码/powerlog_reader.py --watch           # 持续跟随最新对局
    python 代码/powerlog_reader.py --log-file <路径> --once
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from hearthstone.enums import BlockType, GameTag
from hslog.exceptions import MissingPlayerData
from hslog.parser import LogParser
from hslog.packets import (
    Block,
    ChangeEntity,
    CreateGame,
    FullEntity,
    HideEntity,
    ShowEntity,
    TagChange,
)
from hslog.player import PlayerReference, coerce_to_entity_id


BASE_DIR = Path(__file__).resolve().parent
CARD_ID_MAP_PATH = BASE_DIR / "card_id_map.json"

DEFAULT_GAME_DIRS = [
    r"F:\Hearthstone",
    r"C:\Program Files (x86)\Hearthstone",
    r"C:\Program Files\Hearthstone",
]

ZONE_HAND = "HAND"
ZONE_DECK = "DECK"
ZONE_PLAY = "PLAY"
ZONE_GRAVEYARD = "GRAVEYARD"
ZONE_SECRET = "SECRET"
ZONE_SETASIDE = "SETASIDE"

# hslog 只解析 GameState.DebugPrintPower 顶层行，PowerTaskList 前缀与
# SubSpell 内嵌套的重复 TAG_CHANGE 行会被丢弃（其中含法力/资源等关键标签）。
# 这里自行补抓：实体引用格式与玩家名格式。
ENTITY_REF_TAG_RE = re.compile(
    r"TAG_CHANGE Entity=\[.*?\bid=(\d+).*?\] tag=(\w+) value=(\w+)"
)
PLAYER_NAME_TAG_RE = re.compile(r"TAG_CHANGE Entity=([^ \[]+) tag=(\w+) value=(\w+)")
# DebugPrintGame 提供权威 PlayerID/PlayerName 映射（hslog 的名字推断在 CN 日志
# 里会与实体互换，导致玩家名与法力标签落错实体）
DEBUGGAME_PLAYER_RE = re.compile(r"PlayerID=(\d+),\s*PlayerName=([^\r\n]+)")

# Power.log 对部分随从（尤其敌方）不打印 BLOCK_START PLAY 与 ZONE=PLAY 更新，
# 只有 PowerProcessor 的 "unhandled BlockType PLAY for sourceEntity [...]" 行，
# 用它推断实体已进场。
UNHANDLED_PLAY_RE = re.compile(
    r"unhandled BlockType PLAY for sourceEntity \[entityName=.*? id=(\d+)"
)
ZONEPOS_RE = re.compile(r"zonePos=(\d+)")

# 附加效果（enchantment）CardID -> 项目“当前效果”名称。
# 这些实体挂在玩家实体上（ATTACHED=玩家实体ID），用于中途启动时兜底补种。
EFFECT_ENCHANTMENTS = {
    "EX1_145e": "伺机待发",
    "DMF_511e": "狐人老千",
    "BAR_552e": "斯卡布斯·刀油",
    "REV_939e": "锯齿骨刺",
    "REV_939e2": "锯齿骨刺",
    "TRL_092e": "鲨鱼之灵",
    "GDB_873e": "幸运彗星",  # 彗星连击：下一个连击随从触发两次（跨回合生效）
}

# 打出以下牌时叠加“当前效果”（事件计数，和游戏内规则一致）
EFFECT_CARD_NAMES = {"伺机待发", "狐人老千", "斯卡布斯·刀油", "锯齿骨刺", "幸运彗星"}

# 连击牌名单（日志若未打印 tag=COMBO 时兜底）
COMBO_CARD_NAMES = {
    "暗影步", "斯卡布斯·刀油", "行骗", "疾速矿锄",
    "赤烟·腾武", "伪造的幸运币", "可疑交易",
}

# HearthstoneJSON 卡名与项目卡名不一致时的修正
NAME_ALIASES = {
    '"赤烟"腾武': "赤烟·腾武",    # 兼容 ASCII 引号变体
    "“赤烟”腾武": "赤烟·腾武",   # 官方全角引号名
}

# 乐队经理精英牛头人酋长的乐队可选卡（CardID → 项目名）
ETC_BAND_CARD_IDS = {
    "ETC_079": "舞动全场（ft.迦罗娜）",
    "SCH_352": "幻觉药水",
    "CS3_031": "生命的缚誓者阿莱克丝塔萨",
    "LEG_CS3_031": "生命的缚誓者阿莱克丝塔萨",
    "DAL_728": "战略转移",
    "DMF_071": "赤烟·腾武",
}

# 殒命暗影（巫妖王的进军 0 费法术）：进入手牌即自动标记为殒命暗影
DEADLY_SHADOW_CARD_IDS = {"RLK_567", "CORE_RLK_567"}


def _load_card_map() -> Dict[str, dict]:
    global _CARD_MAP
    if _CARD_MAP is None:
        try:
            with open(CARD_ID_MAP_PATH, encoding="utf-8") as f:
                _CARD_MAP = json.load(f)
        except Exception:
            _CARD_MAP = {}
    return _CARD_MAP


_CARD_MAP: Optional[Dict[str, dict]] = None


def card_name(card_id: str) -> str:
    """CardID -> 中文名（带别名修正）；未知 ID 原样返回。"""
    info = _load_card_map().get(card_id) or {}
    name = info.get("name") or card_id
    return NAME_ALIASES.get(name, name)


def find_local_accounts() -> Set[Tuple[int, int]]:
    """从本机离线缓存文件名读账号 hi/lo，用于在 CREATE_GAME 中确认本机玩家。"""
    accounts: Set[Tuple[int, int]] = set()
    env_hi = os.environ.get("HS_ACCOUNT_HI")
    env_lo = os.environ.get("HS_ACCOUNT_LO")

    if env_hi and env_lo:
        try:
            accounts.add((int(env_hi), int(env_lo)))
        except ValueError:
            pass

    offline = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Blizzard"
        / "Hearthstone"
        / "Cache"
        / "Offline"
    )

    if offline.is_dir():
        for f in offline.glob("offlineData_*.cache"):
            m = re.match(r"offlineData_(\d+)_(\d+)_", f.name)

            if m:
                accounts.add((int(m.group(1)), int(m.group(2))))

    return accounts


def detect_game_dir() -> Optional[str]:
    env_dir = os.environ.get("HS_GAME_DIR")

    if env_dir and Path(env_dir).is_dir():
        return env_dir

    for candidate in DEFAULT_GAME_DIRS:
        if Path(candidate).is_dir():
            return candidate

    return None


def find_latest_session(game_dir: Optional[str]) -> Optional[Path]:
    """返回最新会话目录（内含 Power.log）。兼容老版本 Power.log 直接放 Logs 下。"""
    if not game_dir:
        return None

    logs_dir = Path(game_dir) / "Logs"

    if not logs_dir.is_dir():
        return None

    sessions = [
        d
        for d in logs_dir.iterdir()
        if d.is_dir() and (d / "Power.log").exists()
    ]

    if not sessions and (logs_dir / "Power.log").exists():
        return logs_dir

    if not sessions:
        return None

    return max(sessions, key=lambda d: d.stat().st_mtime)


def _value_name(value) -> str:
    """把 hslog 解析出的枚举值转成字符串（Zone.HAND -> "HAND"），其余原样。"""
    if isinstance(value, Enum):
        return value.name
    return value


class PowerLogParser:
    """基于 hslog.LogParser 增量解析 Power.log，维护当前对局的实体与状态。"""

    def __init__(
        self,
        local_accounts: Optional[Set[Tuple[int, int]]] = None,
        player_id: Optional[int] = None,
    ):
        self.local_accounts = set(local_accounts or [])
        self.forced_player_id = player_id
        self._pending_direct_tags: List[tuple] = []  # (tree_id, entity, tag, value)
        # DebugPrintGame 权威 玩家ID→名字（按对局树分桶，重置/换局不丢失已喂入数据）
        self._player_name_by_tree: Dict[int, Dict[int, str]] = {}
        self.reset()

    def reset(self) -> None:
        self._hslog = LogParser()
        self._current_tree = None
        self._last_packet_id = 0
        self.line_errors = 0
        self._player_name_by_tree.clear()
        self._reset_game()

    def _reset_game(self) -> None:
        self.entities: Dict[int, dict] = {}
        self.player_entity_by_player_id: Dict[int, int] = {}
        self.player_id_by_entity: Dict[int, int] = {}
        self.account_by_player_id: Dict[int, Tuple[int, int]] = {}
        self.game_entity_id = 1
        self.game_state: Optional[str] = None
        self.game_over = False
        self.local_controller: Optional[int] = None
        self.local_entity_id: Optional[int] = None
        self.pending_effects: Dict[str, int] = {}
        self.seen_play_events = False
        self._etc_chosen: Set[str] = set()  # 已通过牛头人酋长选走的乐队卡
        self._etc_pending_choices = 0       # 刚打出牛头人酋长，还差几张乐队卡进手（鲨鱼双战吼=2）
        self._deadly_entities: Set[int] = set()  # 进入手牌后被判定为殒命暗影的实体（随变形持续追踪）
        self._local_cards_played_this_turn = 0   # 本方本回合出牌数（连击/快枪判定）
        self._last_turn_seen: Optional[int] = None
    # ---- 行入口 ----

    def feed_line(self, line: str) -> None:
        try:
            self._hslog.read_line(line)
        except Exception:
            # 单行解析失败（未知枚举/新 opcode/脏行）不中断整体解析
            self.line_errors += 1
        self._collect_player_name(line)
        self._collect_direct_tags(line)
        self._infer_play_zone(line)

    def _collect_player_name(self, line: str) -> None:
        """抓取 DebugPrintGame 的权威 玩家ID→名字（跳过 UNKNOWN）。"""
        m = DEBUGGAME_PLAYER_RE.search(line)

        if not m:
            return

        player_id = int(m.group(1))
        name = m.group(2).strip()

        if name and name != "UNKNOWN HUMAN PLAYER":
            tree_id = id(self._hslog.games[-1]) if self._hslog.games else None
            self._player_name_by_tree.setdefault(tree_id, {})[player_id] = name

    def _current_player_names(self) -> Dict[int, str]:
        """当前对局树的权威 玩家ID→名字（无树/换局时为空）。"""
        tree_id = id(self._hslog.games[-1]) if self._hslog.games else None
        return self._player_name_by_tree.get(tree_id, {})

    def _infer_play_zone(self, line: str) -> None:
        """从 unhandled BlockType PLAY 行推断随从已进场（补 ZONE=PLAY 缺失）。"""
        m = UNHANDLED_PLAY_RE.search(line)

        if not m:
            return

        ent = self.entities.get(int(m.group(1)))

        if ent is None:
            return

        if ent.get("zone") != ZONE_PLAY:
            ent["zone"] = ZONE_PLAY

        zp = ZONEPOS_RE.search(line)

        if zp:
            ent["ZONE_POSITION"] = int(zp.group(1))

    def _collect_direct_tags(self, line: str) -> None:
        """收集 hslog 漏掉的 TAG_CHANGE（PowerTaskList/嵌套重复行），按行序补应用。"""
        m = ENTITY_REF_TAG_RE.search(line)
        entity: Optional[int] = None

        if m:
            entity = int(m.group(1))
            tag, value = m.group(2), m.group(3)
        else:
            m = PLAYER_NAME_TAG_RE.search(line)

            if not m:
                return

            entity = self._resolve_direct_entity(m.group(1))
            tag, value = m.group(2), m.group(3)

        if entity is None:
            return

        try:
            value = int(value)
        except ValueError:
            pass

        tree_id = id(self._hslog.games[-1]) if self._hslog.games else None
        self._pending_direct_tags.append((tree_id, entity, tag, value))

    def _resolve_direct_entity(self, name: str) -> Optional[int]:
        """玩家名 → 玩家实体 id（GameEntity/实体引用格式已在正则中处理）。"""
        if name == "GameEntity":
            return self.game_entity_id or 1

        resolved = self._name_to_entity_id(name)

        if resolved is not None:
            return resolved

        player = self._hslog.player_manager._players_by_name.get(name)

        if player is not None:
            return getattr(player, "entity_id", None)

        if self.local_controller is not None:
            local_name = self._player_name(self.local_controller)

            if local_name == name:
                return self.local_entity_id

        return None

    def _flush_direct_tags(self) -> None:
        """快照前把漏掉的 TAG_CHANGE 按行序应用到实体表（幂等，最后值生效）。

        只应用属于当前对局（tree_id 匹配）的条目，避免换局后把上一局的行级
        标签误套到新局；也兼容中途启动（整段日志先喂入再首次快照）。
        """
        current_tree_id = id(self._hslog.games[-1]) if self._hslog.games else None

        for tree_id, entity, tag, value in self._pending_direct_tags:
            if tree_id != current_tree_id:
                continue

            self._apply_tag(entity, tag, value)

        self._pending_direct_tags.clear()

    def sync(self) -> None:
        """处理自上次 sync 之后新出现的 packets（对局开始时全量处理）。"""
        games = self._hslog.games

        if not games:
            return

        tree = games[-1]

        if tree is not self._current_tree:
            self._current_tree = tree
            self._reset_game()
            self._last_packet_id = 0

        if tree.packet_counter <= self._last_packet_id:
            return

        for packet in tree.recursive_iter():
            packet_id = getattr(packet, "packet_id", 0)

            if packet_id <= self._last_packet_id:
                continue

            self._handle_packet(packet)
            self._last_packet_id = packet_id

    # ---- 玩家绑定 ----

    def _bind_player(self, player_id: int, entity_id: int) -> None:
        self.player_entity_by_player_id[player_id] = entity_id
        self.player_id_by_entity[entity_id] = player_id
        self.entities.setdefault(entity_id, {})["is_player"] = True

    def _handle_create_player(self, packet) -> None:
        player_id = packet.player_id
        hi, lo = packet.hi, packet.lo
        self.account_by_player_id[player_id] = (hi, lo)
        ref = packet.entity
        entity_id = getattr(ref, "entity_id", None)

        if entity_id is not None:
            self._bind_player(player_id, entity_id)

        if (hi, lo) in self.local_accounts:
            self.local_controller = player_id
            self.local_entity_id = entity_id

        for tag, value in packet.tags:
            if tag == GameTag.ENTITY_ID:
                new_id = int(value)
                self._bind_player(player_id, new_id)
                entity_id = new_id

                if self.local_controller == player_id:
                    self.local_entity_id = new_id

            if entity_id is not None:
                self._apply_tag(entity_id, tag, value)

    # ---- 实体引用 ----

    def _player_entity_id(self, player_id: int) -> Optional[int]:
        """玩家 ID → 玩家实体 ID（CreateGame 绑定优先，hslog 映射兜底）。"""
        entity_id = self.player_entity_by_player_id.get(player_id)

        if entity_id is not None:
            return entity_id

        ref = self._hslog.player_manager.get_player_by_player_id(player_id)

        if ref is not None:
            return getattr(ref, "entity_id", None)

        return None

    def _name_to_entity_id(self, name: str) -> Optional[int]:
        """玩家名 → 玩家实体 ID。

        DebugPrintGame 是权威映射（hslog 的名字推断在 CN 日志里会互换，
        导致玩家名与法力标签落错实体）；权威名未知的玩家（对手）接受
        未被权威名占用的玩家名。
        """
        for player_id, player_name in self._current_player_names().items():
            if player_name == name:
                return self._player_entity_id(player_id)

        known = set(self._current_player_names())

        for player_id in (1, 2):
            if player_id not in known:
                return self._player_entity_id(player_id)

        return None

    def _resolve_entity(self, entity) -> Optional[int]:
        if isinstance(entity, int):
            return entity

        if isinstance(entity, PlayerReference):
            name = getattr(entity, "name", None)

            if name:
                resolved = self._name_to_entity_id(name)

                if resolved is not None:
                    return resolved

            try:
                return coerce_to_entity_id(entity)
            except MissingPlayerData:
                return None

        return None

    # ---- 标签 ----

    def _apply_tag(self, entity_id: int, tag, value) -> None:
        tag_name = getattr(tag, "name", str(tag))
        val = _value_name(value)
        ent = self.entities.setdefault(entity_id, {})
        ent[tag_name] = val

        if tag_name == "ZONE":
            prev_zone = ent.get("zone")
            ent["zone"] = val
            # 牛头人乐队卡被选中：实体离开 SETASIDE（进手牌/打出）即记录，
            # 不依赖事后扫手牌——选牌后立刻打出也不会漏记
            if (
                prev_zone == ZONE_SETASIDE
                and val != ZONE_SETASIDE
                and ent.get("controller") in (None, self.local_controller)
            ):
                card_id = ent.get("card_id") or ""
                name = ETC_BAND_CARD_IDS.get(card_id)
                if name:
                    self._record_etc_pick(name)
            # 殒命暗影进入手牌即开始追踪；离开手牌（打出/变形离场）停止
            if val == ZONE_HAND and ent.get("card_id") in DEADLY_SHADOW_CARD_IDS:
                self._deadly_entities.add(entity_id)
            elif val != ZONE_HAND:
                self._deadly_entities.discard(entity_id)
        elif tag_name == "TURN":
            # 新回合开始：本方本回合出牌计数清零；本回合类效果（伺机/刀油/骨刺/狐人）
            # 到期移除；幸运彗星效果跨回合保留，直到被真正触发的连击消耗。
            if val != self._last_turn_seen:
                self._last_turn_seen = val
                self._local_cards_played_this_turn = 0
                for name in ("伺机待发", "斯卡布斯·刀油", "锯齿骨刺", "狐人老千"):
                    self.pending_effects.pop(name, None)
        elif tag_name == "CONTROLLER":
            ent["controller"] = val if isinstance(val, int) else None
        elif tag_name == "COST":
            ent["cost"] = val if isinstance(val, int) else None
        elif tag_name == "HEALTH":
            ent["health"] = val if isinstance(val, int) else None
        elif tag_name == "CARDTYPE":
            ent["card_type"] = val
        elif tag_name == "ATTACHED":
            ent["attached"] = val if isinstance(val, int) else None
        elif tag_name == "GHOSTLY":
            ent["ghostly"] = val if isinstance(val, int) else 0
        elif tag_name == "COMBO":
            ent["combo"] = val if isinstance(val, int) else 0
        elif tag_name == "PLAYSTATE" and val in ("WON", "LOST", "TIED"):
            self.game_over = True
        elif tag_name == "STATE":
            self.game_state = val

    # ---- 出牌事件：当前效果计数 ----

    def _on_play_entity(self, entity_id: int) -> None:
        ent = self.entities.get(entity_id)

        if not ent or not ent.get("card_id"):
            return

        if ent.get("controller") not in (None, self.local_controller):
            return

        self.seen_play_events = True
        self._local_cards_played_this_turn += 1
        name = card_name(ent["card_id"])
        self._consume_effects(name, ent)

        if name in EFFECT_CARD_NAMES:
            self.pending_effects[name] = self.pending_effects.get(name, 0) + 1

        if ent["card_id"] == "ETC_080":
            # 乐队经理精英牛头人酋长：鲨鱼之灵在场时战吼触发两次 → 连选两张乐队卡
            shark_on_board = any(
                e.get("card_id") == "TRL_092"
                and e.get("zone") == ZONE_PLAY
                and e.get("controller") == self.local_controller
                for e in self.entities.values()
            )
            self._etc_pending_choices = 2 if shark_on_board else 1

    def _record_etc_pick(self, name: str) -> None:
        """牛头人乐队卡被选中：加入已选集合，等待计数减一。"""
        if name not in self._etc_chosen:
            self._etc_chosen.add(name)

        if self._etc_pending_choices > 0:
            self._etc_pending_choices -= 1

    def _consume_effects(self, name: str, ent: dict) -> None:
        card_type = ent.get("card_type")
        is_combo = ent.get("combo") == 1 or name in COMBO_CARD_NAMES

        # 伺机待发：下一个法术一次性消耗（叠 N 层只作用于第一个法术）
        if (
            name != "伺机待发"
            and card_type == "SPELL"
            and self.pending_effects.get("伺机待发", 0) > 0
        ):
            self.pending_effects["伺机待发"] = 0

        # 狐人老千：下一张连击牌一次性消耗（叠 N 层只作用于第一张连击牌）
        if (
            name != "狐人老千"
            and is_combo
            and self.pending_effects.get("狐人老千", 0) > 0
        ):
            self.pending_effects["狐人老千"] = 0

        # 幸运彗星：下一个连击随从的连击触发两次。效果不随回合结束消失，
        # 持续到被真正触发的连击消耗（本回合此前已出过牌才算连击生效）。
        if (
            name != "幸运彗星"
            and card_type == "MINION"
            and is_combo
            and self._local_cards_played_this_turn > 1
            and self.pending_effects.get("幸运彗星", 0) > 0
        ):
            self.pending_effects["幸运彗星"] = 0

        # 斯卡布斯·刀油：本回合接下来两张牌各减 2，逐张消耗
        if (
            name != "斯卡布斯·刀油"
            and self.pending_effects.get("斯卡布斯·刀油", 0) > 0
        ):
            self.pending_effects["斯卡布斯·刀油"] -= 1

        # 锯齿骨刺：下一张牌一次性消耗（叠 N 层只作用于下一张牌）
        if (
            name != "锯齿骨刺"
            and self.pending_effects.get("锯齿骨刺", 0) > 0
        ):
            self.pending_effects["锯齿骨刺"] = 0

    # ---- packet 分发 ----

    def _handle_packet(self, packet) -> None:
        if isinstance(packet, CreateGame):
            self.game_entity_id = packet.entity

            # hslog 把玩家子包放在 CreateGame.players 里，不在主 packet 树中
            for player_packet in getattr(packet, "players", []):
                self._handle_create_player(player_packet)

            return

        if isinstance(packet, CreateGame.Player):
            self._handle_create_player(packet)
            return

        if isinstance(packet, (FullEntity, ShowEntity, ChangeEntity)):
            entity_id = self._resolve_entity(packet.entity)

            if entity_id is None:
                return

            ent = self.entities.setdefault(entity_id, {})

            if packet.card_id:
                ent["card_id"] = packet.card_id
                # 兜底：ZONE 标签先于 card_id 到达时，补记殒命暗影
                if (
                    ent.get("zone") == ZONE_HAND
                    and packet.card_id in DEADLY_SHADOW_CARD_IDS
                ):
                    self._deadly_entities.add(entity_id)

            for tag, value in getattr(packet, "tags", []):
                self._apply_tag(entity_id, tag, value)

            return

        if isinstance(packet, TagChange):
            entity_id = self._resolve_entity(packet.entity)

            if entity_id is not None:
                self._apply_tag(entity_id, packet.tag, packet.value)

            return

        if isinstance(packet, HideEntity):
            entity_id = self._resolve_entity(packet.entity)

            if entity_id is not None:
                zone = _value_name(packet.zone)
                self.entities.setdefault(entity_id, {})["zone"] = zone

            return

        if isinstance(packet, Block) and packet.type == BlockType.PLAY:
            entity_id = self._resolve_entity(packet.entity)

            if entity_id is not None:
                self._on_play_entity(entity_id)

                ent = self.entities.get(entity_id)

                if ent is not None and ent.get("zone") != ZONE_PLAY:
                    # Power.log 偶尔不打印随从进场的 ZONE 更新（如敌方淡水鳄
                    # HAND→PLAY），用 PLAY block 推断实体已进场
                    ent["zone"] = ZONE_PLAY

    # ---- 快照 ----

    def _detect_local_controller(self) -> Optional[int]:
        if self.forced_player_id is not None:
            return self.forced_player_id

        if self.local_controller is not None:
            return self.local_controller

        # 私密信息启发：本机的手牌/牌库卡牌 ID 在日志中可见，对手隐藏。
        # 手牌权重更高（本机手牌恒可见，牌库可能被揭示/抽空）；
        # 信息量最多且唯一者即本机，避免对手牌库被揭示时误判成本机。
        scores: Dict[int, int] = {}

        for ent in self.entities.values():
            controller = ent.get("controller")

            if controller is None or not ent.get("card_id"):
                continue

            if ent.get("zone") == ZONE_HAND:
                scores[controller] = scores.get(controller, 0) + 2
            elif ent.get("zone") == ZONE_DECK:
                scores[controller] = scores.get(controller, 0) + 1

        if scores:
            best = max(scores.values())
            winners = [player for player, score in scores.items() if score == best]

            if len(winners) == 1:
                return winners[0]

        return None

    def _player_name(self, player_id: int) -> Optional[str]:
        name = self._current_player_names().get(player_id)

        if name:
            return name

        # 权威名未知（对手）：hslog 里未被权威名占用的玩家名即其真名
        used = set(self._current_player_names().values())
        manager = self._hslog.player_manager

        for ref in getattr(manager, "_players_by_player_id", {}).values():
            ref_name = getattr(ref, "name", None)

            if ref_name and ref_name not in used:
                return ref_name

        player = self._hslog.player_manager.get_player_by_player_id(player_id)
        return player.name if player is not None else None

    def _entity_item(self, ent: dict) -> dict:
        card_id = ent.get("card_id") or ""
        card_type = ent.get("card_type")
        health = ent.get("health")
        health_max = health
        if card_type == "MINION":
            # 当前血量 = 基础血量 - 已受伤害（TAG_DAMAGE），随标签变化实时更新
            damage = ent.get("DAMAGE") or ent.get("damage") or 0
            if isinstance(health, int) and isinstance(damage, int) and damage > 0:
                health = max(0, health - damage)

        return {
            "card_id": card_id,
            "name": card_name(card_id),
            "cost": ent.get("cost"),
            "health": health if card_type == "MINION" else None,
            "health_max": health_max if card_type == "MINION" else None,
            "attack": ent.get("ATK"),
            "zone_position": ent.get("ZONE_POSITION"),
            "ghostly": bool(ent.get("ghostly")),
        }

    def _seed_effects_from_enchantments(self) -> Dict[str, int]:
        """中途启动（没看到任何出牌事件）时，用附加效果实体补种当前效果。"""
        local_entity_id = self.local_entity_id or (
            self.player_entity_by_player_id.get(self.local_controller or -1)
        )
        counts: Dict[str, int] = {}

        for ent in self.entities.values():
            card_id = ent.get("card_id") or ""

            if card_id not in EFFECT_ENCHANTMENTS:
                continue

            if ent.get("attached") != local_entity_id:
                continue

            name = EFFECT_ENCHANTMENTS[card_id]
            counts[name] = counts.get(name, 0) + 1

        return counts

    def snapshot(self) -> dict:
        self.sync()
        self._flush_direct_tags()
        local_controller = self._detect_local_controller()

        if local_controller is None:
            return {
                "in_game": False,
                "reason": "对局尚未开始，或无法判断本机玩家",
                "hand": [],
                "board": [],
                "deck": [],
                "secrets": [],
                "weapon": None,
                "current_effects": [],
                "deadly_shadow_hand_indexes": [],
                "crystals": None,
                "mana": None,
                "game_state": self.game_state,
                "game_over": self.game_over,
            }

        self.local_controller = local_controller
        self.local_entity_id = self.player_entity_by_player_id.get(local_controller)

        # 兜底：hslog 玩家映射未同步到 player_entity_by_player_id 时，
        # 直接用 hslog 的 PlayerManager 解析本机玩家实体（法力/资源标签所在实体）。
        if self.local_entity_id is None and self._hslog is not None:
            player_ref = self._hslog.player_manager.get_player_by_player_id(local_controller)

            if player_ref is not None:
                self.local_entity_id = getattr(player_ref, "entity_id", None)
                self.player_entity_by_player_id[local_controller] = self.local_entity_id

        hand: List[dict] = []
        hand_entity_ids: List[int] = []
        board: List[dict] = []
        enemy_board: List[dict] = []
        deck: List[dict] = []
        secrets: List[dict] = []
        weapon: Optional[dict] = None
        etc_band: Optional[List[str]] = None

        # 牛头人乐队：SETASIDE 区的原始乐队卡即本局牛池。
        # 排除发现选项的临时复制体（WAS_DISCOVER_OPTION）并按名去重，
        # 避免双战吼生成的选项复制体把牛池撑成重复多张；被选走的牌由
        # SETASIDE 离场事件实时记入 _etc_chosen，从池中移除。
        raw_band: List[str] = []
        for ent in self.entities.values():
            card_id = ent.get("card_id") or ""
            name = ETC_BAND_CARD_IDS.get(card_id)
            if (
                name
                and ent.get("zone") == ZONE_SETASIDE
                and not ent.get("WAS_DISCOVER_OPTION")
                and ent.get("controller") in (None, self.local_controller)
                and name not in raw_band
            ):
                raw_band.append(name)

        if raw_band:
            # 牛池最多三张：乐队实体按创建顺序收集（真实乐队先于发现选项生成），
            # 排除已选走后截断到 3，避免发现选项残留实体把牛池撑大。
            etc_band = [n for n in raw_band if n not in self._etc_chosen][:3]

        for entity_id, ent in self.entities.items():
            card_id = ent.get("card_id")

            if not card_id:
                continue

            card_type = ent.get("card_type")

            if card_type in (None, "PLAYER", "GAME", "ENCHANTMENT", "HERO", "HERO_POWER"):
                continue

            zone = ent.get("zone")
            item = self._entity_item(ent)

            if ent.get("controller") != local_controller:
                # 敌方随从：只需血量（用于锯齿骨刺击杀抽牌）
                if zone == ZONE_PLAY and card_type == "MINION":
                    enemy_board.append(item)
                continue

            if zone == ZONE_HAND:
                hand.append(item)
                hand_entity_ids.append(entity_id)
            elif zone == ZONE_PLAY:
                if card_type == "MINION":
                    board.append(item)
                elif card_type == "WEAPON":
                    weapon = item
            elif zone == ZONE_DECK:
                deck.append(item)
            elif zone == ZONE_SECRET:
                secrets.append(item)

        hand_pairs = sorted(
            zip(hand, hand_entity_ids),
            key=lambda pair: (pair[0]["zone_position"] is None, pair[0]["zone_position"] or 0),
        )
        hand = [pair[0] for pair in hand_pairs]
        hand_entity_ids = [pair[1] for pair in hand_pairs]
        board.sort(key=lambda item: (item["zone_position"] is None, item["zone_position"] or 0))
        enemy_board.sort(key=lambda item: (item["zone_position"] is None, item["zone_position"] or 0))

        if raw_band:
            etc_band = [n for n in raw_band if n not in self._etc_chosen][:3]

        if self.seen_play_events:
            effect_counts = dict(self.pending_effects)
        else:
            effect_counts = self._seed_effects_from_enchantments()

        current_effects = [
            {"name": name, "count": count}
            for name, count in sorted(effect_counts.items())
            if count > 0
        ]

        deadly_shadow_hand_indexes = [
            index
            for index, (item, entity_id) in enumerate(zip(hand, hand_entity_ids), start=1)
            if item["ghostly"] or entity_id in self._deadly_entities
        ]

        crystals: Optional[int] = None
        mana: Optional[int] = None

        if self.local_entity_id is not None:
            player_ent = self.entities.get(self.local_entity_id, {})
            res = player_ent.get("RESOURCES")

            if res is not None:
                crystals = int(res)
                used = int(player_ent.get("RESOURCES_USED", 0) or 0)
                temp = int(player_ent.get("TEMP_RESOURCES", 0) or 0)
                mana = max(0, crystals - used + temp)

        opponent_id = 1 if local_controller == 2 else 2

        return {
            "in_game": self.game_state in ("RUNNING", "STARTING") or (
                self.game_state is None
                and not self.game_over
                and bool(hand or board or deck)
            ),
            "reason": "对局已结束" if self.game_over else "对局进行中",
            "player_controller": local_controller,
            "player_name": self._player_name(local_controller),
            "opponent_name": self._player_name(opponent_id),
            "game_state": self.game_state,
            "game_over": self.game_over,
            "crystals": crystals,
            "mana": mana,
            "cards_played_this_turn": self._local_cards_played_this_turn,
            "hand": hand,
            "board": board,
            "enemy_board": enemy_board,
            "deck": deck,
            "secrets": secrets,
            "weapon": weapon,
            "etc_band": etc_band,
            "current_effects": current_effects,
            "deadly_shadow_hand_indexes": deadly_shadow_hand_indexes,
            "parser": "hslog",
            "line_errors": self.line_errors,
        }


class LogWatcher:
    """跟随最新会话的 Power.log，增量喂给 hslog 解析器。"""

    # 只解析最新一局（GameState 的 CREATE_GAME 才是一局起点；
    # PowerTaskList 的重复 CREATE_GAME 行不算）
    _GAME_START_MARKER = b"GameState.DebugPrintPower() - CREATE_GAME"

    # 单次刷新最多解析的行数：Power.log 爆发（对局开始/复杂回合）时限制主线程
    # 单次工作量，避免计算器自身卡顿与 CPU 尖峰；剩余行下个 tick 继续。
    MAX_LINES_PER_TICK = 20000

    def __init__(
        self,
        game_dir: Optional[str] = None,
        player_id: Optional[int] = None,
    ):
        self.game_dir = game_dir or detect_game_dir()
        self.player_id = player_id
        self.parser = PowerLogParser(
            local_accounts=find_local_accounts(),
            player_id=player_id,
        )
        self.session_dir: Optional[Path] = None
        self.log_file: Optional[Path] = None
        self._pos = 0

    def _last_game_offset(self) -> Optional[int]:
        """从文件尾部找最后一个 GameState CREATE_GAME 的行首字节偏移。"""
        if self.log_file is None or not self.log_file.exists():
            return None

        size = self.log_file.stat().st_size

        if size <= 0:
            return None

        marker = self._GAME_START_MARKER
        chunk = 1 << 20  # 1 MiB
        pos = size

        while pos > 0:
            start = max(0, pos - chunk - len(marker))

            with open(self.log_file, "rb") as f:
                f.seek(start)
                data = f.read(pos - start)

            idx = data.rfind(marker)

            if idx >= 0:
                line_start = data.rfind(b"\n", 0, idx) + 1
                return start + line_start

            pos = start

            if start == 0:
                break

        return None

    def _restart_at_latest_game(self) -> None:
        """只解析最新一局：重置解析器，游标定位到最后一个 GameState CREATE_GAME。"""
        self.parser.reset()
        offset = self._last_game_offset()
        self._pos = offset if offset is not None else 0

    def _refresh(self) -> None:
        session = find_latest_session(self.game_dir)

        if session != self.session_dir:
            self.session_dir = session
            self.log_file = session / "Power.log" if session is not None else None
            self._restart_at_latest_game()
            return

        if self.log_file is not None and self.log_file.exists():
            size = self.log_file.stat().st_size

            if size < self._pos:
                # 文件被重写/截断：重新定位到最新一局
                self._restart_at_latest_game()

    def read_new(self) -> int:
        if self.log_file is None or not self.log_file.exists():
            return 0

        count = 0
        new_game_offset: Optional[int] = None

        with open(self.log_file, "rb") as f:
            f.seek(self._pos)
            first = True

            for raw in f:
                if not first and self._GAME_START_MARKER in raw:
                    # 新一局开始：只保留最新一局
                    new_game_offset = f.tell() - len(raw)
                    break

                first = False
                self.parser.feed_line(raw.decode("utf-8", errors="ignore"))
                count += 1
                self._pos = f.tell()

                if count >= self.MAX_LINES_PER_TICK:
                    break

        if new_game_offset is not None:
            self.parser.reset()
            self._pos = new_game_offset
            return 0

        return count

    def snapshot(self) -> dict:
        self._refresh()
        self.read_new()
        snap = self.parser.snapshot()
        snap["log_path"] = str(self.log_file) if self.log_file else None
        snap["session_dir"] = str(self.session_dir) if self.session_dir else None
        return snap


def _snapshot_key(snap: dict) -> tuple:
    return (
        snap.get("log_path"),
        snap.get("in_game"),
        json.dumps(snap.get("hand", []), ensure_ascii=False),
        json.dumps(snap.get("board", []), ensure_ascii=False),
        snap.get("crystals"),
        snap.get("mana"),
    )


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Power.log 实时读取器（hslog）")
    parser.add_argument("--once", action="store_true", help="只打印一次快照")
    parser.add_argument("--watch", action="store_true", help="持续跟随最新对局")
    parser.add_argument("--game-dir", default=None, help="炉石安装目录（含 Logs 子目录）")
    parser.add_argument("--log-file", default=None, help="指定 Power.log 文件（测试用）")
    parser.add_argument("--player-id", type=int, default=None, help="强制本机 PlayerID")
    parser.add_argument("--interval", type=float, default=0.5, help="watch 轮询间隔")
    args = parser.parse_args(argv)

    if args.log_file:
        watcher = LogWatcher(game_dir=args.game_dir, player_id=args.player_id)
        path = Path(args.log_file)
        watcher.session_dir = path.parent
        watcher.log_file = path
        watcher.parser.reset()
        watcher._pos = 0
        watcher.read_new()
        snap = watcher.parser.snapshot()
        snap["log_path"] = str(path)
        snap["session_dir"] = str(path.parent)

        print(json.dumps(snap, ensure_ascii=False, indent=2))

        return 0

    watcher = LogWatcher(game_dir=args.game_dir, player_id=args.player_id)

    if args.watch:
        last_key = None

        while True:
            snap = watcher.snapshot()
            key = _snapshot_key(snap)

            if key != last_key:
                last_key = key
                print(json.dumps(snap, ensure_ascii=False, indent=2))
                sys.stdout.flush()

            time.sleep(max(0.1, args.interval))

    snap = watcher.snapshot()
    print(json.dumps(snap, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
