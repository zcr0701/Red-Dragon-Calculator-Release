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

import base64
import hashlib
import html
import ipaddress  # noqa: F401 - PyInstaller 打包必需（frozen urllib.parse 依赖它，缺了无法启动）
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QEventLoop, QPoint, QSettings, QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QCursor, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSpinBox,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

import engine
from powerlog_reader import LogWatcher


BASE_DIR = Path(__file__).resolve().parent

if getattr(sys, "frozen", False):
    # PyInstaller 打包：程序文件（引擎 exe / 卡名映射 / 日志目录）都放在主程序同目录
    BASE_DIR = Path(sys.executable).resolve().parent

# PyInstaller onefile 的运行时数据解压目录（onedir 与开发模式直接使用程序目录）
DATA_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))

LOGS_DIR = BASE_DIR / "logs"

try:
    from _embedded_assets import (  # type: ignore
        EMBEDDED_QR_BASE64,
        ENGINE_EXE_SHA256,
        CARD_MAP_SHA256,
    )
except Exception:  # noqa: BLE001 - 开发环境没有嵌入资产时走文件/无校验
    EMBEDDED_QR_BASE64 = ""
    ENGINE_EXE_SHA256 = ""
    CARD_MAP_SHA256 = ""

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
    # 空场：原十龙局面（8 水晶 8 法力 → 10 龙 / 160 伤）要求场上无随从，
    # 若放一只随从会占掉第 7 格，导致十龙路径在第 9 步刀油前满场而打不出来
    "board": [],
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

# 小窗缩写字底方块颜色（未列出的缩写不画方块；殒不在此表——跟随所变形卡的颜色）
CARD_ABBREV_COLORS = {
    "鱼": "#46F2FF",
    "牛": "#683926",
    "龙": "#FFAC42",
    "狐": "#F9517B",
    "刀": "#C7AA8C",
    "晦": "#7D24DD",
    "暗": "#F978F6",
    "舞": "#FFFCAE",
    "幻": "#1057ED",
    "币": "#31A610",
    "伺": "#B0F46F",
    "步": "#050305",
    "骨": "#EAF0D7",
    "腾": "#D62443",
}


def _contrast_text_color(hex_color: str) -> str:
    """按背景色亮度返回黑/白文字色，保证方块内可读。"""
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
    except (ValueError, IndexError):
        return "#FFFFFF"

    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return "#000000" if luminance > 0.5 else "#FFFFFF"


def _abbr_square(ch: str, color: str) -> str:
    """单个缩写字的正方形字底方块（背景色 + 自动黑/白文字）。"""
    text = _contrast_text_color(color)
    return (
        f'<span style="background-color:{color}; color:{text}; '
        f'padding:0 2px;">{ch}</span>'
    )

# 指向性操作统一标准：卡名（目标名Nnd），N 为目标随从在 board 上的顺序（1 起），
# 由 C++ 核心在路径里直接输出；小窗缩写显示为 卡(目标缩写N)（去掉 nd），
# 如 暗影施法者（斯卡布斯·刀油3nd）-> 暗(刀3)。
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

            # 指向性目标带 board 序号，如 斯卡布斯·刀油3nd -> 刀3（小窗去掉 nd）
            m = re.match(r"^(.*?)(\d+)nd$", p)

            if m:
                parts.append(abbreviate_card_name(m.group(1).strip()) + m.group(2))
            else:
                # 牛头人乐队选择（-> 连接）或无效目标等：无序号，保持原名
                parts.append(abbreviate_card_name(p))

        # 牛头人乐队多个选择直接连写（如 牛(舞龙)），不加分隔符
        target = "(" + "".join(parts) + ")"

    return abbreviate_card_name(name) + target + deadly


def abbreviate_step_html(step: str) -> str:
    """小窗彩色显示：已知缩写字按 CARD_ABBREV_COLORS 上色，其余字符原样保留。

    [殒] 标记跟随“所变形卡”（主卡缩写）的颜色，而不是目标括号里的缩写。
    """
    plain = abbreviate_step(step)
    deadly = plain.endswith("[殒]")
    body = plain[:-3] if deadly else plain

    split_at = -1
    for sep in ("（", "("):
        pos = body.find(sep)
        if pos >= 0 and (split_at < 0 or pos < split_at):
            split_at = pos

    if split_at >= 0:
        main, target = body[:split_at], body[split_at:]
    else:
        main, target = body, ""

    main_color = CARD_ABBREV_COLORS.get(main)
    parts: List[str] = []

    if main_color:
        parts.append(_abbr_square(html.escape(main), main_color))
    else:
        parts.append(html.escape(main))

    index = 0

    while index < len(target):
        ch = target[index]

        if ch in CARD_ABBREV_COLORS:
            # 颜色方块连同随后的序号一起（[刀3] 而不是 [刀]3）
            end = index + 1

            while end < len(target) and target[end].isdigit():
                end += 1

            segment = target[index:end]
            parts.append(_abbr_square(html.escape(segment), CARD_ABBREV_COLORS[ch]))
            index = end
        else:
            parts.append(html.escape(ch))
            index += 1

    if deadly:
        if main_color:
            parts.append("[" + _abbr_square("殒", main_color) + "]")
        else:
            parts.append("[殒]")

    return "".join(parts)


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


def _format_exchange_line(exchanges: List[Tuple[int, int]]) -> str:
    """场面交换处理行：[我方随从X]->[敌方随从X]，……。X 为 board 序号。"""
    if not exchanges:
        return ""

    plan = "，".join(f"[我方随从{fi}]->[敌方随从{ei}]" for fi, ei in exchanges)
    return f"场面交换处理：{plan}。其中X为随从在board中的序号"


# 小窗段落（每轮路径行）开头缩进两个全角空格
PARA_INDENT = "\u3000\u3000"


def format_mini_results(
    data: Dict[str, object],
    colors: bool = False,
    exchanges: Optional[List[Tuple[int, int]]] = None,
) -> str:
    """小窗结果：最高伤害路径按轮次分割，只显示缩写。

    colors=True 时返回 HTML（缩写字上色），否则返回纯文本；
    exchanges 非空时在标题后插入“场面交换处理”行。
    """
    results = data.get("results") or []
    best_mana = (results[0].get("mana") if results else 0) or 0
    exchange_line = _format_exchange_line(list(exchanges or []))
    title = (
        f"最大伤害：{data.get('max_damage', 0)}，"
        f"龙数：{data.get('max_dragons', 0)}，余：{best_mana}费"
    )

    if colors:
        esc = html.escape
        lines = [esc(title)]

        if exchange_line:
            lines.extend(["", esc(exchange_line), ""])

        if not results:
            lines.append(esc("（无路径）"))
            return "<br>".join(lines)

        path = (results[0].get("path") or []) if results else []
        rounds = split_path_rounds(path)

        for index, rnd in enumerate(rounds, start=1):
            abbr = "-".join(abbreviate_step_html(step) for step in rnd)
            lines.append(esc(f"[第{chinese_round_number(index)}轮]："))
            lines.append(PARA_INDENT + (abbr if abbr else esc("（空）")))
            lines.append("")

        return "<br>".join(lines)

    lines = [title]

    if exchange_line:
        lines.extend(["", exchange_line, ""])

    if not results:
        lines.append("（无路径）")
        return "\n".join(lines)

    path = (results[0].get("path") or []) if results else []
    rounds = split_path_rounds(path)

    for index, rnd in enumerate(rounds, start=1):
        abbr = "-".join(abbreviate_step(step) for step in rnd)
        lines.append(f"[第{chinese_round_number(index)}轮]：")
        lines.append(PARA_INDENT + (abbr if abbr else "（空）"))
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
                exchanges=list(self.options.get("exchanges") or []),
                only_best_damage=bool(self.options.get("only_best_damage", True)),
                progress_callback=self.progress.emit,
                found_callback=self.found.emit,
                should_stop=lambda: self._stop,
            )
            if bool(self.options.get("draw_whatif", True)):
                # 独立的“如果机制”：省费打出抽随从卡、抽缺失组合随从后的最高伤害推演
                result["draw_whatif"] = engine.compute_draw_whatif(self.snapshot, self.options)
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


def parse_manual_zone_lines(
    text: str,
) -> Tuple[List[Tuple[Optional[int], str, Optional[int], Optional[int]]], List[str]]:
    """手牌/随从栏一行行解析成 [(费用, 卡名, 血量, 攻击)]。

    支持格式：
    - “4 鲨鱼之灵”（费用 卡名）；
    - “4 鲨鱼之灵 3”（费用 卡名 血量，随从栏）；
    - “4 鲨鱼之灵 0/3”（费用 卡名 攻击/血量，随从栏，用于场面交换）；
    - “4,3 鲨鱼之灵”（费用,血量 卡名，兼容旧写法）；
    - “* 殒命暗影”（* 表示无费用特殊卡，自动标记殒命）；
    - 纯卡名（自动取基础费用，支持简称，如 刀油 -> 斯卡布斯·刀油）；
    - 单独一行费用作为下一行卡名的费用（OCR 式两行一组）。
    """
    entries: List[Tuple[Optional[int], str, Optional[int], Optional[int]]] = []
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
            entries.append((None, engine.resolve_card_name(star_cost.group(1)), None, None))
            pending_cost = None
            continue

        comma_match = _COMMA_ZONE_RE.match(line)

        if comma_match:
            entries.append(
                (
                    int(comma_match.group(1)),
                    engine.resolve_card_name(comma_match.group(3)),
                    int(comma_match.group(2)),
                    None,
                )
            )
            pending_cost = None
            continue

        tokens = line.split()

        if tokens and tokens[0].isdigit() and len(tokens) >= 2:
            cost = int(tokens[0])
            health = None
            attack = None
            name_parts = tokens[1:]

            if len(tokens) >= 3 and "/" in tokens[-1]:
                ab = tokens[-1].split("/", 1)

                if ab[0].isdigit() and ab[1].isdigit():
                    attack, health = int(ab[0]), int(ab[1])
                    name_parts = tokens[1:-1]
                else:
                    warnings.append(f"忽略无法识别的攻击/血量：{line}")
            elif len(tokens) >= 3 and tokens[-1].isdigit():
                health = int(tokens[-1])
                name_parts = tokens[1:-1]

            entries.append(
                (cost, engine.resolve_card_name(" ".join(name_parts)), health, attack)
            )
            pending_cost = None
            continue

        name = engine.resolve_card_name(line)
        base = engine.KNOWN_BASE_COSTS.get(name)

        if pending_cost is not None:
            entries.append((pending_cost, name, None, None))
            pending_cost = None
        elif base is not None:
            entries.append((int(base), name, None, None))
        else:
            warnings.append(f"未知卡名（按杂牌处理）：{line}")
            entries.append((None, name, None, None))

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


def parse_manual_enemy_lines(text: str) -> Tuple[List[Tuple[str, int, Optional[int]]], List[str]]:
    """敌方随从栏：每行“攻击/血量 名字”或“血量 名字”（攻击可省略，用于场面交换）。"""
    entries: List[Tuple[str, int, Optional[int]]] = []
    warnings: List[str] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or _SECTION_RE.match(line):
            continue
        tokens = line.split()
        if not tokens:
            continue
        first = tokens[0]

        if "/" in first:
            ab = first.split("/", 1)

            if not (ab[0].isdigit() and ab[1].isdigit()):
                warnings.append(f"忽略无法识别的敌方随从行：{line}")
                continue

            attack, health = int(ab[0]), int(ab[1])
        elif first.isdigit():
            attack, health = None, int(first)
        else:
            warnings.append(f"忽略无法识别的敌方随从行：{line}")
            continue

        if len(tokens) >= 2:
            name = engine.resolve_card_name(" ".join(tokens[1:]))
        else:
            name = "敌方随从"

        entries.append((name, health, attack))

    return entries, warnings


def parse_exchange_text(
    text: str,
    board_len: int,
    enemy_len: int,
) -> Tuple[List[Tuple[int, int]], List[str]]:
    """场面交换输入：如 “1->1, 2->3”（我方随从序号->敌方随从序号，1 起）。"""
    pairs: List[Tuple[int, int]] = []
    warnings: List[str] = []

    for raw in text.replace("，", ",").replace("→", "->").split(","):
        item = raw.strip()

        if not item:
            continue

        m = re.match(r"^(\d+)\s*[-–]\s*>?\s*(\d+)$", item)

        if not m:
            warnings.append(f"忽略无法识别的场面交换：{raw}")
            continue

        friend_index, enemy_index = int(m.group(1)), int(m.group(2))

        if (
            friend_index < 1
            or friend_index > board_len
            or enemy_index < 1
            or enemy_index > enemy_len
        ):
            warnings.append(
                f"交换序号越界：{raw}（我方 1~{board_len}，敌方 1~{enemy_len}）"
            )
            continue

        pairs.append((friend_index, enemy_index))

    return pairs, warnings


class MainWindow(QWidget):
    def __init__(self, demo: bool = False):
        super().__init__()
        self.setWindowTitle("红龙贼计算器（C++ 核心 + hslog 日志读取）(CreATedBy此人乃天下绝响#5854)")
        self.resize(1120, 780)

        self.watcher = LogWatcher()
        self.snapshot: Dict[str, object] = {}
        self.worker: Optional[CalculationWorker] = None
        self.mini_window: Optional[MiniWindow] = None
        self._last_state_key = ""
        self._manual_mode = False
        self.etc_checks: List[Tuple[str, QCheckBox]] = []
        self._auto_exchange_cache: Optional[Tuple[str, List[Tuple[int, int]]]] = None

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
        self.mini_font_label = QLabel("小窗字号:")
        self.mini_font_spin = QSpinBox()
        self.mini_font_spin.setRange(12, 48)
        self.mini_font_spin.setValue(self.mini_font_size())
        self.mini_font_spin.setSuffix("px")
        self.mini_font_spin.setToolTip("小窗公式字号（默认 32px，可记忆）")
        self.mini_font_spin.valueChanged.connect(self.apply_mini_font)
        self.mini_color_check = QCheckBox("小窗颜色")
        self.mini_color_check.setChecked(self.mini_color_enabled())
        self.mini_color_check.setToolTip("小窗公式缩写字用颜色区分（默认勾选）")
        self.mini_color_check.toggled.connect(self.apply_mini_color)
        self.manual_button = QPushButton("▸ 手动输入")
        self.manual_button.setCheckable(True)
        self.manual_button.setChecked(True)
        status.addWidget(self.status_label, 1)
        status.addWidget(self.manual_button)
        status.addWidget(self.refresh_button)
        status.addWidget(self.demo_button)
        status.addWidget(self.mini_button)
        status.addWidget(self.mini_font_label)
        status.addWidget(self.mini_font_spin)
        status.addWidget(self.mini_color_check)
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
        self.beam_width = self._spin(0, 0, 1000000, step=100)                   
        param_grid.addWidget(QLabel("束宽（0=自动四通道）："), 7, 0)
        param_grid.addWidget(self.beam_width, 7, 1)
        self.no_time_limit = QCheckBox("不限时：按束宽×最大深度跑完（时间预算失效，大束宽可能很慢）")
        self.no_time_limit.setToolTip("勾选后搜索不因时间耗尽而停止，跑满最大深度或状态收敛为止")
        param_grid.addWidget(self.no_time_limit, 8, 0, 1, 2)
        self.best_only_check = QCheckBox("只计算最高伤害：找到最高伤后剪掉无法超越它的低伤路径")
        self.best_only_check.setChecked(True)
        self.best_only_check.setToolTip(
            "默认勾选：一旦找到当前最高伤害，就剪掉无论如何都超不过它的低伤分支，"
            "只返回最高伤害路径，计算更快"
        )
        param_grid.addWidget(self.best_only_check, 9, 0, 1, 2)
        self.draw_whatif_check = QCheckBox("如果机制：抽随从假设最高伤害（省费打出抽随从卡）")
        self.draw_whatif_check.setChecked(True)
        self.draw_whatif_check.setToolTip(
            "计算完成后独立推演：若用手牌中的抽随从卡（默认先伺机待发省费）"
            "抽到预写组合缺失的随从，能达到的最高伤害"
        )
        param_grid.addWidget(self.draw_whatif_check, 10, 0, 1, 2)
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
        enemy_panel.addWidget(QLabel("敌方随从（攻击/血量，攻击可省）"))
        self.manual_enemy_edit = make_manual_edit("例：\n2/2 寒光智者\n3\n6/5 指挥官碧阿崔克丝")
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

        exchange_row = QHBoxLayout()
        exchange_row.addWidget(QLabel("场面交换（我方序号->敌方序号，逗号分隔）："))
        self.exchange_edit = QLineEdit()
        self.exchange_edit.setPlaceholderText("如 1->1, 2->2；按攻击/血量结算，B≤0 死亡移除")
        exchange_row.addWidget(self.exchange_edit, 1)
        manual_layout.addLayout(exchange_row)

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

        def _ab_text(item: Dict[str, object]) -> str:
            attack = item.get("attack")
            health = item.get("health")
            a = attack if attack is not None else "?"
            b = health if health is not None else "?"
            return f"{a}/{b}"

        for index, item in enumerate(snap.get("board") or [], start=1):
            cost = item.get("cost")
            cost_text = f"{cost}费" if cost is not None else "?费"
            board_lines.append(
                f"{index:2d}. [{cost_text}] {item['name']}（{_ab_text(item)}）"
            )

        enemy_lines: List[str] = []

        for index, item in enumerate(snap.get("enemy_board") or [], start=1):
            enemy_lines.append(
                f"{index:2d}. {item.get('name') or '敌方随从'}（{_ab_text(item)}）"
            )

        if enemy_lines:
            board_lines.append("敌方随从：")
            board_lines.extend(enemy_lines)

        board_text = "\n".join(board_lines) or "（空）"

        if self.board_text.toPlainText() != board_text:
            self.board_text.setPlainText(board_text)

        # 日志检测到牛池（SETASIDE 乐队卡）时，实时同步勾选；手动画选择保持用户设置。
        # 牛池最多三张：检测结果超过三张时优先保留用户已勾选，再按检测顺序补足。
        etc_band = snap.get("etc_band")
        if isinstance(etc_band, list):
            option_names = [name for name, _box in self.etc_checks]
            detected = [n for n in etc_band if n in option_names]
            current_checked = [name for name, box in self.etc_checks if box.isChecked()]
            target = [n for n in current_checked if n in detected]
            target += [n for n in detected if n not in target]
            target = target[:3]
            for name, box in self.etc_checks:
                checked = name in target
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

    @staticmethod
    def mini_font_size() -> int:
        """小窗公式字号（QSettings 记忆，默认 32px）。"""
        value = QSettings("RedDragonCalculator", "main").value("mini_font_px", 32)
        return int(value or 32)

    def apply_mini_font(self, size: int) -> None:
        """保存小窗公式字号并即时生效。"""
        QSettings("RedDragonCalculator", "main").setValue("mini_font_px", int(size))

        if self.mini_window is not None:
            self.mini_window.set_formula_font(int(size))

    @staticmethod
    def mini_color_enabled() -> bool:
        """小窗缩写字颜色开关（QSettings 记忆，默认勾选）。"""
        value = QSettings("RedDragonCalculator", "main").value("mini_color", True)
        if isinstance(value, bool):
            return value
        return str(value).lower() not in ("0", "false", "no", "off", "")

    def apply_mini_color(self, checked: bool) -> None:
        """保存小窗颜色开关并即时刷新已显示的结果。"""
        QSettings("RedDragonCalculator", "main").setValue("mini_color", bool(checked))

        if self.mini_window is not None:
            self.mini_window.refresh_result()

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
            {
                "name": name,
                "cost": cost,
                "ghostly": cost is None and name == "殒命暗影",
            }
            for cost, name, _health, _attack in hand_entries
        ]
        board = [
            {"name": name, "cost": cost, "health": health, "attack": attack}
            for cost, name, health, attack in board_entries
        ]
        enemy_board = [
            {"name": name, "health": health, "attack": attack}
            for name, health, attack in enemy_entries
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
            "exchanges": self.current_exchange_pairs(),
            "only_best_damage": self.best_only_check.isChecked(),
            "draw_whatif": self.draw_whatif_check.isChecked(),
        }

    def current_exchange_pairs(self) -> List[Tuple[int, int]]:
        """场面交换方案：手动输入优先；留空时用独立的场面交换搜索自动选。

        场面交换搜索与路径搜索分离——它只看我方随从栏（空位数/序号位置），
        由 engine.plan_exchanges 按 exchange_heuristic 选最优，结果按场面指纹缓存。
        """
        board_len = len(self.snapshot.get("board") or [])
        enemy_len = len(self.snapshot.get("enemy_board") or [])
        text = self.exchange_edit.text().strip()

        if text:
            pairs, _warnings = parse_exchange_text(text, board_len, enemy_len)
            board = self.snapshot.get("board") or []

            # 0 攻随从无法主动攻击（以场面当前攻击为准），交换不生效
            return [
                (friend_index, enemy_index)
                for friend_index, enemy_index in pairs
                if int(board[friend_index - 1].get("attack") or 0) >= 1
            ]

        board = self.snapshot.get("board") or []
        enemy = self.snapshot.get("enemy_board") or []
        hand = self.snapshot.get("hand") or []
        etc_band = self.snapshot.get("etc_band")
        fingerprint = (
            json.dumps(
                [(b.get("name"), b.get("health"), b.get("attack")) for b in board],
                ensure_ascii=False,
            )
            + "|"
            + json.dumps(
                [
                    (e.get("name"), e.get("health"), e.get("attack"))
                    for e in enemy
                ],
                ensure_ascii=False,
            )
            + "|"
            + json.dumps(
                [(h.get("name"), h.get("cost")) for h in hand],
                ensure_ascii=False,
            )
            + "|"
            + json.dumps(etc_band, ensure_ascii=False)
        )

        if self._auto_exchange_cache is not None and self._auto_exchange_cache[0] == fingerprint:
            return list(self._auto_exchange_cache[1])

        plan, _result_board, _score = engine.plan_exchanges(
            board, enemy, hand=hand, etc_band=etc_band
        )
        self._auto_exchange_cache = (fingerprint, list(plan))
        return list(plan)

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
        _pairs, exchange_warnings = parse_exchange_text(
            self.exchange_edit.text(),
            len(snapshot.get("board") or []),
            len(snapshot.get("enemy_board") or []),
        )
        board_items = snapshot.get("board") or []

        for friend_index, _enemy_index in _pairs:
            if (
                friend_index <= len(board_items)
                and int(board_items[friend_index - 1].get("attack") or 0) < 1
            ):
                exchange_warnings.append(
                    f"我方随从{friend_index} 为 0 攻，无法主动攻击，已忽略"
                )

        if exchange_warnings:
            self.engine_label.setText("；".join(exchange_warnings[:3]))

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

        def _ab(item: dict) -> str:
            attack = item.get("attack")
            health = item.get("health")
            a = attack if attack is not None else "?"
            b = health if health is not None else "?"
            return f"{a}/{b}"

        board_parts = []

        for index, item in enumerate(snapshot.get("board") or [], start=1):
            cost = item.get("cost")
            cost_text = f"{cost}费" if cost is not None else "?费"
            board_parts.append(f"{index}. [{cost_text}] {item['name']}（{_ab(item)}）")

        if board_parts:
            lines.append("  战场：" + "  ".join(board_parts))

        enemy_parts = []

        for index, item in enumerate(snapshot.get("enemy_board") or [], start=1):
            enemy_parts.append(f"{index}. {item.get('name') or '敌方随从'}（{_ab(item)}）")

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

        whatif = data.get("draw_whatif")
        if whatif:
            lines.append("")
            lines.append("如果机制：")
            lines.append("如果使用：[" + "][".join(whatif["cards"]) + "]")
            drawn = whatif.get("drawn") or []
            lines.append("可能抽到：[" + "][".join(drawn) + "]")
            discounts = whatif.get("discounts") or []
            if discounts:
                unique = list(dict.fromkeys(discounts))
                lines.append("减费状态：" + "、".join(f"{d}(-2)" for d in unique) + "（当前费用为减费后显示值）")
            if whatif.get("completeness") is not None:
                lines.append(
                    f"随从齐全度：{whatif['completeness']}/{whatif.get('completeness_total', 7)}"
                    f"（法力 {whatif.get('mana_left')} / 水晶 {whatif.get('crystals')}）"
                )
            lines.append(f"预计伤害：{whatif['damage']}，龙数：{whatif['dragons']}，余：{whatif['mana_left']}费")

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
        self.setWindowTitle("红龙小窗(CreATedBy此人乃天下绝响#5854)")
        self.resize(210, 560)  # 高:宽 ≈ 2.7:1（2~4:1）
        self._drag_offset: Optional[QPoint] = None
        self._last_data: Optional[Dict[str, object]] = None
        self._build_ui()
        self.sync_from_main()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(4)

        title = QHBoxLayout()
        self.title_label = QLabel("红龙小窗(CreATedBy此人乃天下绝响#5854)")
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

        self.mini_result = QTextBrowser()
        self.mini_result.setReadOnly(True)
        self.mini_result.document().setMaximumBlockCount(3000)
        root.addWidget(self.mini_result, 1)
        self.set_formula_font(self.main.mini_font_size())

        grip = QSizeGrip(self)
        root.addWidget(grip, 0, Qt.AlignRight)

    def set_formula_font(self, size: int) -> None:
        """设置公式（分轮结果）字号。"""
        font = self.mini_result.font()
        font.setPixelSize(int(size))
        self.mini_result.setFont(font)
        self.mini_result.document().setDefaultFont(font)

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

        def _ab(item: dict) -> str:
            attack = item.get("attack")
            health = item.get("health")
            a = attack if attack is not None else "?"
            b = health if health is not None else "?"
            return f"({a}/{b})"

        parts = [
            abbreviate_card_name(item.get("name") or "?") + _ab(item)
            for item in board
        ]
        enemy_parts = [
            abbreviate_card_name(item.get("name") or "敌方随从") + _ab(item)
            for item in enemy
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
        self._last_data = data
        self._render_result()

    def _render_result(self) -> None:
        """按当前“小窗颜色”开关渲染最近一次结果（HTML 上色 / 纯文本）。"""
        if self._last_data is None:
            return

        exchanges = self.main.current_exchange_pairs()

        if self.main.mini_color_enabled():
            self.mini_result.setHtml(
                format_mini_results(self._last_data, colors=True, exchanges=exchanges)
            )
        else:
            self.mini_result.setPlainText(
                format_mini_results(self._last_data, exchanges=exchanges)
            )

    def refresh_result(self) -> None:
        """小窗颜色开关切换后重绘已显示的结果。"""
        self._render_result()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        event.accept()


class IntroDialog(QDialog):
    """启动介绍弹窗：使用说明 + 作者/反馈 + 收款码（内容可滚动，收款码在底部）。

    收款码图片读取程序目录下的 收款码.png；不存在时显示占位提示。
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("使用说明(CreATedBy此人乃天下绝响#5854)")
        # 去掉标题栏的“?”帮助按钮和关闭 x（只能看完滚动内容后点“知道了”关闭）
        flags = self.windowFlags()
        flags &= ~Qt.WindowContextHelpButtonHint
        flags &= ~Qt.WindowCloseButtonHint
        self.setWindowFlags(flags)
        self.resize(900, 1000)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        self._scroll = scroll

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)

        title = QLabel("使用说明")
        title.setStyleSheet("font-size:40px; font-weight:bold; color:#C2410C;")
        layout.addWidget(title)

        usage = QLabel(
            '<span style="font-size:36px; color:#1F2937;">'
            '1. 启动后会自动解析炉石本地 <b><span style="color:#0E7490;">Power.log</span></b>，'
            '实时读取场面数据；自动更新牛内随从以及殒命暗影追踪。<br/>'
            '2. 主窗口点击“<b><span style="color:#DC2626;">开始计算</span></b>”，或点击'
            '<b><span style="color:#7C3AED;">小窗</span></b>按钮，弹出小窗；小窗处点击“计算”，'
            '运行<b>束宽搜索</b>。<br/>'
            '3. <b>束宽</b>默认填 0 即可；如果怀疑搜出的不是最优解，可勾选'
            '“<b><span style="color:#DC2626;">不限时</span></b>”，并将束宽设置为'
            '<b>十万</b>或更高，<b>深度40</b>或更高，以计算全局最优解。<br/>'
            '4. <b><span style="color:#D97706;">场面交换</span></b>：默认留空，自动搜索最优交换；'
            '会根据当前场面，计算出场面得到的<b><span style="color:#D97706;">最高伤害</span></b>交换解。<br/>'
            '5. 小窗默认置顶，最小化需要点击按钮，默认计算最高伤害，<b>分轮</b>显示路径。'
            '</span>'
        )
        usage.setWordWrap(True)
        usage.setStyleSheet("font-size:36px; color:#1F2937;")
        layout.addWidget(usage)

        layout.addSpacing(20)

        author_header = QLabel("作者附言：")
        author_header.setStyleSheet("font-size:36px; font-weight:bold; color:#D97706;")
        layout.addWidget(author_header)

        author_body = QLabel(
            '<span style="font-size:36px; color:#374151;">'
            '　　该计算器经历了多轮底层架构的优化和算法的设计尝试，以及反复的bug修改，'
            '才实现了将计算时间压缩到<b><span style="color:#DC2626;">3s以内</span></b>，'
            '普遍覆盖了<b><span style="color:#16A34A;">95%以上的最优解</span></b>，'
            '并且计算出许多公式表上的<b><span style="color:#D97706;">更优解</span></b>'
            '以及一些神奇的<b><span style="color:#7C3AED;">等价路径</span></b>，'
            '具体由使用者自己发掘。<br/>'
            '　　开发这个软件的过程耗费了作者不少的时间精力和金钱，'
            '所以如果帮助到了您，请务必给作者<b><span style="color:#D97706;">一点支持</span></b>。'
            '</span>'
        )
        author_body.setWordWrap(True)
        author_body.setStyleSheet("font-size:36px; color:#374151;")
        layout.addWidget(author_body)

        author_id = QLabel(
            '<span style="font-size:36px; color:#1F2937;">该计算器由 '
            '<b><span style="color:#1D4ED8;">战网ID：此人乃天下绝响#5854</span></b> 制作</span>'
        )
        layout.addWidget(author_id)

        feedback = QLabel(
            '<span style="font-size:36px; color:#1F2937;">'
            '如遇到bug或功能建议，请加作者<b><span style="color:#B91C1C;">QQ：2250195126</span></b>提供反馈<br/>'
            '如果您需要<b><span style="color:#7C3AED;">定制</span></b>其他相关的插件或功能，'
            '也可以加我<b><span style="color:#B91C1C;">QQ</span></b>。'
            '</span>'
        )
        feedback.setWordWrap(True)
        feedback.setStyleSheet("font-size:36px; color:#DC2626;")
        layout.addWidget(feedback)

        layout.addSpacing(20)

        thanks = QLabel(
            '<span style="font-size:36px; color:#1F2937;">'
            '　　最后，如果你喜欢该作品，并且对你起到了帮助<br/>'
            '<b><span style="color:#B45309;">不妨请我喝瓶可乐吧~</span></b>'
            '</span>'
        )
        thanks.setWordWrap(True)
        thanks.setStyleSheet("font-size:36px; font-weight:bold; color:#B45309;")
        layout.addWidget(thanks)

        qr_label = QLabel()
        qr_label.setAlignment(Qt.AlignCenter)
        qr_path = BASE_DIR / "收款码.png"

        if EMBEDDED_QR_BASE64:
            pixmap = QPixmap()
            pixmap.loadFromData(base64.b64decode(EMBEDDED_QR_BASE64))
        else:
            pixmap = QPixmap(str(qr_path)) if qr_path.is_file() else QPixmap()

        if not pixmap.isNull():
            pixmap = pixmap.scaled(
                500, 500, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            qr_label.setPixmap(pixmap)
        else:
            qr_label.setText(
                "（未找到收款码图片：请将图片命名为 收款码.png\n"
                "放在程序目录后重新打包，即可内嵌显示）"
            )
            qr_label.setWordWrap(True)
            qr_label.setStyleSheet("font-size:30px; color:#666;")

        layout.addWidget(qr_label)
        layout.addStretch(1)
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.addWidget(scroll, 1)

        close_row = QHBoxLayout()
        close_btn = QPushButton("知道了")
        close_btn.setStyleSheet("font-size:30px; padding:8px 30px;")
        close_btn.setEnabled(False)  # 必须滑到最底看完，且等待 3 秒后才能关闭
        close_btn.clicked.connect(self.accept)
        self._close_btn = close_btn
        self._close_ready = False
        self._close_wait_secs = 5
        self._update_close_button_text()
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._on_countdown)
        self._countdown_timer.start()
        close_row.addStretch(1)
        close_row.addWidget(close_btn)
        close_row.addStretch(1)
        outer.addLayout(close_row)

        sb = scroll.verticalScrollBar()
        sb.valueChanged.connect(self._update_close_enabled)
        sb.rangeChanged.connect(self._update_close_enabled)

    def _update_close_enabled(self) -> None:
        """滑到最底（value >= maximum）且等待满 3 秒，才允许点“知道了”关闭。"""
        sb = self._scroll.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum()
        self._close_btn.setEnabled(at_bottom and self._close_ready)

    def _on_countdown(self) -> None:
        """每秒递减倒计时，满 3 秒后允许关闭（仍需滑到最底）。"""
        self._close_wait_secs -= 1

        if self._close_wait_secs <= 0:
            self._countdown_timer.stop()
            self._close_ready = True

        self._update_close_button_text()
        self._update_close_enabled()

    def _update_close_button_text(self) -> None:
        if self._close_wait_secs > 0:
            self._close_btn.setText(f"知道了（{self._close_wait_secs}秒后可关闭）")
        else:
            self._close_btn.setText("知道了")

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        QTimer.singleShot(0, self._update_close_enabled)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.key() == Qt.Key_Escape:
            event.ignore()  # ESC 不关闭，必须看完后点“知道了”
            return
        super().keyPressEvent(event)


def _verify_integrity() -> Optional[str]:
    """打包版启动自检：计算核心 / 卡牌数据被篡改或缺失时拒绝启动。

    校验哈希在打包时由 build_dist.py 内嵌进代码（随代码一起加密），
    运行时只做一次 SHA256 对比，对计算时间无影响。
    """
    if not getattr(sys, "frozen", False):
        return None

    for filename, expected, label in (
        ("red_dragon_engine.exe", ENGINE_EXE_SHA256, "计算核心"),
        ("card_id_map.json", CARD_MAP_SHA256, "卡牌数据"),
    ):
        if not expected:
            continue

        path = DATA_DIR / filename

        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return f"缺少{label}文件，程序可能被篡改"

        if actual != expected:
            return f"{label}文件校验失败，程序可能被篡改"

    return None


def _center_on_screen(window: QWidget) -> None:
    """主窗口默认居中显示（取鼠标所在屏幕，回退主屏）。"""
    cursor_screen = QApplication.screenAt(QCursor.pos())
    screen = cursor_screen or QApplication.primaryScreen()

    if screen is None:
        return

    geo = screen.availableGeometry()
    frame = window.frameGeometry()
    frame.moveCenter(geo.center())
    window.move(frame.topLeft())


def main() -> int:
    app = QApplication(sys.argv)
    demo = "--demo" in sys.argv
    smoke = "--smoke" in sys.argv
    selftest = "--selftest" in sys.argv
    bench = "--bench" in sys.argv

    integrity_error = _verify_integrity()

    if integrity_error:
        if smoke or selftest:
            print(integrity_error)
            return 1

        QMessageBox.critical(
            None, "校验失败", integrity_error + "\n请从作者处获取原始版本。"
        )
        return 1

    window = MainWindow(demo=demo)
    window.show()
    _center_on_screen(window)

    if not smoke and not selftest and not bench:
        IntroDialog(window).exec_()

    if bench:
        # 性能基准：连续跑 3 次固定预算计算（默认 3 秒/次，--bench N 可改预算，
        # 0 = 不限时自然跑完），输出引擎自报耗时与展开量，用于对比打包前后计算性能。
        bench_sec = 3.0

        try:
            if "--bench" in sys.argv:
                idx = sys.argv.index("--bench")
                if idx + 1 < len(sys.argv):
                    bench_sec = float(sys.argv[idx + 1])
        except ValueError:
            pass

        def run_bench() -> None:
            try:
                for i in range(1, 4):
                    t0 = time.perf_counter()
                    result = engine.compute(
                        DEMO_SNAPSHOT,
                        min_alex=1,
                        max_alex=10,
                        depth=40,
                        max_paths=1000000,
                        threads=4,
                        time_budget_sec=bench_sec,
                    )
                    wall = time.perf_counter() - t0
                    stats = result.get("stats") or {}
                    print(
                        f"BENCH iter={i} wall={wall:.3f}s "
                        f"damage={result.get('max_damage')} "
                        f"dragons={result.get('max_dragons')} "
                        f"expansions={result.get('expansions')} "
                        f"engine_total={stats.get('总耗时(秒)')} "
                        f"exp_per_sec={stats.get('展开/秒')}"
                    )
                app.exit(0)
            except Exception as exc:  # noqa: BLE001
                print(f"BENCH FAILED: {type(exc).__name__}: {exc}")
                app.exit(1)

        QTimer.singleShot(300, run_bench)
        QTimer.singleShot(180000, app.quit)
        return app.exec_()

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
