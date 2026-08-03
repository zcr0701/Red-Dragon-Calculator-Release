import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "card_config.json"

SECTION_RE = re.compile(r"^\s*(当前效果|牌库中|手牌中|战场|其他)\s*[（(]\s*(\d+)\s*[）)]\s*$")
COST_RE = re.compile(r"^\s*(\d+)\s*$")

CARD_COSTS = {
    "伪造的幸运币": 0,
    "伺机待发": 0,
    "暗影步": 0,
    "殒命暗影": None,
    "垂钓时光": 1,
    "挖掘宝藏": 1,
    "黑水弯刀": 1,
    "邪恶短刀": 1,
    "异教地图": 2,
    "狐人老千": 2,
    "疾速矿锄": 2,
    "行骗": 2,
    "锯齿骨刺": 2,
    "闪避": 2,
    "晦鳞巢母": 3,
    "潜伏帷幕": 3,
    "乐队经理精英牛头人酋长": 4,
    "舞动全场（ft.迦罗娜）": 3,
    "幻觉药水": 4,
    "生命的缚誓者阿莱克丝塔萨": 9,
    "可疑交易": 4,
    "斯卡布斯·刀油": 4,
    "鲨鱼之灵": 4,
    "暗影施法者": 5,
    "幸运币": 0
}

DECK_MAX_COUNTS = {
    "伪造的幸运币": 2,
    "伺机待发": 2,
    "暗影步": 2,
    "殒命暗影": 1,
    "垂钓时光": 2,
    "挖掘宝藏": 2,
    "黑水弯刀": 2,
    "邪恶短刀": 2,
    "异教地图": 2,
    "狐人老千": 1,
    "疾速矿锄": 1,
    "行骗": 2,
    "锯齿骨刺": 1,
    "闪避": 2,
    "晦鳞巢母": 1,
    "潜伏帷幕": 2,
    "乐队经理精英牛头人酋长": 1,
    "舞动全场（ft.迦罗娜）": 1,
    "幻觉药水": 1,
    "生命的缚誓者阿莱克丝塔萨": 1,
    "可疑交易": 1,
    "斯卡布斯·刀油": 1,
    "鲨鱼之灵": 1,
    "暗影施法者": 1,
    "幸运币": 0
}


@dataclass
class CardConfigItem:
    name: str
    aliases: List[str] = field(default_factory=list)
    description: str = ""
    no_cost: bool = False


@dataclass
class CardMatch:
    name: str
    score: float
    matched_by: str


@dataclass
class HandCard:
    cost: Optional[int]
    name: str
    recognized_name: str
    count: int = 1
    match_score: float = 1.0
    matched_by: str = "raw"
    description: str = ""
    no_cost: bool = False


@dataclass
class HandRebuildResult:
    expected_effect_count: Optional[int] = None
    expected_deck_count: Optional[int] = None
    expected_hand_count: Optional[int] = None
    expected_battlefield_count: Optional[int] = None
    other_count: Optional[int] = None
    current_effect_cards: List[HandCard] = field(default_factory=list)
    deck_cards: List[HandCard] = field(default_factory=list)
    cards: List[HandCard] = field(default_factory=list)
    battlefield_cards: List[HandCard] = field(default_factory=list)
    deck_total_count: int = 0
    total_count: int = 0
    battlefield_total_count: int = 0
    warnings: List[str] = field(default_factory=list)
    raw_lines: List[str] = field(default_factory=list)
    current_effect_lines: List[str] = field(default_factory=list)
    deck_lines: List[str] = field(default_factory=list)
    hand_lines: List[str] = field(default_factory=list)
    battlefield_lines: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)


def clean_ocr_line(line: str) -> str:
    return (
        line.strip()
        .replace("（", "(")
        .replace("）", ")")
        .replace("：", ":")
    )


def clean_ocr_text(text: str) -> List[str]:
    lines: List[str] = []

    for line in text.splitlines():
        cleaned = clean_ocr_line(line)

        if cleaned:
            lines.append(cleaned)

    return lines


def normalize_card_name(text: str) -> str:
    text = clean_ocr_line(text)
    text = text.lower()
    text = re.sub(r"[\s·・,，.。:：;；'\"“”‘’\-_—()（）\[\]【】<>《》]", "", text)
    return text


def is_marker_line(line: str) -> bool:
    return bool(re.fullmatch(r"[★☆*＊]+", line.strip()))


def parse_section_line(line: str) -> Tuple[Optional[str], Optional[int]]:
    match = SECTION_RE.match(line)

    if not match:
        return None, None

    return match.group(1), int(match.group(2))


def is_cost_line(line: str) -> bool:
    return COST_RE.match(line) is not None


def load_card_config(config_path: Optional[str] = None) -> Tuple[List[CardConfigItem], int]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH

    if not path.exists():
        return [], 1

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    min_common_chars = int(data.get("min_common_chars", 1))
    cards: List[CardConfigItem] = []

    for item in data.get("cards", []):
        if isinstance(item, str):
            cards.append(CardConfigItem(name=item))
            continue

        if not isinstance(item, dict):
            continue

        name = str(item.get("name", "")).strip()

        if not name:
            continue

        aliases = item.get("aliases", [])

        if not isinstance(aliases, list):
            aliases = []

        cards.append(
            CardConfigItem(
                name=name,
                aliases=[str(alias).strip() for alias in aliases if str(alias).strip()],
                description=str(item.get("description", "")),
                no_cost=bool(item.get("no_cost", False))
            )
        )

    return cards, min_common_chars


def count_common_chars(recognized_name: str, candidate_name: str) -> int:
    recognized = normalize_card_name(recognized_name)
    candidate = normalize_card_name(candidate_name)

    if not recognized or not candidate:
        return 0

    candidate_char_count: Dict[str, int] = {}

    for char in candidate:
        candidate_char_count[char] = candidate_char_count.get(char, 0) + 1

    common_count = 0

    for char in recognized:
        remain_count = candidate_char_count.get(char, 0)

        if remain_count <= 0:
            continue

        common_count += 1
        candidate_char_count[char] = remain_count - 1

    return common_count


def score_name_match(recognized_name: str, candidate_name: str) -> Tuple[float, str]:
    recognized = normalize_card_name(recognized_name)
    candidate = normalize_card_name(candidate_name)

    if not recognized or not candidate:
        return 0.0, "common_chars:0"

    common_count = count_common_chars(
        recognized_name=recognized_name,
        candidate_name=candidate_name
    )
    score = common_count / max(len(recognized), len(candidate), 1)

    return score, f"common_chars:{common_count}"


def match_card_name(
    recognized_name: str,
    card_configs: List[CardConfigItem],
    min_common_chars: int
) -> Tuple[str, float, str, str]:
    best_match: Optional[CardMatch] = None
    best_description = ""
    best_common_count = -1
    best_candidate_len = -1
    best_recognized_len = len(normalize_card_name(recognized_name))
    best_score = -1.0

    for card in card_configs:
        candidates = [card.name] + card.aliases

        for candidate in candidates:
            score, matched_by = score_name_match(recognized_name, candidate)
            common_count = count_common_chars(recognized_name, candidate)
            candidate_len = len(normalize_card_name(candidate))

            if (
                best_match is None
                or common_count > best_common_count
                or (
                    common_count == best_common_count
                    and score > best_score
                )
                or (
                    common_count == best_common_count
                    and score == best_score
                    and candidate_len > best_candidate_len
                )
            ):
                best_match = CardMatch(
                    name=card.name,
                    score=score,
                    matched_by=matched_by
                )
                best_description = card.description
                best_common_count = common_count
                best_candidate_len = candidate_len
                best_score = score

    if best_match is None:
        return recognized_name, 0.0, "unmatched", ""

    required_common_count = max(
        min_common_chars,
        best_candidate_len // 2 + 1,
        best_recognized_len // 2 + 1
    )

    if best_common_count < required_common_count:
        return recognized_name, 0.0, "unmatched", ""

    return best_match.name, best_match.score, best_match.matched_by, best_description


def find_card_config(card_name: str, card_configs: List[CardConfigItem]) -> Optional[CardConfigItem]:
    for card in card_configs:
        if card.name == card_name:
            return card

    return None


def get_section_positions(lines: List[str], result: HandRebuildResult) -> Dict[str, int]:
    positions: Dict[str, int] = {}

    for index, line in enumerate(lines):
        section_name, section_count = parse_section_line(line)

        if section_name == "当前效果":
            result.expected_effect_count = section_count
            positions.setdefault("当前效果", index)
            continue

        if section_name == "牌库中":
            result.expected_deck_count = section_count
            positions.setdefault("牌库中", index)
            continue

        if section_name == "手牌中":
            result.expected_hand_count = section_count
            positions.setdefault("手牌中", index)
            continue

        if section_name == "战场":
            result.expected_battlefield_count = section_count
            positions.setdefault("战场", index)
            continue

        if section_name == "其他":
            result.other_count = section_count
            positions.setdefault("其他", index)

    return positions


def next_section_after(positions: Dict[str, int], start_section: str, fallback: Optional[str] = None) -> Optional[str]:
    start_index = positions.get(start_section)

    if start_index is None:
        return fallback

    later_sections = [
        (section, index)
        for section, index in positions.items()
        if index > start_index
    ]

    if not later_sections:
        return fallback

    return min(later_sections, key=lambda item: item[1])[0]


def extract_zone_block(
    lines: List[str],
    positions: Dict[str, int],
    start_section: str,
    end_section: str,
    result: HandRebuildResult
) -> List[str]:
    start_index = positions.get(start_section)
    end_index = positions.get(end_section)

    if start_index is None:
        result.warnings.append(f"未识别到“{start_section}(x)”标题，无法定位区域起点")
        return []

    if end_index is None:
        result.warnings.append(f"未识别到“{end_section}(x)”标题，将“{start_section}(x)”之后的内容全部当作该区域")
        return lines[start_index + 1:]

    if end_index <= start_index:
        result.warnings.append(f"“{end_section}(x)”出现在“{start_section}(x)”之前，无法正确分割区域")
        return []

    return lines[start_index + 1:end_index]


def make_card_entry(
    cost: Optional[int],
    recognized_name: str,
    card_configs: List[CardConfigItem],
    min_common_chars: int,
    result: HandRebuildResult,
    count: int = 1
) -> HandCard:
    matched_name, match_score, matched_by, description = match_card_name(
        recognized_name=recognized_name,
        card_configs=card_configs,
        min_common_chars=min_common_chars
    )
    matched_config = find_card_config(matched_name, card_configs)

    if matched_by == "unmatched":
        result.warnings.append(f"未在配置中匹配到卡牌：{recognized_name}，已保留 OCR 原文")
        match_score = 0.0

    return HandCard(
        cost=cost,
        name=matched_name,
        recognized_name=recognized_name,
        count=max(1, int(count)),
        match_score=match_score,
        matched_by=matched_by,
        description=description,
        no_cost=bool(matched_config.no_cost) if matched_config else False
    )


def parse_card_block(
    zone_lines: List[str],
    zone_name: str,
    card_configs: List[CardConfigItem],
    min_common_chars: int,
    result: HandRebuildResult
) -> List[HandCard]:
    cards: List[HandCard] = []
    i = 0

    while i < len(zone_lines):
        cost_line = zone_lines[i]

        if is_marker_line(cost_line):
            i += 1
            continue

        if not is_cost_line(cost_line):
            special_card = make_card_entry(
                cost=None,
                recognized_name=cost_line,
                card_configs=card_configs,
                min_common_chars=min_common_chars,
                result=result
            )

            if special_card.no_cost:
                cards.append(special_card)
                i += 1
                continue

            result.warnings.append(f"{zone_name}：期望费用行，但识别到：{cost_line}，未匹配到无费用特殊卡，已跳过")
            i += 1
            continue

        if i + 1 >= len(zone_lines):
            result.warnings.append(f"{zone_name}：费用 {cost_line} 后面缺少卡牌名称")
            break

        recognized_name = zone_lines[i + 1]

        if is_cost_line(recognized_name):
            result.warnings.append(f"{zone_name}：费用 {cost_line} 后面仍是数字 {recognized_name}，疑似漏识别卡牌名")
            i += 1
            continue

        cards.append(
            make_card_entry(
                cost=int(cost_line),
                recognized_name=recognized_name,
                card_configs=card_configs,
                min_common_chars=min_common_chars,
                result=result
            )
        )
        i += 2

    return cards


def parse_effect_block(
    effect_lines: List[str],
    card_configs: List[CardConfigItem],
    min_common_chars: int,
    result: HandRebuildResult
) -> List[HandCard]:
    cards: List[HandCard] = []

    for line in effect_lines:
        if is_marker_line(line):
            continue

        if is_cost_line(line):
            continue

        cards.append(
            make_card_entry(
                cost=CARD_COSTS.get(match_name_for_parser(line, card_configs, min_common_chars)[0]),
                recognized_name=line,
                card_configs=card_configs,
                min_common_chars=min_common_chars,
                result=result
            )
        )

    return cards


def is_deck_noise_line(line: str) -> bool:
    if is_cost_line(line):
        return False

    normalized = normalize_card_name(line)

    return len(normalized) <= 1


def clean_deck_lines(deck_lines: List[str]) -> List[str]:
    return [
        line
        for line in deck_lines
        if not is_deck_noise_line(line)
    ]


def match_name_for_parser(
    recognized_name: str,
    card_configs: List[CardConfigItem],
    min_common_chars: int
) -> Tuple[str, Optional[CardConfigItem]]:
    matched_name, _, matched_by, _ = match_card_name(
        recognized_name=recognized_name,
        card_configs=card_configs,
        min_common_chars=min_common_chars
    )

    if matched_by == "unmatched":
        return recognized_name, None

    return matched_name, find_card_config(matched_name, card_configs)


def should_consume_deck_count(
    card_name: str,
    count_line: str,
    next_line: Optional[str],
    card_configs: List[CardConfigItem],
    min_common_chars: int
) -> bool:
    if not is_cost_line(count_line):
        return False

    count = int(count_line)
    max_count = DECK_MAX_COUNTS.get(card_name, 2)

    if count < 1 or count > max_count:
        return False

    if next_line is None:
        return True

    if is_cost_line(next_line):
        return True

    next_normalized = normalize_card_name(next_line)

    if len(next_normalized) <= 1:
        return True

    next_card_name, next_card_config = match_name_for_parser(
        recognized_name=next_line,
        card_configs=card_configs,
        min_common_chars=min_common_chars
    )

    if next_card_config is not None and CARD_COSTS.get(next_card_name) == count:
        return False

    return max_count > 1


def parse_deck_block(
    deck_lines: List[str],
    card_configs: List[CardConfigItem],
    min_common_chars: int,
    result: HandRebuildResult
) -> List[HandCard]:
    lines = clean_deck_lines(deck_lines)
    cards: List[HandCard] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        if is_cost_line(line):
            cost = int(line)

            if i + 1 >= len(lines):
                result.warnings.append(f"牌库：费用 {line} 后面缺少卡牌名称")
                break

            recognized_name = lines[i + 1]

            if is_cost_line(recognized_name):
                result.warnings.append(f"牌库：费用 {line} 后面仍是数字 {recognized_name}，疑似漏识别卡牌名")
                i += 1
                continue

            matched_name, _ = match_name_for_parser(
                recognized_name=recognized_name,
                card_configs=card_configs,
                min_common_chars=min_common_chars
            )
            count = 1
            next_index = i + 2

            if next_index < len(lines):
                next_next_line = lines[next_index + 1] if next_index + 1 < len(lines) else None

                if should_consume_deck_count(
                    card_name=matched_name,
                    count_line=lines[next_index],
                    next_line=next_next_line,
                    card_configs=card_configs,
                    min_common_chars=min_common_chars
                ):
                    count = int(lines[next_index])
                    next_index += 1

            cards.append(
                make_card_entry(
                    cost=cost,
                    recognized_name=recognized_name,
                    card_configs=card_configs,
                    min_common_chars=min_common_chars,
                    result=result,
                    count=count
                )
            )
            i = next_index
            continue

        matched_name, matched_config = match_name_for_parser(
            recognized_name=line,
            card_configs=card_configs,
            min_common_chars=min_common_chars
        )

        if matched_config is None:
            result.warnings.append(f"牌库：未在配置中匹配到卡牌：{line}，已跳过")
            i += 1
            continue

        cost = CARD_COSTS.get(matched_name)
        count = 1
        next_index = i + 1

        if next_index < len(lines):
            next_next_line = lines[next_index + 1] if next_index + 1 < len(lines) else None

            if should_consume_deck_count(
                card_name=matched_name,
                count_line=lines[next_index],
                next_line=next_next_line,
                card_configs=card_configs,
                min_common_chars=min_common_chars
            ):
                count = int(lines[next_index])
                next_index += 1

        cards.append(
            make_card_entry(
                cost=cost,
                recognized_name=line,
                card_configs=card_configs,
                min_common_chars=min_common_chars,
                result=result,
                count=count
            )
        )
        i = next_index

    return cards


def rebuild_hand_from_lines(
    lines: List[str],
    config_path: Optional[str] = None
) -> HandRebuildResult:
    result = HandRebuildResult(raw_lines=lines[:])
    card_configs, min_common_chars = load_card_config(config_path)

    if not card_configs:
        result.warnings.append("未读取到卡牌配置，将直接使用 OCR 识别出的卡名")

    positions = get_section_positions(lines, result)

    effect_lines: List[str] = []
    deck_lines: List[str] = []
    hand_lines: List[str] = []
    battlefield_lines: List[str] = []

    if "当前效果" in positions:
        effect_end = next_section_after(positions, "当前效果", fallback="牌库中")
        if effect_end:
            effect_lines = extract_zone_block(
                lines=lines,
                positions=positions,
                start_section="当前效果",
                end_section=effect_end,
                result=result
            )

    if "牌库中" in positions and "手牌中" in positions:
        deck_lines = extract_zone_block(
            lines=lines,
            positions=positions,
            start_section="牌库中",
            end_section="手牌中",
            result=result
        )

    hand_end = next_section_after(positions, "手牌中", fallback="其他")
    hand_lines = extract_zone_block(
        lines=lines,
        positions=positions,
        start_section="手牌中",
        end_section=hand_end or "其他",
        result=result
    )

    if "战场" in positions:
        battlefield_end = next_section_after(positions, "战场", fallback="其他")
        battlefield_lines = extract_zone_block(
            lines=lines,
            positions=positions,
            start_section="战场",
            end_section=battlefield_end or "其他",
            result=result
        )

    result.current_effect_lines = effect_lines[:]
    result.deck_lines = deck_lines[:]
    result.hand_lines = hand_lines[:]
    result.battlefield_lines = battlefield_lines[:]

    result.current_effect_cards = parse_effect_block(
        effect_lines=effect_lines,
        card_configs=card_configs,
        min_common_chars=min_common_chars,
        result=result
    )
    result.deck_cards = parse_deck_block(
        deck_lines=deck_lines,
        card_configs=card_configs,
        min_common_chars=min_common_chars,
        result=result
    )
    result.cards = parse_card_block(
        zone_lines=hand_lines,
        zone_name="手牌",
        card_configs=card_configs,
        min_common_chars=min_common_chars,
        result=result
    )
    result.battlefield_cards = parse_card_block(
        zone_lines=battlefield_lines,
        zone_name="战场",
        card_configs=card_configs,
        min_common_chars=min_common_chars,
        result=result
    )

    result.deck_total_count = sum(card.count for card in result.deck_cards)
    result.total_count = len(result.cards)
    result.battlefield_total_count = len(result.battlefield_cards)

    if deck_lines and result.expected_deck_count is not None and result.deck_total_count != result.expected_deck_count:
        result.warnings.append(
            f"重建牌库数量 {result.deck_total_count} 与标题数量 {result.expected_deck_count} 不一致"
        )

    if result.expected_hand_count is not None and result.total_count != result.expected_hand_count:
        result.warnings.append(
            f"重建手牌数量 {result.total_count} 与标题数量 {result.expected_hand_count} 不一致"
        )

    if result.expected_battlefield_count is not None and result.battlefield_total_count != result.expected_battlefield_count:
        result.warnings.append(
            f"重建战场数量 {result.battlefield_total_count} 与标题数量 {result.expected_battlefield_count} 不一致"
        )

    return result


def rebuild_hand_from_text(
    text: str,
    config_path: Optional[str] = None
) -> HandRebuildResult:
    return rebuild_hand_from_lines(
        lines=clean_ocr_text(text),
        config_path=config_path
    )


def format_card_lines(title: str, cards: List[HandCard], show_count: bool = False) -> List[str]:
    lines = [f"{title}："]

    if not cards:
        lines.append("  无")
        return lines

    for index, card in enumerate(cards, start=1):
        cost_text = "*费" if card.cost is None else f"{card.cost}费"
        count_text = f" x{card.count}" if show_count else ""

        if card.name != card.recognized_name:
            common_chars = card.matched_by.replace("common_chars:", "")
            lines.append(
                f"  {index}. [{cost_text}] {card.name}{count_text} "
                f"(OCR：{card.recognized_name}，相同字数：{common_chars})"
            )
        else:
            lines.append(f"  {index}. [{cost_text}] {card.name}{count_text}")

    return lines


def format_result(result: HandRebuildResult) -> str:
    lines = []

    if result.expected_effect_count is not None or result.current_effect_cards:
        lines.extend(format_card_lines("当前效果", result.current_effect_cards))
        lines.append("")

    if result.expected_deck_count is not None or result.deck_cards:
        lines.extend(format_card_lines("牌库明细", result.deck_cards, show_count=True))
        lines.append("")

    lines.extend(format_card_lines("手牌明细", result.cards))

    if result.expected_battlefield_count is not None or result.battlefield_cards:
        lines.append("")
        lines.extend(format_card_lines("战场明细", result.battlefield_cards))

    if result.warnings:
        lines.append("")
        lines.append("警告：")

        for warning in result.warnings:
            lines.append(f"  {warning}")

    return "\n".join(lines)


def read_input(args: argparse.Namespace) -> str:
    if args.text:
        return args.text

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            return f.read()

    return sys.stdin.read()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="根据 OCR 文本重建手牌，并使用 card_config.json 修正卡牌名称"
    )
    parser.add_argument("--text", help="直接传入 OCR 文本")
    parser.add_argument("--file", help="从文本文件读取 OCR 文本")
    parser.add_argument("--config", help="指定卡牌配置文件路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--output", help="把结果写入指定文件")

    args = parser.parse_args()
    text = read_input(args)
    result = rebuild_hand_from_text(
        text=text,
        config_path=args.config
    )

    if args.json:
        output = json.dumps(
            result.to_dict(),
            ensure_ascii=False,
            indent=2
        )
    else:
        output = format_result(result)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
    else:
        print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
