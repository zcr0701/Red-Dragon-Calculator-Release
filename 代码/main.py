"""红龙贼计算器主入口（Python GUI + hslog 日志读取 + C++ 计算核心）。

职责划分：
  - Python：PyQt5 图形界面 + 基于 hslog 读取本机 Power.log 对局快照 + 手动输入
  - C++：   red_dragon_engine.exe 纯计算（纯束宽搜索 + 瓶颈模型启发）

运行：
  python 代码/main.py

自测：
  python 代码/main.py --smoke     # 打开窗口 2.5 秒后自动关闭
  python 代码/main.py --demo      # 启动即载入示例局面（无游戏也能试计算）
  python 代码/main.py --selftest  # 打开窗口并跑一次示例计算后退出
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QEventLoop, QPoint, QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizeGrip,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import engine
from powerlog_reader import LogWatcher


BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"

# 示例局面：手牌 + 战场 + 水晶（用于无游戏时的界面/计算自测）
DEMO_SNAPSHOT = {
    "in_game": True,
    "reason": "示例局面",
    "player_name": "示例玩家",
    "opponent_name": "示例对手",
    "game_state": "RUNNING",
    "game_over": False,
    "crystals": 8,
    "mana": 8,
    "hand": [
        {"name": "生命的缚誓者阿莱克丝塔萨", "cost": 9},
        {"name": "鲨鱼之灵", "cost": 1},
        {"name": "斯卡布斯·刀油", "cost": 1},
        {"name": "暗影施法者", "cost": 1},
        {"name": "斯卡布斯·刀油", "cost": 1},
        {"name": "晦鳞巢母", "cost": 1},
        {"name": "斯卡布斯·刀油", "cost": 1},
        {"name": "暗影步", "cost": 0},
        {"name": "舞动全场（ft.迦罗娜）", "cost": 3},
        {"name": "殒命暗影", "cost": None},
    ],
    "board": [
        {"name": "晦鳞巢母", "cost": 3, "health": 3},
    ],
    "deck": [],
    "secrets": [],
    "weapon": None,
    "current_effects": [],
    "deadly_shadow_hand_indexes": [10],
    "log_path": None,
}

# ===================== 小窗：卡牌缩写与轮次分割 =====================

CARD_ABBREVIATIONS = {
    "幸运币": "币",
    "伪造的幸运币": "币",
    "伺机待发": "伺",
    "暗影步": "步",
    "殒命暗影": "殒",
    "狐人老千": "狐",
    "锯齿骨刺": "骨",
    "晦鳞巢母": "晦",
    "乐队经理精英牛头人酋长": "牛",
    "舞动全场（ft.迦罗娜）": "舞",
    "幻觉药水": "幻",
    "生命的缚誓者阿莱克丝塔萨": "龙",
    "斯卡布斯·刀油": "刀",
    "鲨鱼之灵": "鱼",
    "暗影施法者": "暗",
    "赤烟·腾武": "腾",
    "“赤烟”腾武": "腾",
    "幸运彗星": "彗",
    "战略转移": "转",
}

ROUND_SPLIT_NAMES = ("战略转移", "舞动全场")  # 与 split_path_rounds 提取的基础名匹配

_CN_DIGITS = "零一二三四五六七八九"


def chinese_round_number(n: int) -> str:
    """轮次序号转中文数字（1->一, 11->十一, 21->二十一...）。"""
    if n <= 0:
        return str(n)

    if n < 10:
        return _CN_DIGITS[n]

    if n < 20:
        return "十" + (_CN_DIGITS[n % 10] if n % 10 else "")

    tens, ones = divmod(n, 10)
    return _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")


def abbreviate_card_name(name: str) -> str:
    """单卡缩写；未定义缩写的卡保留全名。"""
    name = (name or "").strip()

    if not name:
        return name

    if name in CARD_ABBREVIATIONS:
        return CARD_ABBREVIATIONS[name]

    resolved = engine.resolve_card_name(name)
    return CARD_ABBREVIATIONS.get(resolved, name)


def abbreviate_step(step: str) -> str:
    """把引擎路径一步（如 赤烟·腾武（斯卡布斯·刀油））转成缩写格式。"""
    step = step.strip()
    deadly = ""

    if "[殒命暗影]" in step:
        deadly = "[殒]"
        step = step.replace("[殒命暗影]", "")

    # 卡名本身可能带括号（如 舞动全场（ft.迦罗娜））：先做最长前缀匹配，
    # 剩余部分才是目标括号，避免把卡名内括号误当成目标。
    name = ""
    rest = ""

    for cand in sorted(CARD_ABBREVIATIONS, key=len, reverse=True):
        if step.startswith(cand):
            name = cand
            rest = step[len(cand):]
            break

    if not name:
        if "（" in step:
            name, rest = step.split("（", 1)
            rest = "（" + rest
        else:
            name, rest = step, ""

    target = ""

    if rest.startswith("（") and rest.endswith("）"):
        inner = rest[1:-1]
        parts = []

        for p in inner.split("->"):
            p = p.strip()

            if not p:
                continue

            # 腾武回手目标带 board 顺序标注，如 斯卡布斯·刀油(3nd)
            m = re.match(r"^(.*?)\((\d+)nd\)$", p)

            if m:
                parts.append(f"{abbreviate_card_name(m.group(1).strip())}({m.group(2)}nd)")
            else:
                parts.append(abbreviate_card_name(p))

        target = "(" + ";".join(parts) + ")"

    return abbreviate_card_name(name) + target + deadly


def split_path_rounds(path: List[str]) -> List[List[str]]:
    """按 战略转移/舞动全场 把路径分割为多轮；分割动作归属当前轮末尾。"""
    rounds: List[List[str]] = []
    current: List[str] = []

    for step in path:
        base = step.split("（", 1)[0].replace("[殒命暗影]", "").strip()
        current.append(step)

        if base in ROUND_SPLIT_NAMES:
            rounds.append(current)
            current = []

    if current:
        rounds.append(current)

    return rounds


def format_mini_results(data: Dict[str, object]) -> str:
    """小窗结果：最高伤害路径按轮次分割，只显示缩写。"""
    results = data.get("results") or []
    lines = [
        f"最大伤害：{data.get('max_damage', 0)}，最大龙数：{data.get('max_dragons', 0)}"
    ]

    if not results:
        lines.append("（无路径）")
        return "\n".join(lines)

    path = (results[0].get("path") or []) if results else []
    rounds = split_path_rounds(path)

    for index, rnd in enumerate(rounds, start=1):
        abbr = "-".join(abbreviate_step(step) for step in rnd)
        lines.append(f"[第{chinese_round_number(index)}轮]：")
        lines.append(abbr if abbr else "（空）")
        lines.append("")

    return "\n".join(lines)


class CalculationWorker(QThread):
    """后台线程调用 C++ 核心，进度/结果/错误通过信号回主线程。"""

    progress = pyqtSignal(int, int, int)
    found = pyqtSignal(int, int)
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(
        self,
        snapshot: Dict[str, object],
        options: Dict[str, object],
        parent=None,
    ):
        super().__init__(parent)
        self.snapshot = snapshot
        self.options = options
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            beam = int(self.options.get("beam_width") or 0)
            wide_widths = None
            heuristics = None
            if beam > 0:
                # 用户自设束宽：多启发互补（H6/H1/H2/H2），每通道宽度取
                # max(用户值, 默认四通道该通道宽度)——避免单启发盲区或小束宽
                # 在紧场面漏掉最优（如 6 水晶 48 伤线只搜出 32）；用户调大则更宽。
                default_widths = [1100, 1500, 1100, 3000]
                wide_widths = [max(beam, w) for w in default_widths]
                heuristics = [6, 1, 2, 2]
            result = engine.compute(
                self.snapshot,
                min_alex=int(self.options["min_alex"]),
                max_alex=int(self.options["max_alex"]),
                depth=int(self.options["depth"]),
                max_paths=int(self.options["max_paths"]),
                threads=int(self.options.get("threads", 4)),
                time_budget_sec=float(self.options.get("time_budget_sec", 3.0)),
                wide_widths=wide_widths,
                heuristics=heuristics,
                etc_band=list(self.options.get("etc_band") or []),
                progress_callback=self.progress.emit,
                found_callback=self.found.emit,
                should_stop=lambda: self._stop,
            )
            self.finished_ok.emit(result)
        except InterruptedError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - 统一回传 GUI 显示
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# ---------- 手动输入解析 ----------

_SECTION_RE = re.compile(r"^\s*(当前效果|牌库中|手牌中|战场|随从|其他)\s*[（(]?\s*\d*\s*[）)]?\s*$")
_COST_ONLY_RE = re.compile(r"^\s*(\d+)\s*费?\s*$")
_STAR_COST_RE = re.compile(r"^[*★☆＊]+\s*(.+)$")
_COMMA_ZONE_RE = re.compile(r"^\s*(\d+)\s*[,，、]\s*(\d+)\s*血?\s*(.+)$")


def parse_manual_zone_lines(text: str) -> Tuple[List[Tuple[Optional[int], str, Optional[int]]], List[str]]:
    """手牌/随从栏一行行解析成 [(费用, 卡名, 血量)]。

    支持格式：
    - “4 鲨鱼之灵”（费用 卡名）；
    - “4 鲨鱼之灵 3”（费用 卡名 血量，随从栏）；
    - “4,3 鲨鱼之灵”（费用,血量 卡名，兼容旧写法）；
    - “* 殒命暗影”（* 表示无费用特殊卡，自动标记殒命）；
    - 纯卡名（自动取基础费用，支持简称，如 刀油 -> 斯卡布斯·刀油）；
    - 单独一行费用作为下一行卡名的费用（OCR 式两行一组）。
    """
    entries: List[Tuple[Optional[int], str, Optional[int]]] = []
    pending_cost: Optional[int] = None
    warnings: List[str] = []

    for raw in text.splitlines():
        line = raw.strip()

        if not line or _SECTION_RE.match(line):
            continue

        cost_only = _COST_ONLY_RE.match(line)

        if cost_only:
            pending_cost = int(cost_only.group(1))
            continue

        if re.match(r"^[*★☆＊]+\s*$", line):
            continue

        star_cost = _STAR_COST_RE.match(line)

        if star_cost:
            entries.append((None, engine.resolve_card_name(star_cost.group(1)), None))
            pending_cost = None
            continue

        comma_match = _COMMA_ZONE_RE.match(line)

        if comma_match:
            entries.append(
                (
                    int(comma_match.group(1)),
                    engine.resolve_card_name(comma_match.group(3)),
                    int(comma_match.group(2)),
                )
            )
            pending_cost = None
            continue

        tokens = line.split()

        if tokens and tokens[0].isdigit() and len(tokens) >= 2:
            cost = int(tokens[0])
            health = None
            name_parts = tokens[1:]

            if len(tokens) >= 3 and tokens[-1].isdigit():
                health = int(tokens[-1])
                name_parts = tokens[1:-1]

            entries.append((cost, engine.resolve_card_name(" ".join(name_parts)), health))
            pending_cost = None
            continue

        name = engine.resolve_card_name(line)
        base = engine.KNOWN_BASE_COSTS.get(name)

        if pending_cost is not None:
            entries.append((pending_cost, name, None))
            pending_cost = None
        elif base is not None:
            entries.append((int(base), name, None))
        else:
            warnings.append(f"未知卡名（按杂牌处理）：{line}")
            entries.append((None, name, None))

    return entries, warnings


def parse_manual_effect_lines(text: str) -> Tuple[List[Tuple[str, int]], List[str]]:
    """当前效果栏：每行“中文名 数量”，如“狐人老千 2”（数量 = 叠加层数）。"""
    entries: List[Tuple[str, int]] = []
    warnings: List[str] = []

    for raw in text.splitlines():
        line = raw.strip()

        if not line or _SECTION_RE.match(line):
            continue

        tokens = line.split()
        layers = 1

        if len(tokens) >= 2 and tokens[-1].isdigit():
            layers = max(1, int(tokens[-1]))
            line = " ".join(tokens[:-1])

        name = engine.resolve_card_name(line)

        if name not in engine.EFFECT_NAMES:
            warnings.append(f"未知当前效果（忽略）：{line}")
            continue

        entries.append((name, layers))

    return entries, warnings


def parse_manual_enemy_lines(text: str) -> Tuple[List[Tuple[str, int]], List[str]]:
    """敌方随从栏：每行“血量”或“血量 名字”；只需血量即可参与计算。"""
    entries: List[Tuple[str, int]] = []
    warnings: List[str] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or _SECTION_RE.match(line):
            continue
        tokens = line.split()
        if not tokens:
            continue
        if not tokens[0].isdigit():
            warnings.append(f"忽略无法识别的敌方随从行：{line}")
            continue
        health = int(tokens[0])
        if len(tokens) >= 2:
            name = engine.resolve_card_name(" ".join(tokens[1:]))
        else:
            name = "敌方随从"
        entries.append((name, health))

    return entries, warnings


class MainWindow(QWidget):
    def __init__(self, demo: bool = False):
        super().__init__()
        self.setWindowTitle("红龙贼计算器（C++ 核心 + hslog 日志读取）")
        self.resize(1120, 780)

        self.watcher = LogWatcher()
        self.snapshot: Dict[str, object] = {}
        self.worker: Optional[CalculationWorker] = None
        self.mini_window: Optional[MiniWindow] = None
        self._last_state_key = ""
        self._manual_mode = False
        self.etc_checks: List[Tuple[str, QCheckBox]] = []

        self._build_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_log)
        self.timer.start(1000)

        if demo:
            self._apply_snapshot(DEMO_SNAPSHOT)
        else:
            self.refresh_log()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # 数据源状态条
        status = QHBoxLayout()
        self.status_label = QLabel("数据源：正在查找 Power.log …")
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh_log)
        self.demo_button = QPushButton("载入示例局面")
        self.demo_button.clicked.connect(lambda: self._apply_snapshot(DEMO_SNAPSHOT))
        self.mini_button = QPushButton("小窗")
        self.mini_button.setToolTip("弹出始终置顶的小窗（状态/手牌/场面/牛池/殒命/分轮计算）")
        self.mini_button.clicked.connect(self.toggle_mini_window)
        self.manual_button = QPushButton("▸ 手动输入")
        self.manual_button.setCheckable(True)
        self.manual_button.setChecked(True)
        status.addWidget(self.status_label, 1)
        status.addWidget(self.manual_button)
        status.addWidget(self.refresh_button)
        status.addWidget(self.demo_button)
        status.addWidget(self.mini_button)
        root.addLayout(status)

        main_splitter = QSplitter(Qt.Vertical)
        root.addWidget(main_splitter, 1)

        top = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(top)

        # 左：对局状态
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        state_box = QGroupBox("对局状态")
        state_grid = QGridLayout(state_box)
        self.game_label = QLabel("未在对局中")
        self.mana_label = QLabel("水晶：- / 法力：-")
        self.deck_label = QLabel("牌库：-")
        self.weapon_label = QLabel("武器：无")
        self.secrets_label = QLabel("奥秘：无")
        self.effects_label = QLabel("当前效果：无")
        self.etc_summary_label = QLabel("牛池：-")
        state_grid.addWidget(self.game_label, 0, 0)
        state_grid.addWidget(self.mana_label, 0, 1)
        state_grid.addWidget(self.deck_label, 1, 0)
        state_grid.addWidget(self.weapon_label, 1, 1)
        state_grid.addWidget(self.secrets_label, 2, 0)
        state_grid.addWidget(self.effects_label, 2, 1)
        state_grid.addWidget(self.etc_summary_label, 3, 0)

        # 殒命暗影位置（手牌序号，可手动标记；日志 ghostly 自动标记会自动合并）
        deadly_row = QHBoxLayout()
        self.deadly_check = QCheckBox("殒命暗影位置：")
        self.deadly_input = QLineEdit()
        self.deadly_input.setPlaceholderText("手牌序号，如 3,7（不填则只保留日志/手动自动识别）")
        self.deadly_input.setEnabled(False)
        self.deadly_check.toggled.connect(self.deadly_input.setEnabled)
        self.deadly_check.toggled.connect(self._sync_mini_window)
        self.deadly_input.textChanged.connect(self._sync_mini_window)
        deadly_row.addWidget(self.deadly_check)
        deadly_row.addWidget(self.deadly_input, 1)
        state_grid.addLayout(deadly_row, 4, 0, 1, 2)
        left_layout.addWidget(state_box)

        hand_box = QGroupBox("手牌")
        hand_layout = QVBoxLayout(hand_box)
        self.hand_text = QPlainTextEdit()
        self.hand_text.setReadOnly(True)
        self.hand_text.setMaximumBlockCount(2000)
        hand_layout.addWidget(self.hand_text)
        left_layout.addWidget(hand_box, 2)

        board_box = QGroupBox("战场随从")
        board_layout = QVBoxLayout(board_box)
        self.board_text = QPlainTextEdit()
        self.board_text.setReadOnly(True)
        board_layout.addWidget(self.board_text)
        left_layout.addWidget(board_box, 1)

        top.addWidget(left)

        # 右：计算参数 + 结果
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        param_box = QGroupBox("计算参数")
        param_grid = QGridLayout(param_box)

        mode_label = QLabel("搜索方式：纯束宽搜索（默认四通道并行：H6/1100、H1/1100、H2/1100、H2/3000，"
                            "3 秒硬时限内出结果；束宽填 0 = 自动四通道）")
        mode_label.setWordWrap(True)
        mode_label.setStyleSheet("font-size:12px;color:#555;")

        # 牛头人酋长剩余卡池：最多勾选 3 张，默认 舞/药/龙
        self.etc_checks = []
        default_checked = {"舞动全场（ft.迦罗娜）", "幻觉药水", "生命的缚誓者阿莱克丝塔萨"}
        band_row = QHBoxLayout()

        for card_name, label in engine.ETC_OPTIONS:
            box = QCheckBox(label)
            box.setChecked(card_name in default_checked)
            box.toggled.connect(self._make_etc_toggler(box))
            box.toggled.connect(self._update_etc_summary)
            self.etc_checks.append((card_name, box))
            band_row.addWidget(box)

        band_row.addStretch(1)
        band_hint = QLabel("取消勾选 = 这张已被选走")
        band_hint.setStyleSheet("font-size:11px;color:#888;")
        band_row.addWidget(band_hint)

        self.min_alex = self._spin(1, 1, 10)
        self.max_alex = self._spin(10, 1, 10)
        self.time_budget = self._spin(3, 1, 30, step=1)
        self.beam_depth = self._spin(30, 1, 100)
        self.max_paths = self._spin(1000000, 1000, 100000000, step=1000)

        param_grid.addWidget(mode_label, 0, 0, 1, 2)
        param_grid.addWidget(QLabel("牛头人卡池："), 1, 0)
        param_grid.addLayout(band_row, 1, 1)
        param_grid.addWidget(QLabel("最少龙数："), 2, 0)
        param_grid.addWidget(self.min_alex, 2, 1)
        param_grid.addWidget(QLabel("最多龙数："), 3, 0)
        param_grid.addWidget(self.max_alex, 3, 1)
        param_grid.addWidget(QLabel("时间预算(秒)："), 4, 0)
        param_grid.addWidget(self.time_budget, 4, 1)
        param_grid.addWidget(QLabel("最大深度："), 5, 0)
        param_grid.addWidget(self.beam_depth, 5, 1)
        param_grid.addWidget(QLabel("最大路径数："), 6, 0)
        param_grid.addWidget(self.max_paths, 6, 1)
        self.beam_width = self._spin(0, 0, 9999, step=100)
        param_grid.addWidget(QLabel("束宽（0=自动四通道）："), 7, 0)
        param_grid.addWidget(self.beam_width, 7, 1)
        self.no_time_limit = QCheckBox("不限时：按束宽×最大深度跑完（时间预算失效，大束宽可能很慢）")
        self.no_time_limit.setToolTip("勾选后搜索不因时间耗尽而停止，跑满最大深度或状态收敛为止")
        param_grid.addWidget(self.no_time_limit, 8, 0, 1, 2)
        right_layout.addWidget(param_box)

        run_row = QHBoxLayout()
        self.calc_button = QPushButton("开始计算")
        self.calc_button.clicked.connect(self.on_calc_toggle)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        self.engine_label = QLabel("")
        run_row.addWidget(self.calc_button)
        run_row.addWidget(self.progress_bar, 1)
        run_row.addWidget(self.engine_label)
        right_layout.addLayout(run_row)

        result_box = QGroupBox("计算结果")
        result_layout = QVBoxLayout(result_box)
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumBlockCount(5000)
        result_layout.addWidget(self.result_text)
        right_layout.addWidget(result_box, 1)

        top.addWidget(right)
        top.setSizes([520, 600])

        # 底部：手动输入面板（默认展开）
        self.manual_panel = QWidget()
        manual_layout = QVBoxLayout(self.manual_panel)
        manual_layout.setContentsMargins(0, 4, 0, 0)
        help_label = QLabel(
            "手动输入格式（每行一张牌）：手牌栏“费用 卡名”（4 鲨鱼之灵）；随从栏“费用 卡名 血量”；"
            "殒命暗影写“* 殒命暗影”；当前效果“狐人老千 2”（数量=叠加层数）；卡名支持简称（刀油/狐/鱼/龙/舞/药/转）。"
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("font-size:12px;color:#555;")
        manual_layout.addWidget(help_label)

        manual_row = QHBoxLayout()

        def make_manual_edit(placeholder: str) -> QPlainTextEdit:
            edit = QPlainTextEdit()
            edit.setPlaceholderText(placeholder)
            edit.setMaximumBlockCount(5000)
            return edit

        hand_panel = QVBoxLayout()
        hand_panel.addWidget(QLabel("手牌栏"))
        self.manual_hand_edit = make_manual_edit("例：\n4 鲨鱼之灵\n2 狐人老千\n4 刀油\n* 殒命暗影")
        hand_panel.addWidget(self.manual_hand_edit)
        manual_row.addLayout(hand_panel, 1)

        board_panel = QVBoxLayout()
        board_panel.addWidget(QLabel("随从栏"))
        self.manual_board_edit = make_manual_edit("例：\n4 鲨鱼之灵 3\n4 刀油 3\n5 暗影施法者 2")
        board_panel.addWidget(self.manual_board_edit)
        manual_row.addLayout(board_panel, 1)

        enemy_panel = QVBoxLayout()
        enemy_panel.addWidget(QLabel("敌方随从（只需血量）"))
        self.manual_enemy_edit = make_manual_edit("例：\n3\n5\n4 敌方随从")
        enemy_panel.addWidget(self.manual_enemy_edit)
        manual_row.addLayout(enemy_panel, 1)

        effect_panel = QVBoxLayout()
        effect_panel.addWidget(QLabel("当前效果（可选）"))
        self.manual_effect_edit = make_manual_edit("例：\n狐人老千 2\n伺机待发 1")
        effect_panel.addWidget(self.manual_effect_edit)
        manual_row.addLayout(effect_panel, 1)

        manual_row.addStretch(0)
        manual_layout.addLayout(manual_row)

        manual_buttons = QHBoxLayout()
        manual_buttons.addWidget(QLabel("水晶："))
        self.manual_crystals = QSpinBox()
        self.manual_crystals.setRange(0, 10)
        self.manual_crystals.setValue(8)
        manual_buttons.addWidget(self.manual_crystals)
        manual_buttons.addWidget(QLabel("法力："))
        self.manual_mana = QSpinBox()
        self.manual_mana.setRange(0, 10)
        self.manual_mana.setValue(8)
        manual_buttons.addWidget(self.manual_mana)
        manual_buttons.addSpacing(16)

        self.manual_example_button = QPushButton("填入示例")
        self.manual_example_button.clicked.connect(self.fill_manual_example)
        self.manual_apply_button = QPushButton("解析并应用")
        self.manual_apply_button.clicked.connect(self.apply_manual_input)
        self.manual_clear_button = QPushButton("清空")
        self.manual_clear_button.clicked.connect(self.clear_manual_input)
        self.resume_log_button = QPushButton("恢复日志跟随")
        self.resume_log_button.clicked.connect(self.resume_log)
        manual_buttons.addWidget(self.manual_example_button)
        manual_buttons.addWidget(self.manual_apply_button)
        manual_buttons.addWidget(self.manual_clear_button)
        manual_buttons.addWidget(self.resume_log_button)
        manual_buttons.addStretch(1)
        manual_layout.addLayout(manual_buttons)

        main_splitter.addWidget(self.manual_panel)
        main_splitter.setSizes([560, 220])
        self.manual_button.toggled.connect(self.manual_panel.setVisible)

        self._update_etc_summary()
        self._update_calc_enabled()

    def _spin(
        self,
        value: int,
        minimum: int,
        maximum: int,
        step: int = 1,
    ) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setSingleStep(step)
        return spin

    def _make_etc_toggler(self, box: QCheckBox):
        def handler(checked: bool) -> None:
            if checked and sum(1 for _name, other in self.etc_checks if other.isChecked()) > 3:
                box.blockSignals(True)
                box.setChecked(False)
                box.blockSignals(False)

        return handler

    def _update_etc_summary(self) -> None:
        selected = [name for name, box in self.etc_checks if box.isChecked()]
        self.etc_summary_label.setText("牛池：" + ("、".join(selected) if selected else "空"))
        self._sync_mini_window()

    # ---------- 日志读取 ----------

    def refresh_log(self) -> None:
        if self._manual_mode:
            return

        try:
            snap = self.watcher.snapshot()
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(f"日志读取失败：{exc}")
            return

        key = (
            snap.get("log_path"),
            snap.get("in_game"),
            json.dumps(snap.get("hand") or [], ensure_ascii=False),
            json.dumps(snap.get("board") or [], ensure_ascii=False),
            snap.get("crystals"),
            snap.get("mana"),
        )

        if key != self._last_state_key:
            self._last_state_key = key
            self._apply_snapshot(snap)

    def resume_log(self) -> None:
        self._manual_mode = False
        self._last_state_key = ""
        self.refresh_log()

    def _apply_snapshot(self, snap: Dict[str, object]) -> None:
        self.snapshot = snap
        log_path = snap.get("log_path")
        source = "手动输入" if self._manual_mode else "Power.log"
        self.status_label.setText(f"数据源：{source}（{log_path or '未找到对局'}）")

        in_game = bool(snap.get("in_game"))
        player = snap.get("player_name") or "?"
        opponent = snap.get("opponent_name") or "?"
        reason = snap.get("reason") or ""
        self.game_label.setText(f"{player} vs {opponent}（{reason}）")

        crystals = snap.get("crystals")
        mana = snap.get("mana")
        self.mana_label.setText(
            f"水晶：{crystals if crystals is not None else '-'} / 法力：{mana if mana is not None else '-'}"
        )

        deck = snap.get("deck") or []
        self.deck_label.setText(f"牌库：{len(deck)} 张")

        weapon = snap.get("weapon")
        self.weapon_label.setText(f"武器：{weapon['name'] if weapon else '无'}")

        secrets = snap.get("secrets") or []
        self.secrets_label.setText(
            "奥秘：" + ("、".join(s["name"] for s in secrets) if secrets else "无")
        )

        effects = snap.get("current_effects") or []

        if effects:
            text = "、".join(
                f"{e['name']}×{e.get('count', 1)}" for e in effects
            )
        else:
            text = "无"

        self.effects_label.setText(f"当前效果：{text}")

        deadly_display = set(int(i) for i in (snap.get("deadly_shadow_hand_indexes") or []))
        deadly_display.update(self._manual_deadly_indexes())
        hand_lines: List[str] = []

        for index, item in enumerate(snap.get("hand") or [], start=1):
            cost = item.get("cost")
            cost_text = f"{cost}费" if cost is not None else "?费"
            ghost = "（殒命）" if item.get("ghostly") or index in deadly_display else ""
            hand_lines.append(f"{index:2d}. [{cost_text}] {item['name']}{ghost}")

        hand_text = "\n".join(hand_lines) or "（空）"

        if self.hand_text.toPlainText() != hand_text:
            self.hand_text.setPlainText(hand_text)

        board_lines: List[str] = []

        def _hp_text(item: Dict[str, object]) -> str:
            health = item.get("health")
            health_max = item.get("health_max")
            if health is None:
                return "生命?"
            if health_max not in (None, health):
                return f"生命{health}/{health_max}"
            return f"生命{health}"

        for index, item in enumerate(snap.get("board") or [], start=1):
            cost = item.get("cost")
            cost_text = f"{cost}费" if cost is not None else "?费"
            board_lines.append(
                f"{index:2d}. [{cost_text}] {item['name']}（{_hp_text(item)}）"
            )

        enemy_lines: List[str] = []

        for index, item in enumerate(snap.get("enemy_board") or [], start=1):
            enemy_lines.append(
                f"{index:2d}. {item.get('name') or '敌方随从'}（{_hp_text(item)}）"
            )

        if enemy_lines:
            board_lines.append("敌方随从：")
            board_lines.extend(enemy_lines)

        board_text = "\n".join(board_lines) or "（空）"

        if self.board_text.toPlainText() != board_text:
            self.board_text.setPlainText(board_text)

        # 日志检测到牛池（SETASIDE 乐队卡）时，实时同步勾选；手动画选择保持用户设置
        etc_band = snap.get("etc_band")
        if isinstance(etc_band, list):
            for name, box in self.etc_checks:
                checked = name in etc_band
                if box.isChecked() != checked:
                    box.blockSignals(True)
                    box.setChecked(checked)
                    box.blockSignals(False)

        if not in_game:
            self.result_text.setPlainText("等待进入对局…")

        self._update_etc_summary()
        self._update_calc_enabled()

    # ---------- 小窗 ----------

    def toggle_mini_window(self) -> None:
        """点击“小窗”按钮：弹出/置前小窗。"""
        if self.mini_window is None:
            self.mini_window = MiniWindow(self)

        self.mini_window.sync_from_main()
        self.mini_window.show()
        self.mini_window.raise_()
        self.mini_window.activateWindow()

    def _sync_mini_window(self, *_args) -> None:
        if self.mini_window is None or not self.mini_window.isVisible():
            return

        try:
            self.mini_window.sync_from_main()
        except Exception as exc:  # noqa: BLE001 - 小窗同步失败不影响主窗口
            print(f"小窗同步失败：{type(exc).__name__}: {exc}")

    def _sync_mini_result(self, data: Dict[str, object]) -> None:
        if self.mini_window is not None:
            self.mini_window.show_result(data)

    # ---------- 殒命暗影 ----------

    def _manual_deadly_indexes(self) -> List[int]:
        """返回输入框里的手牌序号（不校验范围）。"""
        if not self.deadly_check.isChecked():
            return []

        text = self.deadly_input.text().strip()

        if not text:
            return []

        parts = text.replace("，", ",").replace("、", ",").replace(" ", ",").split(",")
        indexes: List[int] = []

        for part in parts:
            part = part.strip()

            if part.isdigit():
                indexes.append(int(part))

        return indexes

    def _merged_deadly_indexes(self) -> List[int]:
        """日志 ghostly + 手动标记 + 输入框序号，合并去重。"""
        merged = [int(i) for i in (self.snapshot.get("deadly_shadow_hand_indexes") or [])]

        for index in self._manual_deadly_indexes():
            if index not in merged:
                merged.append(index)

        hand_count = len(self.snapshot.get("hand") or [])

        for index in merged:
            if index <= 0 or index > hand_count:
                raise ValueError(
                    f"殒命暗影位置第 {index} 张超出当前手牌数量 {hand_count}。"
                )

        return merged

    # ---------- 手动输入 ----------

    def fill_manual_example(self) -> None:
        self.manual_hand_edit.setPlainText(
            "4 鲨鱼之灵\n"
            "2 狐人老千\n"
            "4 刀油\n"
            "* 殒命暗影"
        )
        self.manual_board_edit.setPlainText(
            "4 鲨鱼之灵 3\n"
            "2 狐 2\n"
            "4 刀油 3\n"
            "5 暗影施法者 2"
        )
        self.manual_effect_edit.setPlainText("狐人老千 2")
        self.manual_enemy_edit.setPlainText("")
        self.status_label.setText("已填入示例，可点击“解析并应用”")

    def clear_manual_input(self) -> None:
        self.manual_hand_edit.clear()
        self.manual_board_edit.clear()
        self.manual_effect_edit.clear()
        self.manual_enemy_edit.clear()
        self.status_label.setText("手动输入区已清空")

    def apply_manual_input(self) -> None:
        hand_entries, zone_warnings = parse_manual_zone_lines(
            self.manual_hand_edit.toPlainText()
        )
        board_entries, board_warnings = parse_manual_zone_lines(
            self.manual_board_edit.toPlainText()
        )
        effect_entries, effect_warnings = parse_manual_effect_lines(
            self.manual_effect_edit.toPlainText()
        )
        enemy_entries, enemy_warnings = parse_manual_enemy_lines(
            self.manual_enemy_edit.toPlainText()
        )

        hand = [
            {"name": name, "cost": cost, "ghostly": cost is None and name == "殒命暗影"}
            for cost, name, _health in hand_entries
        ]
        board = [
            {"name": name, "cost": cost, "health": health}
            for cost, name, health in board_entries
        ]
        enemy_board = [
            {"name": name, "health": health}
            for name, health in enemy_entries
        ]

        deadly_auto = [
            index
            for index, item in enumerate(hand, start=1)
            if item["name"] == "殒命暗影"
        ]
        merged_deadly = list(deadly_auto)

        for index in self._manual_deadly_indexes():
            if index not in merged_deadly:
                merged_deadly.append(index)

        if not hand and not board:
            QMessageBox.warning(self, "提示", "请先在手牌栏/随从栏填入内容。")
            return

        snap: Dict[str, object] = {
            "in_game": True,
            "reason": "手动输入",
            "player_name": "手动",
            "opponent_name": "手动",
            "game_state": "RUNNING",
            "game_over": False,
            "crystals": self.manual_crystals.value(),
            "mana": self.manual_mana.value(),
            "hand": hand,
            "board": board,
            "enemy_board": enemy_board,
            "deck": [],
            "secrets": [],
            "weapon": None,
            "current_effects": [
                {"name": name, "count": count} for name, count in effect_entries
            ],
            "deadly_shadow_hand_indexes": merged_deadly,
            "log_path": "手动输入",
        }

        self._manual_mode = True
        self._apply_snapshot(snap)
        self.status_label.setText("手动输入已解析并应用，可点击“开始计算”")

        warnings = zone_warnings + board_warnings + effect_warnings + enemy_warnings

        if warnings:
            QMessageBox.warning(self, "解析警告", "\n".join(warnings[:10]))

    # ---------- 计算 ----------

    def _options(self) -> Dict[str, object]:
        band = [name for name, box in self.etc_checks if box.isChecked()]
        return {
            "min_alex": self.min_alex.value(),
            "max_alex": self.max_alex.value(),
            "depth": self.beam_depth.value(),
            "max_paths": self.max_paths.value(),
            "threads": 4,
            "time_budget_sec": 0 if self.no_time_limit.isChecked() else self.time_budget.value(),
            "etc_band": band,
            "beam_width": self.beam_width.value(),
        }

    def _update_calc_enabled(self) -> None:
        has_state = bool(self.snapshot.get("hand")) or bool(self.snapshot.get("board"))
        self.calc_button.setEnabled(has_state and self.worker is None)

    def on_calc_toggle(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.calc_button.setEnabled(False)
            self.calc_button.setText("正在中止…")
            return

        if not self.snapshot.get("in_game") and not self.snapshot.get("hand"):
            return

        try:
            deadly_indexes = self._merged_deadly_indexes()
        except ValueError as exc:
            QMessageBox.warning(self, "提示", str(exc))
            return

        snapshot = dict(self.snapshot)
        snapshot["deadly_shadow_hand_indexes"] = deadly_indexes
        options = self._options()
        self._last_input = {"snapshot": snapshot, "options": options}
        self.result_text.setPlainText("正在运行纯束宽搜索 …")
        self.progress_bar.setVisible(True)
        self.calc_button.setText("中止计算")
        self._update_calc_enabled()

        self.worker = CalculationWorker(snapshot, options, self)
        self.worker.progress.connect(self._on_progress)
        self.worker.found.connect(self._on_found)
        self.worker.finished_ok.connect(self._on_result)
        self.worker.failed.connect(self._on_error)
        self.worker.finished.connect(self._on_worker_done)
        self.worker.start()

    def _on_progress(self, expansions: int, simulations: int, depth: int) -> None:
        self.engine_label.setText(f"展开 {expansions} / 模拟 {simulations} / 深度 {depth}")

    def _on_found(self, dragons: int, damage: int) -> None:
        self.engine_label.setText(f"已找到 {dragons} 龙 {damage} 伤")

    def _input_log_block(self) -> str:
        """把本次计算的输入局面与参数写成可复现的文本块（含完整 JSON）。"""
        last = getattr(self, "_last_input", None)

        if not last:
            return ""

        snapshot = last.get("snapshot") or {}
        options = last.get("options") or {}
        lines = ["输入局面："]
        lines.append(
            f"  水晶：{snapshot.get('crystals', '?')} / 法力：{snapshot.get('mana', '?')}"
        )

        deadly = set(int(i) for i in (snapshot.get("deadly_shadow_hand_indexes") or []))
        hand_parts = []

        for index, item in enumerate(snapshot.get("hand") or [], start=1):
            cost = item.get("cost")
            cost_text = f"{cost}费" if cost is not None else "?费"
            ghost = "（殒命）" if index in deadly else ""
            hand_parts.append(f"{index}. [{cost_text}] {item['name']}{ghost}")

        lines.append("  手牌：" + ("  ".join(hand_parts) if hand_parts else "（空）"))

        def _hp(item: dict) -> str:
            health = item.get("health")
            health_max = item.get("health_max")
            if health is None:
                return "生命?"
            if health_max not in (None, health):
                return f"生命{health}/{health_max}"
            return f"生命{health}"

        board_parts = []

        for index, item in enumerate(snapshot.get("board") or [], start=1):
            cost = item.get("cost")
            cost_text = f"{cost}费" if cost is not None else "?费"
            board_parts.append(f"{index}. [{cost_text}] {item['name']}（{_hp(item)}）")

        if board_parts:
            lines.append("  战场：" + "  ".join(board_parts))

        enemy_parts = []

        for index, item in enumerate(snapshot.get("enemy_board") or [], start=1):
            enemy_parts.append(f"{index}. {item.get('name') or '敌方随从'}（{_hp(item)}）")

        if enemy_parts:
            lines.append("  敌方战场：" + "  ".join(enemy_parts))

        band = snapshot.get("etc_band")
        lines.append("  牛池：" + ("、".join(band) if band else "（未知/未检测）"))

        effects = snapshot.get("current_effects") or []
        lines.append(
            "  当前效果："
            + ("、".join(f"{e['name']}×{e.get('count', 1)}" for e in effects) if effects else "无")
        )

        deadly_text = "、".join(str(i) for i in sorted(deadly)) if deadly else "无"
        lines.append(f"  殒命暗影手牌序号：{deadly_text}")

        beam = int(options.get("beam_width") or 0)
        beam_text = (
            f"{beam}"
            if beam > 0
            else "0（自动四通道：H6/1100 H1/1500 H2/1100 H2/3000）"
        )
        budget = options.get("time_budget_sec")
        budget_text = "不限时" if budget in (None, 0) else f"{budget}秒"
        lines.append(
            f"  搜索参数：束宽={beam_text} 深度={options.get('depth')} "
            f"时间={budget_text} 龙数={options.get('min_alex')}..{options.get('max_alex')} "
            f"路径上限={options.get('max_paths')}"
        )

        json_snapshot = dict(snapshot)
        for key in ("log_path", "session_dir", "parser"):
            json_snapshot.pop(key, None)

        lines.append("  输入JSON：" + json.dumps(json_snapshot, ensure_ascii=False))
        return "\n".join(lines)

    def _on_result(self, data: Dict[str, object]) -> None:
        results = data.get("results") or []
        lines = [
            "搜索方式：纯束宽搜索",
            f"最大伤害：{data.get('max_damage', 0)}，最大龙数：{data.get('max_dragons', 0)}",
            f"展开节点：{data.get('expansions', 0)}，路径数：{len(results)}",
            "",
        ]

        input_block = self._input_log_block()

        if input_block:
            lines.append(input_block)
            lines.append("")

        stats = data.get("stats")

        if isinstance(stats, dict) and stats:
            lines.append("统计：")
            lines.extend(f"  {key}: {value}" for key, value in stats.items())
            lines.append("")

        for index, item in enumerate(results, start=1):
            path = " → ".join(item.get("path") or [])
            lines.append(
                f"{index:3d}. {item.get('dragons', 0)} 龙 / {item.get('damage', 0)} 伤"
                f" / 余{item.get('mana', '?')}费：{path}"
            )

        text = "\n".join(lines)
        self.result_text.setPlainText(text)

        try:
            LOGS_DIR.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = LOGS_DIR / f"red_dragon_all_paths_{stamp}.txt"
            out_path.write_text(text, encoding="utf-8")
            self.engine_label.setText(f"结果已保存：{out_path.name}")
        except OSError as exc:
            self.engine_label.setText(f"结果保存失败：{exc}")

        self._sync_mini_result(data)

    def _on_error(self, message: str) -> None:
        self.result_text.setPlainText(f"计算失败：\n{message}")

    def _on_worker_done(self) -> None:
        self.progress_bar.setVisible(False)
        self.engine_label.setText("")
        self.worker = None
        self.calc_button.setText("开始计算")

        if self.mini_window is not None:
            self.mini_window.on_main_worker_done()

        self._update_calc_enabled()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(3000)

        if self.mini_window is not None:
            self.mini_window.close()

        event.accept()


SNAP_DISTANCE = 14  # 吸附屏幕边缘的阈值（像素）

# 小窗牛池勾选的短标签（对应卡牌缩写，节省竖条宽度）
MINI_ETC_LABELS = {
    "舞动全场（ft.迦罗娜）": "舞",
    "幻觉药水": "幻",
    "生命的缚誓者阿莱克丝塔萨": "龙",
    "战略转移": "转",
    "赤烟·腾武": "腾",
}


class MiniWindow(QWidget):
    """始终置顶的小窗：竖条长方框（长宽比 2~4:1），可拖动/拖长，吸附屏幕边界。

    从上到下：对局状态/手牌/场面（简化）→ 牛池勾选与殒命标记 → 计算按钮 →
    分轮次显示最高伤害的计算结果（按 战略转移/舞动 分割，只显示缩写）。
    """

    def __init__(self, main: "MainWindow"):
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.main = main
        self.setWindowTitle("红龙小窗")
        self.resize(210, 560)  # 高:宽 ≈ 2.7:1（2~4:1）
        self._drag_offset: Optional[QPoint] = None
        self._build_ui()
        self.sync_from_main()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(4)

        title = QHBoxLayout()
        self.title_label = QLabel("红龙小窗")
        min_btn = QPushButton("─")
        min_btn.setFixedSize(24, 18)
        min_btn.setToolTip("最小化")
        min_btn.clicked.connect(self.showMinimized)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 18)
        close_btn.setToolTip("关闭")
        close_btn.clicked.connect(self.hide)
        title.addWidget(self.title_label, 1)
        title.addWidget(min_btn)
        title.addWidget(close_btn)
        root.addLayout(title)

        self.state_label = QLabel("未在对局中")
        self.state_label.setStyleSheet("font-weight:bold;")
        self.state_label.setWordWrap(True)
        root.addWidget(self.state_label)

        self.hand_label = QLabel("手牌：")
        self.hand_label.setWordWrap(True)
        root.addWidget(self.hand_label)

        self.board_label = QLabel("场面：")
        self.board_label.setWordWrap(True)
        root.addWidget(self.board_label)

        band_row = QHBoxLayout()
        self.mini_etc_checks: List[Tuple[str, QCheckBox]] = []
        default_checked = {"舞动全场（ft.迦罗娜）", "幻觉药水", "生命的缚誓者阿莱克丝塔萨"}

        for card_name, _label in engine.ETC_OPTIONS:
            label = MINI_ETC_LABELS.get(card_name, _label)
            box = QCheckBox(label)
            box.setChecked(card_name in default_checked)
            box.toggled.connect(self._on_mini_etc_toggled)
            self.mini_etc_checks.append((card_name, box))
            band_row.addWidget(box)

        band_row.addStretch(1)
        root.addLayout(band_row)

        deadly_row = QHBoxLayout()
        self.mini_deadly_check = QCheckBox("殒命序号：")
        self.mini_deadly_input = QLineEdit()
        self.mini_deadly_input.setPlaceholderText("如 3,7")
        self.mini_deadly_input.setEnabled(False)
        self.mini_deadly_check.toggled.connect(self.mini_deadly_input.setEnabled)
        self.mini_deadly_check.toggled.connect(self._on_mini_deadly_changed)
        self.mini_deadly_input.textChanged.connect(self._on_mini_deadly_changed)
        deadly_row.addWidget(self.mini_deadly_check)
        deadly_row.addWidget(self.mini_deadly_input, 1)
        root.addLayout(deadly_row)

        self.mini_calc_button = QPushButton("计算")
        self.mini_calc_button.clicked.connect(self.start_calc)
        root.addWidget(self.mini_calc_button)

        self.mini_result = QPlainTextEdit()
        self.mini_result.setReadOnly(True)
        self.mini_result.setMaximumBlockCount(3000)
        root.addWidget(self.mini_result, 1)

        grip = QSizeGrip(self)
        root.addWidget(grip, 0, Qt.AlignRight)

    # ---- 拖动 / 吸附 / 调整大小 ----

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            target = event.globalPos() - self._drag_offset
            screen = QApplication.primaryScreen().availableGeometry()
            x, y = target.x(), target.y()

            # 吸附屏幕边界
            if abs(x - screen.left()) < SNAP_DISTANCE:
                x = screen.left()
            elif abs(screen.right() - (x + self.width())) < SNAP_DISTANCE:
                x = screen.right() - self.width()

            if abs(y - screen.top()) < SNAP_DISTANCE:
                y = screen.top()
            elif abs(screen.bottom() - (y + self.height())) < SNAP_DISTANCE:
                y = screen.bottom() - self.height()

            self.move(x, y)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._drag_offset = None
        event.accept()

    # ---- 与主窗口双向同步 ----

    def sync_from_main(self) -> None:
        main = self.main

        for (_, mbox), (_, sbox) in zip(main.etc_checks, self.mini_etc_checks):
            if sbox.isChecked() != mbox.isChecked():
                sbox.blockSignals(True)
                sbox.setChecked(mbox.isChecked())
                sbox.blockSignals(False)

        if self.mini_deadly_check.isChecked() != main.deadly_check.isChecked():
            self.mini_deadly_check.blockSignals(True)
            self.mini_deadly_check.setChecked(main.deadly_check.isChecked())
            self.mini_deadly_check.blockSignals(False)

        if self.mini_deadly_input.text() != main.deadly_input.text():
            self.mini_deadly_input.blockSignals(True)
            self.mini_deadly_input.setText(main.deadly_input.text())
            self.mini_deadly_input.blockSignals(False)

        self.update_state()

    def update_state(self) -> None:
        snap = self.main.snapshot
        in_game = bool(snap.get("in_game"))
        player = snap.get("player_name") or "?"
        opponent = snap.get("opponent_name") or "?"
        crystals = snap.get("crystals")
        mana = snap.get("mana")
        text = f"{player} vs {opponent}"

        if crystals is not None or mana is not None:
            text += (
                f" | 水晶{crystals if crystals is not None else '-'}"
                f"/法力{mana if mana is not None else '-'}"
            )

        if not in_game:
            text += "（未在对局）"

        if self.state_label.text() != text:
            self.state_label.setText(text)

        hand_text = self._mini_hand_text()

        if self.hand_label.text() != hand_text:
            self.hand_label.setText(hand_text)

        board_text = self._mini_board_text()

        if self.board_label.text() != board_text:
            self.board_label.setText(board_text)

    def _mini_hand_text(self) -> str:
        snap = self.main.snapshot
        hand = snap.get("hand") or []
        deadly = set(int(i) for i in (snap.get("deadly_shadow_hand_indexes") or []))

        if self.main.deadly_check.isChecked():
            for part in re.split(r"[,，、\s]+", self.main.deadly_input.text().strip()):
                if part.isdigit():
                    deadly.add(int(part))

        parts: List[str] = []

        for index, item in enumerate(hand, start=1):
            abbr = abbreviate_card_name(item.get("name") or "?")

            if item.get("ghostly") or index in deadly:
                abbr += "[殒]"

            parts.append(abbr)

        return "手牌：" + (" ".join(parts) if parts else "（空）")

    def _mini_board_text(self) -> str:
        snap = self.main.snapshot
        board = snap.get("board") or []
        enemy = snap.get("enemy_board") or []
        parts = [abbreviate_card_name(item.get("name") or "?") for item in board]
        enemy_parts = [
            abbreviate_card_name(item.get("name") or "敌方随从") for item in enemy
        ]
        text = "场面：" + (" ".join(parts) if parts else "（空）")

        if enemy_parts:
            text += " | 敌：" + " ".join(enemy_parts)

        return text

    def _on_mini_etc_toggled(self, _checked: bool) -> None:
        count = sum(1 for _name, box in self.mini_etc_checks if box.isChecked())

        if count > 3:
            sender = self.sender()

            if isinstance(sender, QCheckBox):
                sender.blockSignals(True)
                sender.setChecked(False)
                sender.blockSignals(False)
            return

        for (_, mbox), (_, sbox) in zip(self.main.etc_checks, self.mini_etc_checks):
            if mbox.isChecked() != sbox.isChecked():
                mbox.blockSignals(True)
                mbox.setChecked(sbox.isChecked())
                mbox.blockSignals(False)

        self.main._update_etc_summary()

    def _on_mini_deadly_changed(self, *_args) -> None:
        main = self.main

        if main.deadly_check.isChecked() != self.mini_deadly_check.isChecked():
            main.deadly_check.blockSignals(True)
            main.deadly_check.setChecked(self.mini_deadly_check.isChecked())
            main.deadly_check.blockSignals(False)

        if main.deadly_input.text() != self.mini_deadly_input.text():
            main.deadly_input.blockSignals(True)
            main.deadly_input.setText(self.mini_deadly_input.text())
            main.deadly_input.blockSignals(False)

    # ---- 计算 ----

    def start_calc(self) -> None:
        """小窗只负责触发主界面计算，路径解析显示在主窗口结果上。"""
        if self.main.worker is not None:
            # 主界面正在计算：避免误触发中止
            return

        if not self.main.snapshot.get("in_game") and not self.main.snapshot.get("hand"):
            self.mini_result.setPlainText("（无可用局面：请先进入对局或手动输入）")
            return

        try:
            self.main._merged_deadly_indexes()
        except ValueError as exc:
            self.mini_result.setPlainText(f"殒命标记错误：{exc}")
            return

        self.mini_calc_button.setEnabled(False)
        self.mini_calc_button.setText("计算中…")
        self.mini_result.setPlainText("正在计算…")
        self.main.on_calc_toggle()

    def on_main_worker_done(self) -> None:
        """主窗口计算完成/中止后恢复按钮。"""
        self.mini_calc_button.setEnabled(True)
        self.mini_calc_button.setText("计算")

    def show_result(self, data: Dict[str, object]) -> None:
        """解析并显示主窗口计算结果（小窗只负责解析主界面路径）。"""
        self.mini_result.setPlainText(format_mini_results(data))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    demo = "--demo" in sys.argv
    smoke = "--smoke" in sys.argv
    selftest = "--selftest" in sys.argv
    window = MainWindow(demo=demo)
    window.show()

    if selftest:
        def run_self_test() -> None:
            try:
                options = window._options()
                options.update(
                    threads=4,
                    time_budget_sec=3.0,
                    depth=40,
                    max_paths=200000,
                )
                worker = CalculationWorker(DEMO_SNAPSHOT, options)
                loop = QEventLoop()
                outcome: Dict[str, object] = {}

                def on_ok(data: Dict[str, object]) -> None:
                    outcome["data"] = data
                    loop.quit()

                def on_fail(message: str) -> None:
                    outcome["error"] = message
                    loop.quit()

                worker.finished_ok.connect(on_ok)
                worker.failed.connect(on_fail)
                worker.start()

                watchdog = QTimer()
                watchdog.setSingleShot(True)
                watchdog.timeout.connect(loop.quit)
                watchdog.start(120000)
                loop.exec_()

                if not outcome:
                    raise RuntimeError("自测超时：计算线程未返回")

                if "error" in outcome:
                    raise RuntimeError(str(outcome["error"]))

                data = outcome["data"]
                count = len(data.get("results") or [])
                print(
                    f"SELFTEST OK: mode={data.get('mode')} "
                    f"results={count} max_damage={data.get('max_damage')} "
                    f"max_dragons={data.get('max_dragons')}"
                )
                app.exit(0)
            except Exception as exc:  # noqa: BLE001
                print(f"SELFTEST FAILED: {type(exc).__name__}: {exc}")
                app.exit(1)

        QTimer.singleShot(300, run_self_test)
        QTimer.singleShot(120000, app.quit)
        return app.exec_()

    if smoke:
        QTimer.singleShot(2500, app.quit)

    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
