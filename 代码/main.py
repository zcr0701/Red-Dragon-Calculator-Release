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
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cloud_report
from PyQt5.QtCore import (
    QEventLoop,
    QPoint,
    QRectF,
    QSize,
    QSettings,
    QThread,
    QTimer,
    Qt,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QColor,
    QCursor,
    QPainter,
    QPen,
    QPixmap,
    QTextDocument,
)
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSpinBox,
    QSplitter,
    QStyledItemDelegate,
    QStyle,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
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


def abbreviate_step(step: str, compact: bool = False) -> str:
    """把引擎路径一步（如 赤烟·腾武（斯卡布斯·刀油））转成缩写格式。

    compact=True 时抽随从卡标注用半角括号：潜伏帷幕(狐刀)（原版显示）。
    """
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
        # 抽随从卡标注：潜伏帷幕（斯卡布斯·刀油、狐人老千）-> 潜伏帷幕（刀狐）
        if name in ("潜伏帷幕", "挖掘宝藏"):
            names = [abbreviate_card_name(p.strip()) for p in inner.split("、")]
            target = (
                "(" + "".join(names) + ")"
                if compact
                else "（" + "".join(names) + "）"
            )
        # 持枪要挟（误炸）-> 持枪要挟(误炸)
        elif name == "持枪要挟":
            target = "(" + abbreviate_card_name(inner.strip()) + ")"
        # 误炸（刀、刀、晦）-> 误炸(刀刀晦)：目标随从缩写连写
        elif name == "误炸" and "、" in inner:
            target = "(" + "".join(
                abbreviate_card_name(p.strip()) for p in inner.split("、")
            ) + ")"
        else:
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


def abbreviate_step_html(step: str, compact: bool = False) -> str:
    """小窗彩色显示：已知缩写字按 CARD_ABBREV_COLORS 上色，其余字符原样保留。

    [殒] 标记跟随“所变形卡”（主卡缩写）的颜色，而不是目标括号里的缩写。
    """
    plain = abbreviate_step(step, compact=compact)
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


def _card_box_html(name: str) -> str:
    """单个卡名 → 小窗颜色框（有颜色则画方块，无颜色保留缩写/全名）。"""
    abbr = abbreviate_card_name(name)
    color = CARD_ABBREV_COLORS.get(abbr)

    if color:
        return "[" + _abbr_square(html.escape(abbr), color) + "]"
    return "[" + html.escape(abbr) + "]"
# 可能分支机制：持枪要挟发现牌单独计算的优先级（补水 > 脱水 > 误炸 > 袋底藏沙 > 不许乱动）
QUICKDRAW_BRANCH_ORDER = (
    "补水",
    "脱水",
    "误炸",
    "袋底藏沙",
    "不许乱动",
    "其他快枪牌·随从",
    "其他快枪牌·法术",
)


def _whatif_branch_data(
    results: List[Dict[str, object]],
) -> Tuple[str, Dict[str, Tuple[int, int, int, List[str]]]]:
    """从搜索结果提取持枪要挟分支：返回 (路径前半部分, {发现牌: (伤害,龙数,余费,后半段)})。

    只保留伤害 > 0 的分支；同一发现牌取最高伤害路径；发现牌不进入“如果机制预处理”，
    只作为搜索路径里的分支（持枪要挟（X））。
    """
    prefix = ""
    branches: Dict[str, Tuple[int, int, int, List[str]]] = {}

    for item in results:
        path = item.get("path") or []
        dmg = int(item.get("damage") or 0)

        if dmg <= 0:
            continue

        for i, step in enumerate(path):
            m = re.search(r"持枪要挟[（(](.+?)[）)]", str(step or ""))

            if not m:
                continue

            x = m.group(1)

            if not prefix and i > 0:
                prefix = " -> ".join(path[:i])

            cont = path[i + 1:]

            if x not in branches or dmg > branches[x][0]:
                branches[x] = (
                    dmg,
                    int(item.get("dragons") or 0),
                    int(item.get("mana") or 0),
                    cont,
                )
            break

    return prefix, branches


def _format_whatif_branch_lines(
    results: List[Dict[str, object]],
    branches: Optional[List[Dict[str, object]]] = None,
    colors: bool = False,
    full_names: bool = False,
) -> List[str]:
    """持枪要挟分支显示：路径前半部分 -> 持枪要挟 + 可能分支。

    优先使用“单独完整计算”的 quickdraw_branches（已按优先级排序）；
    无该数据时退化为从主搜索结果解析（_whatif_branch_data）。
    full_names=True 时用完整卡名并省略中间路径（主窗口）；
    colors=True 时路径用缩写+颜色框（小窗），否则用缩写纯文本。
    """
    if not branches:
        _prefix, bmap = _whatif_branch_data(results)

        if not bmap:
            return []

        branches = [
            {"card": x, "damage": d, "dragons": g, "mana_left": m, "path": p}
            for x, (d, g, m, p) in bmap.items()
        ]
        branches.sort(
            key=lambda b: QUICKDRAW_BRANCH_ORDER.index(b["card"])
            if b["card"] in QUICKDRAW_BRANCH_ORDER
            else 99
        )

    if not branches:
        return []

    prefix = ""
    prefix_steps: List[str] = []

    for b in branches:
        path = b.get("path") or []

        for i, step in enumerate(path):
            if "持枪要挟" in str(step or ""):
                if i > 0 and not prefix:
                    prefix = " -> ".join(path[:i])
                    prefix_steps = list(path[:i])
                break

    if not prefix_steps:
        # 分支路径可能因截断未带“持枪要挟”步骤：从主搜索结果提取前缀
        for item in results:
            p = item.get("path") or []

            for i, step in enumerate(p):
                if "持枪要挟" in str(step or ""):
                    prefix_steps = list(p[:i])
                    prefix = " -> ".join(prefix_steps)
                    break

            if prefix_steps:
                break

    if full_names:
        abbr_fn = None
    elif colors:
        abbr_fn = abbreviate_step_html
    else:
        abbr_fn = abbreviate_step

    lines = ["Branch："]

    if full_names and prefix:
        lines.append(prefix + " -> 持枪要挟")
    elif prefix_steps:
        lines.append(" -> ".join(abbr_fn(s) for s in prefix_steps) + " -> 持枪要挟")
    else:
        lines.append("持枪要挟")

    lines.append("可能分支：")

    for b in branches:
        x = b.get("card") or ""
        path = b.get("path") or []
        cont: List[str] = []
        found_branch = False

        for i, step in enumerate(path):
            if "持枪要挟" in str(step or ""):
                cont = path[i + 1:]
                found_branch = True
                break

        if not found_branch and prefix_steps and len(path) > len(prefix_steps):
            # 分支路径未带“持枪要挟（X）”步骤（截断搜索找到的廉价线）：
            # 跳过共享前缀后作为该分支的后续路径展示
            cont = path[len(prefix_steps):]

        lines.append(
            f"最大伤害：{b.get('damage', 0)}；龙数：{b.get('dragons', 0)}；"
            f"余：{b.get('mana_left', 0)}；"
        )

        if cont and full_names:
            lines.append(f"持枪要挟(可能{x}) -> …… -> {cont[-1]}")
        elif cont:
            steps = ["持枪要挟(可能" + abbreviate_card_name(x) + ")"]
            steps.extend(abbr_fn(str(c)) for c in cont)
            lines.append(" -> ".join(steps))
        else:
            lines.append("持枪要挟(可能" + abbreviate_card_name(x) + ")")

    return lines


def _format_exchange_line(exchanges: List[Tuple[int, int]]) -> str:
    """场面交换处理行：[我方随从X]->[敌方随从X]，……。X 为 board 序号。"""
    if not exchanges:
        return ""

    plan = "，".join(f"[我方随从{fi}]->[敌方随从{ei}]" for fi, ei in exchanges)
    return f"场面交换处理：{plan}。其中X为随从在board中的序号"


def _wb_step_abbr(step: str) -> str:
    """路径步骤缩写（伺/币/鱼/刀/龙…）。"""
    return abbreviate_card_name(str(step))


def _wb_branch_label(
    lvl_char: str,
    choice: int,
    card: str,
    outcome: List[str],
) -> str:
    """分支段标签：A1潜伏帷幕(刀狐) / B1持枪要挟(补水)。"""
    abbr = [_wb_step_abbr(o) for o in outcome]
    return f"{lvl_char}{choice}{card}({'、'.join(abbr)})"


def _wb_cont_steps(path: List[str], card: str) -> List[str]:
    """分支后的路径关键步骤（跳过分支卡本身，取前 3，长则加 ……）。"""
    cont = [s for s in path if s != card]
    steps = [_wb_step_abbr(s) for s in cont[:3]]

    if len(cont) > 3:
        steps.append("……")

    return steps


def _wb_tree_lines(wb: Dict[str, object]) -> List[str]:
    """W-B 机制文本树：主干横排，分支点下方用 │/└ 挂出替代路径，
    每条路径带唯一标识符（如 A1B1 = 第一层选A1、第二层选B1）。"""
    nodes = wb.get("nodes") or []

    if not nodes:
        return []

    lines = ["W-B机制（分支树）："]
    _wb_render_chain(lines, nodes, "A", 1, [], [])
    return lines


def _wb_render_chain(
    lines: List[str],
    nodes: List[Dict[str, object]],
    lvl_char: str,
    choice: int,
    path_id: List[str],
    ancestor_cols: List[int],
) -> None:
    """渲染一条链（主干或替代）：横排文本 + 分支点下方挂替代路径。"""
    pairs: List[tuple] = []
    cur = list(nodes)

    while cur:
        best_node = max(
            cur,
            key=lambda n: max(
                (b.get("damage") or 0) or (b.get("delta") or 0)
                for b in (n.get("branches") or [{}])
            ),
        )
        branches = sorted(
            best_node.get("branches") or [],
            key=lambda b: -(b.get("damage") or 0),
        )

        if not branches:
            break

        pairs.append((best_node, branches[0], branches[1:]))
        children = branches[0].get("children") or {}
        cur = children.get("nodes") or []

    if not pairs:
        return

    # 主干字符串 + 分支点列 + 路径 ID
    parts: List[str] = []
    cols: List[tuple] = []
    pid = list(path_id)
    lc = lvl_char
    ci = choice

    for node, best_br, alts in pairs:
        card = str(node.get("card") or "")

        for s in (node.get("path") or []):
            if s != card:
                parts.append(_wb_step_abbr(s))

        outcome = best_br.get("drawn") or ([best_br.get("card", "")] if best_br.get("card") else [])
        col = sum(len(p) + 1 for p in parts) if parts else 0
        parts.append(_wb_branch_label(lc, ci, card, outcome))
        pid.append(f"{lc}{ci}")
        cols.append((col, lc, ci, alts, list(pid), card))
        parts.append("……")
        parts.extend(_wb_cont_steps(best_br.get("path") or [], card))
        lc = chr(ord(lc) + 1)
        ci = 1

    lines.append("-".join(parts) + f"(路径唯一标识符{''.join(pid)})")

    # 从最深分支点开始挂替代路径（先 B 层、后 A 层）
    for depth in range(len(cols) - 1, -1, -1):
        col, lc_i, ci_i, alts, pid_i, node_card = cols[depth]
        ancestors = cols[:depth]

        for ai, alt_br in enumerate(alts, start=1):
            alt_pid = list(pid_i)
            alt_id = f"{lc_i}{ci_i + ai}"
            # 替代分支替换该层的选择：A1B2 而非 A1B1B2
            alt_pid[-1] = alt_id
            outcome = alt_br.get("drawn") or ([alt_br.get("card", "")] if alt_br.get("card") else [])
            segs = [_wb_branch_label(lc_i, ci_i + ai, node_card, outcome)]
            segs.append("……")
            segs.extend(_wb_cont_steps(alt_br.get("path") or [], node_card))
            alt_text = "-".join(segs) + f"(路径唯一标识符{''.join(alt_pid)})"
            prefix = _wb_tree_prefix(ancestors, col)
            lines.append(prefix + "└（可展开）" + alt_text)


def _wb_tree_prefix(ancestors: List[tuple], col: int) -> str:
    """构建替代行的前缀：祖先分支点列画竖线 │，当前列之前补空格（└ 由调用方加）。"""
    chars = []

    for i in range(col + 1):
        if any(a_col == i for a_col, _lc, _ci, _a, _p, _c in ancestors):
            chars.append("│")
        else:
            chars.append(" ")

    return "".join(chars)


# 小窗段落（每轮路径行）开头缩进两个全角空格
PARA_INDENT = "\u3000\u3000"


def _fmt_avg(value: Optional[float]) -> str:
    """平均数值显示：保留 1 位小数，整数去掉小数尾巴（41.6 / 2 / 1.4）。"""
    if value is None:
        return "?"

    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def format_mini_results(
    data: Dict[str, object],
    colors: bool = False,
    exchanges: Optional[List[Tuple[int, int]]] = None,
    branches: bool = False,
) -> str:
    """小窗结果：最高伤害路径按轮次分割，只显示缩写。

    colors=True 时返回 HTML（缩写字上色），否则返回纯文本；
    exchanges 非空时在标题后插入“场面交换处理”行；
    branches=True 时保留完整标注（WhatIF 显示），否则用紧凑标注（原版显示）。
    """
    results = data.get("results") or []
    best_mana = (results[0].get("mana") if results else 0) or 0
    exchange_line = _format_exchange_line(list(exchanges or []))
    title = (
        f"最大伤害：{data.get('max_damage', 0)}，"
        f"龙数：{data.get('max_dragons', 0)}，余：{best_mana}费"
    )
    # 0 伤害时不显示浪费费用的无意义路径，统一显示“（无路径）”。
    if not results or int(data.get("max_damage") or 0) <= 0:
        title = (
            f"最大伤害：{data.get('max_damage', 0)}，"
            f"龙数：{data.get('max_dragons', 0)}，余：0费"
        )
        if colors:
            return "<br>".join([html.escape(title), html.escape("（无路径）")])
        return "\n".join([title, "（无路径）"])

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
            abbr = "-".join(
                abbreviate_step_html(step, compact=not branches) for step in rnd
            )
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
        abbr = "-".join(
            abbreviate_step(step, compact=not branches) for step in rnd
        )
        lines.append(f"[第{chinese_round_number(index)}轮]：")
        lines.append(PARA_INDENT + (abbr if abbr else "（空）"))
        lines.append("")

    return "\n".join(lines)


def _display_width(text: str) -> int:
    """显示宽度：CJK/全角字符按 2 列，其余按 1 列（等宽字体下的列数）。"""
    width = 0

    for ch in text:
        width += 2 if ord(ch) >= 0x2E80 else 1

    return width


def _round_context(
    main_path: List[str], idx: int
) -> Tuple[str, List[str]]:
    """返回分支卡所在轮次的标题（如 [第一轮]：）与轮内该卡之前的步骤。"""
    pos = 0

    for r, rnd in enumerate(split_path_rounds(main_path), start=1):
        if idx < pos + len(rnd):
            return (
                f"[第{chinese_round_number(r)}轮]：",
                [str(s) for s in rnd[: idx - pos]],
            )

        pos += len(rnd)

    return "", []


def _branch_tree_indent(
    round_header: str, before_steps: List[str], colors: bool
) -> str:
    """├─ 同级分支缩进：按可见宽度对齐到主路径中分支卡的起始列。

    行 = 轮次头 + 段首缩进 + 前缀缩写（- 连接）。分支卡从该行“前缀 + '-'”之后开始，
    因此 ├─ 前导宽度 = 轮次头 + 段首缩进 + 前缀 + 1。彩色模式同样按纯文本宽度，
    而不是按 HTML 源码长度（否则会缩进几百个空格）。
    """
    prefix_abbr = "-".join(abbreviate_step(s, compact=False) for s in before_steps)
    width = _display_width(round_header) + _display_width(PARA_INDENT) + _display_width(
        prefix_abbr
    ) + 1
    return ("&nbsp;" * width) if colors else (" " * width)


def format_whatif_tree(data: Dict[str, object], colors: bool = False) -> str:
    """WhatIF 树状显示：主路径（按轮次）+ 行骗/挖掘宝藏/潜伏帷幕/垂钓时光
    同级抽牌分支 + 持枪要挟同级分支，全部用 ├─ 制表符对齐。

    主路径取束宽搜索最高伤线（含分支标注），分支数据来自 worker 的
    draw_branches（抽牌同级分支）与 quickdraw_branches（持枪同级分支）。
    """
    sep = "<br>" if colors else "\n"
    abbr_fn = abbreviate_step_html if colors else abbreviate_step
    lines: List[str] = []
    avg = data.get("whatif_average") or {}

    if avg:
        lines.append(
            f"WhatIF平均最高伤害：{_fmt_avg(avg.get('damage'))}，"
            f"平均龙数：{_fmt_avg(avg.get('dragons'))}"
        )

    results = data.get("results") or []

    if not results:
        return sep.join(lines)

    main = results[0]
    main_path = list(main.get("path") or [])
    main_dmg = int(main.get("damage") or 0)
    main_mana = int(main.get("mana") or 0)

    for index, rnd in enumerate(split_path_rounds(main_path), start=1):
        abbr = "-".join(abbr_fn(s, compact=False) for s in rnd)
        lines.append(f"[第{chinese_round_number(index)}轮]：{PARA_INDENT}{abbr}")

    if main_path:
        lines[-1] += f"({main_dmg}伤余{main_mana}费)"

    # 抽随从卡同级分支（行骗/挖掘宝藏/潜伏帷幕/垂钓时光）
    draw_markers = ("行骗", "挖掘宝藏", "潜伏帷幕", "垂钓时光")
    di = next(
        (i for i, s in enumerate(main_path) if any(m in str(s or "") for m in draw_markers)),
        -1,
    )
    main_draw_key = ""

    if di >= 0:
        m = re.search(r"[（(](.+?)[）)]", str(main_path[di]))

        if m:
            main_draw_key = m.group(1)

    draw_branches = data.get("draw_branches") or []

    if draw_branches:
        header, before = _round_context(main_path, di) if di > 0 else ("", [])
        indent = (
            _branch_tree_indent(header, before, colors)
            if di > 0
            else (("&nbsp;" * 4) if colors else (" " * 4))
        )
        marker = (
            next((m for m in draw_markers if m in str(main_path[di])), "行骗")
            if di > 0
            else "行骗"
        )

        for b in draw_branches:
            key = b.get("card") or ""

            if key == main_draw_key:
                continue

            dmg = int(b.get("damage") or 0)
            mana = int(b.get("mana_left") or 0)
            lines.append(
                indent
                + f"├─{marker}({abbreviate_card_name(key)})-……({dmg}伤余{mana}费)"
            )

    # 持枪要挟同级分支
    qi = next(
        (i for i, s in enumerate(main_path) if "持枪要挟" in str(s or "")),
        -1,
    )
    main_qd_choice = ""

    if qi >= 0:
        m = re.search(r"持枪要挟[（(](.+?)[）)]", str(main_path[qi]))

        if m:
            main_qd_choice = m.group(1)

    qd_branches = data.get("quickdraw_branches") or []

    if qd_branches:
        header, before = _round_context(main_path, qi) if qi > 0 else ("", [])
        indent = (
            _branch_tree_indent(header, before, colors)
            if qi > 0
            else (("&nbsp;" * 4) if colors else (" " * 4))
        )

        for b in qd_branches:
            card = b.get("card") or ""

            if card == main_qd_choice:
                continue

            dmg = int(b.get("damage") or 0)
            mana = int(b.get("mana_left") or 0)
            lines.append(
                indent
                + f"├─持枪要挟({abbreviate_card_name(card)})-……({dmg}伤余{mana}费)"
            )

    return sep.join(lines)


class _TreeHtmlDelegate(QStyledItemDelegate):
    """QTreeWidget 节点用 HTML 渲染（保留缩写字颜色框）。"""

    def paint(self, painter, option, index):  # noqa: N802
        html_txt = index.data(Qt.UserRole)

        if not html_txt:
            super().paint(painter, option, index)
            return

        painter.save()

        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        else:
            painter.fillRect(option.rect, option.palette.base())

        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setHtml(html_txt)
        doc.setTextWidth(max(50.0, option.rect.width() - 6.0))
        painter.translate(option.rect.left() + 3, option.rect.top() + 2)
        doc.drawContents(painter)
        painter.restore()

    def sizeHint(self, option, index):  # noqa: N802
        html_txt = index.data(Qt.UserRole)

        if not html_txt:
            return super().sizeHint(option, index)

        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setHtml(html_txt)
        return QSize(int(doc.idealWidth()) + 12, int(doc.size().height()) + 8)


class WhatIFTreeWidget(QTreeWidget):
    """WhatIF 分支树：主路径按轮次为根节点，行骗/持枪要挟同级分支为子节点。

    代替文本式“├─ 空格对齐”显示：树形结构由 QTreeWidget 原生提供，
    节点内容保留彩色缩写字框（HTML 代理渲染）。
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setAnimated(True)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setItemDelegate(_TreeHtmlDelegate(self))
        self.setStyleSheet("QTreeWidget::item { padding: 1px 0; }")

    @staticmethod
    def _item(text: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setData(0, Qt.UserRole, text)
        # 纯文本兜底（无 delegate 时仍可读）
        item.setText(0, re.sub(r"<[^>]+>", "", text))
        return item

    @staticmethod
    def _round_index(main_path: List[str], idx: int) -> int:
        """返回分支卡所在轮次的下标（0 起）；不在路径中返回 -1。"""
        pos = 0

        for r, rnd in enumerate(split_path_rounds(main_path)):
            if idx < pos + len(rnd):
                return r

            pos += len(rnd)

        return -1

    def set_whatif(self, data: Dict[str, object], colors: bool = True) -> None:
        self.clear()
        tree = data.get("whatif_tree") or {}
        branches = tree.get("branches") or []

        if not branches:
            return

        abbr_fn = abbreviate_step_html if colors else abbreviate_step

        def join_steps(steps: List[str]) -> str:
            return "-".join(abbr_fn(str(s), compact=False) for s in steps)

        # 根：指引路径（到第一个分支卡，如 币-刀-行骗）
        root_steps = [str(s) for s in (tree.get("root") or [])]
        root_item = self._item(join_steps(root_steps) if root_steps else "")
        self.addTopLevelItem(root_item)

        # 保底 / 平均
        worst = int(tree.get("worst") or 0)
        self.addTopLevelItem(self._item(f"保底伤害：{worst}"))
        avg = data.get("whatif_average") or {}

        if avg:
            self.addTopLevelItem(
                self._item(
                    f"平均伤害：{_fmt_avg(avg.get('damage'))}，"
                    f"平均龙数：{_fmt_avg(avg.get('dragons'))}"
                )
            )

        # 次级：每个抽取结果（行骗(牛)/行骗(狐)…）
        for tb in branches:
            outcome = str(tb.get("outcome") or "")
            dmg = int(tb.get("damage") or 0)
            mana = int(tb.get("mana_left") or 0)
            children = tb.get("children") or []
            mid = [str(s) for s in (tb.get("mid") or [])]

            if children:
                # 次级：完整中间路径（如 行骗(牛)-暗(刀2)-步(刀2)-…-持枪要挟）
                node = self._item(join_steps(mid) if mid else "")
                root_item.addChild(node)

                # 次次级：持枪要挟发现结果
                for ch in children:
                    card = str(ch.get("card") or "")
                    cdmg = int(ch.get("damage") or 0)
                    cmana = int(ch.get("mana_left") or 0)
                    # 叶子：从该发现牌的步骤开始到结尾的完整路径
                    tail = WhatIFTreeWidget._tail_steps(
                        ch.get("path") or [], card
                    )
                    leaf = self._item(
                        (join_steps(tail) if tail else "")
                        + f"({cdmg}伤余{cmana}费)"
                    )
                    node.addChild(leaf)
            else:
                # 次级本身就是叶子：完整路径
                node = self._item(
                    (join_steps(mid) if mid else "")
                    + f"({dmg}伤余{mana}费)"
                )
                root_item.addChild(node)

        # 默认折叠（用户自行展开查看各分支/路径）

    @staticmethod
    def _tail_steps(path: List[str], start_marker: str) -> List[str]:
        """从路径中第一个含 start_marker 的步骤（分支卡标注）开始取尾部；
        找不到时返回完整路径。"""
        for i, s in enumerate(path):
            if start_marker and start_marker in str(s):
                return [str(x) for x in path[i:]]

        return [str(x) for x in path]


def _whatif_tree_text(data: Dict[str, object]) -> str:
    """WhatIF 指引树日志文本：根 → 次级（抽取结果完整路径）→ 次次级（发现结果完整路径）。"""
    tree = data.get("whatif_tree") or {}
    branches = tree.get("branches") or []

    if not branches:
        return ""

    root_steps = [str(s) for s in (tree.get("root") or [])]
    lines = [
        "WhatIF指引树：",
        "-".join(abbreviate_step(s, compact=False) for s in root_steps)
        if root_steps
        else "",
    ]

    for tb in branches:
        dmg = int(tb.get("damage") or 0)
        mana = int(tb.get("mana_left") or 0)
        children = tb.get("children") or []
        mid = [str(s) for s in (tb.get("mid") or [])]
        mid_text = "-".join(abbreviate_step(s, compact=False) for s in mid)

        if children:
            lines.append("    " + mid_text)

            for ch in children:
                card = str(ch.get("card") or "")
                cdmg = int(ch.get("damage") or 0)
                cmana = int(ch.get("mana_left") or 0)
                tail = WhatIFTreeWidget._tail_steps(ch.get("path") or [], card)
                tail_text = "-".join(
                    abbreviate_step(s, compact=False) for s in tail
                )
                lines.append(
                    "        "
                    + tail_text
                    + f"({cdmg}伤余{cmana}费)"
                )
        else:
            lines.append("    " + mid_text + f"({dmg}伤余{mana}费)")

    lines.append(f"保底伤害：{int(tree.get('worst') or 0)}（最差随机组合）")
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
            common_kwargs = {
                "min_alex": int(self.options["min_alex"]),
                "max_alex": int(self.options["max_alex"]),
                "depth": int(self.options["depth"]),
                "max_paths": int(self.options["max_paths"]),
                "threads": int(self.options.get("threads", 4)),
                "time_budget_sec": float(self.options.get("time_budget_sec", 3.0)),
                "wide_widths": wide_widths,
                "heuristics": heuristics,
                "etc_band": list(self.options.get("etc_band") or []),
                "only_best_damage": bool(self.options.get("only_best_damage", True)),
                # W-B 重写：抽随从卡分支直接进束宽搜索（由 C++ 引擎展开，
                # 主窗口“W-B机制”勾选框控制是否展开；持枪要挟分支同样在引擎内）。
                "branch_expand": bool(self.options.get("draw_whatif", True)),
            }
            # 场面交换：按价值取前 N 个场面（默认3）分别计算，保留最高伤害的结果
            top_n = max(1, int(self.options.get("exchange_top_n", 3)))
            # 计划列表由主窗口预计算后传入（含手动输入/自动排序+差异化）
            exchange_plans = list(self.options.get("exchange_plans") or [])

            if not exchange_plans:
                exchange_plans = [[]]

            result: Optional[Dict[str, object]] = None
            best_exchange: List[Tuple[int, int]] = list(exchange_plans[0])
            # 框3：需要场面交换调用多个场面分别计算时启用精确截断（框1 也照样生效）
            multi_scene = len(exchange_plans) > 1
            use_exchange_trunc = multi_scene and bool(
                self.options.get("truncate_exchange", True)
            )

            for ex in exchange_plans:
                if self._stop:
                    break

                ex_list = list(ex)
                res = engine.compute(
                    self.snapshot,
                    lethal_threshold=(
                        _lethal_threshold(self.snapshot, ex_list)
                        if (
                            bool(self.options.get("truncate_normal", False))
                            or use_exchange_trunc
                        )
                        else -1
                    ),
                    progress_callback=self.progress.emit,
                    found_callback=self.found.emit,
                    should_stop=lambda: self._stop,
                    exchanges=ex_list,
                    **common_kwargs,
                )

                if result is None or (res.get("max_damage") or 0) > (result.get("max_damage") or 0):
                    result = res
                    best_exchange = ex_list

            if result is None:
                result = engine.compute(
                    self.snapshot,
                    progress_callback=self.progress.emit,
                    found_callback=self.found.emit,
                    should_stop=lambda: self._stop,
                    exchanges=[],
                    **common_kwargs,
                )

            # ========== 统一 W-B 机制（WhatIf-Branch）==========
            # 分支卡（抽随从卡/持枪要挟）的全部分支直接在 C++ 束宽搜索内展开；
            # 主结果含持枪要挟时，从分支点对五个发现牌各做一次独立回溯搜索
            # （记忆节点：公共前缀只重放一次；独立搜索避免束宽内分支竞争漏算），
            # 得到各分支最高伤路径（quickdraw_branches），再求平均（whatif_average）。
            # 原版：C++ 直接输出不含持枪要挟的最优路径（original）。
            result["wb"] = None
            result["draw_whatif"] = None
            quickdraw_branches: Optional[List[Dict[str, object]]] = []
            draw_branches: Optional[List[Dict[str, object]]] = None
            whatif_average: Optional[Dict[str, float]] = None
            whatif_tree: Optional[Dict[str, object]] = None

            if bool(self.options.get("draw_whatif", True)):
                best_path = (result.get("results") or [{}])
                best_path = list((best_path[0].get("path") or []) if best_path else [])
                # 指引树（WhatIF）：一条指引路径 + 中间随机岔路。
                # 行骗/挖掘宝藏/潜伏帷幕/垂钓时光 的每个抽取结果作为次级节点，
                # 其后的持枪要挟发现结果作为次次级节点；叶子伤害的最小值 = 保底伤害
                # （无论随机结果如何都不低于它）。
                draw_markers = ("行骗", "挖掘宝藏", "潜伏帷幕", "垂钓时光")
                di = next(
                    (
                        i
                        for i, s in enumerate(best_path)
                        if any(m in str(s or "") for m in draw_markers)
                    ),
                    -1,
                )

                if di > 0 and self._stop is False:
                    prefix_draw = list(best_path[:di])
                    # 分支点缺失池 = 界面勾选的卡组随从 - 手牌/战场已有随从
                    # （第一个抽牌分支点之前没有抽牌，快照手牌/战场即分支点手牌/战场）
                    checked = list(self.options.get("whatif_combo") or [])
                    have = {
                        str(h.get("name", ""))
                        for h in (self.snapshot.get("hand") or [])
                    } | {
                        str(b.get("name", ""))
                        for b in (self.snapshot.get("board") or [])
                    }
                    missing_draw = [m for m in checked if m not in have]
                    main_draw_key = ""
                    m2 = re.search(r"[（(](.+?)[）)]", str(best_path[di]))

                    if m2:
                        main_draw_key = m2.group(1)

                    # 根：分支卡前的步骤 + 分支卡名（去掉抽取标注，如 行骗（牛）→ 行骗）
                    root_steps = [str(s) for s in best_path[:di]]
                    branch_card = re.sub(r"[（(].*[）)]$", "", str(best_path[di]))
                    root_steps.append(branch_card)
                    tree_branches: List[Dict[str, object]] = []
                    # 性能优化：主线抽取（如 行骗(牛)）用 11s + 束宽 3000 挖深线
                    # （96 伤），其 C++ quickdraw_branches 已含各发现牌的深线结果
                    # （脱水=96/补水=80…），不再逐张发现牌独立搜索；
                    # 其余抽取 5s（结果本来就较低，无需深挖）。
                    is_auto_beam = int(self.options.get("beam_width") or 0) <= 0

                    for mn in missing_draw:
                        if self._stop:
                            break

                        is_main = mn == main_draw_key
                        # 主线抽取 11s（96 深线）；其余抽取 6s（狐分支行骗[殒]抽牛=48）
                        draw_budget = 11.0 if is_main else 6.0
                        draw_kwargs = dict(common_kwargs)
                        draw_kwargs["time_budget_sec"] = draw_budget
                        # 96 深线需约 150 万展开：1M 上限会提前截断，叶子数据变脏
                        draw_kwargs["max_paths"] = max(
                            3000000, int(self.options.get("max_paths") or 0)
                        )

                        if is_auto_beam:
                            draw_kwargs["wide_widths"] = [3000]

                        res_i = engine.compute(
                            self.snapshot,
                            branch_prefix=prefix_draw,
                            forced_draw_choice=mn,
                            exchanges=best_exchange,
                            **draw_kwargs,
                        )
                        best_i = next(
                            (
                                b
                                for b in (res_i.get("draw_branches") or [])
                                if b.get("card") == mn and (b.get("path") or [])
                            ),
                            (res_i.get("results") or [{}])[0],
                        )
                        path_i = list(best_i.get("path") or [])
                        # 强制搜索可能把抽牌卡放到不同位置（如 暗(刀2) 先行骗后），
                        # 必须定位真正的抽取步骤，不能沿用主线里的下标 di。
                        ddi = next(
                            (
                                j
                                for j, s in enumerate(path_i)
                                if mn in str(s)
                                and any(m in str(s) for m in draw_markers)
                            ),
                            di,
                        )
                        # 该抽取结果之后的下一个分支卡（持枪要挟 / 再次抽牌）
                        nd = next(
                            (
                                j
                                for j in range(ddi + 1, len(path_i))
                                if "持枪要挟" in str(path_i[j])
                                or any(m in str(path_i[j]) for m in draw_markers)
                            ),
                            -1,
                        )
                        node: Dict[str, object] = {
                            "outcome": mn,
                            "damage": int(best_i.get("damage") or 0),
                            "dragons": int(best_i.get("dragons") or 0),
                            "mana_left": int(best_i.get("mana") or 0),
                            "path": path_i,
                        }

                        if nd > 0:
                            next_card = str(path_i[nd])

                            if "持枪要挟" in next_card:
                                node["next"] = "持枪要挟"
                                node["mid"] = [str(s) for s in path_i[ddi : nd + 1]]
                                node["children"] = [
                                    {
                                        "card": c.get("card") or "",
                                        "damage": int(c.get("damage") or 0),
                                        "dragons": int(c.get("dragons") or 0),
                                        "mana_left": int(c.get("mana_left") or 0),
                                        "path": list(c.get("path") or []),
                                    }
                                    for c in (res_i.get("quickdraw_branches") or [])
                                ]
                            else:
                                # 抽牌结果后又抽牌（如 行骗 → 行骗[殒]）
                                node["next"] = re.sub(
                                    r"[（(].*[）)]$", "", next_card
                                )
                                node["mid"] = [str(s) for s in path_i[ddi : nd + 1]]
                                node["children"] = [
                                    {
                                        "card": c.get("card") or "",
                                        "damage": int(c.get("damage") or 0),
                                        "dragons": int(c.get("dragons") or 0),
                                        "mana_left": int(c.get("mana_left") or 0),
                                        "path": list(c.get("path") or []),
                                    }
                                    for c in (res_i.get("draw_branches") or [])
                                ]
                        else:
                            node["mid"] = [str(s) for s in path_i[ddi:]]

                        tree_branches.append(node)

                    if tree_branches:
                        # 保底伤害 = 所有叶子（完整随机组合）的最小伤害
                        leaf_damages: List[int] = []

                        for tb in tree_branches:
                            if tb.get("children"):
                                leaf_damages.extend(
                                    int(ch.get("damage") or 0)
                                    for ch in tb["children"]
                                )
                            else:
                                leaf_damages.append(int(tb.get("damage") or 0))

                        whatif_tree = {
                            "root": root_steps,
                            "branches": tree_branches,
                            "worst": min(leaf_damages) if leaf_damages else 0,
                            "main_outcome": main_draw_key,
                        }
                        # 平均沿用主线（主抽取结果）的持枪叶子加权平均
                        main_branch = next(
                            (
                                tb
                                for tb in tree_branches
                                if tb.get("outcome") == main_draw_key
                            ),
                            None,
                        )
                        main_children = main_branch.get("children") if main_branch else None

                        if main_children:
                            quickdraw_branches = list(main_children)
                            total_w = sum(
                                int(
                                    engine.QUICKDRAW_WEIGHTS.get(
                                        ch.get("card") or "", 1
                                    )
                                )
                                for ch in main_children
                            )
                            whatif_average = {
                                "damage": sum(
                                    int(ch.get("damage") or 0)
                                    * int(
                                        engine.QUICKDRAW_WEIGHTS.get(
                                            ch.get("card") or "", 1
                                        )
                                    )
                                    for ch in main_children
                                )
                                / total_w,
                                "dragons": sum(
                                    int(ch.get("dragons") or 0)
                                    * int(
                                        engine.QUICKDRAW_WEIGHTS.get(
                                            ch.get("card") or "", 1
                                        )
                                    )
                                    for ch in main_children
                                )
                                / total_w,
                            }

            result["quickdraw_branches"] = quickdraw_branches or None
            result["draw_branches"] = draw_branches
            result["whatif_average"] = whatif_average
            result["whatif_tree"] = whatif_tree
            result["original"] = result.get("original") or None

            # 静默云端上报数据：场面数据 + 最高伤路径（含交换/预处理/分支完整记录）
            result["upload_payload"] = cloud_report.build_payload(
                self.snapshot, result, best_exchange
            )
            self.finished_ok.emit(result)
        except InterruptedError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - 统一回传 GUI 显示
            self.failed.emit(f"{type(exc).__name__}: {exc}")

    def _process_wb_node(
        self,
        node: Dict[str, object],
        branch_prefix: List[str],
        best_exchange: List[Tuple[int, int]],
        common_kwargs: Dict[str, object],
    ) -> None:
        """递归回溯 W-B 节点：draw 分支在变体上真实搜索，quickdraw 分支用 discover 回溯；
        分支内 children 继续递归处理。"""
        for br in node.get("branches") or []:
            if self._stop:
                break

            if node.get("kind") == "draw":
                variant = br.pop("variant", None)

                if variant:
                    res2 = engine.compute(
                        variant,
                        exchanges=best_exchange,
                        lethal_threshold=(
                            _lethal_threshold(variant, best_exchange)
                            if bool(self.options.get("truncate_branch", True))
                            else -1
                        ),
                        should_stop=lambda: self._stop,
                        **common_kwargs,
                    )
                    best2 = (res2.get("results") or [{}])[0]
                    real_damage = int(res2.get("max_damage") or 0)
                    br["damage"] = real_damage
                    br["dragons"] = int(res2.get("max_dragons") or 0)
                    br["mana_left"] = int(best2.get("mana") or 0)
                    br["path"] = (
                        [node["card"]] + list(best2.get("path") or [])
                        if real_damage > 0
                        else []
                    )
            else:
                choice = br.get("card") or ""
                br2 = engine.compute(
                    self.snapshot,
                    discover_quickdraw_choice=choice,
                    branch_prefix=branch_prefix,
                    lethal_threshold=(
                        _lethal_threshold(self.snapshot, best_exchange)
                        if bool(self.options.get("truncate_branch", True))
                        else -1
                    ),
                    should_stop=lambda: self._stop,
                    exchanges=best_exchange,
                    **common_kwargs,
                )
                best2 = (br2.get("results") or [{}])[0]
                br["damage"] = int(best2.get("damage") or 0)
                br["dragons"] = int(best2.get("dragons") or 0)
                br["mana_left"] = int(best2.get("mana") or 0)
                br["path"] = best2.get("path") or []

            children = br.get("children") or {}

            for child in children.get("nodes") or []:
                self._process_wb_node(
                    child, branch_prefix, best_exchange, common_kwargs
                )


# ---------- 手动输入解析 ----------

_SECTION_RE = re.compile(r"^\s*(当前效果|牌库中|手牌中|战场|随从|其他)\s*[（(]?\s*\d*\s*[）)]?\s*$")
_COST_ONLY_RE = re.compile(r"^\s*(\d+)\s*费?\s*$")
_STAR_COST_RE = re.compile(r"^[*★☆＊]+\s*(.+)$")
_COMMA_ZONE_RE = re.compile(r"^\s*(\d+)\s*[,，、]\s*(\d+)\s*血?\s*(.+)$")


def _hero_text(hero: Optional[Dict[str, object]]) -> str:
    """英雄血量/护甲显示：如 22血/5甲；无数据时显示 ?。"""
    if not hero:
        return "?"

    hp = hero.get("health")
    armor = hero.get("armor") or 0
    return f"{hp if hp is not None else '?'}血/{armor}甲"


def _lethal_threshold(
    snapshot: Dict[str, object],
    exchanges: Optional[List[tuple]] = None,
) -> int:
    """精确截断阈值 = 敌方英雄血量 + 护甲（扣除场面交换里攻击英雄的伤害）；
    无数据（-1）表示不截断。"""
    hero = snapshot.get("opponent_hero") or {}
    hp = hero.get("health")

    if not isinstance(hp, int) or hp <= 0:
        return -1

    total = hp + int(hero.get("armor") or 0)

    if exchanges:
        board = snapshot.get("board") or []

        for fi, ei in exchanges:
            if ei == 0 and 1 <= fi <= len(board):
                total = max(0, total - int(board[fi - 1].get("attack") or 0))

    return max(1, total) if total >= 0 else -1


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

        # 自定义路径（QSettings 记忆）：手动指定的游戏目录 / Power.log，解决自动搜不到日志的问题
        path_settings = QSettings("RedDragonCalculator", "main")
        self._custom_game_dir = str(path_settings.value("custom_game_dir", "") or "")
        self._custom_log_file = str(path_settings.value("custom_log_file", "") or "")
        self.watcher = LogWatcher(
            game_dir=self._custom_game_dir or None,
            log_file=self._custom_log_file or None,
        )
        self.snapshot: Dict[str, object] = {}
        self.worker: Optional[CalculationWorker] = None
        self.mini_window: Optional[MiniWindow] = None
        self.wb_window: Optional["WBTreeWindow"] = None
        self._last_state_key = ""
        self._manual_mode = False
        self.etc_checks: List[Tuple[str, QCheckBox]] = []
        self._auto_exchange_cache: Optional[Tuple[str, List[Tuple[int, int]]]] = None
        self._latest_version: Optional[str] = None

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
        self.path_menu_button = QPushButton("自定义路径")
        self.path_menu_button.setToolTip(
            "手动指定炉石安装目录或 Power.log，解决自动搜不到日志的问题；选择会被记住，重启后仍生效"
        )
        path_menu = QMenu(self)
        path_menu.addAction("选择游戏目录…", self.pick_game_dir)
        path_menu.addAction("选择 Power.log 文件…", self.pick_log_file)
        path_menu.addAction("恢复自动检测", self.reset_auto_detect)
        self.path_menu_button.setMenu(path_menu)
        status.addWidget(self.path_menu_button)
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
        self.hero_label = QLabel("英雄：-")
        self.effects_label = QLabel("当前效果：无")
        self.etc_summary_label = QLabel("牛池：-")
        state_grid.addWidget(self.game_label, 0, 0)
        state_grid.addWidget(self.mana_label, 0, 1)
        state_grid.addWidget(self.deck_label, 1, 0)
        state_grid.addWidget(self.weapon_label, 1, 1)
        state_grid.addWidget(self.secrets_label, 2, 0)
        state_grid.addWidget(self.hero_label, 2, 1)
        state_grid.addWidget(self.effects_label, 3, 1)
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

        # 牌库剩余随从（如果机制抽牌池）：未勾选 = 已不在牌库，不会被抽到
        combo_box = QGroupBox("牌库剩余随从（如果机制抽牌池）")
        combo_grid = QGridLayout(combo_box)
        combo_grid.setSpacing(4)
        self.combo_checks: List[Tuple[str, QCheckBox]] = []

        for i, (card_name, label) in enumerate(COMBO_MINION_CHECKS):
            box = QCheckBox(label)
            # 默认勾选 鱼狐刀暗牛晦；腾武默认不勾（由玩家手动勾选，程序不会自动改）
            box.setChecked(card_name != "赤烟·腾武")
            box.setToolTip(card_name)
            box.toggled.connect(self._sync_mini_window)
            self.combo_checks.append((card_name, box))
            combo_grid.addWidget(box, i // 4, i % 4)

        left_layout.addWidget(combo_box)

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
        self.draw_whatif_check = QCheckBox("W-B机制：抽随从卡/持枪要挟分支直接进束宽搜索")
        self.draw_whatif_check.setChecked(True)
        self.draw_whatif_check.setToolTip(
            "W-B 重写：抽随从卡（挖掘宝藏/潜伏帷幕）按卡组随从勾选直接展开全部分支"
            "进束宽搜索，持枪要挟按发现牌池展开（路径标注（刀狐）/（误炸））；"
            "不勾选时按旧逻辑把抽牌卡当杂牌打出（不展开抽随从分支）。"
        )
        param_grid.addWidget(self.draw_whatif_check, 10, 0, 1, 2)
        # W-B 机制参数：抽随从卡分支数 / 持枪要挟分支数 / 分支节点 TOP-K
        branch_top_row = QHBoxLayout()
        branch_top_row.addWidget(QLabel("抽随从卡分支数："))
        self.whatif_branch_top_k = self._spin(3, 0, 10)
        self.whatif_branch_top_k.setToolTip(
            "抽随从卡分支计算数量（默认 3）：候选 = 自选组合剩余未到手随从的抽取组合数，"
            "按 [场面变化] 增量评分只取前 K 个分支计算；0=不计算"
        )
        branch_top_row.addWidget(self.whatif_branch_top_k)
        branch_top_row.addWidget(QLabel("持枪要挟分支数："))
        self.branch_top_k = self._spin(5, 0, 5)
        self.branch_top_k.setToolTip(
            "持枪要挟分支计算数量（默认 5 = 已知全部：补水/脱水/误炸/袋底藏沙/不许乱动），"
            "按 [场面变化] 增量评分取前 K 个；0=不计算"
        )
        branch_top_row.addWidget(self.branch_top_k)
        branch_top_row.addWidget(QLabel("分支节点TOP-K："))
        self.wb_node_top_k = self._spin(3, 0, 5)
        self.wb_node_top_k.setToolTip(
            "分支节点 TOP-K（默认 3）：按各分支卡最佳分支的增量评分排序，"
            "最多探索前 K 个分支节点，其余不往下展开"
        )
        branch_top_row.addWidget(self.wb_node_top_k)
        param_grid.addLayout(branch_top_row, 11, 0, 1, 2)
        # 精确截断：伤害 ≥ 敌方血量+护甲 即停（加速计算）
        trunc_row = QHBoxLayout()
        trunc_row.addWidget(QLabel("精确截断加速："))
        self.truncate_normal_check = QCheckBox("框1")
        self.truncate_normal_check.setChecked(False)
        self.truncate_normal_check.setToolTip(
            "框1=正常计算：勾选后正常计算搜到 伤害 ≥ 敌方英雄血量+护甲 即停（只求斩杀线，不再追最高伤）"
        )
        self.truncate_branch_check = QCheckBox("框2=W-B机制")
        self.truncate_branch_check.setChecked(True)
        self.truncate_branch_check.setToolTip(
            "框2=W-B机制：抽随从卡/持枪要挟各分支回溯计算搜到 伤害 ≥ 敌方英雄血量+护甲 即停（加速分支计算）"
        )
        self.truncate_exchange_check = QCheckBox("框3")
        self.truncate_exchange_check.setChecked(True)
        self.truncate_exchange_check.setToolTip(
            "框3=场面交换：需要场面交换调用多个场面分别计算时，"
            "每个场面搜到 伤害 ≥ 敌方英雄血量+护甲 即停（加速多场面计算）"
        )
        trunc_row.addWidget(self.truncate_normal_check)
        trunc_row.addWidget(self.truncate_branch_check)
        trunc_row.addWidget(self.truncate_exchange_check)
        trunc_row.addStretch(1)
        param_grid.addLayout(trunc_row, 12, 0, 1, 2)
        right_layout.addWidget(param_box)

        run_row = QHBoxLayout()
        self.calc_button = QPushButton("开始计算")
        self.calc_button.clicked.connect(self.on_calc_toggle)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        self.engine_label = QLabel("")
        self.wb_window_button = QPushButton("W-B分支树")
        self.wb_window_button.setToolTip("在独立大窗口查看 W-B 机制分支树")
        self.wb_window_button.clicked.connect(self.open_wb_window)
        self.update_button = QPushButton("立即更新")
        self.update_button.setToolTip("点击跳转到发布仓库页面")
        self.update_button.clicked.connect(self._on_update_clicked)
        self.update_button.setFixedSize(160, 36)
        run_row.addWidget(self.calc_button)
        # 免责声明：计算完成后静默上传公式到云端公式库（紧挨开始计算）
        self.disclaimer_label = QLabel(
            '<span style="font-size:11px;color:#888;">'
            '免责声明：计算出的公式将上传云端公式库造福更多人喵~'
            '</span>'
        )
        self.disclaimer_label.setWordWrap(False)
        run_row.addWidget(self.disclaimer_label)
        run_row.addWidget(self.progress_bar, 1)
        run_row.addWidget(self.engine_label)
        # 更新按钮：大一点，放到最右侧空白区域
        run_row.addWidget(self.wb_window_button)
        run_row.addWidget(self.update_button)
        right_layout.addLayout(run_row)

        result_box = QGroupBox("计算结果")
        result_layout = QVBoxLayout(result_box)
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumBlockCount(5000)
        result_layout.addWidget(self.result_text)
        self.whatif_tree = WhatIFTreeWidget()
        self.whatif_tree.setVisible(False)
        result_layout.addWidget(self.whatif_tree)
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
        exchange_row.addWidget(QLabel("计算场面数："))
        self.exchange_count = QSpinBox()
        self.exchange_count.setRange(1, 10)
        self.exchange_count.setValue(3)
        self.exchange_count.setToolTip(
            "调试选项：场面交换按价值排序，取前 N 个场面分别计算取最优（默认 3；"
            "调大更全面但计算更慢）"
        )
        exchange_row.addWidget(self.exchange_count)
        exchange_row.addWidget(QLabel("差异化："))
        self.exchange_diversity = QDoubleSpinBox()
        self.exchange_diversity.setRange(0.0, 1.0)
        self.exchange_diversity.setSingleStep(0.1)
        self.exchange_diversity.setValue(0.6)
        self.exchange_diversity.setToolTip(
            "调试选项（temperature）：0=纯按价值取前 N 个场面；"
            ">0 时鼓励覆盖不同的随从栏空位/英雄血量档/存活随从，"
            "让可能产生最优解的异质场面也有机会入选（默认 0.6）"
        )
        exchange_row.addWidget(self.exchange_diversity)
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

    # ---------- 自定义路径 ----------

    def _apply_custom_path(
        self,
        game_dir: Optional[str],
        log_file: Optional[str],
    ) -> None:
        self._custom_game_dir = game_dir or ""
        self._custom_log_file = log_file or ""
        self.watcher = LogWatcher(
            game_dir=self._custom_game_dir or None,
            log_file=self._custom_log_file or None,
        )
        self._manual_mode = False
        self._last_state_key = ""
        self.refresh_log()

        if not self._manual_mode and self.watcher.log_file is None:
            QMessageBox.warning(
                self,
                "未找到日志",
                "所选路径下未找到 Power.log：\n"
                "1) 游戏目录需包含 Logs 子目录（内含会话文件夹）；\n"
                "2) 需在 log.config 中开启 [Power] 详细日志；\n"
                "3) 若选了文件请确认它就是 Power.log。",
            )

    def pick_game_dir(self) -> None:
        start = self._custom_game_dir or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "选择炉石安装目录（含 Logs 子目录）", start
        )

        if not chosen:
            return

        settings = QSettings("RedDragonCalculator", "main")
        settings.setValue("custom_game_dir", chosen)
        settings.remove("custom_log_file")
        self._apply_custom_path(chosen, None)

    def pick_log_file(self) -> None:
        start = self._custom_log_file or str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Power.log 文件",
            start,
            "日志文件 (*.log);;所有文件 (*)",
        )

        if not chosen:
            return

        settings = QSettings("RedDragonCalculator", "main")
        settings.setValue("custom_log_file", chosen)
        settings.remove("custom_game_dir")
        self._apply_custom_path(None, chosen)

    def reset_auto_detect(self) -> None:
        settings = QSettings("RedDragonCalculator", "main")
        settings.remove("custom_game_dir")
        settings.remove("custom_log_file")
        self._apply_custom_path(None, None)

    def _apply_snapshot(self, snap: Dict[str, object]) -> None:
        self.snapshot = snap
        log_path = snap.get("log_path")

        if self._manual_mode:
            self.status_label.setText("数据源：手动输入")
        elif self._custom_log_file:
            self.status_label.setText(f"数据源：Power.log（自定义：{self._custom_log_file}）")
        elif self._custom_game_dir:
            self.status_label.setText(
                f"数据源：Power.log（自定义目录：{self._custom_game_dir}；{log_path or '未找到对局'}）"
            )
        else:
            self.status_label.setText(f"数据源：Power.log（{log_path or '未找到对局'}）")

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

        self.hero_label.setText(
            f"英雄：我方 {_hero_text(snap.get('player_hero'))}"
            f"　敌方 {_hero_text(snap.get('opponent_hero'))}"
        )

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
            "exchange_top_n": self.exchange_count.value(),
            "exchange_diversity": self.exchange_diversity.value(),
            "exchange_plans": self.current_exchange_plans(self.exchange_count.value()),
            "only_best_damage": self.best_only_check.isChecked(),
            "draw_whatif": self.draw_whatif_check.isChecked(),
            "whatif_combo": [name for name, box in self.combo_checks if box.isChecked()],
            "branch_top_k": self.branch_top_k.value(),
            "whatif_branch_top_k": self.whatif_branch_top_k.value(),
            "wb_node_top_k": self.wb_node_top_k.value(),
            "truncate_normal": self.truncate_normal_check.isChecked(),
            "truncate_branch": self.truncate_branch_check.isChecked(),
            "truncate_exchange": self.truncate_exchange_check.isChecked(),
        }

    def current_exchange_plans(self, top_n: int) -> List[List[Tuple[int, int]]]:
        """场面交换方案：手动输入优先；留空时用独立的场面交换搜索按价值取前 top_n 个。

        场面交换搜索与路径搜索分离——评分含我方随从栏（空位数/序号位置）与
        敌方英雄血量（剩余总血量所需龙数越少越好），按价值排序取前 top_n 个场面，
        结果按场面指纹缓存。
        """
        board_len = len(self.snapshot.get("board") or [])
        enemy_len = len(self.snapshot.get("enemy_board") or [])
        text = self.exchange_edit.text().strip()

        if text:
            pairs, _warnings = parse_exchange_text(text, board_len, enemy_len)
            board = self.snapshot.get("board") or []

            # 0 攻随从无法主动攻击（以场面当前攻击为准），交换不生效
            manual = [
                (friend_index, enemy_index)
                for friend_index, enemy_index in pairs
                if int(board[friend_index - 1].get("attack") or 0) >= 1
            ]
            return [manual]

        board = self.snapshot.get("board") or []
        enemy = self.snapshot.get("enemy_board") or []
        hand = self.snapshot.get("hand") or []
        etc_band = self.snapshot.get("etc_band")
        hero = self.snapshot.get("opponent_hero") or {}
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
            + "|"
            + json.dumps(hero, ensure_ascii=False)
        )

        if self._auto_exchange_cache is not None and self._auto_exchange_cache[0] == fingerprint:
            return list(self._auto_exchange_cache[1])

        # 一次算全量排序清单（上限 1000 个计划），按需切片 top_n；
        # 不能只取默认 top_n=3，否则 N=4~10 的调试框拿不到更多场面。
        diversity = float(self.exchange_diversity.value())
        ranked = engine.plan_exchanges_top(
            board, enemy, hand=hand, etc_band=etc_band, hero=hero,
            top_n=1000, diversity=diversity,
        )
        plans = [list(plan) for plan, _rb, _sc, _feat in ranked]
        self._auto_exchange_cache = (fingerprint, plans)
        return plans[:top_n]

    def current_exchange_pairs(self) -> List[Tuple[int, int]]:
        """最优单个场面交换方案（供显示/手动路径使用）。"""
        plans = self.current_exchange_plans(1)
        return list(plans[0]) if plans else []

    def _update_calc_enabled(self) -> None:
        has_state = bool(self.snapshot.get("hand")) or bool(self.snapshot.get("board"))
        self.calc_button.setEnabled(has_state and self.worker is None)

    # ---------- 更新检测 ----------

    def set_latest_version(self, latest: Optional[str]) -> None:
        """后台检测到最新版本后更新按钮提示（主线程调用）。"""
        self._latest_version = latest

        if latest:
            self.update_button.setToolTip(
                f"最新版本 v{latest.lstrip('vV')}；点击跳转到发布仓库页面"
            )
        else:
            self.update_button.setToolTip("点击跳转到发布仓库页面")

    def _on_update_clicked(self) -> None:
        """立即更新按钮：无论当前是否最新版本，都跳转到发布仓库页面。"""
        webbrowser.open(RELEASE_URL)

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

        lines.append(
            f"  我方英雄：{_hero_text(snapshot.get('player_hero'))}　"
            f"敌方英雄：{_hero_text(snapshot.get('opponent_hero'))}"
        )

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
        # 主窗口结果 = 正常线（V1.2.1 逻辑：行骗/挖掘宝藏/潜伏帷幕/垂钓时光可打出，
        # 抽牌按“抽杂牌”确定性处理），与 小窗 正常线 段完全一致。
        orig = data.get("original") or {}
        normal_dmg = int(orig.get("damage") or 0)
        normal_dragons = int(orig.get("dragons") or 0)
        normal_path = list(orig.get("path") or [])
        results = data.get("results") or []
        lines = [
            "搜索方式：纯束宽搜索",
            f"最大伤害：{normal_dmg}，最大龙数：{normal_dragons}",
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

        # 正常线（与 V1.2.1 一致）：抽卡当杂牌打出，结果唯一确定
        lines.append("正常线：")

        if normal_dmg > 0 and normal_path:
            lines.append("  " + " → ".join(str(s) for s in normal_path))
        else:
            lines.append("  （无路径）")

        # WhatIF 树：行骗/持枪要挟同级分支（含 96 等分支最优）→ QTreeWidget 展示
        has_whatif = bool(
            data.get("whatif_tree")
            or data.get("quickdraw_branches")
            or data.get("draw_branches")
        )
        self.whatif_tree.set_whatif(data, colors=self.mini_color_enabled())
        self.whatif_tree.setVisible(has_whatif)

        wb = data.get("wb")
        if wb:
            lines.append("")
            lines.extend(_wb_tree_lines(wb))

        text = "\n".join(lines)
        self.result_text.setPlainText(text)

        try:
            LOGS_DIR.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = LOGS_DIR / f"red_dragon_all_paths_{stamp}.txt"
            # 日志额外记录 WhatIF 指引树（bug1：此前只存了正常线文本，树没进日志）
            tree_text = _whatif_tree_text(data)
            out_path.write_text(
                text + ("\n\n" + tree_text if tree_text else ""),
                encoding="utf-8",
            )
            self.engine_label.setText(f"结果已保存：{out_path.name}")
        except OSError as exc:
            self.engine_label.setText(f"结果保存失败：{exc}")

        # 静默上传计算记录到云端公式库（后台线程，不阻塞、不弹窗）
        cloud_report.upload_async(data.get("upload_payload"))
        self._show_wb_tree(data.get("wb"))
        self._sync_mini_result(data)

    def _show_wb_tree(self, wb: Optional[Dict[str, object]]) -> None:
        """W-B 分支树在独立大窗口展示（有数据则填充并显示）。"""
        if self.wb_window is None:
            self.wb_window = WBTreeWindow(self)

        self.wb_window.set_wb(wb)

        if wb:
            self.wb_window.show()
            self.wb_window.raise_()
            self.wb_window.activateWindow()

    def open_wb_window(self) -> None:
        """手动打开 W-B 分支树窗口。"""
        if self.wb_window is None:
            self.wb_window = WBTreeWindow(self)

        self.wb_window.show()
        self.wb_window.raise_()
        self.wb_window.activateWindow()

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

# 牌库剩余随从（如果机制抽牌池）：勾选 = 仍在牌库（可被抽随从卡抽到）
COMBO_MINION_CHECKS = [
    ("鲨鱼之灵", "鱼"),
    ("狐人老千", "狐"),
    ("斯卡布斯·刀油", "刀"),
    ("暗影施法者", "暗"),
    ("乐队经理精英牛头人酋长", "牛"),
    ("晦鳞巢母", "晦"),
    ("赤烟·腾武", "腾"),
]


class WBTreeWindow(QDialog):
    """W-B 机制分支树独立大窗口：经典多叉树布局（根在上、兄弟横排、子节点在下，
    兄弟按伤害/增量广度优先排序，深度优先遍历绘制）；收起时只显示 DFS 主干链。
    QGraphicsView 自带底部/右侧滚动条。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("W-B机制分支树(CreATedBy此人乃天下绝响#5854)")
        self.resize(1100, 800)

        layout = QVBoxLayout(self)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene, self)
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        layout.addWidget(self.view, 1)
        self.expand_check = QCheckBox("展开分支")
        self.expand_check.setChecked(False)
        self.expand_check.toggled.connect(lambda _checked: self._relayout())
        layout.addWidget(self.expand_check)
        self._wb: Optional[Dict[str, object]] = None

    def set_wb(self, wb: Optional[Dict[str, object]]) -> None:
        """填充 W-B 分支树。"""
        self._wb = wb
        self._relayout()

    def _relayout(self) -> None:
        self.scene.clear()

        if not self._wb:
            return

        expanded = self.expand_check.isChecked()
        nodes = self._wb.get("nodes") or []

        if expanded:
            root = self._build_tree(nodes)
        else:
            root = self._build_trunk(nodes)

        self._layout(root)
        self._draw(root, 0, 0)
        rect = self.scene.itemsBoundingRect()
        self.scene.setSceneRect(rect.adjusted(-40, -40, 80, 80))
        self.view.centerOn(rect.left(), rect.top())

    # ---------- 显示树结构 ----------

    class _DispNode:
        __slots__ = ("label", "children", "x", "y", "w", "h", "color")

        def __init__(
            self,
            label: str,
            children: Optional[List["WBTreeWindow._DispNode"]] = None,
            color: str = "#FFFFFF",
        ):
            self.label = label
            self.children = children or []
            self.x = 0.0
            self.y = 0.0
            self.w = 200.0
            self.h = 46.0
            self.color = color

    def _build_tree(self, nodes: List[Dict[str, object]]) -> "_DispNode":
        """构造多叉树：分支卡 → 打法 → 分支（兄弟按伤害排序）→ 递归分支卡。"""
        root = self._DispNode("W-B机制（分支树）", color="#DBEAFE")
        root.children = self._build_nodes(nodes)
        return root

    def _build_nodes(self, nodes: List[Dict[str, object]]) -> List["_DispNode"]:
        """把分支卡节点列表转成显示节点（递归分支卡直接平铺，不再包新根）。"""
        card_nodes: List[WBTreeWindow._DispNode] = []

        for node in nodes:
            card = node.get("card") or ""
            play = node.get("play") or "直接"
            card_node = self._DispNode(f"「{card}」·{play}", color="#FEF3C7")

            branches = sorted(
                node.get("branches") or [],
                key=lambda b: -(b.get("damage") or 0),
            )

            for br in branches:
                drawn = br.get("drawn") or []
                drawn_txt = (
                    "抽到{" + "、".join(drawn) + "}"
                    if drawn
                    else "发现" + str(br.get("card", ""))
                )
                label = (
                    f"{drawn_txt} 增量{br.get('delta', 0)}："
                    f"{br.get('damage', 0)}伤/{br.get('dragons', 0)}龙/"
                    f"余{br.get('mana_left', 0)}费"
                )
                br_node = self._DispNode(label, color="#E0F2FE")
                path = br.get("path") or []

                if path:
                    path_txt = " → ".join(str(s) for s in path[:6])
                    path_txt += " …" if len(path) > 6 else ""
                    br_node.children.append(
                        self._DispNode(path_txt, color="#FFFFFF")
                    )

                children = br.get("children") or {}

                if children.get("nodes"):
                    br_node.children.extend(
                        self._build_nodes(children.get("nodes") or [])
                    )

                card_node.children.append(br_node)

            card_nodes.append(card_node)

        return card_nodes

    def _build_trunk(self, nodes: List[Dict[str, object]]) -> "_DispNode":
        """收起态：只保留 DFS 主干链（每层取最高伤分支）。"""
        root = self._DispNode("W-B机制（主干）", color="#DBEAFE")
        cur = list(nodes)
        parent = root

        while cur:
            best_node = max(
                cur,
                key=lambda n: max(
                    (b.get("damage") or 0) or (b.get("delta") or 0)
                    for b in (n.get("branches") or [{}])
                ),
            )
            card = best_node.get("card") or ""
            play = best_node.get("play") or "直接"
            node_box = self._DispNode(f"「{card}」·{play}", color="#FEF3C7")
            parent.children.append(node_box)
            branches = best_node.get("branches") or []

            if not branches:
                break

            best_br = max(branches, key=lambda b: b.get("damage") or 0)
            drawn = best_br.get("drawn") or []
            drawn_txt = (
                "抽到{" + "、".join(drawn) + "}"
                if drawn
                else "发现" + str(best_br.get("card", ""))
            )
            br_label = (
                f"{drawn_txt} {best_br.get('damage', 0)}伤/"
                f"{best_br.get('dragons', 0)}龙/余{best_br.get('mana_left', 0)}费"
            )
            br_box = self._DispNode(br_label, color="#E0F2FE")
            node_box.children.append(br_box)
            path = best_br.get("path") or []

            if path:
                path_txt = " → ".join(str(s) for s in path[:6])
                path_txt += " …" if len(path) > 6 else ""
                br_box.children.append(self._DispNode(path_txt, color="#FFFFFF"))

            children = best_br.get("children") or {}
            cur = children.get("nodes") or []
            parent = br_box

        return root

    # ---------- 树布局（广度优先排序后自上而下） ----------

    _NODE_GAP_X = 26.0
    _LEVEL_GAP_Y = 70.0

    def _layout(self, node: "_DispNode") -> float:
        """递归计算子树宽度并把子节点 x 设为相对父中心的偏移。返回子树宽度。"""
        if not node.children:
            return node.w

        child_widths = [self._layout(c) for c in node.children]
        total = sum(child_widths) + self._NODE_GAP_X * (len(child_widths) - 1)
        x = -total / 2.0

        for child, cw in zip(node.children, child_widths):
            child.x = x + cw / 2.0
            x += cw + self._NODE_GAP_X

        return total

    # ---------- 深度优先遍历绘制 ----------

    def _draw(self, node: "_DispNode", cx: float, cy: float) -> None:
        """DFS 遍历绘制：节点中心 (cx, cy)，子节点向下展开。"""
        for child in node.children:
            ccx = cx + child.x
            ccy = cy + node.h + self._LEVEL_GAP_Y
            line = QGraphicsLineItem(cx, cy + node.h, ccx, ccy)
            pen = QPen(QColor("#9CA3AF"))
            pen.setWidth(2)
            line.setPen(pen)
            self.scene.addItem(line)
            self._draw(child, ccx, ccy)

        box = QGraphicsRectItem(QRectF(cx - node.w / 2.0, cy, node.w, node.h))
        box.setBrush(QColor(node.color))
        box.setPen(QPen(QColor("#666666")))
        self.scene.addItem(box)
        txt = QGraphicsSimpleTextItem(node.label, box)
        txt.setPos(cx - node.w / 2.0 + 6, cy + 12)
        self.scene.addItem(txt)


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

        # 牛头人卡池：说明一行（字号同殒命说明），勾选框一行
        band_title = QHBoxLayout()
        band_label = QLabel("牛头人卡池：")
        band_title.addWidget(band_label)
        band_note = QLabel("会自动同步不用管，不一样时再手动标记")
        band_note.setWordWrap(True)
        band_note.setStyleSheet("color:#888;")
        band_title.addWidget(band_note, 1)
        root.addLayout(band_title)

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

        # 卡组随从（如果机制抽牌池）：说明一行（字号同殒命说明），勾选框一行
        combo_title = QHBoxLayout()
        combo_label = QLabel("卡组随从：")
        combo_title.addWidget(combo_label)
        combo_note = QLabel("换卡组时再手动更改，WhatIf计算需要用到")
        combo_note.setWordWrap(True)
        combo_note.setStyleSheet("color:#888;")
        combo_title.addWidget(combo_note, 1)
        root.addLayout(combo_title)

        combo_row = QHBoxLayout()
        combo_row.setSpacing(3)
        self.mini_combo_checks: List[Tuple[str, QCheckBox]] = []

        for i, (card_name, label) in enumerate(COMBO_MINION_CHECKS):
            box = QCheckBox(label)
            # 默认勾选 鱼狐刀暗牛晦；腾武默认不勾（与主窗口一致，玩家手动勾选）
            box.setChecked(card_name != "赤烟·腾武")
            box.setToolTip(card_name)
            box.toggled.connect(self._on_mini_combo_toggled)
            self.mini_combo_checks.append((card_name, box))
            combo_row.addWidget(box)

        combo_row.addStretch(1)
        root.addLayout(combo_row)

        deadly_row = QHBoxLayout()
        self.mini_deadly_check = QCheckBox("殒命序号：")
        self.mini_deadly_check.setToolTip("重开游戏时需要填，平时不用管")
        self.mini_deadly_input = QLineEdit()
        self.mini_deadly_input.setPlaceholderText("重开游戏时需要手动标记，其余情况不用管")
        self.mini_deadly_input.setToolTip("重开游戏时需要填，平时不用管")
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
        self.mini_whatif_tree = WhatIFTreeWidget()
        self.mini_whatif_tree.setVisible(False)
        root.addWidget(self.mini_whatif_tree, 1)
        self.set_formula_font(self.main.mini_font_size())

        grip = QSizeGrip(self)
        root.addWidget(grip, 0, Qt.AlignRight)

    def set_formula_font(self, size: int) -> None:
        """设置公式（分轮结果）字号。"""
        font = self.mini_result.font()
        font.setPixelSize(int(size))
        self.mini_result.setFont(font)
        self.mini_result.document().setDefaultFont(font)
        self.mini_whatif_tree.setFont(font)

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

        for (_, mbox), (_, sbox) in zip(main.combo_checks, self.mini_combo_checks):
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

    def _on_mini_combo_toggled(self, _checked: bool) -> None:
        """小窗牌库剩余随从勾选 → 同步到主窗口。"""
        for (_, mbox), (_, sbox) in zip(self.main.combo_checks, self.mini_combo_checks):
            if mbox.isChecked() != sbox.isChecked():
                mbox.blockSignals(True)
                mbox.setChecked(sbox.isChecked())
                mbox.blockSignals(False)

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
        """小窗显示顺序：正常线（QTextBrowser）在前 → WhatIF 分支树（QTreeWidget）。"""
        if self._last_data is None:
            return

        exchanges = self.main.current_exchange_pairs()
        data = self._last_data
        colors = self.main.mini_color_enabled()

        # 1) 正常线（V1.2.1 抽杂牌逻辑）
        text = self._mini_original_text(data, colors, exchanges)

        if colors:
            self.mini_result.setHtml(text)
        else:
            self.mini_result.setPlainText(text)

        # 2) WhatIF 分支树（QTreeWidget）
        has_whatif = bool(
            data.get("whatif_tree")
            or data.get("quickdraw_branches")
            or data.get("draw_branches")
        )
        self.mini_whatif_tree.set_whatif(data, colors=colors)
        self.mini_whatif_tree.setVisible(has_whatif)

    def _mini_original_text(
        self,
        data: Dict[str, object],
        colors: bool,
        exchanges: Optional[List[Tuple[int, int]]] = None,
    ) -> str:
        """正常线（V1.2.1 逻辑）：抽卡当杂牌打出，结果唯一确定。

        C++ 的 original 已按“抽杂牌”计算（行骗保底抽法术杂牌、垂钓连击抽未知杂牌、
        挖掘宝藏/潜伏帷幕不模拟抽牌）；0 伤害时统一显示（无路径）。
        """
        orig = data.get("original")

        if (
            orig
            and (orig.get("path") or [])
            and int(orig.get("damage") or 0) > 0
        ):
            orig_data: Dict[str, object] = {
                "max_damage": orig.get("damage"),
                "max_dragons": orig.get("dragons"),
                "results": [orig],
            }
        else:
            # 原版线不存在或 0 伤害：直接显示“（无路径）”，
            # 不退回主搜索的 0 伤长路径（把费用用光也无意义）。
            orig_data = {
                "max_damage": 0,
                "max_dragons": 0,
                "results": [],
            }

        text = format_mini_results(
            orig_data, colors=colors, exchanges=exchanges or [], branches=False
        )
        return text

    def _mini_whatif_text(self, data: Dict[str, object], colors: bool) -> str:
        """带可能性分支的 WhatIF 树状显示（行骗同级分支 + 持枪同级分支）。"""
        return format_whatif_tree(data, colors=colors)

    def _mini_wb_block(self, wb: Dict[str, object], colors: bool) -> str:
        """统一 W-B 分支树（缩写+颜色框，全角空格缩进表示层级）。"""
        if colors:
            box_fn = _card_box_html
            sep = "<br>"
        else:
            box_fn = lambda name: "[" + abbreviate_card_name(name) + "]"  # noqa: E731
            sep = "\n"

        abbr_fn = abbreviate_step_html if colors else abbreviate_step
        lines = ["W-B机制（分支树）："]

        def walk(nodes: List[Dict[str, object]], level: int) -> None:
            # 按分支卡分组：分支卡作为树的节点
            groups: Dict[str, List[Dict[str, object]]] = {}

            for node in nodes:
                groups.setdefault(str(node.get("card") or ""), []).append(node)

            for card, card_nodes in groups.items():
                lines.append("\u3000" * level + f"「{card}」")

                for node in card_nodes:
                    play = node.get("play") or "直接"
                    branches = node.get("branches") or []
                    lines.append(
                        "\u3000" * (level + 1) + f"{play}（{len(branches)}）"
                    )

                    for bi, br in enumerate(branches, start=1):
                        drawn = br.get("drawn") or []

                        if drawn:
                            drawn_txt = "".join(box_fn(str(c)) for c in drawn)
                        else:
                            drawn_txt = box_fn(str(br.get("card", "")))

                        lines.append(
                            "\u3000" * (level + 2)
                            + f"Branch{bi} 抽到{drawn_txt} 增量{br.get('delta', 0)}："
                            + f"最大伤害：{br.get('damage', 0)}，"
                            + f"龙数：{br.get('dragons', 0)}，余：{br.get('mana_left', 0)}费"
                        )
                        path = br.get("path") or []

                        for index, rnd in enumerate(split_path_rounds(path), start=1):
                            abbr = "-".join(abbr_fn(str(s)) for s in rnd)
                            lines.append(
                                "\u3000" * (level + 2)
                                + f"[第{chinese_round_number(index)}轮]：{abbr}"
                            )

                        children = br.get("children") or {}

                        if children.get("nodes"):
                            walk(children.get("nodes") or [], level + 3)

        walk(wb.get("nodes") or [], 1)

        return sep.join(lines)

    def _mini_whatif_branch_text(
        self, data: Dict[str, object], colors: bool = False
    ) -> str:
        """持枪要挟分支显示文本（路径前半部分 + 可能分支）。"""
        lines = _format_whatif_branch_lines(
            data.get("results") or [],
            data.get("quickdraw_branches"),
            colors=colors,
        )
        return ("<br>" if colors else "\n").join(lines)

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
            '　　为了实现将计算时间压缩到<b><span style="color:#DC2626;">3s以内</span></b>，'
            '以及实现支持持枪要挟，随机抽牌等分支尝试效果。该计算器经历了'
            '<b><span style="color:#D97706;">多轮底层架构的优化</span></b>和'
            '<b><span style="color:#D97706;">相关算法的设计尝试</span></b>'
            '(<b><span style="color:#D97706;">场面交换</span></b>算法，'
            '<b><span style="color:#7C3AED;">分支尝试</span></b>算法，'
            '<b><span style="color:#0E7490;">束搜索剪枝</span></b>等)，以及'
            '<b><span style="color:#EA580C;">反反复复</span></b>的bug修改，'
            '普遍覆盖了<b><span style="color:#16A34A;">95%以上的最优解</span></b>，'
            '并且计算出许多公式表上的<b><span style="color:#D97706;">更优解</span></b>'
            '以及一些神奇的<b><span style="color:#7C3AED;">等价路径</span></b>，'
            '具体由使用者自己发掘。<br/>'
            '　　由于开发这个软件的过程耗费了作者'
            '<b><span style="color:#DC2626;">大量</span></b>的时间精力和金钱，'
            '所以如果该软件帮助到了您，请务必给作者'
            '<b><span style="color:#D97706;">一点支持</span></b>。'
            '</span>'
        )
        author_body.setWordWrap(True)
        author_body.setStyleSheet("font-size:36px; color:#374151;")
        layout.addWidget(author_body)

        # “给一点支持”之后的变色鼓励语：你的支持就是我的动力~
        self.support_dynamic = QLabel(
            '<span style="font-size:36px; font-weight:bold; color:#DC2626;">'
            '　　你的支持就是我更新的动力~'
            '</span>'
        )
        self.support_dynamic.setWordWrap(True)
        layout.addWidget(self.support_dynamic)

        self._support_colors = [
            "#DC2626",
            "#EA580C",
            "#D97706",
            "#16A34A",
            "#0EA5E9",
            "#7C3AED",
            "#DB2777",
        ]
        self._support_color_idx = 0
        self._support_timer = QTimer(self)
        self._support_timer.setInterval(600)
        self._support_timer.timeout.connect(self._cycle_support_color)
        self._support_timer.start()

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

    def _cycle_support_color(self) -> None:
        """“你的支持就是我的动力~”轮换颜色（红→橙→金→绿→蓝→紫→粉）。"""
        self._support_color_idx = (self._support_color_idx + 1) % len(self._support_colors)
        color = self._support_colors[self._support_color_idx]
        self.support_dynamic.setText(
            f'<span style="font-size:36px; font-weight:bold; color:{color};">'
            "　　你的支持就是我更新的动力~"
            "</span>"
        )

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


# ===================== 版本与更新检测 =====================

# 当前程序版本（与已发布版本一致；发布新版时更新此值）
APP_VERSION = "1.2.1"
# 发布仓库：立即更新时跳转到此页面
RELEASE_URL = "https://github.com/zcr0701/Red-Dragon-Calculator-Release"
RELEASE_API = "https://api.github.com/repos/zcr0701/Red-Dragon-Calculator-Release/releases/latest"


def _version_tuple(version: str) -> tuple:
    """版本字符串转可比较元组（支持 v/V 前缀与 . _ - 分隔，如 V1.2.1）。"""
    text = str(version or "").strip().lstrip("vV")
    parts = []

    for part in re.split(r"[._\-]+", text):
        if part.isdigit():
            parts.append(int(part))
        else:
            parts.append(part)

    return tuple(parts)


def is_newer_version(latest: str, current: str) -> bool:
    """latest 是否比 current 新。"""
    return _version_tuple(latest) > _version_tuple(current)


def check_latest_version() -> Optional[str]:
    """查询发布仓库最新 release 的 tag 版本号；失败或非预期返回 None（静默）。"""
    try:
        req = urllib.request.Request(
            RELEASE_API,
            headers={
                "User-Agent": "RedDragonCalculator",
                "Accept": "application/vnd.github+json",
            },
        )

        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))

        tag = str(data.get("tag_name") or "").strip()
        return tag or None

    except Exception:  # noqa: BLE001 - 静默失败，不影响使用
        return None


def _prompt_update(parent: QWidget, latest: str) -> None:
    """发现新版本：弹窗提示，点击“立即更新”跳转发布仓库页面。"""
    box = QMessageBox(parent)
    box.setWindowTitle("发现新版本")
    box.setIcon(QMessageBox.Information)
    box.setText(
        f"检测到新版本 {latest}\n"
        f"当前版本：{APP_VERSION}\n"
        "点击“立即更新”将跳转到发布页面下载。"
    )
    update_btn = box.addButton("立即更新", QMessageBox.AcceptRole)
    box.addButton("稍后再说", QMessageBox.RejectRole)
    box.exec_()

    if box.clickedButton() is update_btn:
        webbrowser.open(RELEASE_URL)


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
        # 后台检查更新（与介绍弹窗并行，不阻塞）
        update_result: Dict[str, object] = {}

        def _check_update() -> None:
            update_result["latest"] = check_latest_version()

        check_thread = threading.Thread(target=_check_update, daemon=True)
        check_thread.start()
        IntroDialog(window).exec_()
        check_thread.join(timeout=6)
        latest = update_result.get("latest")
        window.set_latest_version(str(latest) if latest else None)

        if latest and is_newer_version(str(latest), APP_VERSION):
            _prompt_update(window, str(latest))

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
