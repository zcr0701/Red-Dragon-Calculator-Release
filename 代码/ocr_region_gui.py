import ctypes
import ctypes.wintypes
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt5.QtCore import Qt, QRect, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QLineEdit,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ocr_interface import OCRInterface
from rebuild_hand import (
    CARD_COSTS,
    format_result,
    load_card_config,
    match_name_for_parser,
    rebuild_hand_from_text,
)
from red_dragon_calculator import (
    ETC_BAND,
    beam_search_paths,
    cpp_beam_search_paths,
    cpp_symbolic_prove_paths,
    enumerate_play_paths,
    find_cpp_core,
    format_paths,
    state_from_rebuild_result,
)
from archive import (
    lookup_situation,
    remember_situation,
    update_formula,
)


if sys.platform == "win32":
    class _WINDOWPOS(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.wintypes.HWND),
            ("hwndInsertAfter", ctypes.wintypes.HWND),
            ("x", ctypes.c_int),
            ("y", ctypes.c_int),
            ("cx", ctypes.c_int),
            ("cy", ctypes.c_int),
            ("flags", ctypes.c_uint),
        ]

    _WM_WINDOWPOSCHANGING = 0x0046
    _SWP_NOMOVE = 0x0002


class ScreenClampMixin:
    """拖动窗口贴住屏幕边界（Windows 原生消息级钳制）。

    在 WM_WINDOWPOSCHANGING 里直接改写窗口目标位置，系统不会把窗口移出屏幕，
    避免在 moveEvent 里再 move() 与系统拖动互相拉扯导致的闪烁抽搐。
    """

    def _window_borders(self, hwnd):
        """返回窗口四边隐形边框宽度 (left, top, right, bottom)。

        Windows 可缩放窗口四周有透明缩放边框（约 7px），WM_WINDOWPOSCHANGING 里的
        cx/cy 含这些边框；按“可见区域”钳制才能真正贴边。
        用 DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS) 直接取可见边框矩形，
        避免把标题栏高度误当成上边框。
        """
        cached = getattr(self, "_border_cache", None)

        if cached is not None and cached[0] == hwnd:
            return cached[1]

        user32 = ctypes.windll.user32
        frame = ctypes.wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(frame)):
            return (0, 0, 0, 0)

        visible = None

        try:
            dwmapi = ctypes.windll.dwmapi
            visible_rect = ctypes.wintypes.RECT()

            if dwmapi.DwmGetWindowAttribute(
                hwnd, 9, ctypes.byref(visible_rect), ctypes.sizeof(visible_rect)
            ) == 0:
                visible = visible_rect
        except Exception:
            visible = None

        if visible is not None and (visible.left or visible.top or visible.right or visible.bottom):
            borders = (
                visible.left - frame.left,
                visible.top - frame.top,
                frame.right - visible.right,
                frame.bottom - visible.bottom,
            )
        else:
            # 兜底：左/右/底用客户端区域差值，顶部用 1px 缩放边框
            client = ctypes.wintypes.RECT()
            pt = ctypes.wintypes.POINT(0, 0)

            if (
                not user32.GetClientRect(hwnd, ctypes.byref(client))
                or not user32.ClientToScreen(hwnd, ctypes.byref(pt))
            ):
                return (0, 0, 0, 0)

            borders = (
                pt.x - frame.left,
                1,
                frame.right - (pt.x + client.right),
                frame.bottom - (pt.y + client.bottom),
            )

        self._border_cache = (hwnd, borders)
        return borders

    def nativeEvent(self, eventType, message):
        if sys.platform == "win32" and eventType == b"windows_generic_MSG":
            self._native_clamp_ok = True
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))

                if msg.message == _WM_WINDOWPOSCHANGING:
                    wp = _WINDOWPOS.from_address(int(msg.lParam))

                    if not (wp.flags & _SWP_NOMOVE):
                        bl, bt, br, bb = self._window_borders(wp.hwnd)
                        # 用整个屏幕范围钳制（含任务栏区域），贴边无空隙
                        screen = QApplication.primaryScreen().geometry()
                        min_x = screen.x() - bl
                        min_y = screen.y() - bt
                        max_x = screen.x() + screen.width() - wp.cx + br
                        max_y = screen.y() + screen.height() - wp.cy + bb
                        new_x = min(max(wp.x, min_x), max_x)
                        new_y = min(max(wp.y, min_y), max_y)

                        if new_x != wp.x or new_y != wp.y:
                            wp.x = new_x
                            wp.y = new_y
            except Exception:
                pass
        return super().nativeEvent(eventType, message)


SECTION_NAME_RE = re.compile(r"^\s*(当前效果|牌库中|手牌中|战场|其他)\s*[（(]?\s*\d*\s*[）)]?\s*$")
COST_ONLY_RE = re.compile(r"^\s*(\d+)\s*费?\s*$")
COMMA_ZONE_RE = re.compile(r"^\s*(\d+)\s*[,，、]\s*(\d+)\s*血?\s*(.+)$")
STAR_ONLY_RE = re.compile(r"^[*★☆＊]+\s*$")
STAR_COST_RE = re.compile(r"^[*★☆＊]+\s*(.+)$")


def parse_mana_ratio(text: str) -> Optional[Tuple[int, int]]:
    """从第二个 OCR 框的文本里解析 法力/水晶，格式 A/B（A=法力，B=水晶，即炉石的水晶图标 当前法力/水晶上限）。

    兼容 OCR 常见变形：全角数字、分隔符被识别成 / ／ ╱ | ｜ . · ： , 或空格、
    数字被拆成两行（“3\\n3”）、以及“水晶3 / 法力3”带关键词写法。
    返回 (水晶, 法力)，供 OCRWorker 直接使用。
    取文本中最靠前且取值在合理区间（0~20）的一对数字。
    """
    if not text:
        return None

    norm = text.translate(
        str.maketrans(
            "０１２３４５６７８９／｜，。：",
            "0123456789/|,.:",
        )
    )

    # 带关键词：水晶 N ... 法力 M
    crystal_kw = re.search(r"水晶\s*(\d+)", norm)
    mana_kw = re.search(r"法力\s*(\d+)", norm)

    if crystal_kw and mana_kw:
        crystals, mana = int(crystal_kw.group(1)), int(mana_kw.group(1))

        if 0 <= crystals <= 20 and 0 <= mana <= 20:
            return crystals, mana

    patterns = (
        r"(\d+)\s*[/╱\\|｜]\s*(\d+)",
        r"(\d+)\s*[:：]\s*(\d+)",
        r"(\d+)\s*[.．·•・]\s*(\d+)",
        r"(\d+)\s*[,，]\s*(\d+)",
        r"(\d+)\s*\n\s*(\d+)",
        r"(\d+)\s+(\d+)",
    )
    best = None

    for pattern in patterns:
        for match in re.finditer(pattern, norm):
            # 左数=法力，右数=水晶（炉石水晶图标 当前法力/水晶上限）
            mana, crystals = int(match.group(1)), int(match.group(2))

            if not (0 <= crystals <= 20 and 0 <= mana <= 20):
                continue

            if best is None or match.start() < best[0]:
                best = (match.start(), crystals, mana)

    if best is None:
        return None

    return best[1], best[2]

_MANUAL_CARD_CONFIGS, _MANUAL_MIN_COMMON_CHARS = load_card_config()


def _fuzzy_default_cost(name: str) -> Optional[int]:
    matched_name, _ = match_name_for_parser(
        name,
        _MANUAL_CARD_CONFIGS,
        _MANUAL_MIN_COMMON_CHARS,
    )
    return CARD_COSTS.get(matched_name)


def parse_manual_zone_lines(text: str) -> Tuple[List[Tuple[Optional[int], str, Optional[int]]], List[str]]:
    """把手动输入区的一行行文字转成 [(cost, name, health)]。

    支持格式：
    - “4 鲨鱼之灵”（费用 卡名）；
    - “4 鲨鱼之灵 3”（费用 卡名 血量，随从栏）；
    - “4,3 鲨鱼之灵”（费用,血量 卡名，兼容旧写法）；
    - “* 殒命暗影”（* 表示无费用特殊卡，如殒命暗影）；
    - 纯卡名（自动取卡库默认费用，支持简称/错字模糊匹配，如 刀油 -> 斯卡布斯·刀油）；
    - OCR 式两行一组（“3” 换行 “晦鳞巢母”）。
    """
    entries = []
    pending_cost = None
    warnings = []

    for raw in text.splitlines():
        line = raw.strip()

        if not line or SECTION_NAME_RE.match(line):
            continue

        cost_only = COST_ONLY_RE.match(line)

        if cost_only:
            pending_cost = int(cost_only.group(1))
            continue

        if STAR_ONLY_RE.match(line):
            continue

        comma_match = COMMA_ZONE_RE.match(line)

        if comma_match:
            entries.append(
                (
                    int(comma_match.group(1)),
                    comma_match.group(3).strip(),
                    int(comma_match.group(2)),
                )
            )
            pending_cost = None
            continue

        star_cost = STAR_COST_RE.match(line)

        if star_cost:
            entries.append((None, star_cost.group(1).strip(), None))
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

            entries.append((cost, " ".join(name_parts), health))
            pending_cost = None
            continue

        cost = pending_cost if pending_cost is not None else _fuzzy_default_cost(line)

        if cost is None:
            matched_name, matched_config = match_name_for_parser(
                line,
                _MANUAL_CARD_CONFIGS,
                _MANUAL_MIN_COMMON_CHARS,
            )

            if matched_config is not None and matched_config.no_cost:
                entries.append((None, line, None))
                pending_cost = None
                continue

            warnings.append(f"缺少费用且卡库无默认费用：{line}（该行已跳过）")
            pending_cost = None
            continue

        entries.append((cost, line, None))
        pending_cost = None

    return entries, warnings


def parse_manual_effect_lines(text: str) -> Tuple[List[Tuple[str, int]], List[str]]:
    """状态栏格式：每行“中文名 数量”，如“狐人老千 2”（数量 = 叠加层数）。"""
    entries = []
    warnings = []

    for raw in text.splitlines():
        line = raw.strip()

        if not line or SECTION_NAME_RE.match(line):
            continue

        tokens = line.split()
        layers = 1

        if len(tokens) >= 2 and tokens[-1].isdigit():
            layers = max(1, int(tokens[-1]))
            line = " ".join(tokens[:-1])

        entries.append((line, layers))

    return entries, warnings


def build_manual_section_text(
    hand_text: str,
    board_text: str,
    effect_text: str,
) -> Tuple[str, List[str], List[Optional[int]]]:
    """把手动输入区拼成 rebuild_hand_from_text 认识的区段文本。

    返回 (区段文本, 警告, 随从血量列表)：
    随从血量列表与战场条目一一对应，调用方解析后可直接写回结果。
    """
    hand_entries, hand_warnings = parse_manual_zone_lines(hand_text)
    board_entries, board_warnings = parse_manual_zone_lines(board_text)
    effect_entries, effect_warnings = parse_manual_effect_lines(effect_text)
    lines = []

    def append_zone(title: str, entries):
        if not entries:
            return

        lines.append(f"{title}({len(entries)})")

        for cost, name, _health in entries:
            if cost is None:
                lines.append(name)
            else:
                lines.append(str(cost))
                lines.append(name)

    append_zone("手牌中", hand_entries)
    append_zone("战场", board_entries)

    if effect_entries:
        lines.append(f"当前效果({len(effect_entries)})")

        for name, layers in effect_entries:
            lines.append(name)

            if layers > 1:
                lines.append(str(layers))

    # 解析器用“其他(x)”作为最后一个区段的结束标记，加一个空区避免边界警告。
    lines.append("其他(0)")

    board_healths = [health for _cost, _name, health in board_entries]
    return "\n".join(lines), hand_warnings + board_warnings + effect_warnings, board_healths


class OCRWorker(QThread):
    result_signal = pyqtSignal(str, str, object, object, object, str)
    error_signal = pyqtSignal(str)

    def __init__(self, get_box_func, get_mana_box_func, ocr_api):
        super().__init__()
        self.get_box_func = get_box_func
        self.get_mana_box_func = get_mana_box_func
        self.ocr_api = ocr_api
        self.running = True

    def run(self):
        last_crystals = None
        last_mana = None

        while self.running:
            try:
                box = self.get_box_func()

                if box is None:
                    time.sleep(0.1)
                    continue

                ocr_text = self.ocr_api.recognize_box(box)
                hand_result = rebuild_hand_from_text(ocr_text)
                hand_text = format_result(hand_result)
                crystals, mana = last_crystals, last_mana
                mana_box = self.get_mana_box_func()
                mana_text = ""

                if mana_box is not None:
                    mana_text = self.ocr_api.recognize_box(mana_box)
                    parsed = parse_mana_ratio(mana_text)

                    if parsed is not None:
                        crystals, mana = parsed
                        last_crystals, last_mana = crystals, mana

                self.result_signal.emit(ocr_text, hand_text, hand_result, crystals, mana, mana_text)
            except Exception as e:
                msg = f"OCR错误: {e}"
                print(msg)
                self.error_signal.emit(msg)

            time.sleep(0.1)

    def stop(self):
        self.running = False


class CalculationWorker(QThread):
    progress_signal = pyqtSignal(str)
    partial_result_signal = pyqtSignal(str)
    result_signal = pyqtSignal(str)
    best_result_signal = pyqtSignal(bool, str)
    error_signal = pyqtSignal(str)

    def __init__(self, rebuild_result, mana_crystals, mana, max_depth=100, max_paths=1000000, max_alex_count=10, min_alex_count=1, deadly_shadow_hand_indexes=None, etc_band_remaining=None, beam_mode=False, operator_depth=5, beam_width=3000, beam_depth=25):
        super().__init__()
        self.rebuild_result = rebuild_result
        self.mana_crystals = mana_crystals
        self.mana = mana
        self.max_depth = max_depth
        self.max_paths = max_paths
        self.max_alex_count = max_alex_count
        self.min_alex_count = min_alex_count
        self.deadly_shadow_hand_indexes = deadly_shadow_hand_indexes or []
        self.etc_band_remaining = list(etc_band_remaining) if etc_band_remaining is not None else ETC_BAND[:]
        self.beam_mode = bool(beam_mode)
        self.operator_depth = int(operator_depth)
        self.beam_width = int(beam_width)
        self.beam_depth = int(beam_depth)
        self.stats_title = "束搜索统计：" if self.beam_mode else "符号链条证明统计："

    def format_initial_state_note(self, state):
        def card_cost_health_text(card):
            cost = card.current_cost()
            cost_text = "*" if cost is None else str(cost)
            health_text = f"{card.health}血" if getattr(card, "card_type", "") == "minion" and getattr(card, "health", None) is not None else ""

            if health_text:
                return f"{cost_text}费,{health_text}"

            return f"{cost_text}费"

        if self.beam_mode:
            search_mode_text = "beam束搜索"
        else:
            search_mode_text = "双向符号链证明"
        lines = [
            f"计算参数：{self.mana_crystals}水晶 / {self.mana}法力 / 链条步数上限 {self.max_depth} / 路径上限 {self.max_paths} / 搜索龙数上限 {self.max_alex_count} / 搜索龙数下限 {self.min_alex_count} / 搜索方式：{search_mode_text}",
        ]
        marked_cards = []

        for index, card in enumerate(state.hand, start=1):
            if card.is_deadly_shadow:
                marked_cards.append(f"第{index}张：{card.name}[殒命暗影]")

        if marked_cards:
            lines.append("殒命暗影标记已生效：" + "；".join(marked_cards))
        else:
            lines.append("殒命暗影标记：未生效（没有手牌被标记为殒命暗影）")

        if getattr(state, "log", None):
            initial_effects = [item for item in state.log if item.startswith("初始当前效果：")]
            if initial_effects:
                lines.append("当前效果已生效：" + "；".join(item.replace("初始当前效果：", "") for item in initial_effects))
            else:
                lines.append("当前效果：无")
        else:
            lines.append("当前效果：无")

        lines.append(
            "初始手牌："
            + "，".join(
                f"{index}.{card.name}{'[殒命暗影]' if card.is_deadly_shadow else ''}[{card_cost_health_text(card)}]"
                for index, card in enumerate(state.hand, start=1)
            )
        )
        lines.append(
            "初始战场："
            + ("，".join(f"{index}.{card.name}[{card_cost_health_text(card)}]" for index, card in enumerate(state.board, start=1)) or "空")
        )
        lines.append(
            "初始奥秘："
            + ("，".join(f"{index}.{card.name}[{card_cost_health_text(card)}]" for index, card in enumerate(state.secrets, start=1)) or "空")
        )
        lines.append("初始武器：" + (f"{state.weapon.name}[{card_cost_health_text(state.weapon)}]" if state.weapon else "空"))
        lines.append("牛头人酋长剩余卡池：" + ("，".join(state.etc_band_remaining) if state.etc_band_remaining else "空"))
        lines.append("")
        return "\n".join(lines)

    def params_line(self) -> str:
        return (
            f"链条步数上限{self.max_depth} | 路径上限{self.max_paths} | "
            f"搜索龙数 {self.min_alex_count}~{self.max_alex_count} | "
            f"{'beam束搜索' if self.beam_mode else '双向符号链证明'}"
        )

    def format_cached_paths_minimal(self, entry) -> str:
        lines = ["命中存档："]
        paths = entry.get("路径", {})

        if not paths:
            lines.append("（该局面此前未搜到红龙路径）")
            return "\n".join(lines)

        best = str(max(int(count) for count in paths))
        lines.append(f"最多龙数：{best}龙")

        for count_text in sorted(paths, key=lambda item: -int(item)):
            for path_text in paths[count_text]:
                lines.append("  " + path_text)

        return "\n".join(lines)

    def format_prune_stats(self, prune_stats):
        if not prune_stats:
            return f"{self.stats_title}暂无记录\n"

        meta_keys = {"当前证明目标", "已证明龙数"}
        regular_stats = {
            key: value
            for key, value in prune_stats.items()
            if key not in meta_keys and not str(key).startswith("当前搜索 ") and isinstance(value, int)
            and not str(key).startswith("链条失败详情：")
        }
        failure_details = {
            key: value
            for key, value in prune_stats.items()
            if isinstance(value, int) and str(key).startswith("链条失败详情：")
        }
        lines = [self.stats_title]

        if "已证明龙数" in prune_stats:
            lines.append(f"- 已证明龙数：{prune_stats['已证明龙数']}")
        elif "当前证明目标" in prune_stats:
            lines.append(f"- 当前证明目标：{prune_stats['当前证明目标']}龙")

        for key, value in prune_stats.items():
            if str(key).startswith("当前搜索 "):
                lines.append(f"- {key}：{value}")

        if "符号候选链条数" in regular_stats:
            lines.append(f"- 符号候选链条数：{regular_stats.pop('符号候选链条数')}")

        if "符号链条DP复用" in regular_stats:
            lines.append(f"- 符号链条DP复用：{regular_stats.pop('符号链条DP复用')}")

        for reason, count in sorted(regular_stats.items(), key=lambda item: -item[1])[:30]:
            lines.append(f"- {reason}：{count}")

        if failure_details:
            lines.append("- 关键失败现场（最多显示20条）：")

            for reason, count in sorted(failure_details.items(), key=lambda item: -item[1])[:20]:
                lines.append(f"- {reason.replace('链条失败详情：', '')}：{count}")

        lines.append("")
        return "\n".join(lines)

    def export_all_paths_document(self, state, states, prune_stats, stop_note, limit_note):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        logs_dir = Path(__file__).resolve().parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        output_path = logs_dir / f"red_dragon_all_paths_{timestamp}.txt"
        header = (
            # 完整路径文档附带场面数据（手牌/战场/奥秘/武器/牛池/殒命）
            self.format_initial_state_note(state)
            + self.format_prune_stats(prune_stats)
            + stop_note
            + limit_note
            + "以下为全部路径：\n\n"
        )
        body = format_paths(
            states,
            limit=max(1, len(states)),
            non_alex_limit=max(1, len(states))
        )

        output_path.write_text(header + body, encoding="utf-8")
        return output_path

    def _format_best(self, states) -> str:
        """筛选最高伤害路径，只保留一条（伤害相同的只取第一条）。"""
        best_damage = max((item.alex_damage for item in states), default=0)
        best = [item for item in states if item.alex_damage == best_damage]

        if not best:
            return "（未搜到红龙路径）"

        item = best[0]
        return (
            f"1. {' -> '.join(item.path)}"
            f" | 龙数：{item.alex_play_count} | 伤害：{item.alex_damage}点"
            f" | 剩余法力：{item.mana}"
        )

    def format_situation_note(self, state) -> str:
        """返回场面/状态数据（手牌/战场/奥秘/武器/牛池/殒命/当前效果），不含参数行。"""
        full = self.format_initial_state_note(state)
        lines = full.splitlines()
        return "\n".join(lines[1:]) if lines else ""

    def _record_beam_improvement(self, state, bidir_states, beam_states):
        """beam 束搜索结果比双向链更好时，记录到本地并附上场面数据。"""
        try:
            bidir_best = max(bidir_states, key=lambda item: item.alex_damage, default=None)
            beam_best = max(beam_states, key=lambda item: item.alex_damage, default=None)

            if beam_best is None or beam_best.alex_damage <= (bidir_best.alex_damage if bidir_best else 0):
                return

            def card_text(card):
                cost = card.current_cost()
                return f"{card.name}[{'*' if cost is None else cost}费]"

            entry = {
                "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "水晶": state.mana_crystals,
                "法力": state.mana,
                "手牌": [card_text(card) for card in state.hand],
                "战场": [card_text(card) for card in state.board],
                "奥秘": [card_text(card) for card in state.secrets],
                "武器": card_text(state.weapon) if state.weapon else None,
                "牛池": list(state.etc_band_remaining),
                "殒命暗影": [card_text(card) for card in state.hand if card.is_deadly_shadow],
                "双向链最高": (
                    {"伤害": bidir_best.alex_damage, "龙数": bidir_best.alex_play_count,
                     "路径": list(bidir_best.path)}
                    if bidir_best else None
                ),
                "beam最高": (
                    {"伤害": beam_best.alex_damage, "龙数": beam_best.alex_play_count,
                     "路径": list(beam_best.path)}
                ),
            }
            logs_dir = Path(__file__).resolve().parent / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            record_path = logs_dir / "beam_improvements.json"
            records = []

            if record_path.exists():
                try:
                    loaded = json.loads(record_path.read_text(encoding="utf-8"))
                    records = loaded if isinstance(loaded, list) else []
                except Exception:
                    records = []

            records.append(entry)
            record_path.write_text(
                json.dumps(records, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def run(self):
        try:
            state = state_from_rebuild_result(
                result=self.rebuild_result,
                mana_crystals=self.mana_crystals,
                mana=self.mana,
                deadly_shadow_hand_indexes=self.deadly_shadow_hand_indexes,
                etc_band_remaining=self.etc_band_remaining
            )

            cached = lookup_situation(state)

            if cached:
                # 立即展示缓存，但不返回：后台继续完整计算，搜出更高龙数就更新存档
                self.partial_result_signal.emit(
                    self.params_line()
                    + "\n\n"
                    + self.format_cached_paths_minimal(cached)
                    + "\n\n（以上为缓存结果，正在后台重新计算；若搜出更高龙数的新路径，会自动更新存档并展示新结果）"
                )

            def on_progress(done_count, stack_count, pruned_count=0):
                if self.beam_mode:
                    self.progress_signal.emit(
                        f"正在束搜索路径：已保留 {done_count} 个状态，当前束 {stack_count} 个状态，累计展开 {pruned_count} 次..."
                    )
                    return

                self.progress_signal.emit(
                    f"正在符号化证明路径：已找到 {done_count} 条目标路径，剩余候选链 {stack_count} 条，复用/失败记录 {pruned_count} 条..."
                )

            def on_found(found_states, target_alex_count):
                if isinstance(found_states, int):
                    # C++ 核心实时回调：(龙数, 当前最高伤害)
                    self.partial_result_signal.emit(
                        self.params_line()
                        + f"\n\n已即时发现 {found_states} 龙路径（当前最高伤害 {target_alex_count} 点），仍在计算；可点击“中止计算”立即导出当前全部结果。\n\n"
                    )
                    return

                self.partial_result_signal.emit(
                    self.params_line()
                    + f"\n\n已即时发现 {len(found_states)} 条 {target_alex_count} 龙路径，仍在继续计算；可点击“中止计算”立即导出当前全部结果。\n\n"
                    + format_paths(found_states, limit=300)
                )

            prune_stats = {}
            search_started = time.time()
            cpp_exe = find_cpp_core()
            if self.beam_mode:
                # beam 模式先跑双向链瞬间出结果，再跑 beam 束搜索
                if cpp_exe is not None:
                    bidir_states, _bidir_stats = cpp_symbolic_prove_paths(
                        initial_state=state,
                        max_depth=self.max_depth,
                        max_paths=self.max_paths,
                        max_alex_count=self.max_alex_count,
                        min_alex_count=self.min_alex_count,
                        forward_depth=self.operator_depth,
                        exe_path=cpp_exe,
                        progress_callback=on_progress,
                        found_callback=on_found,
                        should_stop=self.isInterruptionRequested,
                    )
                else:
                    bidir_states = enumerate_play_paths(
                        initial_state=state,
                        max_depth=self.max_depth,
                        max_paths=self.max_paths,
                        max_alex_count=self.max_alex_count,
                        min_alex_count=self.min_alex_count,
                        progress_callback=on_progress,
                        found_callback=on_found,
                        prune_stats={},
                        should_stop=self.isInterruptionRequested,
                        forward_mining=False,
                        forward_depth=self.operator_depth,
                    )
                self.best_result_signal.emit(False, self._format_best(bidir_states))
                self.partial_result_signal.emit(
                    self.params_line()
                    + "\n\n【双向链阶段结果】\n"
                    + format_paths(bidir_states, limit=300)
                )

                if cpp_exe is not None:
                    states = cpp_beam_search_paths(
                        initial_state=state,
                        max_depth=self.beam_depth,
                        max_paths=self.max_paths,
                        max_alex_count=self.max_alex_count,
                        min_alex_count=self.min_alex_count,
                        beam_width=self.beam_width,
                        exe_path=cpp_exe,
                        progress_callback=on_progress,
                        found_callback=on_found,
                        should_stop=self.isInterruptionRequested,
                    )
                    prune_stats["束搜索束宽"] = self.beam_width
                    prune_stats["束搜索深度"] = self.beam_depth
                    prune_stats["计算核心"] = "C++"
                else:
                    states = beam_search_paths(
                        initial_state=state,
                        max_depth=self.beam_depth,
                        max_paths=self.max_paths,
                        max_alex_count=self.max_alex_count,
                        min_alex_count=self.min_alex_count,
                        beam_width=self.beam_width,
                        progress_callback=on_progress,
                        found_callback=on_found,
                        prune_stats=prune_stats,
                        should_stop=self.isInterruptionRequested
                    )
                # beam 比双向链更好时记录到本地（附场面数据）
                self._record_beam_improvement(state, bidir_states, states)
            else:
                if cpp_exe is not None:
                    states, cpp_stats = cpp_symbolic_prove_paths(
                        initial_state=state,
                        max_depth=self.max_depth,
                        max_paths=self.max_paths,
                        max_alex_count=self.max_alex_count,
                        min_alex_count=self.min_alex_count,
                        forward_depth=self.operator_depth,
                        exe_path=cpp_exe,
                        progress_callback=on_progress,
                        found_callback=on_found,
                        should_stop=self.isInterruptionRequested,
                    )
                    prune_stats.update(cpp_stats)
                    prune_stats["计算核心"] = "C++"
                else:
                    states = enumerate_play_paths(
                        initial_state=state,
                        max_depth=self.max_depth,
                        max_paths=self.max_paths,
                        max_alex_count=self.max_alex_count,
                        min_alex_count=self.min_alex_count,
                        progress_callback=on_progress,
                        found_callback=on_found,
                        prune_stats=prune_stats,
                        should_stop=self.isInterruptionRequested,
                        forward_mining=False,
                        forward_depth=self.operator_depth,
                    )

            elapsed_seconds = time.time() - search_started

            remember_situation(
                state,
                states,
                params={
                    "搜索方式": "beam束搜索" if self.beam_mode else "反向符号链证明",
                    "搜索龙数上限": self.max_alex_count,
                    "搜索龙数下限": self.min_alex_count,
                    "路径上限": self.max_paths,
                    "链条步数上限": self.max_depth,
                    "计算总耗时(秒)": round(elapsed_seconds, 1),
                },
            )
            update_formula(state, states)
            limit_note = ""

            if len(states) >= self.max_paths:
                limit_note = f"注意：本次结果达到路径上限 {self.max_paths}，可能还没有完整算完；可继续调高路径上限。\n"

            stop_note = ""

            if self.isInterruptionRequested():
                stop_note = "已中止计算，以下为中止前已经算出的部分结果。\n"

            export_path = self.export_all_paths_document(
                state=state,
                states=states,
                prune_stats=prune_stats,
                stop_note=stop_note,
                limit_note=limit_note
            )

            # 筛选最高伤害路径（只保留一条），单独发给界面放进对应框
            self.best_result_signal.emit(self.beam_mode, self._format_best(states))

            self.result_signal.emit(
                self.params_line()
                + "\n\n"
                + self.format_situation_note(state)
                + "\n\n"
                + (stop_note + limit_note if (stop_note or limit_note) else "")
                + format_paths(states, limit=300)
            )
        except Exception as e:
            self.error_signal.emit(f"计算错误: {e}")


class SelectionOverlay(QWidget):
    selected_signal = pyqtSignal(tuple)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("OCR区域选择")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(QApplication.primaryScreen().geometry())
        self.start = None
        self.end = None
        self.close_on_select = False

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start = event.pos()
            self.end = self.start
            self.update()

    def mouseMoveEvent(self, event):
        if self.start:
            self.end = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.start is None:
            return

        self.end = event.pos()
        x1 = min(self.start.x(), self.end.x())
        y1 = min(self.start.y(), self.end.y())
        x2 = max(self.start.x(), self.end.x())
        y2 = max(self.start.y(), self.end.y())

        self.start = None
        self.end = None

        if x2 - x1 > 20 and y2 - y1 > 20:
            box = (x1, y1, x2, y2)
            print("选择区域:", box)
            self.selected_signal.emit(box)

        # 连续选框模式：框完第一个框不退出，继续框第二个（水晶/法力）
        if self.close_on_select:
            self.hide()

        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 80))

        if self.start and self.end:
            selected_rect = QRect(self.start, self.end).normalized()
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(selected_rect, Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setPen(QPen(Qt.red, 3))
            painter.drawRect(selected_rect)


class QuickPanel(ScreenClampMixin, QDialog):
    """主操作弹窗：识别 / 计算 / 牛池勾选 / 殒命标记 / 手牌·战场·状态显示 / OCR原始文本 / 计算结果。

    引擎与设置都在 MainWindow（后台设置窗口），弹窗只负责操作和展示。
    """

    slot_style = """
    QLabel{
        background:white;
        color:#333;
        font-size:13px;
        font-family:微软雅黑;
        border:1px solid #ddd;
        border-radius:8px;
        padding:2px;
    }
    """
    toggle_style = """
    QPushButton{
        background:#f0f0f0;
        color:#333;
        font-size:15px;
        font-family:微软雅黑;
        border:1px solid #ccc;
        border-radius:6px;
        padding:8px 10px;
        text-align:left;
        min-height:34px;
    }
    QPushButton:checked{ background:#e8e8ff; }
    """
    ocr_raw_style = """
    QTextEdit{
        color:#444;
        background:#fafafa;
        font-size:13px;
        font-family:微软雅黑;
        padding:4px;
        border:1px solid #ddd;
        border-radius:6px;
    }
    """
    calc_text_style = """
    QTextEdit{
        color:red;
        background:white;
        font-size:22px;
        font-family:微软雅黑;
        padding:10px;
        border:1px solid #ddd;
        border-radius:8px;
    }
    """

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setWindowTitle("红龙贼计算器")
        self._native_clamp_ok = False
        flags = self.windowFlags() | Qt.WindowStaysOnTopHint
        flags &= ~Qt.WindowContextHelpButtonHint  # 去掉标题栏的 “?” 帮助按钮
        self.setWindowFlags(flags)
        self.setMinimumWidth(520)

        # ---- 顶部操作按钮：识别为开始/停止切换按钮，设置按钮弹出后台设置窗口 ----
        self.startButton = QPushButton("开始识别")
        self.calculateButton = QPushButton("开始计算")
        self.settingsButton = QPushButton("设置")
        self.startButton.setEnabled(False)
        self.calculateButton.setEnabled(False)

        self.startButton.clicked.connect(self.on_start_toggle)
        self.calculateButton.clicked.connect(self.on_calc_toggle)
        self.settingsButton.clicked.connect(owner.toggle_settings_window)

        button_layout = QHBoxLayout()
        for btn in (self.startButton, self.calculateButton, self.settingsButton):
            button_layout.addWidget(btn)

        # ---- 殒命暗影位置 ----
        self.deadlyShadowCheck = QCheckBox("殒命暗影位置")
        self.deadlyShadowInput = QLineEdit()
        self.deadlyShadowInput.setPlaceholderText("手牌序号")
        # 宽度刚好容纳“手牌序号”四个字
        self.deadlyShadowInput.setFixedWidth(
            QFontMetrics(self.deadlyShadowInput.font()).horizontalAdvance("手牌序号") + 14
        )
        self.deadlyShadowInput.setEnabled(False)
        self.deadlyShadowCheck.toggled.connect(self.deadlyShadowInput.setEnabled)

        # ---- 牛头人酋长剩余卡池：最多勾选 3 张 ----
        self.etcBandChecks: List[Tuple[str, QCheckBox]] = []
        self.etcBandOptions = [
            ("舞动全场（ft.迦罗娜）", "舞动全场（ft.迦罗娜）"),
            ("幻觉药水", "幻觉药水"),
            ("生命的缚誓者阿莱克丝塔萨", "红龙"),
            ("晦鳞巢母", "晦鳞巢母"),
            ("赤烟·腾武", "赤烟·腾武"),
        ]

        def make_etc_toggler(box):
            def handler(checked):
                if checked and sum(
                    1 for _name, other in self.etcBandChecks if other.isChecked()
                ) > 3:
                    box.blockSignals(True)
                    box.setChecked(False)
                    box.blockSignals(False)
            return handler

        for card_name, label in self.etcBandOptions:
            box = QCheckBox(label)
            box.setChecked(card_name in {"舞动全场（ft.迦罗娜）", "幻觉药水", "生命的缚誓者阿莱克丝塔萨"})
            box.toggled.connect(make_etc_toggler(box))
            self.etcBandChecks.append((card_name, box))

        # 牛头人卡池做成折叠区块（默认展开）
        self.etcToggle = QPushButton("▸ 牛头人剩余卡池")
        self.etcToggle.setCheckable(True)
        self.etcToggle.setChecked(True)
        self.etcToggle.setStyleSheet(self.toggle_style)
        self.etcToggle.setMinimumHeight(38)
        self.etcPanel = QWidget()
        etc_panel_layout = QVBoxLayout()
        etc_panel_layout.setContentsMargins(4, 2, 4, 2)
        etc_checkbox_row = QHBoxLayout()
        for _card_name, box in self.etcBandChecks:
            etc_checkbox_row.addWidget(box)
        etc_checkbox_row.addStretch()
        etc_panel_layout.addLayout(etc_checkbox_row)
        etc_hint = QLabel("取消勾选表示这张已被选走")
        etc_hint.setStyleSheet("font-size:12px;color:#888;")
        etc_panel_layout.addWidget(etc_hint)
        self.etcPanel.setLayout(etc_panel_layout)
        self.etcToggle.toggled.connect(self.etcPanel.setVisible)

        # ---- 手牌折叠区块（10 格固定，默认展开） ----
        hand_section = QWidget()
        hand_section_layout = QVBoxLayout()
        hand_section_layout.setContentsMargins(0, 0, 0, 0)
        self.handToggle = QPushButton("▸ 手牌（10 格固定）")
        self.handToggle.setCheckable(True)
        self.handToggle.setChecked(True)
        self.handToggle.setStyleSheet(self.toggle_style)
        self.handToggle.setMinimumHeight(38)
        self.handPanel = QWidget()
        hand_panel_layout = QVBoxLayout()
        hand_panel_layout.setContentsMargins(4, 2, 4, 2)
        hand_grid = QGridLayout()
        hand_grid.setSpacing(2)
        self.hand_slots = []
        for index in range(10):
            slot = QLabel("空")
            slot.setAlignment(Qt.AlignCenter)
            slot.setStyleSheet(self.slot_style)
            slot.setMinimumHeight(28)
            self.hand_slots.append(slot)
            hand_grid.addWidget(slot, index // 5, index % 5)
        hand_panel_layout.addLayout(hand_grid)
        self.handPanel.setLayout(hand_panel_layout)
        self.handToggle.toggled.connect(self.handPanel.setVisible)
        hand_section_layout.addWidget(self.handToggle)
        hand_section_layout.addWidget(self.handPanel)
        hand_section.setLayout(hand_section_layout)

        # ---- 战场折叠区块（7 格固定，默认收起） ----
        board_section = QWidget()
        board_section_layout = QVBoxLayout()
        board_section_layout.setContentsMargins(0, 0, 0, 0)
        self.boardToggle = QPushButton("▸ 战场（7 格固定）")
        self.boardToggle.setCheckable(True)
        self.boardToggle.setChecked(False)
        self.boardToggle.setStyleSheet(self.toggle_style)
        self.boardToggle.setMinimumHeight(38)
        self.boardPanel = QWidget()
        board_panel_layout = QVBoxLayout()
        board_panel_layout.setContentsMargins(4, 2, 4, 2)
        board_grid = QGridLayout()
        board_grid.setSpacing(2)
        self.board_slots = []
        for index in range(7):
            slot = QLabel("空")
            slot.setAlignment(Qt.AlignCenter)
            slot.setStyleSheet(self.slot_style)
            slot.setMinimumHeight(28)
            self.board_slots.append(slot)
            board_grid.addWidget(slot, index // 4, index % 4)
        board_panel_layout.addLayout(board_grid)
        self.boardPanel.setLayout(board_panel_layout)
        self.boardToggle.toggled.connect(self.boardPanel.setVisible)
        board_section_layout.addWidget(self.boardToggle)
        board_section_layout.addWidget(self.boardPanel)
        board_section.setLayout(board_section_layout)

        # ---- 状态折叠区块 ----
        status_section = QWidget()
        status_section_layout = QVBoxLayout()
        status_section_layout.setContentsMargins(0, 0, 0, 0)
        self.statusToggle = QPushButton("▸ 状态（水晶 / 法力 / 牛池 / 殒命）")
        self.statusToggle.setCheckable(True)
        self.statusToggle.setChecked(False)
        self.statusToggle.setStyleSheet(self.toggle_style)
        self.statusToggle.setMinimumHeight(38)
        self.statusPanel = QWidget()
        status_panel_layout = QHBoxLayout()
        status_panel_layout.setContentsMargins(4, 2, 4, 2)
        self.statusSummaryLabel = QLabel("水晶 ? / 法力 ?　|　牛池：-　|　殒命：-")
        self.statusSummaryLabel.setStyleSheet("font-size:13px;color:#333;")
        status_panel_layout.addWidget(self.statusSummaryLabel)
        status_panel_layout.addStretch()
        self.statusPanel.setLayout(status_panel_layout)
        self.statusToggle.toggled.connect(self.statusPanel.setVisible)
        status_section_layout.addWidget(self.statusToggle)
        status_section_layout.addWidget(self.statusPanel)
        status_section.setLayout(status_section_layout)

        # ---- 计算进度小字 ----
        self.progressLabel = QLabel("")
        self.progressLabel.setStyleSheet("font-size:12px;color:#666;padding:2px 4px;")

        # ---- 三个结果框（自动适应内容高度，保证刚好能看完全） ----
        result_style = """
        QTextEdit{
            color:#333;
            background:white;
            font-size:20px;
            font-family:微软雅黑;
            padding:6px;
            border:1px solid #ddd;
            border-radius:6px;
        }
        """
        self.result_style = result_style

        def make_result_edit():
            edit = QTextEdit()
            edit.setReadOnly(True)
            edit.setStyleSheet(self.result_style)
            edit.setMinimumHeight(36)
            return edit

        self.fullText = make_result_edit()

        # 最高伤害路径：按“舞”分段成多个小框，避免人眼在长路径上重定位出错
        self.bidirRounds = QWidget()
        self.bidirRoundsLayout = QVBoxLayout()
        self.bidirRoundsLayout.setContentsMargins(0, 0, 0, 0)
        self.bidirRoundsLayout.setSpacing(4)
        self.bidirRounds.setLayout(self.bidirRoundsLayout)
        self.beamRounds = QWidget()
        self.beamRoundsLayout = QVBoxLayout()
        self.beamRoundsLayout.setContentsMargins(0, 0, 0, 0)
        self.beamRoundsLayout.setSpacing(4)
        self.beamRounds.setLayout(self.beamRoundsLayout)

        self.fullToggle, self.fullPanel, full_section = self._make_fold_section(
            "完整路径计算结果", False, self.fullText,
            refit=lambda: self._fit_edit(self.fullText, 420),
        )
        self.bidirToggle, self.bidirPanel, bidir_section = self._make_fold_section(
            "双向链计算最高伤害路径", True, self.bidirRounds,
        )
        self.beamToggle, self.beamPanel, beam_section = self._make_fold_section(
            "beam束状计算最高伤害路径", True, self.beamRounds,
        )

        # ---- 殒命位置与牛头人卡池：同一行，牛头人展开后在其下一行 ----
        actions_section = QWidget()
        actions_layout = QVBoxLayout()
        actions_layout.setContentsMargins(4, 2, 4, 2)
        actions_row = QHBoxLayout()
        # 牛头人剩余卡池按钮和殒命暗影位置紧贴在一起
        actions_row.addWidget(self.etcToggle)
        actions_row.addWidget(self.deadlyShadowCheck)
        actions_row.addWidget(self.deadlyShadowInput)
        actions_row.addStretch()
        actions_layout.addLayout(actions_row)
        actions_layout.addWidget(self.etcPanel)
        actions_section.setLayout(actions_layout)

        # ---- 主布局：折叠区块堆叠，计算结果区自动填满剩余空间 ----
        stack_layout = QVBoxLayout()
        stack_layout.setContentsMargins(0, 0, 0, 0)
        stack_layout.setSpacing(0)
        stack_layout.addWidget(hand_section)
        stack_layout.addWidget(board_section)
        stack_layout.addWidget(status_section)
        # 识别/计算/设置按钮放在状态栏下面、牛头人卡池上面
        stack_layout.addLayout(button_layout)
        stack_layout.addWidget(actions_section)
        stack_layout.addWidget(self.progressLabel)
        stack_layout.addWidget(full_section)
        stack_layout.addWidget(bidir_section)
        stack_layout.addWidget(beam_section)
        stack_layout.addStretch(1)
        stack_container = QWidget()
        stack_container.setLayout(stack_layout)

        # 内容放进滚动区：窗口尺寸固定，内容超出时滚动而不是把窗口顶大
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(stack_container)
        scroll.setFrameShape(QFrame.NoFrame)

        layout = QVBoxLayout()
        layout.addWidget(scroll)
        self.setLayout(layout)
        # 窗口高度按屏幕自适应封顶，防止折叠/展开时布局把窗口顶出屏幕
        screen = QApplication.primaryScreen().availableGeometry()
        max_height = max(480, screen.height() - 60)
        self.resize(540, min(960, max_height))

    def _make_fold_section(self, title, default_open, body_widget, refit=None):
        """生成一个折叠区块：标题按钮 + 内容控件（可选展开时回调 refit）。"""
        toggle = QPushButton("▸ " + title)
        toggle.setCheckable(True)
        toggle.setChecked(default_open)
        toggle.setStyleSheet(self.toggle_style)
        toggle.setMinimumHeight(38)
        panel = QWidget()
        lay = QVBoxLayout()
        lay.setContentsMargins(4, 2, 4, 2)
        lay.addWidget(body_widget)
        panel.setLayout(lay)

        def on_toggle(checked):
            panel.setVisible(checked)
            if checked and refit is not None:
                refit()

        toggle.toggled.connect(on_toggle)
        # 初始折叠/展开状态同步到面板（setChecked 在 connect 之前调用，不会触发信号）
        panel.setVisible(default_open)
        section = QWidget()
        slay = QVBoxLayout()
        slay.setContentsMargins(0, 0, 0, 0)
        slay.addWidget(toggle)
        slay.addWidget(panel)
        section.setLayout(slay)
        return toggle, panel, section

    def _show_rounds(self, container, best_line):
        """把一条最高伤害路径按“舞动全场”分段，每个阶段一个独立小框。"""
        while container.count():
            item = container.takeAt(0)
            widget = item.widget()

            if widget is not None:
                widget.deleteLater()

        match = re.match(
            r"^1\. (.*?) \| 龙数：(\d+) \| 伤害：(\d+)点 \| 剩余法力：(\d+)$",
            best_line.strip(),
        )

        if match is None:
            box = QTextEdit()
            box.setReadOnly(True)
            box.setStyleSheet(self.result_style)
            box.setMinimumHeight(36)
            box.setPlainText(best_line)
            container.addWidget(box)
            self._fit_edit(box, 420)
            return

        steps_text, dragons, damage, mana = match.groups()
        steps = [step.strip() for step in steps_text.split(" -> ")]
        rounds = []
        current = []

        for step in steps:
            current.append(step)

            if step.startswith("舞动全场（ft.迦罗娜）"):
                rounds.append(current)
                current = []

        if current:
            rounds.append(current)

        summary = QLabel(f"最高伤害：{damage}点 | 龙数：{dragons} | 剩余法力：{mana}")
        summary.setStyleSheet("font-size:13px;color:#333;")
        container.addWidget(summary)

        for index, round_steps in enumerate(rounds, start=1):
            box = QTextEdit()
            box.setReadOnly(True)
            box.setStyleSheet(self.result_style)
            box.setMinimumHeight(30)
            box.setPlainText(f"第{index}轮：" + " -> ".join(round_steps))
            container.addWidget(box)
            self._fit_edit(box, 220)

    def _fit_edit(self, edit, cap=520):
        """文本框自动适应内容高度（刚好能看完全，超出上限则滚动）。"""

        def fit():
            width = edit.viewport().width()
            if width <= 0:
                return
            doc = edit.document()
            doc.setTextWidth(width)
            height = int(doc.size().height()) + 12
            edit.setFixedHeight(max(36, min(height, cap)))

        QTimer.singleShot(0, fit)

    def on_start_toggle(self):
        """开始识别 / 停止识别 切换：点击开始扫描，再点停止；识别到结果后自动恢复。"""
        if self.startButton.text() == "开始识别":
            self.owner.start_ocr()
        else:
            self.owner.stop_ocr()

    def on_calc_toggle(self):
        """开始计算 / 中止计算 切换。"""
        if self.calculateButton.text() == "开始计算":
            self.owner.start_calculation()
        else:
            self.owner.cancel_calculation()

    def closeEvent(self, event):
        """关闭主弹窗 = 关闭整个程序（连设置窗口一起关）。"""
        self.owner.stop_ocr()
        if self.owner.calc_worker is not None:
            self.owner.calc_worker.requestInterruption()
            self.owner.calc_worker.wait()
        self.owner.close()
        event.accept()

    def moveEvent(self, event):
        """拖动窗口时贴住屏幕边界，不允许拖出屏幕外。"""
        super().moveEvent(event)
        if getattr(self, "_native_clamp_ok", False):
            # 原生消息钳制已生效（Windows 真实拖动），这里不再 move()，避免拉扯
            return
        if getattr(self, "_clamp_move", False):
            return
        self._clamp_move = True
        try:
            geo = self.frameGeometry()
            screen = QApplication.primaryScreen().availableGeometry()
            max_x = screen.x() + max(0, screen.width() - geo.width())
            max_y = screen.y() + max(0, screen.height() - geo.height())
            x = min(max(geo.x(), screen.x()), max_x)
            y = min(max(geo.y(), screen.y()), max_y)
            if x != geo.x() or y != geo.y():
                self.move(x, y)
        finally:
            self._clamp_move = False


class MainWindow(ScreenClampMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("红龙贼计算器 · 设置")
        self._native_clamp_ok = False
        self.setWindowFlags(Qt.WindowStaysOnTopHint)

        self.box = None
        self.mana_box = None
        self._select_target = "main"
        self.worker = None
        self.calc_worker = None
        self.latest_rebuild_result = None
        # 状态区的水晶/法力只跟随 OCR 扫描结果，不读手动输入框
        self.scan_crystals = None
        self.scan_mana = None
        # 识别自动停止：点一下开始扫描（0.1s 循环），识别到结果自动停止
        self._auto_stop = False
        self._run_got_hand = False
        self._run_got_mana = False
        self.ocr_api = OCRInterface(lang="ch")

        self.overlay = SelectionOverlay()
        self.overlay.selected_signal.connect(self.on_area_selected)

        self.statusLabel = QLabel("状态：设置窗口，操作请使用弹窗")
        self.statusLabel.setStyleSheet("font-size:14px;font-family:微软雅黑;")
        self.manualInputButton = QPushButton("手动输入")
        self.manualInputButton.clicked.connect(self.toggle_manual_input)

        self.crystalInput = QLineEdit("4")
        self.crystalInput.setFixedWidth(60)
        self.manaInput = QLineEdit("8")
        self.manaInput.setFixedWidth(60)
        self.maxDepthInput = QLineEdit("100")
        self.maxDepthInput.setFixedWidth(70)
        self.operatorDepthInput = QLineEdit("5")
        self.operatorDepthInput.setFixedWidth(50)
        self.operatorDepthInput.setToolTip("双向符号链前向算子深度（个位数展开，默认5）。长距离结构由子链/引理组合完成。")
        self.maxPathsInput = QLineEdit("1000000")
        self.maxPathsInput.setFixedWidth(90)
        self.maxAlexInput = QLineEdit("10")
        self.maxAlexInput.setFixedWidth(60)
        self.minAlexInput = QLineEdit("1")
        self.minAlexInput.setFixedWidth(60)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("链长上限："))
        search_row.addWidget(self.maxDepthInput)
        search_row.addWidget(QLabel("算子深度："))
        search_row.addWidget(self.operatorDepthInput)
        search_row.addStretch()

        alex_row = QHBoxLayout()
        alex_row.addWidget(QLabel("龙数上限："))
        alex_row.addWidget(self.maxAlexInput)
        alex_row.addWidget(QLabel("龙数下限："))
        alex_row.addWidget(self.minAlexInput)
        alex_row.addStretch()

        beam_row = QHBoxLayout()
        self.beamModeCheck = QCheckBox("beam模式")
        self.beamModeCheck.setToolTip("beam束搜索（默认关闭，计算时间长）。勾选后用束搜索直接枚举真实后继状态，可自行填束宽与算子深度。")
        beam_row.addWidget(self.beamModeCheck)
        self.beamWidthInput = QLineEdit("3000")
        self.beamWidthInput.setFixedWidth(60)
        self.beamWidthInput.setToolTip("beam束搜索束宽，默认3000。")
        self.beamDepthInput = QLineEdit("25")
        self.beamDepthInput.setFixedWidth(50)
        self.beamDepthInput.setToolTip("beam束搜索算子深度（展开步数上限），默认25。")
        self.beamWidthInput.setEnabled(False)
        self.beamDepthInput.setEnabled(False)
        self.maxPathsInput.setEnabled(False)
        self.beamModeCheck.toggled.connect(self.beamWidthInput.setEnabled)
        self.beamModeCheck.toggled.connect(self.beamDepthInput.setEnabled)
        self.beamModeCheck.toggled.connect(self.maxPathsInput.setEnabled)
        beam_row.addWidget(QLabel("束宽："))
        beam_row.addWidget(self.beamWidthInput)
        beam_row.addWidget(QLabel("束深："))
        beam_row.addWidget(self.beamDepthInput)
        beam_row.addWidget(QLabel("路径上限："))
        beam_row.addWidget(self.maxPathsInput)
        beam_row.addStretch()

        self.manualInputPanel = QWidget()
        self.manualInputPanel.setVisible(False)
        manual_layout = QVBoxLayout()
        manual_layout.setContentsMargins(0, 4, 0, 4)
        manual_help = QLabel(
            "手动输入格式（每行一张牌）：\n"
            "手牌栏：费用 卡名（如 4 鲨鱼之灵）\n"
            "随从栏：费用 卡名 血量（如 4 鲨鱼之灵 3）\n"
            "状态栏：卡名 数量（如 狐人老千 2，数量=叠加层数）\n"
            "殒命暗影写：* 殒命暗影（* 表示无费用，会自动标记）\n"
            "卡名支持简称和错字，会用内置模糊识别自动匹配；填好后点“解析并应用”。"
        )
        manual_help.setWordWrap(True)
        manual_layout.addWidget(manual_help)

        manual_mana_row = QHBoxLayout()
        manual_mana_row.addWidget(QLabel("水晶："))
        manual_mana_row.addWidget(self.crystalInput)
        manual_mana_row.addWidget(QLabel("法力："))
        manual_mana_row.addWidget(self.manaInput)
        manual_mana_row.addStretch()
        manual_layout.addLayout(manual_mana_row)

        manual_zone_row = QHBoxLayout()

        manual_hand_panel = QWidget()
        manual_hand_layout = QVBoxLayout()
        manual_hand_layout.setContentsMargins(0, 0, 0, 0)
        manual_hand_layout.addWidget(QLabel("手牌栏"))
        self.manualHandEdit = QTextEdit()
        self.manualHandEdit.setPlaceholderText("例：\n4 鲨鱼之灵\n2 狐狸老千\n4 刀油\n* 殒命暗影")
        self.manualHandEdit.setFixedHeight(150)
        manual_hand_layout.addWidget(self.manualHandEdit)
        manual_hand_panel.setLayout(manual_hand_layout)

        manual_board_panel = QWidget()
        manual_board_layout = QVBoxLayout()
        manual_board_layout.setContentsMargins(0, 0, 0, 0)
        manual_board_layout.addWidget(QLabel("战场（随从栏）"))
        self.manualBoardEdit = QTextEdit()
        self.manualBoardEdit.setPlaceholderText("例：\n4 鲨鱼之灵 3\n2 狐 2\n4 刀油 3")
        self.manualBoardEdit.setFixedHeight(150)
        manual_board_layout.addWidget(self.manualBoardEdit)
        manual_board_panel.setLayout(manual_board_layout)

        manual_effect_panel = QWidget()
        manual_effect_layout = QVBoxLayout()
        manual_effect_layout.setContentsMargins(0, 0, 0, 0)
        manual_effect_layout.addWidget(QLabel("当前效果（可选）"))
        self.manualEffectEdit = QTextEdit()
        self.manualEffectEdit.setPlaceholderText("例：\n狐人老千 2")
        self.manualEffectEdit.setFixedHeight(150)
        manual_effect_layout.addWidget(self.manualEffectEdit)
        manual_effect_panel.setLayout(manual_effect_layout)

        manual_zone_row.addWidget(manual_hand_panel)
        manual_zone_row.addWidget(manual_board_panel)
        manual_zone_row.addWidget(manual_effect_panel)
        manual_layout.addLayout(manual_zone_row)

        manual_button_row = QHBoxLayout()
        self.manualExampleButton = QPushButton("填入示例")
        self.manualApplyButton = QPushButton("解析并应用")
        self.manualClearButton = QPushButton("清空")
        self.manualExampleButton.clicked.connect(self.fill_manual_example)
        self.manualApplyButton.clicked.connect(self.apply_manual_input)
        self.manualClearButton.clicked.connect(self.clear_manual_input)
        manual_button_row.addWidget(self.manualExampleButton)
        manual_button_row.addWidget(self.manualApplyButton)
        manual_button_row.addWidget(self.manualClearButton)
        manual_button_row.addStretch()
        manual_layout.addLayout(manual_button_row)
        self.manualInputPanel.setLayout(manual_layout)

        # ---- 框选区域（放到设置窗口） ----
        self.selectButton = QPushButton("框选手牌/战场")
        self.selectManaButton = QPushButton("框选水晶/法力")
        self.selectButton.clicked.connect(self.open_selector)
        self.selectManaButton.clicked.connect(self.open_mana_selector)
        select_layout = QHBoxLayout()
        select_layout.addWidget(QLabel("框选 OCR 区域："))
        select_layout.addWidget(self.selectButton)
        select_layout.addWidget(self.selectManaButton)
        select_layout.addStretch()

        # ---- OCR 原始文本（放到设置窗口） ----
        self.ocrTitleLabel = QLabel("OCR原始文本（手牌/战场）")
        self.ocrText = QTextEdit()
        self.ocrText.setReadOnly(True)
        self.ocrText.setMinimumWidth(400)
        self.ocrText.setFixedHeight(88)
        self.ocrText.setStyleSheet(QuickPanel.ocr_raw_style)
        self.manaOcrTitleLabel = QLabel("OCR原始文本（水晶/法力）")
        self.manaOcrText = QTextEdit()
        self.manaOcrText.setReadOnly(True)
        self.manaOcrText.setMinimumWidth(400)
        self.manaOcrText.setFixedHeight(44)
        self.manaOcrText.setStyleSheet(QuickPanel.ocr_raw_style)

        # 设置窗口布局：参数 + 手动输入 + 框选 + OCR原始文本
        settings_layout = QVBoxLayout()
        settings_layout.addWidget(self.statusLabel)
        settings_layout.addLayout(select_layout)
        settings_layout.addLayout(search_row)
        settings_layout.addLayout(alex_row)
        settings_layout.addLayout(beam_row)
        settings_layout.addWidget(self.manualInputButton)
        settings_layout.addWidget(self.manualInputPanel)
        settings_layout.addWidget(self.ocrTitleLabel)
        settings_layout.addWidget(self.ocrText)
        settings_layout.addWidget(self.manaOcrTitleLabel)
        settings_layout.addWidget(self.manaOcrText)
        settings_layout.addStretch()
        self.setLayout(settings_layout)
        self.resize(520, 820)
        self.move(120, 80)

        # 操作弹窗（识别/计算/牛池/殒命/手牌·战场·状态/计算结果），默认只显示弹窗
        self.panel = QuickPanel(self)
        self.panel.show()
        self.setResult("识别结果会显示在这里", "重建后的牌库与手牌会显示在这里", None)
        self.panel.fullText.setPlainText("计算完成后，完整路径结果会显示在这里（可折叠）")
        for container in (self.panel.bidirRoundsLayout, self.panel.beamRoundsLayout):
            hint = QLabel("（尚未计算，计算完成后按“舞”分段显示）")
            hint.setStyleSheet("font-size:13px;color:#888;")
            container.addWidget(hint)

    def toggle_settings_window(self):
        """点“设置”才弹出/收起后台设置窗口。"""
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def moveEvent(self, event):
        """拖动设置窗口时同样贴住屏幕边界。"""
        super().moveEvent(event)
        if getattr(self, "_native_clamp_ok", False):
            return
        if getattr(self, "_clamp_move", False):
            return
        self._clamp_move = True
        try:
            geo = self.frameGeometry()
            screen = QApplication.primaryScreen().availableGeometry()
            max_x = screen.x() + max(0, screen.width() - geo.width())
            max_y = screen.y() + max(0, screen.height() - geo.height())
            x = min(max(geo.x(), screen.x()), max_x)
            y = min(max(geo.y(), screen.y()), max_y)
            if x != geo.x() or y != geo.y():
                self.move(x, y)
        finally:
            self._clamp_move = False

    def _slot_text(self, index: int, card) -> str:
        if card is None:
            return "空"

        cost = getattr(card, "cost", None)
        cost_text = "*费" if cost is None else f"{cost}费"
        health = getattr(card, "health", None)
        health_text = f",{health}血" if health is not None else ""
        name = getattr(card, "name", "") or getattr(card, "recognized_name", "") or "?"
        count = max(1, int(getattr(card, "count", 1) or 1))
        count_text = f"×{count}" if count > 1 else ""
        return f"{index}. {name}{count_text}[{cost_text}{health_text}]"

    # 参数顺序与 OCRWorker.result_signal 一致：
    # (ocr_text, hand_text, hand_result, crystals, mana, mana_raw_text)
    def setResult(self, ocr_text, hand_text, rebuild_result=None,
                  mana_crystals=None, mana=None, mana_raw_text="", source="ocr"):
        self.ocrText.setPlainText(ocr_text if ocr_text else "未识别到文字")
        self.manaOcrText.setPlainText(mana_raw_text if mana_raw_text else "（未框选或未识别）")
        self.ocrTitleLabel.setText(
            "OCR原始文本（手牌/战场）" if source == "ocr" else "手动输入文本（已按现有规则解析）"
        )
        hand_cards = list(getattr(rebuild_result, "cards", None) or [])
        board_cards = list(getattr(rebuild_result, "battlefield_cards", None) or [])

        if mana_crystals is not None and mana is not None:
            self.scan_crystals = int(mana_crystals)
            self.scan_mana = int(mana)
            self._run_got_mana = True

        if hand_cards:
            self._run_got_hand = True

        for index, slot in enumerate(self.panel.hand_slots):
            card = hand_cards[index] if index < len(hand_cards) else None
            slot.setText(self._slot_text(index + 1, card))

        for index, slot in enumerate(self.panel.board_slots):
            card = board_cards[index] if index < len(board_cards) else None
            slot.setText(self._slot_text(index + 1, card))

        if rebuild_result is not None:
            self.latest_rebuild_result = rebuild_result
            self.panel.calculateButton.setEnabled(True)

        self.update_status_display()

        # 识别自动停止：点一下开始扫描，识别到手牌且（若框了法力框）法力后自动停止
        if (
            self._auto_stop
            and self.worker is not None
            and self._run_got_hand
            and self._run_got_mana
        ):
            self.stop_ocr()
            self.statusLabel.setText("状态：已识别到结果，自动停止")

    def update_status_display(self):
        if self.scan_crystals is not None and self.scan_mana is not None:
            crystals = str(self.scan_crystals)
            mana = str(self.scan_mana)
        else:
            crystals = "?"
            mana = "?"
        band = "、".join(
            card_name for card_name, checkbox in self.panel.etcBandChecks if checkbox.isChecked()
        ) or "空"
        deadly_text = self.panel.deadlyShadowInput.text().strip() or "未标记"
        self.panel.statusSummaryLabel.setText(
            f"水晶 {crystals} / 法力 {mana}　|　牛池：{band}　|　殒命：{deadly_text}"
        )

    def toggle_manual_input(self):
        visible = self.manualInputPanel.isHidden()
        self.manualInputPanel.setVisible(visible)
        self.manualInputButton.setText("收起手动输入" if visible else "手动输入")

    def fill_manual_example(self):
        self.manualHandEdit.setPlainText(
            "4 鲨鱼之灵\n"
            "2 狐狸老千\n"
            "4 刀油"
        )
        self.manualBoardEdit.setPlainText(
            "4 鲨鱼之灵 3\n"
            "2 狐 2\n"
            "4 刀油 3\n"
            "5 暗影施法者 2"
        )
        self.manualEffectEdit.setPlainText("狐人老千 2")
        self.statusLabel.setText("状态：已填入示例，可点击“解析并应用”")

    def clear_manual_input(self):
        self.manualHandEdit.clear()
        self.manualBoardEdit.clear()
        self.manualEffectEdit.clear()
        self.statusLabel.setText("状态：手动输入区已清空")

    def apply_manual_input(self):
        section_text, zone_warnings, board_healths = build_manual_section_text(
            hand_text=self.manualHandEdit.toPlainText(),
            board_text=self.manualBoardEdit.toPlainText(),
            effect_text=self.manualEffectEdit.toPlainText()
        )

        if not section_text.strip():
            QMessageBox.warning(self, "提示", "请先在手动输入区填入卡牌内容。")
            return

        try:
            result = rebuild_hand_from_text(section_text)
        except Exception as e:
            QMessageBox.warning(self, "解析失败", str(e))
            return

        for card, health in zip(result.battlefield_cards, board_healths):
            if health is not None:
                card.health = health

        # 手动输入里写了“* 殒命暗影”的手牌，自动标记为殒命暗影
        result.deadly_shadow_hand_indexes = [
            index
            for index, card in enumerate(result.cards, start=1)
            if card.name == "殒命暗影"
        ]

        self.setResult(
            ocr_text=section_text,
            hand_text=format_result(result),
            rebuild_result=result,
            source="manual"
        )
        self.statusLabel.setText("状态：手动输入已解析，可点击“开始计算”")

        all_warnings = zone_warnings + result.warnings

        if all_warnings:
            QMessageBox.warning(self, "解析警告", "\n".join(all_warnings[:10]))

    def open_selector(self):
        if self.worker is not None:
            QMessageBox.information(self, "提示", "请先停止识别，再重新框选区域。")
            return

        self._select_target = "main"
        self.statusLabel.setText("状态：拖拽鼠标框选手牌/战场区域，按 Esc 可取消")
        self.overlay.close_on_select = True
        self.overlay.showFullScreen()
        self.overlay.raise_()
        self.overlay.activateWindow()

    def open_mana_selector(self):
        if self.worker is not None:
            QMessageBox.information(self, "提示", "请先停止识别，再重新框选区域。")
            return

        self._select_target = "mana"
        self.statusLabel.setText("状态：拖拽鼠标框选水晶/法力区域（格式 A/B，如 3/3），按 Esc 可取消")
        self.overlay.close_on_select = True
        self.overlay.showFullScreen()
        self.overlay.raise_()
        self.overlay.activateWindow()

    def on_area_selected(self, box):
        if self._select_target == "mana":
            self.mana_box = box
            self.statusLabel.setText("状态：已选水晶/法力区域，可点击“开始识别”")
        else:
            self.box = box
            self.statusLabel.setText("状态：已选手牌/战场区域，可再框选水晶/法力区域")
        self._refresh_start_enabled()

    def _refresh_start_enabled(self):
        self.panel.startButton.setEnabled(self.box is not None and self.mana_box is not None)

    def get_box(self):
        return self.box

    def get_mana_box(self):
        return self.mana_box

    def get_deadly_shadow_hand_indexes(self):
        if not self.panel.deadlyShadowCheck.isChecked():
            return []

        text = self.panel.deadlyShadowInput.text().strip()

        if not text:
            raise ValueError("启用殒命暗影标记后，请输入手牌序号。")

        parts = (
            text.replace("，", ",")
            .replace("、", ",")
            .replace(" ", ",")
            .split(",")
        )
        indexes = []

        for part in parts:
            item = part.strip()

            if not item:
                continue

            if not item.isdigit():
                raise ValueError("殒命暗影位置只能填写数字，例如 3 或 3,7。")

            indexes.append(int(item))

        if not indexes:
            raise ValueError("启用殒命暗影标记后，请输入手牌序号。")

        hand_count = len(getattr(self.latest_rebuild_result, "cards", []))

        for index in indexes:
            if index <= 0:
                raise ValueError("殒命暗影位置必须从 1 开始。")

            if index > hand_count:
                raise ValueError(f"殒命暗影位置第 {index} 张超出当前手牌数量 {hand_count}。")

        return indexes

    def get_etc_band_remaining(self):
        return [
            card_name
            for card_name, checkbox in self.panel.etcBandChecks
            if checkbox.isChecked()
        ]

    def start_ocr(self):
        if self.box is None:
            QMessageBox.warning(self, "提示", "请先在设置窗口框选手牌/战场区域。")
            return

        if self.worker is not None:
            return

        self.worker = OCRWorker(self.get_box, self.get_mana_box, self.ocr_api)
        self.worker.result_signal.connect(self.setResult)
        self.worker.error_signal.connect(self.on_ocr_error)
        self._auto_stop = True
        self._run_got_hand = False
        self._run_got_mana = self.mana_box is None
        self.worker.start()

        self.statusLabel.setText("状态：正在扫描识别（0.1s 循环，识别到结果自动停止）")
        self.panel.startButton.setText("停止识别")

    def start_calculation(self):
        if self.latest_rebuild_result is None:
            QMessageBox.warning(self, "提示", "请先完成一次识别，得到牌库和手牌。")
            return

        if self.calc_worker is not None:
            QMessageBox.information(self, "提示", "当前正在计算，请稍等。")
            return

        if self.scan_crystals is not None and self.scan_mana is not None:
            # 已扫描到水晶/法力：以扫描结果为准
            mana_crystals = self.scan_crystals
            mana = self.scan_mana
        else:
            try:
                mana_crystals = int(self.crystalInput.text().strip())
                mana = int(self.manaInput.text().strip())
            except ValueError:
                QMessageBox.warning(self, "提示", "水晶和法力必须是数字。")
                return

        try:
            max_depth = int(self.maxDepthInput.text().strip())
        except ValueError:
            QMessageBox.warning(self, "提示", "链条步数上限必须是数字。")
            return

        if max_depth <= 0:
            QMessageBox.warning(self, "提示", "链条步数上限必须大于 0。")
            return

        try:
            operator_depth = int(self.operatorDepthInput.text().strip())
        except ValueError:
            QMessageBox.warning(self, "提示", "双向算子深度必须是数字。")
            return

        if operator_depth <= 0:
            QMessageBox.warning(self, "提示", "双向算子深度必须大于 0。")
            return

        try:
            beam_width = int(self.beamWidthInput.text().strip())
            beam_depth = int(self.beamDepthInput.text().strip())
        except ValueError:
            QMessageBox.warning(self, "提示", "beam束宽/深度必须是数字。")
            return

        if beam_width <= 0 or beam_depth <= 0:
            QMessageBox.warning(self, "提示", "beam束宽/深度必须大于 0。")
            return

        try:
            max_paths = int(self.maxPathsInput.text().strip())
        except ValueError:
            QMessageBox.warning(self, "提示", "路径上限必须是数字。")
            return

        if max_paths <= 0:
            QMessageBox.warning(self, "提示", "路径上限必须大于 0。")
            return

        try:
            max_alex_count = int(self.maxAlexInput.text().strip())
        except ValueError:
            QMessageBox.warning(self, "提示", "搜索龙数上限必须是数字。")
            return

        if max_alex_count <= 0:
            QMessageBox.warning(self, "提示", "搜索龙数上限必须大于 0。")
            return

        try:
            min_alex_count = int(self.minAlexInput.text().strip())
        except ValueError:
            QMessageBox.warning(self, "提示", "搜索龙数下限必须是数字。")
            return

        if min_alex_count <= 0:
            QMessageBox.warning(self, "提示", "搜索龙数下限必须大于 0。")
            return

        if min_alex_count > max_alex_count:
            QMessageBox.warning(self, "提示", "搜索龙数下限不能大于上限。")
            return

        try:
            deadly_shadow_hand_indexes = self.get_deadly_shadow_hand_indexes()
        except ValueError as e:
            QMessageBox.warning(self, "提示", str(e))
            return

        self.panel.fullText.setPlainText("正在计算所有可行出牌路径...")
        for container in (self.panel.bidirRoundsLayout, self.panel.beamRoundsLayout):
            while container.count():
                item = container.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        self.panel.progressLabel.setText("")
        # 合并后的按钮在计算期间要可点（此时是“中止计算”）
        self.panel.calculateButton.setEnabled(True)
        self.panel.calculateButton.setText("中止计算")
        self._calc_beam_mode = bool(self.beamModeCheck.isChecked())

        self.calc_worker = CalculationWorker(
            rebuild_result=self.latest_rebuild_result,
            mana_crystals=mana_crystals,
            mana=mana,
            max_depth=max_depth,
            max_paths=max_paths,
            max_alex_count=max_alex_count,
            min_alex_count=min_alex_count,
            deadly_shadow_hand_indexes=deadly_shadow_hand_indexes,
            etc_band_remaining=self.get_etc_band_remaining(),
            beam_mode=self.beamModeCheck.isChecked(),
            operator_depth=operator_depth,
            beam_width=beam_width,
            beam_depth=beam_depth
        )
        self.calc_worker.progress_signal.connect(self.on_calculation_progress)
        self.calc_worker.partial_result_signal.connect(self.on_calculation_partial)
        self.calc_worker.result_signal.connect(self.on_calculation_finished)
        self.calc_worker.best_result_signal.connect(self.on_best_result)
        self.calc_worker.error_signal.connect(self.on_calculation_error)
        self.calc_worker.finished.connect(self.on_calculation_thread_finished)
        self.calc_worker.start()

    def cancel_calculation(self):
        if self.calc_worker is None:
            return

        self.panel.fullText.append("\n正在中止计算，将展示并导出已经算出的全部结果...")
        self.panel.calculateButton.setText("开始计算")
        self.calc_worker.requestInterruption()

    def on_calculation_progress(self, text):
        self.statusLabel.setText("状态：" + text)
        # 路径上方小字实时显示链/剪分支/beam束数据
        self.panel.progressLabel.setText(text)

    def on_calculation_partial(self, text):
        self.panel.fullText.setPlainText(text)
        self.panel._fit_edit(self.panel.fullText, 900)

    def on_calculation_finished(self, text):
        self.panel.fullText.setPlainText(text)
        self.panel._fit_edit(self.panel.fullText, 900)
        self.statusLabel.setText("状态：计算完成，完整路径文档已生成")

    def on_calculation_error(self, msg):
        self.panel.fullText.setPlainText(msg)
        self.panel._fit_edit(self.panel.fullText, 900)
        self.statusLabel.setText("状态：计算失败")

    def on_best_result(self, beam_mode, text):
        """双向链/beam 计算完成后，把最高伤害路径放进对应框并自动适应高度。"""
        if beam_mode:
            self.panel._show_rounds(self.panel.beamRoundsLayout, text)
        else:
            self.panel._show_rounds(self.panel.bidirRoundsLayout, text)

    def on_calculation_thread_finished(self):
        self.calc_worker = None
        self.panel.calculateButton.setText("开始计算")
        self.panel.calculateButton.setEnabled(self.latest_rebuild_result is not None)

    def stop_ocr(self):
        if self.worker is None:
            return

        self.worker.stop()
        self.worker.wait()
        self.worker = None
        self._auto_stop = False

        self.statusLabel.setText("状态：已停止识别，可重新框选区域")
        self.panel.startButton.setText("开始识别")
        self.panel.startButton.setEnabled(self.box is not None and self.mana_box is not None)

    def on_ocr_error(self, msg):
        self.statusLabel.setText(msg)

    def closeEvent(self, event):
        # 设置窗口只是后台辅助窗口：关闭它不影响主弹窗（计算/识别照常进行）
        self.overlay.close()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    gui = MainWindow()
    # 设置窗口默认隐藏，点弹窗里的“设置”才弹出
    gui.panel.show()
    sys.exit(app.exec_())
