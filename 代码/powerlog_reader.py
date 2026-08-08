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
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from hearthstone.enums import BlockType, CardType, GameTag, PlayState, Zone
from hslog.export import EntityTreeExporter
from hslog.exceptions import MissingPlayerData
from hslog.parser import LogParser
from hslog.packets import (
    Block,
    TagChange,
)
from hslog.player import coerce_to_entity_id


BASE_DIR = Path(__file__).resolve().parent
CARD_ID_MAP_PATH = BASE_DIR / "card_id_map.json"

DEFAULT_GAME_DIRS = [
    r"F:\Hearthstone",
    r"C:\Program Files (x86)\Hearthstone",
    r"C:\Program Files\Hearthstone",
]

# 打出以下牌时叠加“当前效果”（事件计数，和游戏内规则一致）
EFFECT_CARD_NAMES = {"伺机待发", "狐人老千", "斯卡布斯·刀油", "锯齿骨刺", "幸运彗星"}

# 敌方随从战吼 → 我方下个回合法术增费：CardID -> 当前效果名
SP_COST_CARD_IDS = {
    "SCH_713": "[spcost+1]",        # 异教低阶牧师
    "CORE_SCH_713": "[spcost+1]",
    "JAM_034": "[spcost+2]",        # 音箱践踏者
}

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


def _deep_packets(root):
    """完整遍历 hslog 包树：hslog 的 recursive_iter 只递归 Block、不递归 SubSpell，
    导致 POWER/SUB_SPELL 深嵌套内的 SHOW_ENTITY / FULL_ENTITY / TAG_CHANGE 等包
    永远到不了 reader，新进手牌/战场的牌会丢失。这里遍历所有含 packets 的容器
    （PacketTree/Block/SubSpell），按注册顺序（=行序）完整取包。"""
    for packet in getattr(root, "packets", ()):
        yield packet

        children = getattr(packet, "packets", None)

        if children:
            yield from _deep_packets(packet)


class _TolerantExporter(EntityTreeExporter):
    """官方导出器的容错子类：对局中“UNKNOWN HUMAN PLAYER”等未解析玩家引用的
    TAG_CHANGE / SHOW / HIDE / CHANGE 包直接跳过（官方 tolerate_missing_entities
    只覆盖缺失实体，不覆盖缺失玩家引用）。"""

    def handle_tag_change(self, packet):
        try:
            return super().handle_tag_change(packet)
        except MissingPlayerData:
            return None

    def handle_show_entity(self, packet):
        try:
            return super().handle_show_entity(packet)
        except MissingPlayerData:
            return None

    def handle_hide_entity(self, packet):
        try:
            return super().handle_hide_entity(packet)
        except MissingPlayerData:
            return None

    def handle_change_entity(self, packet):
        try:
            return super().handle_change_entity(packet)
        except MissingPlayerData:
            return None


class PowerLogParser:
    """基于 hslog 官方接口（LogParser + EntityTreeExporter）读取对局状态。

    手牌/场面/牌库/法力/玩家等状态全部来自 tree.export()：官方导出器完整遍历
    Block 与 SubSpell，深嵌套揭示不再丢失；当前效果等事件型特征由 hslog 包树
    增量补事件（出牌/回合切换）。
    """

    def __init__(
        self,
        local_accounts: Optional[Set[Tuple[int, int]]] = None,
        player_id: Optional[int] = None,
    ):
        self.local_accounts = set(local_accounts or [])
        self.forced_player_id = player_id
        self.reset()

    def reset(self) -> None:
        self._hslog = LogParser()
        self._last_packet_id = 0
        self.line_errors = 0
        self.pending_effects: Dict[str, int] = {}
        self._last_turn_seen: Optional[int] = None
        self._sp_cost_expire_turn: Optional[int] = None
        self._cards_played_this_turn = 0

    def feed_line(self, line: str) -> None:
        try:
            self._hslog.read_line(line)
        except Exception:
            # 单行解析失败（未知枚举/新 opcode/脏行）不中断整体解析
            self.line_errors += 1

    # ---- 事件：当前效果 / 出牌计数 / spcost 到期 ----

    def _process_events(self, tree, game) -> None:
        for packet in _deep_packets(tree):
            packet_id = getattr(packet, "packet_id", 0)

            if packet_id <= self._last_packet_id:
                continue

            self._last_packet_id = packet_id

            if isinstance(packet, Block) and packet.type == BlockType.PLAY:
                try:
                    entity_id = coerce_to_entity_id(packet.entity)
                except MissingPlayerData:
                    continue

                self._on_play_entity(entity_id, game)
            elif isinstance(packet, TagChange) and packet.tag == GameTag.TURN:
                self._on_turn_change(packet.value)

    def _on_play_entity(self, entity_id: int, game) -> None:
        ent = game.find_entity_by_id(entity_id)

        if ent is None or not ent.card_id:
            return

        controller = ent.tags.get(GameTag.CONTROLLER)

        if controller not in (None, self.local_controller):
            # 敌方随从：异教低阶牧师 / 音箱践踏者 → 我方下个回合法术 +1/+2 费
            effect = SP_COST_CARD_IDS.get(ent.card_id)

            if effect:
                self.pending_effects[effect] = self.pending_effects.get(effect, 0) + 1
                expire = (self._last_turn_seen or 0) + 1
                self._sp_cost_expire_turn = max(self._sp_cost_expire_turn or 0, expire)

            return

        self._cards_played_this_turn += 1
        name = card_name(ent.card_id)
        self._consume_effects(name, ent)

        if name in EFFECT_CARD_NAMES:
            self.pending_effects[name] = self.pending_effects.get(name, 0) + 1

    def _on_turn_change(self, value) -> None:
        if value == self._last_turn_seen:
            return

        self._last_turn_seen = value
        self._cards_played_this_turn = 0

        # 本回合类效果（伺机/刀油/骨刺/狐人）到期移除；幸运彗星跨回合保留
        for name in ("伺机待发", "斯卡布斯·刀油", "锯齿骨刺", "狐人老千"):
            self.pending_effects.pop(name, None)

        # spcost：敌方战吼的“下个回合法术+1/+2”只在我方下一回合生效
        if self._sp_cost_expire_turn is not None and value > self._sp_cost_expire_turn:
            self.pending_effects.pop("[spcost+1]", None)
            self.pending_effects.pop("[spcost+2]", None)
            self._sp_cost_expire_turn = None

    def _consume_effects(self, name: str, ent) -> None:
        card_type = ent.type
        is_combo = ent.tags.get(GameTag.COMBO) == 1 or name in COMBO_CARD_NAMES

        # 伺机待发：下一个法术一次性消耗（叠 N 层只作用于第一个法术）
        if (
            name != "伺机待发"
            and card_type == CardType.SPELL
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
            and card_type == CardType.MINION
            and is_combo
            and self._cards_played_this_turn > 1
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

    # ---- 快照：状态全部来自 hslog 官方导出 ----

    def snapshot(self) -> dict:
        games = self._hslog.games

        if not games:
            return {
                "in_game": False,
                "reason": "对局尚未开始",
                "hand": [],
                "board": [],
                "deck": [],
                "secrets": [],
                "weapon": None,
                "current_effects": [],
                "deadly_shadow_hand_indexes": [],
                "crystals": None,
                "mana": None,
                "game_state": None,
                "game_over": False,
            }

        tree = games[-1]
        exporter = _TolerantExporter(tree, player_manager=self._hslog.player_manager)
        exporter.export()
        game = exporter.game

        local_controller = self.forced_player_id
        local_player: Optional[object] = None
        opponent_player: Optional[object] = None

        for player in game.players:
            if (
                local_controller is None
                and (player.account_hi, player.account_lo) in self.local_accounts
            ):
                local_controller = player.player_id

            if player.player_id == local_controller:
                local_player = player
            else:
                opponent_player = player

        self.local_controller = local_controller

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
                "game_state": None,
                "game_over": False,
            }

        self._process_events(tree, game)

        hand: List[dict] = []
        hand_entities: List[object] = []
        board: List[dict] = []
        enemy_board: List[dict] = []
        deck: List[dict] = []
        secrets: List[dict] = []
        weapon: Optional[dict] = None
        raw_band: List[str] = []
        picked_band: List[str] = []

        # 手牌/场面/牌库/奥秘：全部走 hslog 官方 player.in_zone() 接口（按控制器
        # 返回该玩家某区域的实体，不按卡牌类型过滤——面具变装大师给的英雄卡等
        # 任何类型都会正确进入手牌）。
        if local_player is not None:
            for ent in local_player.in_zone(Zone.HAND):
                hand.append(self._entity_item(ent))
                hand_entities.append(ent)

            for ent in local_player.in_zone(Zone.PLAY):
                if ent.type == CardType.MINION:
                    board.append(self._entity_item(ent))
                elif ent.type == CardType.WEAPON:
                    weapon = self._entity_item(ent)

            for ent in local_player.in_zone(Zone.DECK):
                deck.append(self._entity_item(ent))

            for ent in local_player.in_zone(Zone.SECRET):
                secrets.append(self._entity_item(ent))

        if opponent_player is not None:
            for ent in opponent_player.in_zone(Zone.PLAY):
                if ent.type == CardType.MINION:
                    enemy_board.append(self._entity_item(ent))

        # 牛池：真实乐队牌（无 CREATOR）始终留在 SETASIDE；被选走的牌以
        # 发现复制体（有 CREATOR）离开 SETASIDE 为准，剩余池 = 真实牌 - 已选。
        for ent in game.entities:
            band_name = ETC_BAND_CARD_IDS.get(getattr(ent, "card_id", None) or "")

            if not band_name or ent.tags.get(GameTag.CONTROLLER) != local_controller:
                continue

            if ent.zone == Zone.SETASIDE:
                if not ent.tags.get(GameTag.CREATOR) and band_name not in raw_band:
                    raw_band.append(band_name)
            elif ent.tags.get(GameTag.CREATOR) and band_name not in picked_band:
                picked_band.append(band_name)

        hand_pairs = sorted(
            zip(hand, hand_entities),
            key=lambda pair: (pair[0]["zone_position"] is None, pair[0]["zone_position"] or 0),
        )
        hand = [pair[0] for pair in hand_pairs]
        hand_entities = [pair[1] for pair in hand_pairs]
        board.sort(key=lambda item: (item["zone_position"] is None, item["zone_position"] or 0))
        enemy_board.sort(key=lambda item: (item["zone_position"] is None, item["zone_position"] or 0))

        etc_band = [n for n in raw_band if n not in picked_band][:3] if raw_band else None

        deck_unknown_cards = (
            sum(1 for ent in local_player.in_zone(Zone.DECK) if not ent.card_id)
            if local_player is not None
            else 0
        )

        current_effects = [
            {"name": name, "count": count}
            for name, count in sorted(self.pending_effects.items())
            if count > 0
        ]

        deadly_shadow_hand_indexes = [
            index
            for index, (item, ent) in enumerate(zip(hand, hand_entities), start=1)
            if item["ghostly"]
            or (ent.card_id or "") in DEADLY_SHADOW_CARD_IDS
            or (ent.initial_card_id or "") in DEADLY_SHADOW_CARD_IDS
        ]

        crystals: Optional[int] = None
        mana: Optional[int] = None
        cards_played_this_turn = 0

        if local_player is not None:
            tags = local_player.tags
            res = tags.get(GameTag.RESOURCES)

            if res is not None:
                crystals = int(res)
                used = int(tags.get(GameTag.RESOURCES_USED, 0) or 0)
                temp = int(tags.get(GameTag.TEMP_RESOURCES, 0) or 0)
                mana = max(0, crystals - used + temp)

            cards_played_this_turn = int(
                tags.get(GameTag.NUM_CARDS_PLAYED_THIS_TURN) or 0
            )

        opponent_id = 1 if local_controller == 2 else 2

        state = game.tags.get(GameTag.STATE)
        game_state = getattr(state, "name", None) if state is not None else None
        game_over = any(
            p.tags.get(GameTag.PLAYSTATE) in (PlayState.WON, PlayState.LOST, PlayState.TIED)
            for p in game.players
        )

        def _player_name(player_id: int) -> Optional[str]:
            for player in game.players:
                if player.player_id == player_id:
                    return player.name
            return None

        return {
            "in_game": game_state in ("RUNNING", "LOADING", "STARTING")
            or (
                game_state is None
                and not game_over
                and bool(hand or board or deck)
            ),
            "reason": "对局已结束" if game_over else "对局进行中",
            "player_controller": local_controller,
            "player_name": _player_name(local_controller),
            "opponent_name": _player_name(opponent_id),
            "game_state": game_state,
            "game_over": game_over,
            "crystals": crystals,
            "mana": mana,
            "cards_played_this_turn": cards_played_this_turn,
            "hand": hand,
            "board": board,
            "enemy_board": enemy_board,
            "deck": deck,
            "deck_unknown_cards": deck_unknown_cards,
            "secrets": secrets,
            "weapon": weapon,
            "etc_band": etc_band,
            "current_effects": current_effects,
            "deadly_shadow_hand_indexes": deadly_shadow_hand_indexes,
            "parser": "hslog",
            "line_errors": self.line_errors,
        }

    def _entity_item(self, ent) -> dict:
        card_id = ent.card_id or ""
        card_type = ent.type
        health = ent.tags.get(GameTag.HEALTH)
        health_max = health

        if card_type == CardType.MINION:
            # 当前血量 = 基础血量 - 已受伤害（DAMAGE），随标签变化实时更新
            damage = ent.tags.get(GameTag.DAMAGE) or 0

            if isinstance(health, int) and damage:
                health = max(0, health - damage)

        return {
            "card_id": card_id,
            "name": card_name(card_id),
            "cost": ent.tags.get(GameTag.COST),
            "health": health if card_type == CardType.MINION else None,
            "health_max": health_max if card_type == CardType.MINION else None,
            "attack": ent.tags.get(GameTag.ATK),
            "zone_position": ent.tags.get(GameTag.ZONE_POSITION),
            "ghostly": bool(ent.tags.get(GameTag.GHOSTLY)),
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
