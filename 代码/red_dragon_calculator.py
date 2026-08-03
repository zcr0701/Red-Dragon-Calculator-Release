import argparse
import copy
import heapq
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


MAX_HAND_SIZE = 10
MAX_BOARD_SIZE = 7
MAX_SECRET_SIZE = 5
MAX_MANA_CRYSTALS = 10
CHAIN_VALIDATION_BEAM = 80


@dataclass
class CardDef:
    name: str
    cost: Optional[int]
    card_type: str
    description: str
    effect_id: str
    tags: List[str] = field(default_factory=list)
    no_fixed_cost: bool = False
    health: Optional[int] = None


@dataclass
class CardInstance:
    name: str
    cost: Optional[int]
    original_name: str
    card_type: str
    description: str
    effect_id: str
    tags: List[str] = field(default_factory=list)
    no_fixed_cost: bool = False
    temp_cost: Optional[int] = None
    is_deadly_shadow: bool = False
    health: Optional[int] = None

    @classmethod
    def from_def(cls, card_def: CardDef) -> "CardInstance":
        return cls(
            name=card_def.name,
            cost=card_def.cost,
            original_name=card_def.name,
            card_type=card_def.card_type,
            description=card_def.description,
            effect_id=card_def.effect_id,
            tags=card_def.tags[:],
            no_fixed_cost=card_def.no_fixed_cost,
            temp_cost=None,
            is_deadly_shadow=card_def.effect_id == "deadly_shadow",
            health=card_def.health
        )

    def current_cost(self) -> Optional[int]:
        if self.temp_cost is not None:
            return max(0, self.temp_cost)

        return self.cost


@dataclass
class GameState:
    deck: List[CardInstance] = field(default_factory=list)
    deck_is_known: bool = False
    hand: List[CardInstance] = field(default_factory=list)
    board: List[CardInstance] = field(default_factory=list)
    secrets: List[CardInstance] = field(default_factory=list)
    weapon: Optional[CardInstance] = None
    mana_crystals: int = 10
    mana: int = 10
    initial_mana_crystals: int = 10
    initial_mana: int = 10
    cards_played_this_turn: int = 0
    next_spell_discount: int = 0
    next_combo_discount: int = 0
    next_card_discount: int = 0
    next_two_cards_discount: int = 0
    next_two_cards_discount_count: int = 0
    active_card_discounts: List[Tuple[int, int]] = field(default_factory=list)
    hero_immune_this_turn: bool = False
    last_spell_original_name: Optional[str] = None
    burned_cards: int = 0
    alex_play_count: int = 0
    alex_damage: int = 0
    etc_band_remaining: List[str] = field(default_factory=list)
    path: List[str] = field(default_factory=list)
    log: List[str] = field(default_factory=list)
    record_log: bool = True

    def clone(self) -> "GameState":
        return copy.deepcopy(self)

    def has_shark(self) -> bool:
        return any(card.name == "鲨鱼之灵" for card in self.board)

    def add_log(self, text: str):
        if not self.record_log:
            return

        self.log.append(text)

    @property
    def hand_zone(self) -> "HandZone":
        return HandZone(self)

    @property
    def deck_zone(self) -> "DeckZone":
        return DeckZone(self)

    @property
    def board_zone(self) -> "BoardZone":
        return BoardZone(self)

    @property
    def secret_zone(self) -> "SecretZone":
        return SecretZone(self)

    @property
    def mana_pool(self) -> "ManaPool":
        return ManaPool(self)


class HandZone:
    def __init__(self, state: GameState):
        self.state = state

    @property
    def cards(self) -> List[CardInstance]:
        return self.state.hand

    def __len__(self) -> int:
        return len(self.cards)

    def is_full(self) -> bool:
        return len(self.cards) >= MAX_HAND_SIZE

    def add(self, card: CardInstance) -> bool:
        if self.is_full():
            self.state.burned_cards += 1
            self.state.add_log(f"爆牌：{card.name}")
            return False

        self.cards.append(card)
        self.state.add_log(f"加入手牌：{card.name}")
        return True

    def remove_at(self, index: int) -> CardInstance:
        return self.cards.pop(index)

    def names(self) -> List[str]:
        return [card.name for card in self.cards]


class DeckZone:
    def __init__(self, state: GameState):
        self.state = state

    @property
    def cards(self) -> List[CardInstance]:
        return self.state.deck

    def __len__(self) -> int:
        return len(self.cards)

    def draw_top(self) -> Optional[CardInstance]:
        if not self.cards:
            self.state.add_log("牌库为空，无法抽牌")
            return None

        card = self.cards.pop(0)
        self.state.hand_zone.add(card)
        return card

    def draw_first_by_type(self, card_type: str) -> Optional[CardInstance]:
        for index, card in enumerate(self.cards):
            if card.card_type == card_type:
                found = self.cards.pop(index)
                self.state.hand_zone.add(found)
                return found

        self.state.add_log(f"牌库中没有可抽取的{card_type}")
        return None

    def remove_at(self, index: int) -> CardInstance:
        return self.cards.pop(index)

    def append_bottom(self, card: CardInstance):
        self.cards.append(card)

    def indexes_by_type(self, card_type: str, limit: Optional[int] = None) -> List[int]:
        indexes = [
            index
            for index, card in enumerate(self.cards)
            if card.card_type == card_type
        ]

        if limit is not None:
            return indexes[:limit]

        return indexes

    def names(self) -> List[str]:
        return [card.name for card in self.cards]


class BoardZone:
    def __init__(self, state: GameState):
        self.state = state

    @property
    def cards(self) -> List[CardInstance]:
        return self.state.board

    def __len__(self) -> int:
        return len(self.cards)

    def can_add(self) -> bool:
        return len(self.cards) < MAX_BOARD_SIZE

    def add(self, card: CardInstance) -> bool:
        if not self.can_add():
            self.state.add_log(f"随从栏已满，无法进入：{card.name}")
            return False

        self.cards.append(card)
        return True

    def remove_at(self, index: int) -> CardInstance:
        return self.cards.pop(index)

    def valid_target_indexes(self) -> List[int]:
        return list(range(len(self.cards)))

    def names(self) -> List[str]:
        return [card.name for card in self.cards]


class SecretZone:
    def __init__(self, state: GameState):
        self.state = state

    @property
    def cards(self) -> List[CardInstance]:
        return self.state.secrets

    def __len__(self) -> int:
        return len(self.cards)

    def can_add(self) -> bool:
        return len(self.cards) < MAX_SECRET_SIZE

    def add(self, card: CardInstance) -> bool:
        if not self.can_add():
            self.state.add_log(f"奥秘栏已满，无法进入：{card.name}")
            return False

        self.cards.append(card)
        return True

    def remove_at(self, index: int) -> CardInstance:
        return self.cards.pop(index)

    def names(self) -> List[str]:
        return [card.name for card in self.cards]


class ManaPool:
    def __init__(self, state: GameState):
        self.state = state

    def can_pay(self, amount: int) -> bool:
        return self.state.mana >= amount

    def pay(self, amount: int) -> bool:
        if not self.can_pay(amount):
            return False

        self.state.mana -= amount
        return True

    def restore(self, amount: int):
        old_mana = self.state.mana
        self.state.mana = min(self.state.mana + amount, self.state.mana_crystals)
        self.state.add_log(f"复原法力：{old_mana} -> {self.state.mana}")

    def gain_temporary(self, amount: int):
        old_mana = self.state.mana
        self.state.mana += amount
        self.state.add_log(f"获得临时法力：{old_mana} -> {self.state.mana}")


CARD_DATABASE: Dict[str, CardDef] = {
    "暗影步": CardDef(
        name="暗影步",
        cost=0,
        card_type="spell",
        description="将一个友方随从移回手牌，其法力值消耗减少2点。",
        effect_id="shadowstep"
    ),
    "伪造的幸运币": CardDef(
        name="伪造的幸运币",
        cost=0,
        card_type="spell",
        description="本回合获得1点法力。",
        effect_id="fake_coin"
    ),
    "幸运币": CardDef(
        name="幸运币",
        cost=0,
        card_type="spell",
        description="本回合获得1点法力。",
        effect_id="coin"
    ),
    "伺机待发": CardDef(
        name="伺机待发",
        cost=0,
        card_type="spell",
        description="本回合施放的下一个法术法力值消耗减少2点。",
        effect_id="preparation"
    ),
    "殒命暗影": CardDef(
        name="殒命暗影",
        cost=None,
        card_type="spell",
        description="每当你施放一个法术，变形成为该法术的原始复制。",
        effect_id="deadly_shadow",
        no_fixed_cost=True
    ),
    "黑水弯刀": CardDef(
        name="黑水弯刀",
        cost=1,
        card_type="weapon",
        description="可交易。交易后，使手牌中一张法术牌法力值消耗减少1点。",
        effect_id="blackwater_cutlass",
        tags=["tradeable"]
    ),
    "邪恶短刀": CardDef(
        name="邪恶短刀",
        cost=1,
        card_type="weapon",
        description="1费1/2武器，无效果。",
        effect_id=""
    ),
    "垂钓时光": CardDef(
        name="垂钓时光",
        cost=1,
        card_type="spell",
        description="探底。连击：抽一张牌。",
        effect_id="gone_fishin",
        tags=["combo"]
    ),
    "挖掘宝藏": CardDef(
        name="挖掘宝藏",
        cost=1,
        card_type="spell",
        description="抽一张随从牌。如果是海盗牌，获取一张幸运币。",
        effect_id="dig_for_treasure"
    ),
    "闪避": CardDef(
        name="闪避",
        cost=2,
        card_type="secret",
        description="奥秘：英雄受到伤害后，本回合免疫。",
        effect_id="evasion",
        tags=["secret"]
    ),
    "狐人老千": CardDef(
        name="狐人老千",
        cost=2,
        card_type="minion",
        description="战吼：本回合下一张连击牌法力值消耗减少2点。",
        effect_id="foxy_fraud",
        tags=["battlecry", "pirate"],
        health=2
    ),
    "行骗": CardDef(
        name="行骗",
        cost=2,
        card_type="spell",
        description="抽一张法术牌。连击：并抽一张随从牌。",
        effect_id="swindle",
        tags=["combo"]
    ),
    "锯齿骨刺": CardDef(
        name="锯齿骨刺",
        cost=2,
        card_type="spell",
        description="造成3点伤害。如果目标死亡，本回合下一张牌法力值消耗减少2点。",
        effect_id="serrated_bone_spike"
    ),
    "疾速矿锄": CardDef(
        name="疾速矿锄",
        cost=2,
        card_type="weapon",
        description="英雄攻击后，抽一张牌。",
        effect_id="quick_pick"
    ),
    "异教地图": CardDef(
        name="异教地图",
        cost=2,
        card_type="spell",
        description="从牌库发现一张牌。如果本回合使用该牌，再从其余选项中选择一张。",
        effect_id="cultist_map"
    ),
    "潜伏帷幕": CardDef(
        name="潜伏帷幕",
        cost=3,
        card_type="spell",
        description="抽两张随从牌，本回合使用则使其潜行一回合。",
        effect_id="shroud_of_concealment"
    ),
    "晦鳞巢母": CardDef(
        name="晦鳞巢母",
        cost=3,
        card_type="minion",
        description="战吼：如果手牌中有龙牌，复原两个法力水晶。",
        effect_id="candlebreath_mother",
        tags=["battlecry"],
        health=3
    ),
    "鲨鱼之灵": CardDef(
        name="鲨鱼之灵",
        cost=4,
        card_type="minion",
        description="潜行一回合。随从的战吼和连击触发两次。",
        effect_id="spirit_of_the_shark",
        health=3
    ),
    "斯卡布斯·刀油": CardDef(
        name="斯卡布斯·刀油",
        cost=4,
        card_type="minion",
        description="连击：本回合使用的下两张牌法力值消耗减少2点。",
        effect_id="scabbs_cutterbutter",
        tags=["combo"],
        health=3
    ),
    "乐队经理精英牛头人酋长": CardDef(
        name="乐队经理精英牛头人酋长",
        cost=4,
        card_type="minion",
        description="战吼：发现乐队中的一张牌。",
        effect_id="elite_tauren_champion",
        tags=["battlecry"],
        health=4
    ),
    "幻觉药水": CardDef(
        name="幻觉药水",
        cost=4,
        card_type="spell",
        description="将所有友方随从的1/1复制置入手牌，法力值消耗变为1点。",
        effect_id="potion_of_illusion"
    ),
    "舞动全场（ft.迦罗娜）": CardDef(
        name="舞动全场（ft.迦罗娜）",
        cost=3,
        card_type="spell",
        description="将所有友方随从移回手牌，本回合其法力值消耗为1点。",
        effect_id="breakdance"
    ),
    "生命的缚誓者阿莱克丝塔萨": CardDef(
        name="生命的缚誓者阿莱克丝塔萨",
        cost=9,
        card_type="minion",
        description="战吼：友方角色恢复8点生命值；敌方角色受到8点伤害。",
        effect_id="alexstrasza",
        tags=["battlecry", "dragon"],
        health=8
    ),
    "可疑交易": CardDef(
        name="可疑交易",
        cost=4,
        card_type="spell",
        description="抽三张牌。连击：随机消灭一个敌方随从。",
        effect_id="dubious_purchase",
        tags=["combo"]
    ),
    "暗影施法者": CardDef(
        name="暗影施法者",
        cost=5,
        card_type="minion",
        description="战吼：选择友方随从，将其1/1复制置入手牌，法力值消耗为1点。",
        effect_id="shadowcaster",
        tags=["battlecry"],
        health=4
    ),
}


ETC_BAND = [
    "舞动全场（ft.迦罗娜）",
    "幻觉药水",
    "生命的缚誓者阿莱克丝塔萨",
]

CORE_CARD_NAMES = {
    "鲨鱼之灵",
    "狐人老千",
    "斯卡布斯·刀油",
    "暗影施法者",
    "乐队经理精英牛头人酋长",
    "晦鳞巢母",
    "生命的缚誓者阿莱克丝塔萨",
    "暗影步",
    "舞动全场（ft.迦罗娜）",
    "幻觉药水",
    "锯齿骨刺",
    "殒命暗影",
}

def ordered_breakdance_returning(minions: List[CardInstance]) -> List[CardInstance]:
    # 真炉石规则：群体回手按随从进场顺序结算（先上场的先回手）。
    # 场面数组从左到右即进场顺序，因此保持原顺序即可。
    return list(minions)


HIGH_COST_DISCOUNT_TARGETS = {
    "乐队经理精英牛头人酋长",
    "暗影施法者",
    "生命的缚誓者阿莱克丝塔萨",
    "晦鳞巢母",
    "斯卡布斯·刀油",
    "鲨鱼之灵",
}

DRAW_CARD_EFFECTS = {
    "dig_for_treasure",
    "shroud_of_concealment",
    "swindle",
    "dubious_purchase",
    "gone_fishin",
    "quick_pick",
}

SHADOWCASTER_ALLOWED_TARGETS = {
    "暗影施法者",
    "斯卡布斯·刀油",
    "生命的缚誓者阿莱克丝塔萨",
    "晦鳞巢母",
}

MANA_GAIN_OR_DISCOUNT_EFFECTS = {
    "coin",
    "fake_coin",
    "preparation",
    "shadowstep",
    "foxy_fraud",
    "scabbs_cutterbutter",
    "serrated_bone_spike",
}

PREFERRED_COMBO_PATTERNS = [
    ("鲨鱼之灵", "狐人老千", "斯卡布斯·刀油"),
    ("斯卡布斯·刀油", "暗影施法者", "乐队经理精英牛头人酋长"),
    ("鲨鱼之灵", "斯卡布斯·刀油", "斯卡布斯·刀油", "生命的缚誓者阿莱克丝塔萨"),
    ("生命的缚誓者阿莱克丝塔萨", "暗影施法者", "生命的缚誓者阿莱克丝塔萨", "生命的缚誓者阿莱克丝塔萨"),
]


def make_card(name: str, cost: Optional[int] = None, use_runtime_cost: bool = False) -> CardInstance:
    if name not in CARD_DATABASE:
        raise KeyError(f"未知卡牌：{name}")

    card = CardInstance.from_def(CARD_DATABASE[name])

    if use_runtime_cost:
        card.cost = cost
        card.temp_cost = None

    return card


def make_card_from_rebuild_entry(card_entry) -> Optional[CardInstance]:
    name = getattr(card_entry, "name", "")

    if name not in CARD_DATABASE:
        return None

    card = make_card(
        name=name,
        cost=getattr(card_entry, "cost", None),
        use_runtime_cost=True
    )
    health = getattr(card_entry, "health", None)

    if health is not None:
        card.health = int(health)

    return card


def mark_as_deadly_shadow(card: CardInstance) -> CardInstance:
    card.is_deadly_shadow = True
    return card


def make_deadly_shadow_copy_from_spell(spell_card: CardInstance) -> Optional[CardInstance]:
    if spell_card.original_name not in CARD_DATABASE:
        return None

    copied = make_card(spell_card.original_name)
    copied.is_deadly_shadow = True
    return copied


def make_cards(names: List[str]) -> List[CardInstance]:
    return [make_card(name) for name in names]


def display_card_name(card: CardInstance) -> str:
    if card.is_deadly_shadow:
        return f"{card.name}[殒命暗影]"

    return card.name


def card_health_text(card: CardInstance) -> str:
    if card.card_type != "minion":
        return ""

    return f"{card.health if card.health is not None else '?'}血"


def card_cost_health_label(card: CardInstance) -> str:
    cost = card.current_cost()
    cost_text = "*" if cost is None else str(cost)
    health_text = card_health_text(card)

    if health_text:
        return f"{display_card_name(card)}[{cost_text}费,{health_text}]"

    return f"{display_card_name(card)}[{cost_text}费]"


def cards_drawn_if_played(state: GameState, card: CardInstance) -> int:
    """计算打出该牌是否会产生不确定抽牌。

    默认 OCR 场景不知道牌库，未知牌库一律按“会抽到牌”处理并禁用抽牌法术。
    只有像垂钓时光这种“不触发连击就不抽牌”的法术，可作为第一张牌安全使用。
    """
    effect_id = card.effect_id

    if effect_id not in DRAW_CARD_EFFECTS:
        return 0

    if effect_id == "gone_fishin" and not combo_active(state):
        return 0

    if not state.deck_is_known:
        unknown_draw_counts = {
            "dubious_purchase": 3,
            "dig_for_treasure": 1,
            "shroud_of_concealment": 2,
            "swindle": 2 if combo_active(state) else 1,
            "gone_fishin": 1,
            "quick_pick": 1,
        }
        return unknown_draw_counts.get(effect_id, 1)

    deck_minions = sum(1 for c in state.deck_zone.cards if c.card_type == "minion")
    deck_spells = sum(1 for c in state.deck_zone.cards if is_spell_like(c))
    deck_total = len(state.deck_zone.cards)

    if effect_id == "dubious_purchase":
        return min(3, deck_total)

    if effect_id == "gone_fishin":
        return min(1, deck_total)

    if effect_id == "dig_for_treasure":
        return min(1, deck_minions)

    if effect_id == "swindle":
        count = min(1, deck_spells)

        if combo_active(state):
            count += min(1, deck_minions)

        return count

    if effect_id == "shroud_of_concealment":
        return min(2, deck_minions)

    if effect_id == "quick_pick":
        return min(1, deck_total)

    return 0


def make_one_one_copy(card: CardInstance) -> CardInstance:
    copied = copy.deepcopy(card)
    copied.temp_cost = 1
    copied.health = 1
    return copied


def is_spell_like(card: CardInstance) -> bool:
    return card.card_type in {"spell", "secret"}


def add_to_hand(state: GameState, card: CardInstance) -> bool:
    return state.hand_zone.add(card)


def draw_top_card(state: GameState) -> Optional[CardInstance]:
    return state.deck_zone.draw_top()


def draw_first_by_type(state: GameState, card_type: str) -> Optional[CardInstance]:
    return state.deck_zone.draw_first_by_type(card_type)


def draw_cards(state: GameState, count: int):
    for _ in range(count):
        draw_top_card(state)


def restore_mana(state: GameState, amount: int):
    state.mana_pool.restore(amount)


def gain_temporary_mana(state: GameState, amount: int):
    state.mana_pool.gain_temporary(amount)


def effective_cost(state: GameState, card: CardInstance) -> Optional[int]:
    base_cost = card.current_cost()

    if base_cost is None:
        return None

    discount = 0

    if state.next_card_discount > 0:
        discount += state.next_card_discount

    if state.next_two_cards_discount_count > 0:
        discount += state.next_two_cards_discount

    for remaining_count, discount_amount in state.active_card_discounts:
        if remaining_count > 0 and discount_amount > 0:
            discount += discount_amount

    if is_spell_like(card) and state.next_spell_discount > 0:
        discount += state.next_spell_discount

    if "combo" in card.tags and state.next_combo_discount > 0:
        discount += state.next_combo_discount

    return max(0, base_cost - discount)


def active_oil_discount(state: GameState) -> bool:
    return any(
        remaining_count > 0 and discount_amount > 0
        for remaining_count, discount_amount in state.active_card_discounts
    )


def original_card_cost(card: CardInstance) -> Optional[int]:
    card_def = CARD_DATABASE.get(card.original_name) or CARD_DATABASE.get(card.name)

    if card_def is None:
        return card.current_cost()

    return card_def.cost


def is_original_cost_play(card: CardInstance, cost: Optional[int]) -> bool:
    return cost is not None and card.current_cost() == original_card_cost(card) and cost == card.current_cost()


def hand_has_dragon_after_playing(state: GameState, hand_index: int) -> bool:
    for index, card in enumerate(state.hand_zone.cards):
        if index == hand_index:
            continue

        if "dragon" in card.tags:
            return True

    return False


def target_name_for_index(state: GameState, target_friendly_index: Optional[int]) -> Optional[str]:
    if target_friendly_index is None:
        return None

    if target_friendly_index < 0 or target_friendly_index >= len(state.board_zone):
        return None

    return state.board_zone.cards[target_friendly_index].name


def preferred_combo_bonus(path: List[str], next_name: str) -> int:
    names = path + [next_name]
    bonus = 0

    for pattern in PREFERRED_COMBO_PATTERNS:
        prefix_length = min(len(pattern), len(names))

        if tuple(names[-prefix_length:]) == pattern[:prefix_length]:
            bonus += prefix_length * 10

        if len(names) >= len(pattern) and tuple(names[-len(pattern):]) == pattern:
            bonus += 100

    return bonus


def heuristic_score(
    state: GameState,
    card: CardInstance,
    cost: int,
    target_friendly_index: Optional[int],
    target_enemy_is_killed: bool
) -> int:
    score = 0
    current_cost = card.current_cost() or 0
    saved_cost = max(0, current_cost - cost)

    if cost == 0 and card.effect_id in MANA_GAIN_OR_DISCOUNT_EFFECTS:
        score += 120

    if card.effect_id in {"coin", "fake_coin"}:
        early_bonus = max(0, 8 - state.cards_played_this_turn) * 10
        hand_pressure = max(0, len(state.hand_zone.cards) - 6) * 50
        score += early_bonus + hand_pressure

    if saved_cost > 0:
        score += saved_cost * 25

    if active_oil_discount(state) and current_cost in {3, 4}:
        score += 80

    if card.name in CORE_CARD_NAMES or card.is_deadly_shadow:
        score += 60

    score += preferred_combo_bonus(state.path, display_card_name(card).split("(")[0])

    if card.effect_id == "shadowcaster" and target_name_for_index(state, target_friendly_index) in SHADOWCASTER_ALLOWED_TARGETS:
        score += 40

    if card.effect_id == "serrated_bone_spike" and target_enemy_is_killed:
        score += 30

    return score


def consume_discounts(state: GameState, card: CardInstance):
    state.next_card_discount = 0

    if state.next_two_cards_discount_count > 0:
        state.next_two_cards_discount_count -= 1

        if state.next_two_cards_discount_count <= 0:
            state.next_two_cards_discount = 0

    if state.active_card_discounts:
        remaining_discounts: List[Tuple[int, int]] = []

        for remaining_count, discount_amount in state.active_card_discounts:
            next_remaining_count = remaining_count - 1

            if next_remaining_count > 0:
                remaining_discounts.append((next_remaining_count, discount_amount))

        state.active_card_discounts = remaining_discounts

    if is_spell_like(card):
        state.next_spell_discount = 0

    if "combo" in card.tags:
        state.next_combo_discount = 0


def combo_active(state: GameState) -> bool:
    return state.cards_played_this_turn > 0


def minion_trigger_multiplier(state: GameState, card: CardInstance) -> int:
    if card.card_type == "minion" and state.has_shark():
        return 2

    return 1


def alex_damage_amount(state: GameState) -> int:
    # 红龙战吼对敌方角色造成8点伤害；鲨鱼之灵使战吼触发两次。
    multiplier = 2 if state.has_shark() else 1
    return 8 * multiplier


def transform_deadly_shadows(state: GameState, spell_card: CardInstance):
    if spell_card.original_name == "殒命暗影":
        return

    for index, card in enumerate(state.hand_zone.cards):
        if card.is_deadly_shadow:
            copied = make_deadly_shadow_copy_from_spell(spell_card)

            if copied is None:
                continue

            state.hand_zone.cards[index] = copied
            state.add_log(f"殒命暗影变形为：{copied.name}")


def play_card_by_name(
    state: GameState,
    name: str,
    target_friendly_index: Optional[int] = None,
    target_enemy_is_killed: bool = False,
    discover_choice_index: int = 0,
    enemy_target: bool = True
) -> bool:
    for index, card in enumerate(state.hand_zone.cards):
        if card.name == name:
            return play_card(
                state=state,
                hand_index=index,
                target_friendly_index=target_friendly_index,
                target_enemy_is_killed=target_enemy_is_killed,
                discover_choice_index=discover_choice_index,
                enemy_target=enemy_target
            )

    state.add_log(f"手牌中没有：{name}")
    return False


def play_card(
    state: GameState,
    hand_index: int,
    target_friendly_index: Optional[int] = None,
    target_enemy_is_killed: bool = False,
    discover_choice_index: int = 0,
    enemy_target: bool = True
) -> bool:
    if hand_index < 0 or hand_index >= len(state.hand_zone):
        state.add_log("无效的手牌序号")
        return False

    card = state.hand_zone.cards[hand_index]
    card_display_name = display_card_name(card)
    cost = effective_cost(state, card)

    if cost is None:
        state.add_log(f"无法使用：{card_display_name} 当前没有费用")
        return False

    if not state.mana_pool.can_pay(cost):
        state.add_log(f"法力不足，无法使用：{card_display_name}，需要{cost}，当前{state.mana}")
        return False

    if card.card_type == "minion" and not state.board_zone.can_add():
        state.add_log(f"随从栏已满，无法使用：{card_display_name}")
        return False

    if card.card_type == "secret" and not state.secret_zone.can_add():
        state.add_log(f"奥秘栏已满，无法使用：{card_display_name}")
        return False

    state.mana_pool.pay(cost)
    state.hand_zone.remove_at(hand_index)
    consume_discounts(state, card)
    state.add_log(f"使用：{card_display_name}，费用：{cost}，剩余法力：{state.mana}")

    if card.card_type == "minion":
        state.board_zone.add(card)
    elif card.card_type == "secret":
        state.secret_zone.add(card)
    elif card.card_type == "weapon":
        state.weapon = card

    if card.name == "生命的缚誓者阿莱克丝塔萨":
        state.alex_play_count += 1

        if enemy_target:
            state.alex_damage += alex_damage_amount(state)

    apply_card_effect(
        state=state,
        card=card,
        target_friendly_index=target_friendly_index,
        target_enemy_is_killed=target_enemy_is_killed,
        discover_choice_index=discover_choice_index,
        enemy_target=enemy_target
    )

    if is_spell_like(card):
        state.last_spell_original_name = card.original_name
        transform_deadly_shadows(state, card)

    state.cards_played_this_turn += 1
    return True


def apply_card_effect(
    state: GameState,
    card: CardInstance,
    target_friendly_index: Optional[int],
    target_enemy_is_killed: bool,
    discover_choice_index: int,
    enemy_target: bool
):
    effect = EFFECT_HANDLERS.get(card.effect_id)

    if effect is None:
        return

    times = 1

    if "battlecry" in card.tags or "combo" in card.tags:
        times = minion_trigger_multiplier(state, card)

    for _ in range(times):
        effect(
            state=state,
            card=card,
            target_friendly_index=target_friendly_index,
            target_enemy_is_killed=target_enemy_is_killed,
            discover_choice_index=discover_choice_index,
            enemy_target=enemy_target
        )


def effect_shadowstep(state: GameState, card: CardInstance, target_friendly_index: Optional[int], **kwargs):
    if target_friendly_index is None or target_friendly_index >= len(state.board_zone):
        state.add_log("暗影步缺少有效友方随从目标")
        return

    target = state.board_zone.remove_at(target_friendly_index)
    base_cost = target.current_cost() or 0
    target.temp_cost = max(0, base_cost - 2)
    add_to_hand(state, target)


def effect_coin(state: GameState, **kwargs):
    gain_temporary_mana(state, 1)


def effect_preparation(state: GameState, **kwargs):
    state.next_spell_discount += 2
    state.add_log("下一张法术减2费")


def effect_foxy_fraud(state: GameState, **kwargs):
    state.next_combo_discount += 2
    state.add_log("下一张连击牌减2费")


def effect_scabbs(state: GameState, **kwargs):
    if combo_active(state):
        state.active_card_discounts.append((2, 2))
        state.add_log("本回合下两张牌减2费")


def effect_swindle(state: GameState, **kwargs):
    draw_first_by_type(state, "spell")

    if combo_active(state):
        draw_first_by_type(state, "minion")


def effect_dig_for_treasure(state: GameState, **kwargs):
    drawn = draw_first_by_type(state, "minion")

    if drawn and "pirate" in drawn.tags:
        add_to_hand(state, make_card("幸运币"))


def effect_gone_fishin(state: GameState, **kwargs):
    if combo_active(state):
        draw_top_card(state)

    state.add_log("探底效果已简化为记录，不自动重排牌库")


def effect_blackwater_cutlass_trade(state: GameState):
    for card in state.hand_zone.cards:
        if is_spell_like(card):
            base_cost = card.current_cost() or 0
            card.temp_cost = max(0, base_cost - 1)
            state.add_log(f"黑水弯刀交易后减费：{card.name}")
            break


def trade_card_by_name(state: GameState, name: str) -> bool:
    for index, card in enumerate(state.hand_zone.cards):
        if card.name == name and "tradeable" in card.tags:
            traded = state.hand_zone.remove_at(index)
            state.deck_zone.append_bottom(traded)
            state.add_log(f"交易：{traded.name} 放回牌库")
            state.deck_zone.draw_top()

            if traded.effect_id == "blackwater_cutlass":
                effect_blackwater_cutlass_trade(state)

            return True

    state.add_log(f"没有可交易的手牌：{name}")
    return False


def effect_serrated_bone_spike(
    state: GameState,
    target_friendly_index: Optional[int] = None,
    target_enemy_is_killed: bool = False,
    **kwargs
):
    if target_enemy_is_killed:
        if target_friendly_index is not None and target_friendly_index < len(state.board_zone):
            killed = state.board_zone.remove_at(target_friendly_index)
            state.add_log(f"锯齿骨刺击杀目标：{killed.name}")

        state.next_card_discount += 2
        state.add_log("锯齿骨刺击杀目标，下一张牌减2费")


def effect_quick_pick_attack(state: GameState):
    draw_top_card(state)


def effect_cultist_map(state: GameState, discover_choice_index: int = 0, **kwargs):
    discover_from_deck(state, discover_choice_index)


def discover_from_deck(state: GameState, choice_index: int = 0) -> Optional[CardInstance]:
    if len(state.deck_zone) == 0:
        state.add_log("牌库为空，无法发现")
        return None

    choices = state.deck_zone.cards[:3]
    choice_index = max(0, min(choice_index, len(choices) - 1))
    chosen = choices[choice_index]
    state.deck_zone.cards.remove(chosen)
    add_to_hand(state, chosen)
    state.add_log(f"发现：{chosen.name}")
    return chosen


def effect_shroud(state: GameState, **kwargs):
    draw_first_by_type(state, "minion")
    draw_first_by_type(state, "minion")


def effect_candlebreath_mother(state: GameState, **kwargs):
    has_dragon = any("dragon" in card.tags for card in state.hand_zone.cards)

    if has_dragon:
        restore_mana(state, 2)
    else:
        state.add_log("手牌中没有龙牌，晦鳞巢母不复原水晶")


def effect_etc(state: GameState, discover_choice_index: int = 0, **kwargs):
    if not state.etc_band_remaining:
        state.add_log("牛头人酋长：乐队中已没有可选牌")
        return

    discover_choice_index = max(0, min(discover_choice_index, len(state.etc_band_remaining) - 1))
    chosen_name = state.etc_band_remaining.pop(discover_choice_index)
    add_to_hand(state, make_card(chosen_name))
    state.add_log(f"牛头人酋长选择：{chosen_name}")


def effect_potion_of_illusion(state: GameState, **kwargs):
    for minion in state.board_zone.cards[:]:
        copy_card = copy.deepcopy(minion)
        copy_card.temp_cost = 1
        add_to_hand(state, copy_card)


def effect_breakdance(state: GameState, **kwargs):
    returning = ordered_breakdance_returning(state.board_zone.cards[:])
    state.board_zone.cards.clear()

    for minion in returning:
        minion.temp_cost = 1
        add_to_hand(state, minion)


def breakdance_search_branches(state: GameState) -> List[GameState]:
    returning = state.board_zone.cards[:]
    free_slots = max(0, MAX_HAND_SIZE - len(state.hand_zone.cards))
    new_state = state.clone()
    new_returning = new_state.board_zone.cards[:]
    new_state.board_zone.cards.clear()

    if len(returning) <= free_slots:
        # 手牌放得下：按进场顺序全部回手，费用变为1。
        for minion in ordered_breakdance_returning(new_returning):
            minion.temp_cost = 1
            add_card_to_hand_or_burn(new_state, minion)

        return [new_state]

    # 真炉石规则：手牌满时按进场顺序回手，先进场的优先占位，后进场的被消灭。
    kept = new_returning[:free_slots]
    burned = new_returning[free_slots:]

    for minion in ordered_breakdance_returning(kept):
        minion.temp_cost = 1
        add_card_to_hand_or_burn(new_state, minion)

    for minion in burned:
        new_state.burned_cards += 1
        new_state.add_log(f"爆牌：{minion.name}")

    return [new_state]


def effect_alexstrasza(state: GameState, enemy_target: bool = True, **kwargs):
    if enemy_target:
        state.add_log("红龙对敌方角色造成8点伤害")
    else:
        state.add_log("红龙为友方角色恢复8点生命值")


def effect_dubious_purchase(state: GameState, **kwargs):
    draw_cards(state, 3)

    if combo_active(state):
        state.add_log("可疑交易连击：随机消灭一个敌方随从")


def effect_shadowcaster(state: GameState, target_friendly_index: Optional[int], **kwargs):
    if target_friendly_index is None or target_friendly_index >= len(state.board_zone):
        state.add_log("暗影施法者缺少有效友方随从目标")
        return

    target = copy.deepcopy(state.board_zone.cards[target_friendly_index])
    target.temp_cost = 1
    add_to_hand(state, target)


def effect_evasion_trigger(state: GameState):
    for index, secret in enumerate(state.secret_zone.cards):
        if secret.name == "闪避":
            state.secret_zone.remove_at(index)
            state.hero_immune_this_turn = True
            state.add_log("触发闪避：本回合英雄免疫")
            return


EFFECT_HANDLERS: Dict[str, Callable] = {
    "shadowstep": effect_shadowstep,
    "fake_coin": effect_coin,
    "coin": effect_coin,
    "preparation": effect_preparation,
    "foxy_fraud": effect_foxy_fraud,
    "scabbs_cutterbutter": effect_scabbs,
    "swindle": effect_swindle,
    "dig_for_treasure": effect_dig_for_treasure,
    "gone_fishin": effect_gone_fishin,
    "serrated_bone_spike": effect_serrated_bone_spike,
    "cultist_map": effect_cultist_map,
    "shroud_of_concealment": effect_shroud,
    "candlebreath_mother": effect_candlebreath_mother,
    "elite_tauren_champion": effect_etc,
    "potion_of_illusion": effect_potion_of_illusion,
    "breakdance": effect_breakdance,
    "alexstrasza": effect_alexstrasza,
    "dubious_purchase": effect_dubious_purchase,
    "shadowcaster": effect_shadowcaster,
}


def create_state(
    deck_names: Optional[List[str]] = None,
    hand_names: Optional[List[str]] = None,
    mana_crystals: int = 10,
    mana: Optional[int] = None
) -> GameState:
    mana_crystals = max(0, min(mana_crystals, MAX_MANA_CRYSTALS))

    if mana is None:
        mana = mana_crystals

    return GameState(
        deck=make_cards(deck_names or []),
        deck_is_known=deck_names is not None,
        hand=make_cards(hand_names or []),
        mana_crystals=mana_crystals,
        mana=mana,
        initial_mana_crystals=mana_crystals,
        initial_mana=mana,
        etc_band_remaining=ETC_BAND[:]
    )


def state_summary(state: GameState) -> Dict:
    return {
        "mana_crystals": state.mana_crystals,
        "mana": state.mana,
        "initial_mana_crystals": state.initial_mana_crystals,
        "initial_mana": state.initial_mana,
        "hand": state.hand_zone.names(),
        "hand_detail": [
            {
                "name": card.name,
                "cost": card.current_cost(),
                "health": card.health,
                "is_deadly_shadow": card.is_deadly_shadow
            }
            for card in state.hand_zone.cards
        ],
        "deck": state.deck_zone.names(),
        "deck_is_known": state.deck_is_known,
        "board": state.board_zone.names(),
        "secrets": state.secret_zone.names(),
        "weapon": state.weapon.name if state.weapon else None,
        "etc_band_remaining": state.etc_band_remaining,
        "burned_cards": state.burned_cards,
        "alex_play_count": state.alex_play_count,
        "alex_damage": state.alex_damage,
        "path": state.path,
        "next_spell_discount": state.next_spell_discount,
        "next_combo_discount": state.next_combo_discount,
        "next_card_discount": state.next_card_discount,
        "next_two_cards_discount": state.next_two_cards_discount,
        "next_two_cards_discount_count": state.next_two_cards_discount_count,
        "active_card_discounts": state.active_card_discounts,
        "log": state.log
    }


def parse_names(text: str) -> List[str]:
    if not text:
        return []

    return [item.strip() for item in text.split(",") if item.strip()]


def card_label(card: CardInstance) -> str:
    return card_cost_health_label(card)


def playable_card_indexes(state: GameState) -> List[int]:
    indexes: List[int] = []

    for index, card in enumerate(state.hand_zone.cards):
        if card.name.startswith("未知"):
            continue

        cost = effective_cost(state, card)

        if cost is None:
            continue

        if not state.mana_pool.can_pay(cost):
            continue

        if card.card_type == "minion" and not state.board_zone.can_add():
            continue

        if card.card_type == "secret" and not state.secret_zone.can_add():
            continue

        indexes.append(index)

    return indexes


def add_unknown_cards_to_hand(state: GameState, count: int, label: str):
    for _ in range(count):
        unknown = CardInstance(
            name=f"未知{label}",
            cost=None,
            original_name=f"未知{label}",
            card_type="unknown",
            description="简化模型：只记录进入手牌，不参与后续出牌。",
            effect_id="unknown"
        )

        state.hand_zone.add(unknown)


def add_card_to_hand_or_burn(state: GameState, card: CardInstance):
    state.hand_zone.add(card)


def draw_top_cards_branch(state: GameState, count: int, label: str) -> GameState:
    new_state = state.clone()

    for _ in range(count):
        drawn = new_state.deck_zone.draw_top()

        if drawn is not None:
            new_state.add_log(f"{label}抽到：{drawn.name}")

    return new_state


def draw_first_by_type_branch(state: GameState, card_type: str, label: str) -> GameState:
    new_state = state.clone()
    drawn = new_state.deck_zone.draw_first_by_type(card_type)

    if drawn is not None:
        new_state.add_log(f"{label}抽到：{drawn.name}")

    return new_state


def remove_deck_card_by_index(state: GameState, index: int) -> CardInstance:
    return state.deck_zone.remove_at(index)


def minion_draw_choices(state: GameState, limit: int = 6) -> List[int]:
    return state.deck_zone.indexes_by_type("minion", limit=limit)


def draw_specific_minion_branch(state: GameState, deck_index: int, label: str) -> GameState:
    new_state = state.clone()
    card = remove_deck_card_by_index(new_state, deck_index)
    add_card_to_hand_or_burn(new_state, card)
    new_state.add_log(f"{label}抽到随从：{card.name}")
    return new_state


def draw_minion_branches(state: GameState, count: int, label: str, limit: int = 6) -> List[GameState]:
    states = [state]

    for draw_index in range(count):
        next_states: List[GameState] = []

        for current in states:
            choices = minion_draw_choices(current, limit=limit)

            if not choices:
                current_clone = current.clone()
                current_clone.add_log(f"{label}没有可抽随从")
                next_states.append(current_clone)
                continue

            for deck_index in choices:
                next_states.append(
                    draw_specific_minion_branch(
                        state=current,
                        deck_index=deck_index,
                        label=label
                    )
                )

        states = next_states

    return states


def append_choice_to_last_path(state: GameState, choice_name: str):
    if not state.path:
        return

    last_path = state.path[-1]

    if "（" in last_path and last_path.endswith("）"):
        state.path[-1] = f"{last_path[:-1]}->{choice_name}）"
    else:
        state.path[-1] = f"{last_path}（{choice_name}）"


def discover_fixed_choices(state: GameState, names: List[str], label: str) -> List[GameState]:
    states: List[GameState] = []

    if not names:
        new_state = state.clone()
        new_state.add_log(f"{label}：没有可选牌")
        states.append(new_state)
        return states

    for name in names:
        new_state = state.clone()
        add_card_to_hand_or_burn(new_state, make_card(name))
        if name in new_state.etc_band_remaining:
            new_state.etc_band_remaining.remove(name)
        append_choice_to_last_path(new_state, name)
        new_state.add_log(f"{label}选择：{name}")
        states.append(new_state)

    return states


def discover_deck_count_only(state: GameState, label: str) -> List[GameState]:
    new_state = state.clone()
    add_unknown_cards_to_hand(new_state, 1, "发现牌")
    new_state.add_log(f"{label}：发现简化为加入1张未知牌")
    return [new_state]


def target_friendly_options(state: GameState, card: CardInstance) -> List[Optional[int]]:
    if card.effect_id in {"shadowstep", "shadowcaster", "serrated_bone_spike"}:
        return state.board_zone.valid_target_indexes()

    return [None]


def target_enemy_kill_options(card: CardInstance) -> List[bool]:
    if card.effect_id == "serrated_bone_spike":
        return [False, True]

    return [False]


def target_enemy_options(card: CardInstance) -> List[bool]:
    if card.effect_id == "alexstrasza":
        # 先枚举打脸伤害分支，保证去重后保留伤害更高的路径。
        return [True, False]

    return [True]


def play_card_base_for_search(
    state: GameState,
    hand_index: int,
    target_friendly_index: Optional[int],
    target_enemy_is_killed: bool,
    enemy_target: bool
) -> Optional[Tuple[GameState, CardInstance]]:
    if hand_index < 0 or hand_index >= len(state.hand_zone):
        return None

    new_state = state.clone()
    card = new_state.hand_zone.cards[hand_index]
    card_display_name = display_card_name(card)
    cost = effective_cost(new_state, card)

    if cost is None:
        return None

    if not new_state.mana_pool.can_pay(cost):
        return None

    if card.card_type == "minion" and not new_state.board_zone.can_add():
        return None

    if card.card_type == "secret" and not new_state.secret_zone.can_add():
        return None

    new_state.mana_pool.pay(cost)
    new_state.hand_zone.remove_at(hand_index)
    consume_discounts(new_state, card)

    target_text = ""

    if target_friendly_index is not None:
        if 0 <= target_friendly_index < len(new_state.board_zone):
            target_text = f"({new_state.board_zone.cards[target_friendly_index].name})"
        else:
            target_text = "(无效目标)"

    new_state.path.append(f"{card_display_name}{target_text}")
    new_state.add_log(f"使用：{card_display_name}，费用：{cost}，剩余法力：{new_state.mana}")

    if card.name == "生命的缚誓者阿莱克丝塔萨":
        new_state.alex_play_count += 1

        if enemy_target:
            new_state.alex_damage += alex_damage_amount(new_state)

    if card.card_type == "minion":
        new_state.board_zone.add(card)
    elif card.card_type == "secret":
        new_state.secret_zone.add(card)
    elif card.card_type == "weapon":
        new_state.weapon = card

    return new_state, card


def apply_search_effect(
    state: GameState,
    card: CardInstance,
    target_friendly_index: Optional[int],
    target_enemy_is_killed: bool,
    enemy_target: bool
) -> List[GameState]:
    multiplier = 1

    if "battlecry" in card.tags or "combo" in card.tags:
        multiplier = minion_trigger_multiplier(state, card)

    states = [state]

    for _ in range(multiplier):
        next_states: List[GameState] = []

        for current in states:
            effect_id = card.effect_id

            if effect_id in {"coin", "fake_coin"}:
                new_state = current.clone()
                gain_temporary_mana(new_state, 1)
                next_states.append(new_state)
            elif effect_id == "preparation":
                new_state = current.clone()
                new_state.next_spell_discount += 2
                next_states.append(new_state)
            elif effect_id == "foxy_fraud":
                new_state = current.clone()
                new_state.next_combo_discount += 2
                next_states.append(new_state)
            elif effect_id == "scabbs_cutterbutter":
                new_state = current.clone()

                if combo_active(new_state):
                    new_state.active_card_discounts.append((2, 2))

                next_states.append(new_state)
            elif effect_id == "shadowstep":
                new_state = current.clone()

                if target_friendly_index is not None and target_friendly_index < len(new_state.board_zone):
                    target = new_state.board_zone.remove_at(target_friendly_index)
                    base_cost = target.current_cost() or 0
                    target.temp_cost = max(0, base_cost - 2)
                    add_card_to_hand_or_burn(new_state, target)

                next_states.append(new_state)
            elif effect_id == "shadowcaster":
                new_state = current.clone()

                if target_friendly_index is not None and target_friendly_index < len(new_state.board_zone):
                    copied = make_one_one_copy(new_state.board_zone.cards[target_friendly_index])
                    add_card_to_hand_or_burn(new_state, copied)

                next_states.append(new_state)
            elif effect_id == "breakdance":
                next_states.extend(breakdance_search_branches(current))
            elif effect_id == "potion_of_illusion":
                new_state = current.clone()

                for minion in new_state.board_zone.cards[:]:
                    copied = make_one_one_copy(minion)
                    add_card_to_hand_or_burn(new_state, copied)

                next_states.append(new_state)
            elif effect_id == "candlebreath_mother":
                new_state = current.clone()

                if any("dragon" in card_in_hand.tags for card_in_hand in new_state.hand_zone.cards):
                    restore_mana(new_state, 2)

                next_states.append(new_state)
            elif effect_id == "serrated_bone_spike":
                new_state = current.clone()

                if target_enemy_is_killed:
                    if target_friendly_index is None or target_friendly_index >= len(new_state.board_zone):
                        continue

                    target = new_state.board_zone.cards[target_friendly_index]

                    if target.health is None or target.health > 3:
                        continue

                    new_state.board_zone.remove_at(target_friendly_index)
                    new_state.add_log(f"锯齿骨刺击杀：{target.name}")

                    new_state.next_card_discount += 2

                next_states.append(new_state)
            elif effect_id == "dig_for_treasure":
                next_states.extend(draw_minion_branches(current, 1, "挖掘宝藏", limit=6))
            elif effect_id == "shroud_of_concealment":
                next_states.extend(draw_minion_branches(current, 2, "潜伏帷幕", limit=6))
            elif effect_id == "swindle":
                new_state = draw_first_by_type_branch(current, "spell", "行骗")

                if combo_active(new_state):
                    next_states.extend(draw_minion_branches(new_state, 1, "行骗连击", limit=6))
                else:
                    next_states.append(new_state)
            elif effect_id == "dubious_purchase":
                next_states.append(draw_top_cards_branch(current, 3, "可疑交易"))
            elif effect_id == "gone_fishin":
                new_state = current.clone()

                if combo_active(new_state):
                    add_unknown_cards_to_hand(new_state, 1, "抽牌")

                next_states.append(new_state)
            elif effect_id == "cultist_map":
                next_states.extend(discover_deck_count_only(current, "异教地图"))
            elif effect_id == "elite_tauren_champion":
                next_states.extend(discover_fixed_choices(current, current.etc_band_remaining, "牛头人酋长"))
            elif effect_id == "alexstrasza":
                new_state = current.clone()
                new_state.add_log("红龙目标：敌方" if enemy_target else "红龙目标：友方")
                next_states.append(new_state)
            else:
                next_states.append(current.clone())

        states = next_states

    for result_state in states:
        if is_spell_like(card):
            result_state.last_spell_original_name = card.original_name
            transform_deadly_shadows(result_state, card)

        result_state.cards_played_this_turn += 1

    return states


def generate_successors(state: GameState, prune_stats: Optional[Dict[str, int]] = None) -> List[GameState]:
    prioritized_successors: List[Tuple[int, int, GameState]] = []
    sequence = 0

    for hand_index in playable_card_indexes(state):
        card = state.hand_zone.cards[hand_index]
        cost = effective_cost(state, card)

        if cost is None:
            continue

        if cards_drawn_if_played(state, card) > 0:
            if prune_stats is not None:
                prune_stats["排除抽牌法术"] = prune_stats.get("排除抽牌法术", 0) + 1

            continue

        for target_friendly_index in target_friendly_options(state, card):
            for target_enemy_is_killed in target_enemy_kill_options(card):
                for enemy_target in target_enemy_options(card):
                    base = play_card_base_for_search(
                        state=state,
                        hand_index=hand_index,
                        target_friendly_index=target_friendly_index,
                        target_enemy_is_killed=target_enemy_is_killed,
                        enemy_target=enemy_target
                    )

                    if base is None:
                        continue

                    base_state, played_card = base
                    score = heuristic_score(
                        state=state,
                        card=card,
                        cost=cost,
                        target_friendly_index=target_friendly_index,
                        target_enemy_is_killed=target_enemy_is_killed
                    )

                    for successor in apply_search_effect(
                        state=base_state,
                        card=played_card,
                        target_friendly_index=target_friendly_index,
                        target_enemy_is_killed=target_enemy_is_killed,
                        enemy_target=enemy_target
                    ):
                        prioritized_successors.append((score, sequence, successor))
                        sequence += 1

    prioritized_successors.sort(key=lambda item: (item[0], item[1]))
    return [successor for _, _, successor in prioritized_successors]


def state_signature(state: GameState) -> Tuple:
    def safe_cost(cost: Optional[int]) -> int:
        return -1 if cost is None else cost

    def card_key(card: CardInstance) -> Tuple:
        return (
            card.name,
            card.original_name,
            card.card_type,
            card.effect_id,
            safe_cost(card.cost),
            safe_cost(card.temp_cost),
            safe_cost(card.current_cost()),
            card.is_deadly_shadow,
            -1 if card.health is None else card.health,
        )

    return (
        state.mana,
        state.deck_is_known,
        tuple(sorted(card_key(card) for card in state.hand_zone.cards)),
        tuple(card_key(card) for card in state.deck_zone.cards),
        tuple(card_key(card) for card in state.board_zone.cards),
        tuple(card_key(card) for card in state.secret_zone.cards),
        None if state.weapon is None else card_key(state.weapon),
        tuple(state.etc_band_remaining),
        state.cards_played_this_turn,
        state.next_spell_discount,
        state.next_combo_discount,
        state.next_card_discount,
        state.next_two_cards_discount,
        state.next_two_cards_discount_count,
        tuple(state.active_card_discounts),
        state.burned_cards,
    )


def state_key_for_dedup(state: GameState) -> Tuple:
    def safe_cost(cost: Optional[int]) -> int:
        return -1 if cost is None else cost

    def card_key(card: CardInstance) -> Tuple:
        return (
            card.name,
            card.original_name,
            card.card_type,
            card.effect_id,
            safe_cost(card.cost),
            safe_cost(card.temp_cost),
            safe_cost(card.current_cost()),
            card.is_deadly_shadow,
            -1 if card.health is None else card.health,
        )

    return (
        state.deck_is_known,
        tuple(sorted(card_key(card) for card in state.hand_zone.cards)),
        tuple(card_key(card) for card in state.deck_zone.cards),
        tuple(card_key(card) for card in state.board_zone.cards),
        tuple(card_key(card) for card in state.secret_zone.cards),
        None if state.weapon is None else card_key(state.weapon),
        tuple(state.etc_band_remaining),
        state.cards_played_this_turn,
        state.next_spell_discount,
        state.next_combo_discount,
        state.next_card_discount,
        state.next_two_cards_discount,
        state.next_two_cards_discount_count,
        tuple(state.active_card_discounts),
    )


def dominance_value(state: GameState) -> Tuple[int, int, int]:
    return (
        state.mana,
        state.alex_play_count,
        -state.burned_cards,
    )


def dominates(existing_value: Tuple[int, int, int], new_value: Tuple[int, int, int]) -> bool:
    return all(
        existing >= new
        for existing, new in zip(existing_value, new_value)
    )


@dataclass(frozen=True)
class SymbolicAction:
    name: str
    target: Optional[str] = None
    choices: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ()


@dataclass
class SymbolicChain:
    name: str
    target_alex_count: int
    reasoning: List[str]
    actions: List[SymbolicAction]


def action_label(action: SymbolicAction) -> str:
    label = action.name

    if action.aliases:
        label += "{" + "|".join(action.aliases) + "}"

    if action.target:
        label += f"({action.target})"

    if action.choices:
        label += "（" + "->".join(action.choices) + "）"

    return label


def canonical_path_item(path_item: str) -> str:
    return path_item.replace("[殒命暗影]", "")


def symbolic_chain_key(state: GameState, action_index: int, target_alex_count: int) -> Tuple:
    return (
        state_key_for_dedup(state),
        action_index,
        max(0, target_alex_count - state.alex_play_count),
        state.mana,
    )


def chain_action_matches(path_item: str, action: SymbolicAction) -> bool:
    canonical_item = canonical_path_item(path_item)
    candidate_names = (action.name,) + action.aliases

    if not any(canonical_item.startswith(candidate_name) for candidate_name in candidate_names):
        return False

    if action.target and f"({action.target})" not in canonical_item:
        return False

    for choice in action.choices:
        if choice not in canonical_item:
            return False

    return True


def chain_action_already_satisfied(initial_state: GameState, state: GameState, action: SymbolicAction) -> bool:
    if action.target or action.choices:
        return False

    initial_board_names = {card.name for card in initial_state.board_zone.cards}
    board_names = {card.name for card in state.board_zone.cards}

    if action.name == "鲨鱼之灵":
        return "鲨鱼之灵" in initial_board_names and "鲨鱼之灵" in board_names

    if action.name == "狐人老千":
        return initial_state.next_combo_discount > 0 and state.next_combo_discount > 0

    if action.name == "斯卡布斯·刀油":
        return bool(initial_state.active_card_discounts) and any(
            remaining_count > 0 and discount_amount > 0
            for remaining_count, discount_amount in state.active_card_discounts
        )

    if action.name == "伺机待发":
        return initial_state.next_spell_discount > 0 and state.next_spell_discount > 0

    if action.name == "锯齿骨刺":
        return initial_state.next_card_discount > 0 and state.next_card_discount > 0

    if action.name == "晦鳞巢母":
        return "晦鳞巢母" in initial_board_names and "晦鳞巢母" in board_names

    return False


def summarize_chain_failure(states: List[GameState], action: SymbolicAction) -> str:
    if not states:
        return f"需要 {action_label(action)}，但没有可继续验证的状态"

    state = sort_path_states(states)[0]
    hand_text = "，".join(
        card_cost_health_label(card)
        for card in state.hand_zone.cards[:10]
    ) or "空"
    board_text = "，".join(
        card_cost_health_label(card)
        for card in state.board_zone.cards
    ) or "空"
    discount_text = (
        f"法术减{state.next_spell_discount}/连击减{state.next_combo_discount}/"
        f"下张减{state.next_card_discount}/刀油{state.active_card_discounts}"
    )
    return (
        f"需要 {action_label(action)}；当前法力 {state.mana}；"
        f"手牌：{hand_text}；场面：{board_text}；减费：{discount_text}"
    )


def validate_symbolic_chain(
    initial_state: GameState,
    chain: SymbolicChain,
    max_states: int,
    prune_stats: Optional[Dict[str, int]] = None,
    should_stop: Optional[Callable[[], bool]] = None
) -> List[GameState]:
    states = [initial_state.clone()]
    seen = set()
    intermediate_limit = max(1, min(max_states, CHAIN_VALIDATION_BEAM))

    for action_index, action in enumerate(chain.actions):
        if should_stop is not None and should_stop():
            return []

        next_states: List[GameState] = []

        for state in states:
            cache_key = symbolic_chain_key(state, action_index, chain.target_alex_count)

            if cache_key in seen:
                if prune_stats is not None:
                    prune_stats["符号链条DP复用"] = prune_stats.get("符号链条DP复用", 0) + 1

                continue

            seen.add(cache_key)

            if chain_action_already_satisfied(initial_state, state, action):
                next_states.append(state.clone())

                if prune_stats is not None:
                    prune_stats["符号链条预启动跳步"] = prune_stats.get("符号链条预启动跳步", 0) + 1

                if len(next_states) >= intermediate_limit:
                    break

                continue

            for successor in generate_successors(state, prune_stats=prune_stats):
                if not successor.path:
                    continue

                if chain_action_matches(successor.path[-1], action):
                    next_states.append(successor)

                    if len(next_states) >= intermediate_limit:
                        break

            if len(next_states) >= intermediate_limit:
                break

        if not next_states:
            if prune_stats is not None:
                prune_stats[f"链条失败：{chain.name} @ {action_label(action)}"] = (
                    prune_stats.get(f"链条失败：{chain.name} @ {action_label(action)}", 0) + 1
                )

                if chain.target_alex_count <= 3:
                    failure_detail = summarize_chain_failure(states, action)
                    prune_stats[f"链条失败详情：{failure_detail}"] = (
                        prune_stats.get(f"链条失败详情：{failure_detail}", 0) + 1
                    )

            return []

        states = sort_path_states(next_states)[:intermediate_limit]

    return [
        state
        for state in states
        if state.alex_play_count >= chain.target_alex_count
    ]


def build_symbolic_chains(target_alex_count: int) -> List[SymbolicChain]:
    alex = "生命的缚誓者阿莱克丝塔萨"
    shark = "鲨鱼之灵"
    foxy = "狐人老千"
    scabbs = "斯卡布斯·刀油"
    shadowcaster = "暗影施法者"
    etc = "乐队经理精英牛头人酋长"
    mother = "晦鳞巢母"
    dance = "舞动全场（ft.迦罗娜）"
    potion = "幻觉药水"
    preparation = "伺机待发"
    shadowstep = "暗影步"
    bone_spike = "锯齿骨刺"
    coin_prefixes = [
        [SymbolicAction("伪造的幸运币"), SymbolicAction("伪造的幸运币")],
        [SymbolicAction("幸运币"), SymbolicAction("伪造的幸运币")],
        [SymbolicAction("伪造的幸运币"), SymbolicAction("幸运币")],
        [SymbolicAction("幸运币")],
        [SymbolicAction("伪造的幸运币")],
        [],
    ]
    spell_discount_prefixes = [
        [SymbolicAction(preparation)],
        [SymbolicAction(bone_spike)],
        [],
    ]
    chains: List[SymbolicChain] = []

    def append_alex_tail(actions: List[SymbolicAction]) -> List[SymbolicAction]:
        tailed = actions[:]
        current_alex_count = sum(1 for action in tailed if action.name == alex)

        if current_alex_count <= 0:
            tailed.append(SymbolicAction(alex))
            current_alex_count += 1

        while current_alex_count < target_alex_count:
            tailed += [
                SymbolicAction(shadowcaster, target=alex),
                SymbolicAction(alex),
            ]
            current_alex_count += 1

            if current_alex_count < target_alex_count:
                tailed.append(SymbolicAction(alex))
                current_alex_count += 1

        return tailed

    def add_chain(name: str, reasoning: List[str], actions: List[SymbolicAction]):
        chains.append(SymbolicChain(
            name=name,
            target_alex_count=target_alex_count,
            reasoning=reasoning,
            actions=actions
        ))

    add_chain(
        name="基础32/48鱼狐刀暗牛刀晦舞链",
        reasoning=[
            "公式表基础骨架：鱼狐刀后先暗影施法者复制刀油，再牛找舞动和红龙。",
            "随后打1费刀油、晦鳞巢母回费、舞动回收；第二阶段鱼刀刀压低红龙和暗影施法者。",
            "鲨鱼在场时暗影施法者复制红龙会给两张1费红龙，因此尾部可以龙龙。",
        ],
        actions=append_alex_tail([
            SymbolicAction(shark),
            SymbolicAction(foxy),
            SymbolicAction(scabbs),
            SymbolicAction(shadowcaster, target=scabbs),
            SymbolicAction(etc, choices=(dance, alex)),
            SymbolicAction(scabbs),
            SymbolicAction(mother),
            SymbolicAction(dance),
            SymbolicAction(shark),
            SymbolicAction(scabbs),
            SymbolicAction(scabbs),
        ])
    )

    add_chain(
        name="基础32/48暗步回狐重铺链",
        reasoning=[
            "公式表步杂牌骨架：鱼狐刀暗复制刀油后牛找舞龙，随后暗影步回手狐人。",
            "重打狐人和刀油，再接晦鳞巢母与舞动；第二阶段仍按鱼刀刀龙暗龙收尾。",
        ],
        actions=append_alex_tail([
            SymbolicAction(shark),
            SymbolicAction(foxy),
            SymbolicAction(scabbs),
            SymbolicAction(shadowcaster, target=scabbs),
            SymbolicAction(etc, choices=(dance, alex)),
            SymbolicAction(shadowstep, target=foxy),
            SymbolicAction(foxy),
            SymbolicAction(scabbs),
            SymbolicAction(mother),
            SymbolicAction(dance),
            SymbolicAction(shark),
            SymbolicAction(scabbs),
            SymbolicAction(scabbs),
        ])
    )

    add_chain(
        name="6费无殒牛找龙舞骨刺狐链",
        reasoning=[
            "6费无殒基准链：鱼狐刀后，鲨鱼让牛头人双发现红龙和舞动。",
            "晦鳞巢母在鲨鱼下回4费；暗影步回刀，伺机待发接骨刺杀狐，使刀油变0费。",
            "随后暗影施法者复制刀油得到1/1刀油，继续刀油与舞动回收，最后鱼刀刀压低红龙。",
        ],
        actions=append_alex_tail([
            SymbolicAction(shark),
            SymbolicAction(foxy),
            SymbolicAction(scabbs),
            SymbolicAction(etc, choices=(dance, alex)),
            SymbolicAction(mother),
            SymbolicAction(shadowstep, target=scabbs),
            SymbolicAction(preparation),
            SymbolicAction(bone_spike, target=foxy),
            SymbolicAction(scabbs),
            SymbolicAction(shadowcaster, target=scabbs),
            SymbolicAction(scabbs),
            SymbolicAction(dance),
            SymbolicAction(shark),
            SymbolicAction(scabbs),
            SymbolicAction(scabbs),
        ])
    )

    add_chain(
        name="双伪造硬币牛找舞龙暗复制刀油链",
        reasoning=[
            "双伪造硬币先开，腾手牌并获得可突破水晶上限的临时法力。",
            "鱼狐刀启动后，牛头人在鲨鱼下找舞动和红龙；暗影施法者复制刀油制造后续减费窗口。",
            "晦鳞巢母因手牌有红龙回费，舞动回手后用1费鱼和双刀油压低红龙。",
        ],
        actions=append_alex_tail([
            SymbolicAction("伪造的幸运币"),
            SymbolicAction("伪造的幸运币"),
            SymbolicAction(shark),
            SymbolicAction(foxy),
            SymbolicAction(scabbs),
            SymbolicAction(etc, choices=(dance, alex)),
            SymbolicAction(shadowcaster, target=scabbs),
            SymbolicAction(scabbs),
            SymbolicAction(mother),
            SymbolicAction(dance),
            SymbolicAction(shark),
            SymbolicAction(scabbs),
            SymbolicAction(scabbs),
        ])
    )

    for coins in coin_prefixes:
        setup = coins + [
            SymbolicAction(shark),
            SymbolicAction(foxy),
            SymbolicAction(scabbs),
        ]

        add_chain(
            name=f"直出龙复制链-{len(coins)}资源",
            reasoning=[
                f"目标 {target_alex_count} 龙先假设存在第一张红龙。",
                "多龙需要暗影施法者复制红龙；若费用不足，前置鱼狐刀制造减费。",
            ],
            actions=append_alex_tail(setup)
        )

        add_chain(
            name=f"暗影步复制龙链-{len(coins)}资源",
            reasoning=[
                f"目标 {target_alex_count} 龙可由红龙上场后用暗影步回手降费继续构造。",
                "该模板用于验证暗影步本体或殒命暗影变形出的暗影步是否能续接红龙次数。",
            ],
            actions=append_alex_tail(setup + [
                SymbolicAction(alex),
                SymbolicAction(shadowstep, target=alex),
            ])
        )

        for spell_prefix in spell_discount_prefixes:
            discount_label = "+".join(action.name for action in spell_prefix) or "无额外法术减费"

            add_chain(
                name=f"手牌舞动回收链-{len(coins)}资源-{discount_label}",
                reasoning=[
                    f"目标 {target_alex_count} 龙反推为先铺鱼狐刀，再用舞动把核心随从变成1费复制回手。",
                    "伺机待发或殒命暗影变形出的伺机待发可让舞动更早发生；回手后用 1 费鲨鱼和双刀油压低红龙/暗影施法者。",
                ],
                actions=append_alex_tail(setup + spell_prefix + [
                    SymbolicAction(dance),
                    SymbolicAction(shark),
                    SymbolicAction(scabbs),
                    SymbolicAction(scabbs),
                ])
            )

            add_chain(
                name=f"手牌药水复制链-{len(coins)}资源-{discount_label}",
                reasoning=[
                    f"目标 {target_alex_count} 龙反推为先铺核心随从，再用幻觉药水生成1费复制体。",
                    "药水链专门覆盖场上已有鲨鱼/刀油后，通过1费复制体继续制造减费窗口的结构。",
                ],
                actions=append_alex_tail(setup + spell_prefix + [
                    SymbolicAction(potion),
                    SymbolicAction(scabbs),
                    SymbolicAction(shark),
                    SymbolicAction(scabbs),
                ])
            )

            etc_dance_actions = setup + [
                SymbolicAction(etc, choices=(dance, alex)),
                SymbolicAction(mother),
                SymbolicAction(scabbs),
            ] + spell_prefix + [
                SymbolicAction(dance),
                SymbolicAction(shark),
                SymbolicAction(scabbs),
                SymbolicAction(scabbs),
            ]

            add_chain(
                name=f"牛找舞龙回手链-{len(coins)}资源-{discount_label}",
                reasoning=[
                    f"目标 {target_alex_count} 龙需要红龙来源，反推牛头人找红龙。",
                    "费用不足时反推舞动全场制造 1 费鱼/刀/晦/牛，第二轮再用鱼刀刀压低红龙和暗影施法者费用。",
                ],
                actions=append_alex_tail(etc_dance_actions)
            )

            etc_potion_actions = setup + [
                SymbolicAction(etc, choices=(potion, alex)),
            ] + spell_prefix + [
                SymbolicAction(potion),
                SymbolicAction(scabbs),
                SymbolicAction(mother),
                SymbolicAction(shark),
                SymbolicAction(scabbs),
                SymbolicAction(scabbs),
            ]

            add_chain(
                name=f"牛找药龙复制链-{len(coins)}资源-{discount_label}",
                reasoning=[
                    f"目标 {target_alex_count} 龙需要复制资源，反推牛头人找幻觉药水和红龙。",
                    "药水提供 1 费复制体，再配合鱼刀刀降低后续红龙/暗影施法者费用。",
                ],
                actions=append_alex_tail(etc_potion_actions)
            )

    # 5费暗影步回狐舞动重铺链
    # 鱼狐刀暗(刀) 牛(舞,龙) 步(狐) 狐 刀 晦 舞 鱼 刀 刀 龙 暗(龙) 龙 龙
    five_mana_setup = [
        SymbolicAction(shark),
        SymbolicAction(foxy),
        SymbolicAction(scabbs),
        SymbolicAction(shadowcaster, target=scabbs),
        SymbolicAction(etc, choices=(dance, alex)),
        SymbolicAction(shadowstep, target=foxy),
        SymbolicAction(foxy),
        SymbolicAction(scabbs),
        SymbolicAction(mother),
        SymbolicAction(dance),
        SymbolicAction(shark),
        SymbolicAction(scabbs),
        SymbolicAction(scabbs),
    ]

    def count_symbolic_alex(actions: List[SymbolicAction]) -> int:
        return sum(1 for action in actions if action.name == alex)

    def append_direct_alex_if_needed(actions: List[SymbolicAction]) -> Tuple[List[SymbolicAction], int]:
        tailed = actions[:]
        current_alex_count = count_symbolic_alex(tailed)

        if current_alex_count <= 0:
            tailed.append(SymbolicAction(alex))
            current_alex_count += 1

        return tailed, current_alex_count

    def append_shadowcaster_alex_tail(actions: List[SymbolicAction]) -> List[SymbolicAction]:
        tailed, current_alex_count = append_direct_alex_if_needed(actions)

        if current_alex_count < target_alex_count:
            tailed += [
                SymbolicAction(shadowcaster, target=alex),
                SymbolicAction(alex),
            ]
            current_alex_count += 1

        # 鲨鱼在场时暗影施法者会给两个红龙复制；后续裸红龙动作表示继续消耗这些1费复制体。
        while current_alex_count < target_alex_count:
            tailed.append(SymbolicAction(alex))
            current_alex_count += 1

        return tailed

    def append_deadly_dance_recycle_tail(actions: List[SymbolicAction]) -> List[SymbolicAction]:
        tailed, current_alex_count = append_direct_alex_if_needed(actions)

        if current_alex_count < target_alex_count:
            tailed += [
                SymbolicAction(dance),
                SymbolicAction(scabbs),
                SymbolicAction(shark),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
            ]
            current_alex_count += 1

        if current_alex_count < target_alex_count:
            tailed += [
                SymbolicAction(scabbs),
                SymbolicAction(shadowcaster, target=alex),
                SymbolicAction(alex),
            ]
            current_alex_count += 1

        # 暗影施法者在鲨鱼下复制两次，可能留下额外1费红龙复制体。
        while current_alex_count < target_alex_count:
            tailed.append(SymbolicAction(alex))
            current_alex_count += 1

        return tailed

    def append_shadowcaster_alex_then_deadly_dance_tail(actions: List[SymbolicAction]) -> List[SymbolicAction]:
        tailed, current_alex_count = append_direct_alex_if_needed(actions)

        if current_alex_count < target_alex_count:
            tailed += [
                SymbolicAction(shadowcaster, target=alex),
                SymbolicAction(alex),
            ]
            current_alex_count += 1

        if current_alex_count < target_alex_count:
            tailed += [
                SymbolicAction(scabbs),
                SymbolicAction(dance),
                SymbolicAction(scabbs),
                SymbolicAction(shark),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
            ]
            current_alex_count += 1

        while current_alex_count < target_alex_count:
            tailed.append(SymbolicAction(alex))
            current_alex_count += 1

        return tailed

    def append_shadowcaster_alex_then_deadly_dance_shadowcaster_scabbs_tail(actions: List[SymbolicAction]) -> List[SymbolicAction]:
        tailed, current_alex_count = append_direct_alex_if_needed(actions)

        if current_alex_count < target_alex_count:
            tailed += [
                SymbolicAction(shadowcaster, target=alex),
                SymbolicAction(alex),
            ]
            current_alex_count += 1

        if current_alex_count < target_alex_count:
            tailed += [
                SymbolicAction(scabbs),
                SymbolicAction(dance),
                SymbolicAction(scabbs),
                SymbolicAction(shark),
                SymbolicAction(scabbs),
                SymbolicAction(shadowcaster, target=scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
            ]
            current_alex_count += 1

        while current_alex_count < target_alex_count:
            tailed.append(SymbolicAction(alex))
            current_alex_count += 1

        return tailed

    five_mana_tail_strategies = [
        (
            "暗复制红龙尾巴",
            append_shadowcaster_alex_tail,
            [
                f"目标 {target_alex_count} 龙：鲨鱼让战吼/连击x2，狐人0费启动。",
                "刀油减费后暗影施法者复制刀油，牛找舞动+龙，暗影步回手狐人再利用。",
                "晦鳞巢母复原法力，舞动全场回手后1费重铺：鱼刀刀龙。",
                "之后用暗影施法者复制红龙；鲨鱼在场时可产生两个1费红龙复制体。",
            ],
        ),
        (
            "殒命舞动回收尾巴",
            append_deadly_dance_recycle_tail,
            [
                f"目标 {target_alex_count} 龙：先走5费暗影步回狐舞动重铺基础链，完成第一条红龙。",
                "第一条红龙后，把殒命暗影变形出的舞动全场视作可复用回收资源，而不是一次性动作。",
                "殒命舞动再次回手场面后，按刀油、鲨鱼、刀油、红龙、刀油、暗影施法者复制红龙、红龙续出。",
            ],
        ),
        (
            "暗复制红龙后殒命舞动回收尾巴",
            append_shadowcaster_alex_then_deadly_dance_tail,
            [
                f"目标 {target_alex_count} 龙：先用暗影施法者在鲨鱼下复制红龙，获得两个1费红龙资源。",
                "先打一条复制红龙后，再接刀油和殒命暗影变形出的舞动全场回收场面。",
                "回收后按刀油、鲨鱼、刀油、红龙、红龙续出；这是先复制龙、后殒命舞动的尾部结构。",
            ],
        ),
        (
            "暗复制红龙后殒命舞动暗复制刀油尾巴",
            append_shadowcaster_alex_then_deadly_dance_shadowcaster_scabbs_tail,
            [
                f"目标 {target_alex_count} 龙：先暗影施法者复制红龙并消耗一条复制龙。",
                "随后刀油接殒命舞动回收场面，重铺刀油、鲨鱼、刀油后，再用暗影施法者复制刀油。",
                "最后按刀油、红龙、红龙收尾，覆盖“刀鱼刀暗刀龙龙”的尾部结构。",
            ],
        ),
    ]

    five_mana_prefixes = [
        ("无前置资源", []),
        ("伪造硬币前置", [SymbolicAction("伪造的幸运币")]),
        ("幸运币前置", [SymbolicAction("幸运币")]),
    ]

    for prefix_name, prefix_actions in five_mana_prefixes:
        for tail_name, tail_builder, reasoning in five_mana_tail_strategies:
            add_chain(
                name=f"5费暗影步回狐舞动重铺链-{prefix_name}-{tail_name}",
                reasoning=reasoning,
                actions=tail_builder(prefix_actions + five_mana_setup)
            )

    add_chain(
        name="公式骨刺回费后杀狐续刀链",
        reasoning=[
            "骨刺公式不能放在鱼狐刀之后立即使用；正确断点是先牛找舞动和红龙，再用晦鳞巢母回费。",
            "骨刺击杀狐人后给下一张刀油减2费，接复制刀油和舞动回收，最后按鱼刀刀压低红龙与暗影施法者。",
        ],
        actions=append_shadowcaster_alex_tail([
            SymbolicAction(shark),
            SymbolicAction(foxy),
            SymbolicAction(scabbs),
            SymbolicAction(shadowcaster, target=scabbs),
            SymbolicAction(scabbs),
            SymbolicAction(etc, choices=(dance, alex)),
            SymbolicAction(mother),
            SymbolicAction(bone_spike, target=foxy),
            SymbolicAction(scabbs),
            SymbolicAction(dance),
            SymbolicAction(scabbs),
            SymbolicAction(shark),
            SymbolicAction(scabbs),
            SymbolicAction(scabbs),
        ])
    )

    add_chain(
        name="公式48骨刺牛后暗复制链",
        reasoning=[
            "48骨刺公式中费用更宽，牛找舞动和红龙可以放在暗影施法者复制刀油之前。",
            "晦鳞回费后骨刺杀狐，下一张刀油减费；舞动回手后直接鱼刀刀红龙，再复制红龙补足三龙。",
        ],
        actions=append_shadowcaster_alex_tail([
            SymbolicAction(shark),
            SymbolicAction(foxy),
            SymbolicAction(scabbs),
            SymbolicAction(etc, choices=(dance, alex)),
            SymbolicAction(shadowcaster, target=scabbs),
            SymbolicAction(scabbs),
            SymbolicAction(mother),
            SymbolicAction(bone_spike, target=foxy),
            SymbolicAction(scabbs),
            SymbolicAction(dance),
            SymbolicAction(shark),
            SymbolicAction(scabbs),
            SymbolicAction(scabbs),
        ])
    )

    add_chain(
        name="公式伺机先开舞动链",
        reasoning=[
            "伺机待发公式应先释放伺机待发，把后续舞动全场压到1费。",
            "随后走鱼狐刀、暗复制刀油、牛找舞龙、晦鳞回费、舞动回收，再由鱼刀刀完成红龙费用压低。",
        ],
        actions=append_shadowcaster_alex_tail([
            SymbolicAction(preparation),
            SymbolicAction(shark),
            SymbolicAction(foxy),
            SymbolicAction(scabbs),
            SymbolicAction(shadowcaster, target=scabbs),
            SymbolicAction(scabbs),
            SymbolicAction(etc, choices=(dance, alex)),
            SymbolicAction(mother),
            SymbolicAction(dance),
            SymbolicAction(scabbs),
            SymbolicAction(shark),
            SymbolicAction(scabbs),
            SymbolicAction(scabbs),
        ])
    )

    if target_alex_count == 3:
        add_chain(
            name="公式伺步回晦三龙链",
            reasoning=[
                "新版伺+步/骨公式的步分支：伺机待发压低舞动，普通舞动后用暗影步回手晦鳞巢母保留回费资源。",
                "随后刀油、刀油、红龙、暗影施法者复制红龙，利用复制红龙补足三龙。",
            ],
            actions=[
                SymbolicAction(preparation),
                SymbolicAction(shark),
                SymbolicAction(foxy),
                SymbolicAction(scabbs),
                SymbolicAction(shadowcaster, target=scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(etc, choices=(dance, alex)),
                SymbolicAction(mother),
                SymbolicAction(dance),
                SymbolicAction(shark),
                SymbolicAction(mother),
                SymbolicAction(shadowstep, target=mother),
                SymbolicAction(scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
                SymbolicAction(shadowcaster, target=alex),
                SymbolicAction(alex),
                SymbolicAction(alex),
            ]
        )

        add_chain(
            name="公式伺骨杀晦三龙链",
            reasoning=[
                "新版伺+步/骨公式的骨分支：伺机待发压低舞动，普通舞动后先鱼晦刀刀龙。",
                "骨刺击杀晦鳞巢母给暗影施法者减费，再复制红龙并打出复制体完成三龙。",
            ],
            actions=[
                SymbolicAction(preparation),
                SymbolicAction(shark),
                SymbolicAction(foxy),
                SymbolicAction(scabbs),
                SymbolicAction(shadowcaster, target=scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(etc, choices=(dance, alex)),
                SymbolicAction(mother),
                SymbolicAction(dance),
                SymbolicAction(shark),
                SymbolicAction(mother),
                SymbolicAction(scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
                SymbolicAction(bone_spike, target=mother),
                SymbolicAction(shadowcaster, target=alex),
                SymbolicAction(alex),
                SymbolicAction(alex),
            ]
        )

    add_chain(
        name="公式低费暗影步回刀链",
        reasoning=[
            "低费暗影步公式先牛找舞龙和晦鳞回费，再用暗影步回手刀油降低费用。",
            "回手刀油后补暗影施法者复制刀油，接舞动回收，最后用1费鱼、晦鳞和双刀油完成红龙压费。",
        ],
        actions=append_shadowcaster_alex_tail([
            SymbolicAction(shark),
            SymbolicAction(foxy),
            SymbolicAction(scabbs),
            SymbolicAction(etc, choices=(dance, alex)),
            SymbolicAction(mother),
            SymbolicAction(shadowstep, target=scabbs),
            SymbolicAction(scabbs),
            SymbolicAction(shadowcaster, target=scabbs),
            SymbolicAction(scabbs),
            SymbolicAction(dance),
            SymbolicAction(shark),
            SymbolicAction(mother),
            SymbolicAction(scabbs),
            SymbolicAction(scabbs),
        ])
    )

    if target_alex_count == 3:
        add_chain(
            name="公式殒命暗影二次舞动三龙链",
            reasoning=[
                "殒命暗影在打出普通舞动后变形成舞动全场，因此可二次回收场面。",
                "第一条红龙后打出殒命舞动，再用刀油、红龙、刀油、暗影施法者复制红龙完成三龙。",
            ],
            actions=[
                SymbolicAction(shark),
                SymbolicAction(foxy),
                SymbolicAction(scabbs),
                SymbolicAction(etc, choices=(dance, alex)),
                SymbolicAction(dance),
                SymbolicAction(shark),
                SymbolicAction(scabbs),
                SymbolicAction(mother),
                SymbolicAction(shadowcaster, target=scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
                SymbolicAction(dance),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
                SymbolicAction(scabbs),
                SymbolicAction(shadowcaster, target=alex),
                SymbolicAction(alex),
            ]
        )

        add_chain(
            name="公式殒命三杂复制刀油三龙链",
            reasoning=[
                "三杂殒命公式保留更多手牌空间，可先暗复制刀油后走普通舞动。",
                "殒命暗影变成第二张舞动后，先补第二条红龙，再由刀油和暗影施法者复制红龙完成第三条。",
            ],
            actions=[
                SymbolicAction(shark),
                SymbolicAction(foxy),
                SymbolicAction(scabbs),
                SymbolicAction(shadowcaster, target=scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(etc, choices=(dance, alex)),
                SymbolicAction(mother),
                SymbolicAction(dance),
                SymbolicAction(scabbs),
                SymbolicAction(shark),
                SymbolicAction(mother),
                SymbolicAction(scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
                SymbolicAction(dance),
                SymbolicAction(alex),
                SymbolicAction(scabbs),
                SymbolicAction(shadowcaster, target=alex),
                SymbolicAction(alex),
            ]
        )

    if target_alex_count == 4:
        add_chain(
            name="公式预启动鱼在场步刀伺骨四龙链",
            reasoning=[
                "覆盖鱼已在场的预启动局面：初始鲨鱼之灵等价于省掉第一步鱼和4费。",
                "先狐刀暗复制刀油，牛找舞动和红龙；暗影步回刀，伺机待发配合骨刺杀狐，再连续打刀油叠减费。",
                "先打第一条红龙后用刀油接舞动回收；第二轮鱼刀龙暗复制红龙，再用复制体完成四龙。",
            ],
            actions=[
                SymbolicAction(shark),
                SymbolicAction(foxy),
                SymbolicAction(scabbs),
                SymbolicAction(shadowcaster, target=scabbs),
                SymbolicAction(etc, choices=(dance, alex)),
                SymbolicAction(shadowstep, target=scabbs),
                SymbolicAction(preparation),
                SymbolicAction(bone_spike, target=foxy),
                SymbolicAction(scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
                SymbolicAction(scabbs),
                SymbolicAction(dance),
                SymbolicAction(shark),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
                SymbolicAction(shadowcaster, target=alex),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
                SymbolicAction(alex),
            ]
        )

        add_chain(
            name="公式步殒四龙二次舞动链",
            reasoning=[
                "步殒四龙先用暗影步回狐人腾费用并制造舞动窗口。",
                "普通舞动后完成第一条红龙，殒命暗影变成第二张舞动，二次回收后再用红龙、暗影施法者复制红龙和额外复制体完成四龙。",
            ],
            actions=[
                SymbolicAction(shark),
                SymbolicAction(foxy),
                SymbolicAction(scabbs),
                SymbolicAction(etc, choices=(dance, alex)),
                SymbolicAction(mother),
                SymbolicAction(shadowstep, target=foxy),
                SymbolicAction(foxy),
                SymbolicAction(dance),
                SymbolicAction(shark),
                SymbolicAction(scabbs),
                SymbolicAction(mother),
                SymbolicAction(shadowcaster, target=scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
                SymbolicAction(dance),
                SymbolicAction(scabbs),
                SymbolicAction(shark),
                SymbolicAction(mother),
                SymbolicAction(alex),
                SymbolicAction(shadowcaster, target=alex),
                SymbolicAction(alex),
                SymbolicAction(alex),
            ]
        )

        add_chain(
            name="公式硬币殒步行骗四龙链",
            reasoning=[
                "硬币殒步行骗公式的核心不是行骗抽牌，而是用暗影步回狐人先腾费用，行骗可作为杂牌留在手中不参与链条。",
                "普通舞动后打第一条红龙，殒命暗影变成第二张舞动；二次舞动后通过刀油、鲨鱼、暗影施法者复制红龙和伪造硬币补足第四条。",
            ],
            actions=[
                SymbolicAction(foxy),
                SymbolicAction(shadowstep, target=foxy),
                SymbolicAction(foxy),
                SymbolicAction(shark),
                SymbolicAction(scabbs),
                SymbolicAction(shadowcaster, target=scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(etc, choices=(dance, alex)),
                SymbolicAction(mother),
                SymbolicAction(dance),
                SymbolicAction(scabbs),
                SymbolicAction(shark),
                SymbolicAction(mother),
                SymbolicAction(scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
                SymbolicAction(dance),
                SymbolicAction(alex),
                SymbolicAction(scabbs),
                SymbolicAction(shark),
                SymbolicAction(scabbs),
                SymbolicAction(shadowcaster, target=alex),
                SymbolicAction(alex),
                SymbolicAction("伪造的幸运币"),
                SymbolicAction(alex),
            ]
        )

    if target_alex_count >= 5:
        potion_deadly_setup = [
            SymbolicAction("伪造的幸运币"),
            SymbolicAction(shark),
            SymbolicAction(foxy),
            SymbolicAction(scabbs),
            SymbolicAction(etc, choices=(dance, alex)),
            SymbolicAction(shadowcaster, target=scabbs),
            SymbolicAction(shadowstep, target=foxy),
            SymbolicAction(foxy),
            SymbolicAction(scabbs),
            SymbolicAction(mother),
            SymbolicAction(bone_spike, target=foxy),
            SymbolicAction(scabbs),
            SymbolicAction(dance),
        ]

        add_chain(
            name="公式硬币步骨殒牛药水五龙链",
            reasoning=[
                "覆盖用户指出的药水五龙：第一轮硬币鱼狐刀牛暗步狐刀晦骨刀舞。",
                "第二轮鱼刀刀龙暗复制龙，再用回手牛找药水，随后殒命舞动回收。",
                "第三轮先打两条复制龙，再用刀油和幻觉药水复制场面红龙，晦鳞回费后再打两条龙。",
            ],
            actions=[
                *potion_deadly_setup,
                SymbolicAction(shark),
                SymbolicAction(scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
                SymbolicAction(shadowcaster, target=alex),
                SymbolicAction(etc, choices=(potion,)),
                SymbolicAction(scabbs),
                SymbolicAction(dance),
                SymbolicAction(shark),
                SymbolicAction(alex),
                SymbolicAction(alex),
                SymbolicAction(scabbs),
                SymbolicAction(potion),
                SymbolicAction(mother),
                SymbolicAction(alex),
                SymbolicAction(alex),
            ]
        )

        add_chain(
            name="公式硬币步骨殒先龙后舞五龙链",
            reasoning=[
                "覆盖用户指出的另一条五龙：第一轮同样用硬币、暗影步和骨刺完成舞动回手。",
                "第二轮暗影施法者复制红龙后，先消耗一条复制红龙，再用殒命舞动回收。",
                "第三轮鱼、双红龙、刀油、晦鳞后继续出龙，构成五龙。",
            ],
            actions=[
                *potion_deadly_setup,
                SymbolicAction(shark),
                SymbolicAction(scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
                SymbolicAction(shadowcaster, target=alex),
                SymbolicAction(scabbs),
                SymbolicAction(alex),
                SymbolicAction(dance),
                SymbolicAction(shark),
                SymbolicAction(alex),
                SymbolicAction(alex),
                SymbolicAction(scabbs),
                SymbolicAction(mother),
                SymbolicAction(alex),
            ]
        )

        add_chain(
            name="公式预启动鲨鱼舞动重铺五龙链",
            reasoning=[
                "覆盖战场预启动鲨鱼之灵的8费五龙：狐人0费起手，狐刀后牛双发现舞动和红龙。",
                "第一轮红龙后暗影步回刀油、暗影施法者复制红龙、晦鳞回4费、伺机接骨刺杀狐。",
                "舞动全场按进场顺序全员回手且不爆牌，第二轮用1费红龙、刀油、鲨鱼、晦鳞回费续出三龙。",
                "本链依赖狐人老千[0费]的运行时费用；真规则下舞动回手按进场顺序，本链舞动时手牌恰好放得下全部随从。",
            ],
            actions=[
                SymbolicAction(foxy),
                SymbolicAction(scabbs),
                SymbolicAction(etc, choices=(dance, alex)),
                SymbolicAction(alex),
                SymbolicAction(shadowstep, target=scabbs),
                SymbolicAction(scabbs),
                SymbolicAction(shadowcaster, target=alex),
                SymbolicAction(mother),
                SymbolicAction(preparation),
                SymbolicAction(bone_spike, target=foxy),
                SymbolicAction(alex),
                SymbolicAction(dance),
                SymbolicAction(alex),
                SymbolicAction("伪造的幸运币"),
                SymbolicAction(scabbs),
                SymbolicAction(shark),
                SymbolicAction(mother),
                SymbolicAction(alex),
                SymbolicAction(alex),
            ]
        )

    chains.sort(key=lambda chain: (
        0 if chain.name.startswith("公式") else 1 if chain.name.startswith("基础") else 2,
        len(chain.actions),
        chain.name,
    ))

    return chains


@dataclass(frozen=True)
class SymbolicOperator:
    name: str
    priority: int
    action_names: Tuple[str, ...] = ()
    target_names: Tuple[str, ...] = ()
    choices: Tuple[str, ...] = ()

    def matches(self, action_text: str) -> bool:
        canonical = canonical_path_item(action_text)

        if self.action_names and not any(canonical.startswith(name) for name in self.action_names):
            return False

        if self.target_names and not any(f"({target})" in canonical for target in self.target_names):
            return False

        if self.choices and not all(choice in canonical for choice in self.choices):
            return False

        return True

    def score(self, before: GameState, after: GameState) -> int:
        if not after.path:
            return 0

        action_text = after.path[-1]

        if not self.matches(action_text):
            return 0

        score = self.priority
        alex_gain = after.alex_play_count - before.alex_play_count

        if alex_gain > 0:
            score += 500 * alex_gain

        score += max(0, after.mana - before.mana) * 30
        score += max(0, count_hand_cards(after, "生命的缚誓者阿莱克丝塔萨") - count_hand_cards(before, "生命的缚誓者阿莱克丝塔萨")) * 120
        score += max(0, count_hand_cards(after, "舞动全场（ft.迦罗娜）") - count_hand_cards(before, "舞动全场（ft.迦罗娜）")) * 80
        score += max(0, count_hand_cards(after, "幻觉药水") - count_hand_cards(before, "幻觉药水")) * 80
        score += max(0, len(after.hand_zone.cards) - len(before.hand_zone.cards)) * 8
        return score


def count_hand_cards(state: GameState, name: str) -> int:
    return sum(1 for card in state.hand_zone.cards if card.name == name)


def count_board_cards(state: GameState, name: str) -> int:
    return sum(1 for card in state.board_zone.cards if card.name == name)


def count_hand_dragons(state: GameState) -> int:
    return sum(1 for card in state.hand_zone.cards if "dragon" in card.tags)


def count_recycle_spells(state: GameState) -> int:
    return sum(
        1
        for card in state.hand_zone.cards
        if card.name in {"舞动全场（ft.迦罗娜）", "幻觉药水"}
    )


def total_discount_potential(state: GameState) -> int:
    active_discount = sum(
        remaining_count * discount_amount
        for remaining_count, discount_amount in state.active_card_discounts
        if remaining_count > 0 and discount_amount > 0
    )
    return (
        state.next_spell_discount
        + state.next_combo_discount
        + state.next_card_discount
        + state.next_two_cards_discount * max(0, state.next_two_cards_discount_count)
        + active_discount
    )


class ObjectSymbolicReasoner:
    def __init__(self, target_alex_count: int, max_steps: int, max_results: int, max_expansions: Optional[int] = None):
        self.target_alex_count = target_alex_count
        self.max_steps = max_steps
        self.max_results = max_results
        self.max_expansions = max_expansions
        self.operators = self.build_operators()

    def build_operators(self) -> List[SymbolicOperator]:
        alex = "生命的缚誓者阿莱克丝塔萨"
        shark = "鲨鱼之灵"
        foxy = "狐人老千"
        scabbs = "斯卡布斯·刀油"
        shadowcaster = "暗影施法者"
        etc = "乐队经理精英牛头人酋长"
        mother = "晦鳞巢母"
        dance = "舞动全场（ft.迦罗娜）"
        potion = "幻觉药水"
        shadowstep = "暗影步"
        bone_spike = "锯齿骨刺"
        preparation = "伺机待发"

        return [
            SymbolicOperator("打出红龙", 1000, (alex,)),
            SymbolicOperator("暗影施法者复制红龙", 900, (shadowcaster,), (alex,)),
            SymbolicOperator("暗影步回手红龙", 860, (shadowstep,), (alex,)),
            SymbolicOperator("药水复制场面红龙", 840, (potion,)),
            SymbolicOperator("舞动回收场面红龙", 830, (dance,)),
            SymbolicOperator("牛找红龙舞动", 780, (etc,), choices=(alex, dance)),
            SymbolicOperator("牛找红龙药水", 760, (etc,), choices=(alex, potion)),
            SymbolicOperator("暗影施法者复制刀油", 720, (shadowcaster,), (scabbs,)),
            SymbolicOperator("暗影步回手狐人", 700, (shadowstep,), (foxy,)),
            SymbolicOperator("暗影步回手刀油", 690, (shadowstep,), (scabbs,)),
            SymbolicOperator("鲨鱼战吼引擎", 660, (shark,)),
            SymbolicOperator("刀油减费窗口", 650, (scabbs,)),
            SymbolicOperator("狐人连击减费", 620, (foxy,)),
            SymbolicOperator("晦鳞巢母回费", 610, (mother,)),
            SymbolicOperator("伺机待发法术减费", 560, (preparation,)),
            SymbolicOperator("骨刺击杀减费", 540, (bone_spike,)),
            SymbolicOperator("舞动回收场面", 500, (dance,)),
            SymbolicOperator("药水复制场面", 490, (potion,)),
        ]

    def state_score(self, state: GameState) -> int:
        return (
            state.alex_play_count * 2000
            + count_hand_dragons(state) * 180
            + count_board_cards(state, "生命的缚誓者阿莱克丝塔萨") * 150
            + count_hand_cards(state, "暗影施法者") * 70
            + count_hand_cards(state, "暗影步") * 60
            + count_recycle_spells(state) * 85
            + total_discount_potential(state) * 35
            + state.mana * 20
            - state.burned_cards * 100
        )

    def transition_score(self, before: GameState, after: GameState) -> int:
        operator_score = 0

        for operator in self.operators:
            operator_score = max(operator_score, operator.score(before, after))

        return operator_score + self.state_score(after)

    def search(
        self,
        initial_state: GameState,
        prune_stats: Optional[Dict[str, int]] = None,
        should_stop: Optional[Callable[[], bool]] = None
    ) -> List[GameState]:
        queue: List[Tuple[int, int, int, GameState]] = []
        sequence = 0
        start = initial_state.clone()
        heapq.heappush(queue, (-self.state_score(start), 0, sequence, start))
        best_seen: Dict[Tuple, Tuple[int, int, int]] = {}
        results: List[GameState] = []
        expansions = 0

        while queue and len(results) < self.max_results:
            if should_stop is not None and should_stop():
                break

            if self.max_expansions is not None and expansions >= self.max_expansions:
                if prune_stats is not None:
                    prune_stats["对象化证明预算耗尽"] = prune_stats.get("对象化证明预算耗尽", 0) + 1
                break

            _, steps, _, state = heapq.heappop(queue)

            if state.alex_play_count >= self.target_alex_count:
                results.append(state)
                continue

            if steps >= self.max_steps:
                continue

            signature = state_key_for_dedup(state)
            value = dominance_value(state)

            if signature in best_seen and dominates(best_seen[signature], value):
                if prune_stats is not None:
                    prune_stats["对象化证明DP复用"] = prune_stats.get("对象化证明DP复用", 0) + 1
                continue

            best_seen[signature] = value
            expansions += 1

            successors = generate_successors(state, prune_stats=None)

            for successor in successors:
                if len(successor.path) > self.max_steps:
                    continue

                sequence += 1
                score = self.transition_score(state, successor)
                heapq.heappush(queue, (-score, steps + 1, sequence, successor))

        if prune_stats is not None:
            prune_stats["对象化证明展开状态数"] = prune_stats.get("对象化证明展开状态数", 0) + expansions
            prune_stats["对象化证明剩余队列"] = prune_stats.get("对象化证明剩余队列", 0) + len(queue)

        return sort_path_states(results)[:self.max_results]


def has_prestarted_resources(state: GameState) -> bool:
    return (
        len(state.board_zone.cards) > 0
        or len(state.secret_zone.cards) > 0
        or state.weapon is not None
        or state.next_spell_discount > 0
        or state.next_combo_discount > 0
        or state.next_card_discount > 0
        or state.next_two_cards_discount_count > 0
        or bool(state.active_card_discounts)
    )


def parse_path_item_to_action(path_item: str) -> Optional[SymbolicAction]:
    """把具体路径项解析成符号动作，供“正向发现→反推符号链→反向验证”使用。

    路径项格式：
      - 卡名 / 卡名[殒命暗影]
      - 卡名(目标名)
      - 卡名（选择1->选择2）
    """
    item = canonical_path_item(path_item).strip()

    if not item:
        return None

    known_names = set(CARD_DATABASE.keys())
    known_names.update(ETC_BAND)
    best_name: Optional[str] = None

    for name in known_names:
        if item.startswith(name) and (best_name is None or len(name) > len(best_name)):
            best_name = name

    if best_name is None:
        return None

    rest = item[len(best_name):]
    target: Optional[str] = None
    choices: Tuple[str, ...] = ()

    if rest.startswith("("):
        close_index = rest.find(")")

        if close_index != -1:
            target = rest[1:close_index]
            rest = rest[close_index + 1:]

    if rest.startswith("（") and rest.endswith("）"):
        inner = rest[1:-1]
        choices = tuple(part.strip() for part in inner.split("->"))

    return SymbolicAction(name=best_name, target=target, choices=choices)


def mine_symbolic_chain_from_path(state: GameState, chain_index: int) -> Optional[SymbolicChain]:
    """把正向搜索发现的具体路径反推成一条符号链。

    模拟人类思考的“前后关联”：先正向试探出一条可行路线，再把它抽象成
    符号链模板，最后用反向符号链验证去确认/复用这条路线。
    """
    if not state.path or state.alex_play_count <= 0:
        return None

    actions: List[SymbolicAction] = []

    for path_item in state.path:
        action = parse_path_item_to_action(path_item)

        if action is None:
            return None

        actions.append(action)

    return SymbolicChain(
        name=f"正向束搜索自动挖掘链-{chain_index}",
        target_alex_count=state.alex_play_count,
        reasoning=[
            "由正向束搜索发现的可行路径自动反推成符号链，再经反向符号链验证确认；",
            "用于覆盖手工符号链模板尚未总结的新线路（先正向试探、再反向证明）。",
        ],
        actions=actions,
    )


# =====================================================================
# 双向符号链证明：前向符号链（从初始局面展开）+ 反向符号链（从目标龙数
# 反推尾链），在前沿状态处“拼接”。拼接后的完整链条仍走反向验证确认，
# 保证结果可复现、可直接落盘。
# =====================================================================

ALEX_TAIL_NAME = "生命的缚誓者阿莱克丝塔萨"
SHARK_TAIL_NAME = "鲨鱼之灵"
SHADOWCASTER_TAIL_NAME = "暗影施法者"
ETC_TAIL_NAME = "乐队经理精英牛头人酋长"
DANCE_TAIL_NAME = "舞动全场（ft.迦罗娜）"
POTION_TAIL_NAME = "幻觉药水"
SHADOWSTEP_TAIL_NAME = "暗影步"
PREP_TAIL_NAME = "伺机待发"
BONE_TAIL_NAME = "锯齿骨刺"


def forward_symbolic_frontier(
    initial_state: GameState,
    max_depth: int = 22,
    beam_width: int = 1500,
    max_per_depth: int = 300,
    max_states: int = 6000,
    should_stop: Optional[Callable[[], bool]] = None,
) -> List[GameState]:
    """前向符号链：从初始局面按束展开真实后继，返回去重后的可达状态前沿。

    这些状态是“拼接”的前半段：只要某个反向尾链的资源要求能被该状态满足，
    就能拼成一条完整证明。状态路径即真实出牌序列，可复现。
    """
    start = initial_state.clone()
    start.record_log = False

    def potential(state: GameState) -> int:
        dragons = sum(1 for card in state.hand if "dragon" in card.tags)
        dragons += sum(1 for card in state.board if "dragon" in card.tags)
        return dragons

    seen = set()
    seen.add(state_key_for_dedup(start))
    level = [start]
    collected: List[GameState] = []

    for _ in range(1, max_depth + 1):
        if should_stop is not None and should_stop():
            break

        if not level:
            break

        next_level: List[GameState] = []
        next_seen = set()

        for state in level:
            for successor in generate_successors(state):
                key = state_key_for_dedup(successor)

                if key in seen or key in next_seen:
                    continue

                next_seen.add(key)
                next_level.append(successor)

        if not next_level:
            break

        next_level.sort(
            key=lambda state: (state.alex_play_count, state.mana, potential(state)),
            reverse=True,
        )
        level = next_level[:beam_width]
        seen |= next_seen
        # 每层都保留一部分状态，避免深层（法力低但资源齐）的拼接点被浅层挤掉
        collected.extend(level[:max_per_depth])

    collected.sort(
        key=lambda state: (state.alex_play_count, state.mana, potential(state)),
        reverse=True,
    )
    return collected[:max_states]


@dataclass
class AlexTail:
    """反向目标尾链：从“还要打出 N 条红龙”反推的动作后缀 + 起始资源要求。"""

    name: str
    actions: List[SymbolicAction]
    gain: int
    hand_req: Dict[str, int]
    board_req: Dict[str, int]
    band_req: Tuple[str, ...]
    any_friendly_minion: bool


class _TailSim:
    """尾链的抽象推演器：同时维护“当前已产生/消耗的资源”和“起始必须满足的要求”。"""

    def __init__(self) -> None:
        self.actions: List[SymbolicAction] = []
        self.hand: Dict[str, int] = {}
        self.board: Dict[str, int] = {}
        self.alex_played: int = 0
        self.hand_req: Dict[str, int] = {}
        self.board_req: Dict[str, int] = {}
        self.band_req = set()
        self.any_friendly_minion: bool = False
        self.prep_used: bool = False
        self.bone_used: bool = False

    def clone(self) -> "_TailSim":
        new = _TailSim()
        new.actions = self.actions[:]
        new.hand = dict(self.hand)
        new.board = dict(self.board)
        new.alex_played = self.alex_played
        new.hand_req = dict(self.hand_req)
        new.board_req = dict(self.board_req)
        new.band_req = set(self.band_req)
        new.any_friendly_minion = self.any_friendly_minion
        new.prep_used = self.prep_used
        new.bone_used = self.bone_used
        return new

    def hand_need(self, name: str) -> None:
        self.hand_req[name] = max(self.hand_req.get(name, 0), 1 - self.hand.get(name, 0))

    def board_need(self, name: str) -> None:
        self.board_req[name] = max(self.board_req.get(name, 0), 1 - self.board.get(name, 0))

    def play_minion(self, name: str) -> None:
        self.hand_need(name)
        self.hand[name] = self.hand.get(name, 0) - 1
        self.board[name] = self.board.get(name, 0) + 1

    def play_spell(self, name: str) -> None:
        self.hand_need(name)
        self.hand[name] = self.hand.get(name, 0) - 1

    def add_hand(self, name: str, count: int = 1) -> None:
        self.hand[name] = self.hand.get(name, 0) + count

    def remove_board(self, name: str) -> None:
        self.board[name] = self.board.get(name, 0) - 1

    def to_tail(self, index: int) -> AlexTail:
        return AlexTail(
            name=f"反向目标尾链-{index}",
            actions=self.actions,
            gain=self.alex_played,
            hand_req={name: count for name, count in self.hand_req.items() if count > 0},
            board_req={name: count for name, count in self.board_req.items() if count > 0},
            band_req=tuple(sorted(self.band_req)),
            any_friendly_minion=self.any_friendly_minion,
        )


def _tail_direct(sim: _TailSim) -> int:
    """直出红龙（手牌里有龙直接打出）。"""
    sim.actions.append(SymbolicAction(ALEX_TAIL_NAME))
    sim.play_minion(ALEX_TAIL_NAME)
    sim.alex_played += 1
    return 1


def _tail_shadowcaster(sim: _TailSim, doubled: bool) -> int:
    """暗影施法者复制场上的红龙（鲨鱼在场翻倍=两张1费红龙），再打出。"""
    sim.actions.append(SymbolicAction(SHADOWCASTER_TAIL_NAME, target=ALEX_TAIL_NAME))
    sim.board_need(ALEX_TAIL_NAME)
    sim.play_minion(SHADOWCASTER_TAIL_NAME)
    copies = 2 if doubled else 1

    if doubled:
        sim.board_need(SHARK_TAIL_NAME)

    sim.add_hand(ALEX_TAIL_NAME, copies)

    for _ in range(copies):
        sim.actions.append(SymbolicAction(ALEX_TAIL_NAME))
        sim.play_minion(ALEX_TAIL_NAME)
        sim.alex_played += 1

    return copies


def _tail_shadowstep(sim: _TailSim) -> int:
    """暗影步回手场上的红龙再打出。"""
    sim.actions.append(SymbolicAction(SHADOWSTEP_TAIL_NAME, target=ALEX_TAIL_NAME))
    sim.board_need(ALEX_TAIL_NAME)
    sim.play_spell(SHADOWSTEP_TAIL_NAME)
    sim.remove_board(ALEX_TAIL_NAME)
    sim.add_hand(ALEX_TAIL_NAME)
    sim.actions.append(SymbolicAction(ALEX_TAIL_NAME))
    sim.play_minion(ALEX_TAIL_NAME)
    sim.alex_played += 1
    return 1


def _tail_dance(sim: _TailSim) -> int:
    """舞动全场回手场上的红龙再打出。"""
    sim.actions.append(SymbolicAction(DANCE_TAIL_NAME))
    sim.board_need(ALEX_TAIL_NAME)
    sim.play_spell(DANCE_TAIL_NAME)
    sim.remove_board(ALEX_TAIL_NAME)
    sim.add_hand(ALEX_TAIL_NAME)
    sim.actions.append(SymbolicAction(ALEX_TAIL_NAME))
    sim.play_minion(ALEX_TAIL_NAME)
    sim.alex_played += 1
    return 1


def _tail_potion(sim: _TailSim) -> int:
    """幻觉药水复制场上的红龙再打出。"""
    sim.actions.append(SymbolicAction(POTION_TAIL_NAME))
    sim.board_need(ALEX_TAIL_NAME)
    sim.play_spell(POTION_TAIL_NAME)
    sim.add_hand(ALEX_TAIL_NAME)
    sim.actions.append(SymbolicAction(ALEX_TAIL_NAME))
    sim.play_minion(ALEX_TAIL_NAME)
    sim.alex_played += 1
    return 1


def _tail_etc(sim: _TailSim, potion_variant: bool) -> int:
    """牛头人酋长（鲨鱼在场双发现）发现 舞动/药水 + 红龙，再打出红龙。"""
    choice = POTION_TAIL_NAME if potion_variant else DANCE_TAIL_NAME
    choices = (choice, ALEX_TAIL_NAME)
    sim.actions.append(SymbolicAction(ETC_TAIL_NAME, choices=choices))
    sim.board_need(SHARK_TAIL_NAME)
    sim.play_minion(ETC_TAIL_NAME)
    sim.band_req.update(choices)
    sim.add_hand(choice)
    sim.add_hand(ALEX_TAIL_NAME)
    sim.actions.append(SymbolicAction(ALEX_TAIL_NAME))
    sim.play_minion(ALEX_TAIL_NAME)
    sim.alex_played += 1
    return 1


def _tail_prep(sim: _TailSim) -> int:
    if sim.prep_used:
        return -1

    sim.actions.append(SymbolicAction(PREP_TAIL_NAME))
    sim.play_spell(PREP_TAIL_NAME)
    sim.prep_used = True
    return 0


def _tail_bone(sim: _TailSim) -> int:
    if sim.bone_used:
        return -1

    sim.actions.append(SymbolicAction(BONE_TAIL_NAME))
    sim.play_spell(BONE_TAIL_NAME)
    sim.any_friendly_minion = True
    sim.bone_used = True
    return 0


def generate_alex_tails(
    max_gain: int = 6,
    max_actions: int = 12,
    max_tails_per_gain: int = 150,
    max_tails: int = 1500,
) -> List[AlexTail]:
    """反推生成目标尾链：递归组合“生产红龙”的机制，直到凑够目标龙数。"""
    tails: Dict[Tuple, AlexTail] = {}
    tail_index = [0]
    visits = [0]
    gain_counts: Dict[int, int] = {}

    def rec(sim: _TailSim, remaining: int) -> None:
        visits[0] += 1

        if visits[0] > 80000:
            return

        if remaining <= 0:
            tail = sim.to_tail(tail_index[0])
            tail_index[0] += 1
            key = (
                tuple((action.name, action.target, action.choices) for action in sim.actions),
                tuple(sorted(sim.hand_req.items())),
                tuple(sorted(sim.board_req.items())),
                tuple(sorted(sim.band_req)),
                sim.any_friendly_minion,
            )

            if key not in tails:
                if gain_counts.get(tail.gain, 0) >= max_tails_per_gain:
                    return

                tails[key] = tail
                gain_counts[tail.gain] = gain_counts.get(tail.gain, 0) + 1

            return

        if len(tails) >= max_tails:
            return

        if len(sim.actions) >= max_actions:
            return

        gain_options = [
            ("direct", lambda s: _tail_direct(s)),
            ("shadowcaster", lambda s: _tail_shadowcaster(s, False)),
            ("shadowcaster2", lambda s: _tail_shadowcaster(s, True)),
            ("shadowstep", lambda s: _tail_shadowstep(s)),
            ("dance", lambda s: _tail_dance(s)),
            ("potion", lambda s: _tail_potion(s)),
            ("etc_dance", lambda s: _tail_etc(s, False)),
            ("etc_potion", lambda s: _tail_etc(s, True)),
        ]

        for label, fn in gain_options:
            next_sim = sim.clone()
            gain = fn(next_sim)

            if gain <= 0 or gain > remaining:
                continue

            rec(next_sim, remaining - gain)

        prep_options = [
            ("prep", lambda s: _tail_prep(s)),
            ("bone", lambda s: _tail_bone(s)),
        ]

        for label, fn in prep_options:
            next_sim = sim.clone()
            gain = fn(next_sim)

            if gain < 0:
                continue

            rec(next_sim, remaining)

    for target_gain in range(1, max_gain + 1):
        rec(_TailSim(), target_gain)

    return list(tails.values())


def tail_meets_state(state: GameState, tail: AlexTail) -> bool:
    """检查前向状态是否满足反向尾链的起始资源要求。"""
    hand_counts = Counter(card.name for card in state.hand)
    board_counts = Counter(card.name for card in state.board)

    for name, count in tail.hand_req.items():
        if hand_counts.get(name, 0) < count:
            return False

    for name, count in tail.board_req.items():
        if board_counts.get(name, 0) < count:
            return False

    if tail.band_req:
        band = set(state.etc_band_remaining)

        if not band.issuperset(set(tail.band_req)):
            return False

    if tail.any_friendly_minion and not state.board_zone.cards:
        return False

    return True


def tail_mana_feasible(state: GameState, tail: AlexTail) -> bool:
    """快速检查尾链在给定状态下的费用可行性（只算费用，不做完整模拟）。"""
    mana = state.mana
    next_spell = state.next_spell_discount
    next_combo = state.next_combo_discount
    next_card = state.next_card_discount
    actives = [
        (remaining_count, discount_amount)
        for remaining_count, discount_amount in state.active_card_discounts
        if remaining_count > 0 and discount_amount > 0
    ]

    for action in tail.actions:
        if action.name not in CARD_DATABASE:
            continue

        card = make_card(action.name)
        base_cost = card.current_cost() or 0
        discount = next_card + sum(
            discount_amount for _remaining, discount_amount in actives
        )

        if is_spell_like(card):
            discount += next_spell

        if "combo" in card.tags:
            discount += next_combo

        cost = max(0, base_cost - discount)

        if mana < cost:
            return False

        mana -= cost
        next_card = 0
        actives = [
            (remaining_count - 1, discount_amount)
            for remaining_count, discount_amount in actives
            if remaining_count - 1 > 0
        ]

        if is_spell_like(card):
            next_spell = 0

        if "combo" in card.tags:
            next_combo = 0

        if action.name == PREP_TAIL_NAME:
            next_spell += 2
        elif action.name == BONE_TAIL_NAME:
            next_card += 2

    return True


def bidirectional_symbolic_prove_paths(
    initial_state: GameState,
    max_alex_count: int,
    min_alex_count: int,
    max_paths: int,
    max_chain_steps: int = 100,
    progress_callback: Optional[Callable[[int, int, int], None]] = None,
    found_callback: Optional[Callable[[List[GameState], int], None]] = None,
    prune_stats: Optional[Dict[str, int]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    forward_depth: int = 22,
    forward_beam_width: int = 1500,
    validate_candidates_per_target: int = 20,
) -> List[GameState]:
    """双向符号链证明：前向探索 + 反向尾链 + 前沿拼接 + 反向验证。"""
    search_initial_state = initial_state.clone()
    search_initial_state.record_log = False
    min_alex_count = max(1, min(min_alex_count, max_alex_count))

    if prune_stats is not None:
        prune_stats["双向拼接证明"] = "前向探索中"

    frontier = forward_symbolic_frontier(
        initial_state=search_initial_state,
        max_depth=forward_depth,
        beam_width=forward_beam_width,
        should_stop=should_stop,
    )

    if prune_stats is not None:
        prune_stats["双向前向探索状态数"] = len(frontier)
        prune_stats["双向拼接证明"] = "反向尾链生成中"

    tails = generate_alex_tails(max_gain=min(max_alex_count, 6))
    tails_by_gain: Dict[int, List[AlexTail]] = {}

    for tail in tails:
        tails_by_gain.setdefault(tail.gain, []).append(tail)

    if prune_stats is not None:
        prune_stats["双向生成尾链数"] = len(tails)
        prune_stats["双向拼接证明"] = "拼接验证中"

    all_proved: List[GameState] = []
    seen_paths = set()
    validated_count = 0

    def add_state(chain_state: GameState) -> bool:
        path_key = tuple(chain_state.path)

        if path_key in seen_paths:
            return False

        seen_paths.add(path_key)
        all_proved.append(chain_state)
        return True

    # 1) 前向直接命中：前沿状态本身已经打出 >= 下限的龙
    for state in frontier:
        if state.alex_play_count >= min_alex_count:
            if add_state(state.clone()):
                if prune_stats is not None:
                    prune_stats["已证明龙数"] = max(
                        prune_stats.get("已证明龙数", 0),
                        state.alex_play_count,
                    )
                    prune_stats["证明方式"] = "双向前向直接命中"

            if len(all_proved) >= max_paths:
                break

    # 2) 前沿拼接：找到满足尾链资源要求的前沿状态，从该状态直接验证尾链
    for target in range(max_alex_count, min_alex_count - 1, -1):
        if len(all_proved) >= max_paths:
            break

        if should_stop is not None and should_stop():
            break

        candidates: List[Tuple[GameState, AlexTail]] = []

        for state in frontier:
            if state.alex_play_count >= target:
                continue

            needed = target - state.alex_play_count

            if needed <= 0:
                continue

            for tail in tails_by_gain.get(needed, []):
                if tail_meets_state(state, tail) and tail_mana_feasible(state, tail):
                    candidates.append((state, tail))

        candidates.sort(
            key=lambda pair: (-pair[0].mana, len(pair[1].actions), len(pair[0].path))
        )

        for state, tail in candidates[:validate_candidates_per_target]:
            if should_stop is not None and should_stop():
                break

            chain = SymbolicChain(
                name=f"双向拼接-{tail.name}",
                target_alex_count=target,
                reasoning=[
                    "前向符号链（从初始局面展开的前沿状态）与反向符号链（从目标龙数反推的尾链）",
                    f"在 {state.alex_play_count} 龙状态处拼接；尾链再从该状态经反向验证确认。",
                ],
                actions=tail.actions,
            )
            chain_states = validate_symbolic_chain(
                initial_state=state,
                chain=chain,
                max_states=max(1, max_paths - len(all_proved)),
                prune_stats=prune_stats,
                should_stop=should_stop,
            )
            validated_count += 1

            for chain_state in sort_path_states(chain_states):
                if add_state(chain_state):
                    if prune_stats is not None:
                        prune_stats["已证明龙数"] = max(
                            prune_stats.get("已证明龙数", 0),
                            chain_state.alex_play_count,
                        )
                        prune_stats["证明方式"] = "双向符号链拼接证明"
                        prune_stats[f"当前搜索 {target}龙"] = "存在"

                    if found_callback:
                        found_callback(
                            sort_path_states(all_proved)[:max_paths],
                            target,
                        )

                if len(all_proved) >= max_paths:
                    break

            if len(all_proved) >= max_paths:
                break

    if prune_stats is not None:
        prune_stats["双向验证链条数"] = validated_count
        prune_stats["双向拼接证明"] = "完成"

    return sort_path_states(all_proved)[:max_paths]


def reverse_symbolic_prove_paths(
    initial_state: GameState,
    max_alex_count: int,
    max_paths: int,
    max_chain_steps: int = 100,
    min_alex_count: int = 1,
    progress_callback: Optional[Callable[[int, int, int], None]] = None,
    found_callback: Optional[Callable[[List[GameState], int], None]] = None,
    prune_stats: Optional[Dict[str, int]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    forward_mining: bool = True,
    forward_beam_width: int = 1000,
    use_bidirectional: bool = True
) -> List[GameState]:
    search_initial_state = initial_state.clone()
    search_initial_state.record_log = False
    min_alex_count = max(1, min(min_alex_count, max_alex_count))
    all_proved_states: List[GameState] = []
    seen_paths = set()
    best_alex_count = 0
    start_alex_count = max_alex_count

    for target_alex_count in range(max(1, start_alex_count), min_alex_count - 1, -1):
        if prune_stats is not None:
            prune_stats["当前证明目标"] = target_alex_count
            prune_stats[f"当前搜索 {target_alex_count}龙"] = "搜索中"

        chains = [
            chain
            for chain in build_symbolic_chains(target_alex_count)
            if len(chain.actions) <= max_chain_steps
        ]
        proved_states = []

        if prune_stats is not None:
            prune_stats["符号候选链条数"] = prune_stats.get("符号候选链条数", 0) + len(chains)

        for chain_index, chain in enumerate(chains, start=1):
            if should_stop is not None and should_stop():
                return sort_path_states(all_proved_states)[:max_paths]

            chain_states = validate_symbolic_chain(
                initial_state=search_initial_state,
                chain=chain,
                max_states=max(1, max_paths - len(all_proved_states)),
                prune_stats=prune_stats,
                should_stop=should_stop
            )

            proved_states.extend(chain_states)

            if chain_states:
                for chain_state in sort_path_states(chain_states):
                    path_key = tuple(chain_state.path)

                    if path_key in seen_paths:
                        continue

                    seen_paths.add(path_key)
                    all_proved_states.append(chain_state)
                    best_alex_count = max(best_alex_count, chain_state.alex_play_count)

                    if prune_stats is not None:
                        prune_stats["已证明龙数"] = best_alex_count
                        prune_stats[f"当前搜索 {target_alex_count}龙"] = "存在"
                        prune_stats["证明方式"] = "反向符号链条证明"

                    if found_callback:
                        found_callback(sort_path_states(all_proved_states)[:max_paths], target_alex_count)

                    if len(all_proved_states) >= max_paths:
                        break

            if progress_callback:
                pruned_count = sum(
                    count
                    for key, count in (prune_stats or {}).items()
                    if isinstance(count, int)
                    and key not in {"当前证明目标", "已证明龙数"}
                    and not str(key).startswith("当前搜索 ")
                )
                progress_callback(len(all_proved_states), len(chains) - chain_index, pruned_count)

            if len(all_proved_states) >= max_paths:
                break

        if proved_states:
            if prune_stats is not None:
                prune_stats["已证明龙数"] = best_alex_count
                prune_stats[f"当前搜索 {target_alex_count}龙"] = "存在"
                prune_stats["证明方式"] = "反向符号链条证明"
        else:
            if prune_stats is not None:
                prune_stats["全局正向搜索已禁用"] = prune_stats.get("全局正向搜索已禁用", 0) + 1
                prune_stats[f"当前搜索 {target_alex_count}龙"] = "不存在"

        if len(all_proved_states) >= max_paths:
            break

    bidirectional_proved_any = False

    if (
        use_bidirectional
        and best_alex_count < min_alex_count
        and not (should_stop is not None and should_stop())
    ):
        if prune_stats is not None:
            prune_stats["双向拼接证明"] = "运行中"

        bidir_results = bidirectional_symbolic_prove_paths(
            initial_state=search_initial_state,
            max_alex_count=max_alex_count,
            min_alex_count=min_alex_count,
            max_paths=max_paths,
            max_chain_steps=max_chain_steps,
            progress_callback=progress_callback,
            found_callback=found_callback,
            prune_stats=prune_stats,
            should_stop=should_stop,
        )

        for bidir_state in sort_path_states(bidir_results):
            path_key = tuple(bidir_state.path)

            if path_key in seen_paths:
                continue

            seen_paths.add(path_key)
            all_proved_states.append(bidir_state)
            best_alex_count = max(best_alex_count, bidir_state.alex_play_count)

            if bidir_state.alex_play_count >= min_alex_count:
                bidirectional_proved_any = True

            if prune_stats is not None:
                prune_stats["已证明龙数"] = best_alex_count
                prune_stats["证明方式"] = "双向符号链拼接证明"
                prune_stats[f"当前搜索 {bidir_state.alex_play_count}龙"] = "存在"

            if found_callback:
                found_callback(
                    sort_path_states(all_proved_states)[:max_paths],
                    bidir_state.alex_play_count,
                )

            if len(all_proved_states) >= max_paths:
                break

    if (
        forward_mining
        and best_alex_count < max_alex_count
        and not bidirectional_proved_any
        and not (should_stop is not None and should_stop())
    ):
        if prune_stats is not None:
            prune_stats["正向挖掘"] = "束搜索自动挖掘"

        # 束宽自动升级：先用默认束宽，摸不到搜索下限就加大，避免漏掉深度线路。
        mining_widths = [forward_beam_width]

        for extra_width in (2500, 5000):
            if forward_beam_width < extra_width:
                mining_widths.append(extra_width)

        if prune_stats is not None:
            prune_stats["正向挖掘尝试束宽"] = list(mining_widths)

        mined_paths: List[GameState] = []

        for mining_width in mining_widths:
            if should_stop is not None and should_stop():
                break

            mined_paths = beam_search_paths(
                initial_state=search_initial_state,
                max_depth=max_chain_steps,
                max_paths=max_paths,
                max_alex_count=max_alex_count,
                min_alex_count=min_alex_count,
                beam_width=mining_width,
                progress_callback=None,
                found_callback=None,
                prune_stats=prune_stats,
                should_stop=should_stop,
            )
            mined_max = max(
                (mined_state.alex_play_count for mined_state in mined_paths),
                default=0,
            )

            if mined_max >= min_alex_count:
                break

        mined_chains: List[SymbolicChain] = []
        seen_chain_keys = set()

        for mined_state in sorted(mined_paths, key=lambda item: -item.alex_play_count):
            if mined_state.alex_play_count <= best_alex_count:
                continue

            chain = mine_symbolic_chain_from_path(mined_state, len(mined_chains) + 1)

            if chain is None:
                continue

            chain_key = tuple(
                (action.name, action.target, action.choices)
                for action in chain.actions
            )

            if chain_key in seen_chain_keys:
                continue

            seen_chain_keys.add(chain_key)
            mined_chains.append(chain)

        if prune_stats is not None:
            prune_stats["正向挖掘链条数"] = len(mined_chains)

        for chain in mined_chains:
            if should_stop is not None and should_stop():
                break

            if chain.target_alex_count <= best_alex_count:
                continue

            chain_states = validate_symbolic_chain(
                initial_state=search_initial_state,
                chain=chain,
                max_states=max(1, max_paths - len(all_proved_states)),
                prune_stats=prune_stats,
                should_stop=should_stop,
            )

            for chain_state in sort_path_states(chain_states):
                path_key = tuple(chain_state.path)

                if path_key in seen_paths:
                    continue

                seen_paths.add(path_key)
                all_proved_states.append(chain_state)
                best_alex_count = max(best_alex_count, chain_state.alex_play_count)

                if prune_stats is not None:
                    prune_stats["已证明龙数"] = best_alex_count
                    prune_stats["证明方式"] = "正向束搜索发现+符号链自动挖掘反证"
                    prune_stats[f"当前搜索 {chain.target_alex_count}龙"] = "存在"

                if found_callback:
                    found_callback(
                        sort_path_states(all_proved_states)[:max_paths],
                        chain.target_alex_count,
                    )

                if len(all_proved_states) >= max_paths:
                    break

            if len(all_proved_states) >= max_paths:
                break

        if prune_stats is not None:
            prune_stats["正向挖掘"] = "完成"

    if prune_stats is not None:
        prune_stats["已证明龙数"] = best_alex_count
        prune_stats["已搜索到龙数下限"] = min_alex_count

    return sort_path_states(all_proved_states)[:max_paths]


def enumerate_play_paths(
    initial_state: GameState,
    max_depth: int = 100,
    max_paths: int = 500000,
    max_alex_count: int = 10,
    min_alex_count: int = 1,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    found_callback: Optional[Callable[[List[GameState], int], None]] = None,
    prune_stats: Optional[Dict[str, int]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    forward_mining: bool = True,
    forward_beam_width: int = 1000,
    use_bidirectional: bool = True
) -> List[GameState]:
    return reverse_symbolic_prove_paths(
        initial_state=initial_state,
        max_alex_count=max_alex_count,
        max_paths=max_paths,
        max_chain_steps=max_depth,
        min_alex_count=min_alex_count,
        progress_callback=progress_callback,
        found_callback=found_callback,
        prune_stats=prune_stats,
        should_stop=should_stop,
        forward_mining=forward_mining,
        forward_beam_width=forward_beam_width,
        use_bidirectional=use_bidirectional
    )


def beam_search_paths(
    initial_state: GameState,
    max_depth: int = 100,
    max_paths: int = 500000,
    max_alex_count: int = 10,
    min_alex_count: int = 1,
    beam_width: int = 4000,
    progress_callback: Optional[Callable[[int, int, int], None]] = None,
    found_callback: Optional[Callable[[List[GameState], int], None]] = None,
    prune_stats: Optional[Dict[str, int]] = None,
    should_stop: Optional[Callable[[], bool]] = None
) -> List[GameState]:
    """正向束搜索：直接枚举真实后继状态，按（红龙数、剩余法力、龙资源）排序保留束宽。

    与反向符号链证明互补，用于验证符号链模板未覆盖的线路。搜索依据与链条验证完全一致
    （generate_successors），舞动回手同样按真炉石规则结算。
    """
    start = initial_state.clone()
    start.record_log = False
    min_alex_count = max(1, min(min_alex_count, max_alex_count))

    def potential(state: GameState) -> int:
        dragons = sum(1 for card in state.hand if "dragon" in card.tags)
        dragons += sum(1 for card in state.board if "dragon" in card.tags)
        return dragons

    best: Dict[int, GameState] = {start.alex_play_count: start.clone()}
    level = [start]
    seen = set()
    seen.add((state_key_for_dedup(start), start.alex_play_count))
    expansions = 0
    max_reached = start.alex_play_count
    reached_depth = 0

    for depth in range(1, max_depth + 1):
        if should_stop is not None and should_stop():
            break

        if not level:
            break

        reached_depth = depth
        next_level: List[GameState] = []
        next_seen = set()

        for state in level:
            for successor in generate_successors(state, prune_stats=prune_stats):
                expansions += 1
                key = (state_key_for_dedup(successor), successor.alex_play_count)

                if key in next_seen or key in seen:
                    continue

                next_seen.add(key)
                next_level.append(successor)

                current = best.get(successor.alex_play_count)

                if current is None or (successor.mana, potential(successor)) > (
                    current.mana,
                    potential(current),
                ):
                    best[successor.alex_play_count] = successor.clone()

        if not next_level:
            break

        seen |= next_seen
        next_level.sort(
            key=lambda state: (state.alex_play_count, state.mana, potential(state)),
            reverse=True,
        )
        level = next_level[:beam_width]

        if prune_stats is not None:
            prune_stats["束搜索深度"] = depth
            prune_stats["束搜索展开状态数"] = expansions

        new_max = max(state.alex_play_count for state in level)

        if new_max > max_reached:
            max_reached = new_max

            if found_callback is not None and max_reached >= min_alex_count:
                found_paths = sort_path_states([
                    item
                    for count, item in best.items()
                    if count == max_reached
                ])[:max_paths]
                found_callback(found_paths, max_reached)

        if progress_callback is not None:
            progress_callback(len(next_seen), len(level), expansions)

    if prune_stats is not None:
        prune_stats["束搜索深度"] = reached_depth
        prune_stats["束搜索展开状态数"] = expansions
        prune_stats["束搜索束宽"] = beam_width
        prune_stats["束搜索最高龙数"] = max(best.keys()) if best else 0

    return sort_path_states([
        item
        for count, item in best.items()
        if min_alex_count <= count <= max_alex_count
    ])[:max_paths]


def sort_path_states(states: List[GameState]) -> List[GameState]:
    return sorted(
        states,
        key=lambda item: (
            -item.alex_play_count,
            -len(item.path),
            -item.mana,
            item.initial_mana_crystals,
            item.initial_mana
        )
    )


def compress_path_steps(path: List[str], max_chunk_size: int = 10) -> List[str]:
    compressed: List[str] = []
    index = 0

    while index < len(path):
        best_chunk_size = 0
        best_repeat_count = 1
        max_size = min(max_chunk_size, (len(path) - index) // 2)

        for chunk_size in range(1, max_size + 1):
            chunk = path[index:index + chunk_size]
            repeat_count = 1
            cursor = index + chunk_size

            while path[cursor:cursor + chunk_size] == chunk:
                repeat_count += 1
                cursor += chunk_size

            if repeat_count > 1 and chunk_size * repeat_count > best_chunk_size * best_repeat_count:
                best_chunk_size = chunk_size
                best_repeat_count = repeat_count

        if best_repeat_count > 1:
            chunk_text = " -> ".join(path[index:index + best_chunk_size])
            compressed.append(f"（{chunk_text}）×{best_repeat_count}")
            index += best_chunk_size * best_repeat_count
        else:
            compressed.append(path[index])
            index += 1

    return compressed


def format_path_text(path: List[str]) -> str:
    if not path:
        return "无可用出牌"

    return " -> ".join(compress_path_steps(path))


def format_paths(states: List[GameState], limit: int = 200, non_alex_limit: int = 10) -> str:
    unique_states: List[GameState] = []
    seen_paths = set()

    for state in states:
        key = tuple(state.path)

        if key in seen_paths:
            continue

        seen_paths.add(key)
        unique_states.append(state)

    alex_states = sort_path_states([
        state for state in unique_states
        if state.alex_play_count > 0
    ])
    non_alex_states = sort_path_states([
        state for state in unique_states
        if state.alex_play_count <= 0
    ])
    shown_alex_states = alex_states[:limit]
    remain_limit = max(0, limit - len(shown_alex_states))
    shown_non_alex_states = non_alex_states[:min(non_alex_limit, remain_limit)]

    lines = [
        f"共计算出 {len(unique_states)} 条不重复终局路径",
        f"其中可打出红龙路径 {len(alex_states)} 条，未打出红龙路径 {len(non_alex_states)} 条",
        f"显示红龙路径前 {len(shown_alex_states)} 条；未打出红龙路径最多仅保留 {len(shown_non_alex_states)} 条：",
        ""
    ]

    index = 1

    if shown_alex_states:
        lines.append("可打出红龙路径：")

    for state in shown_alex_states:
        path = format_path_text(state.path)
        alex_text = (
            f" | 红龙次数：{state.alex_play_count} | 伤害：{state.alex_damage}点"
            if state.alex_play_count > 0
            else ""
        )
        lines.append(
            f"{index}. {path} | 需求：{state.initial_mana_crystals}水晶 / {state.initial_mana}法力 | 剩余法力：{state.mana}{alex_text}"
        )
        index += 1

    if shown_non_alex_states:
        if shown_alex_states:
            lines.append("")

        lines.append("未打出红龙备用路径：")

    for state in shown_non_alex_states:
        path = format_path_text(state.path)
        lines.append(
            f"{index}. {path} | 需求：{state.initial_mana_crystals}水晶 / {state.initial_mana}法力 | 剩余法力：{state.mana}"
        )
        index += 1

    return "\n".join(lines)


def state_from_rebuild_result(
    result,
    mana_crystals: int = 10,
    mana: Optional[int] = None,
    deadly_shadow_hand_indexes: Optional[List[int]] = None,
    etc_band_remaining: Optional[List[str]] = None
) -> GameState:
    deck_cards: List[CardInstance] = []
    hand_cards: List[CardInstance] = []
    board_cards: List[CardInstance] = []
    secret_cards: List[CardInstance] = []
    weapon_card: Optional[CardInstance] = None
    deck_entries = list(getattr(result, "deck_cards", []) or [])

    for card_entry in deck_entries:
        count = max(1, int(getattr(card_entry, "count", 1)))

        for _ in range(count):
            card = make_card_from_rebuild_entry(card_entry)

            if card is not None:
                deck_cards.append(card)

    for card_entry in getattr(result, "cards", []):
        count = max(1, int(getattr(card_entry, "count", 1)))

        for _ in range(count):
            card = make_card_from_rebuild_entry(card_entry)

            if card is not None:
                hand_cards.append(card)

    for card_entry in getattr(result, "battlefield_cards", []):
        count = max(1, int(getattr(card_entry, "count", 1)))

        for _ in range(count):
            card = make_card_from_rebuild_entry(card_entry)

            if card is None:
                continue

            if card.card_type == "minion" and len(board_cards) < MAX_BOARD_SIZE:
                board_cards.append(card)
            elif card.card_type == "secret" and len(secret_cards) < MAX_SECRET_SIZE:
                secret_cards.append(card)
            elif card.card_type == "weapon":
                weapon_card = card

    merged_shadow_indexes = list(deadly_shadow_hand_indexes or [])

    for result_index in getattr(result, "deadly_shadow_hand_indexes", []) or []:
        if result_index not in merged_shadow_indexes:
            merged_shadow_indexes.append(result_index)

    for hand_index in merged_shadow_indexes:
        zero_based_index = hand_index - 1

        if zero_based_index < 0 or zero_based_index >= len(hand_cards):
            continue

        if not is_spell_like(hand_cards[zero_based_index]):
            continue

        mark_as_deadly_shadow(hand_cards[zero_based_index])

    mana_crystals = max(0, min(mana_crystals, MAX_MANA_CRYSTALS))

    if mana is None:
        mana = mana_crystals

    state = GameState(
        deck=deck_cards,
        deck_is_known=bool(deck_entries),
        hand=hand_cards,
        board=board_cards,
        secrets=secret_cards,
        weapon=weapon_card,
        mana_crystals=mana_crystals,
        mana=mana,
        initial_mana_crystals=mana_crystals,
        initial_mana=mana,
        etc_band_remaining=list(etc_band_remaining) if etc_band_remaining is not None else ETC_BAND[:]
    )

    apply_current_effects_from_rebuild_result(state, result)
    return state


def apply_current_effects_from_rebuild_result(state: GameState, result) -> None:
    effect_entries = list(getattr(result, "current_effect_cards", []) or [])

    if not effect_entries:
        return

    state.cards_played_this_turn = max(state.cards_played_this_turn, 1)

    for effect_entry in effect_entries:
        name = getattr(effect_entry, "name", "")
        layers = max(1, int(getattr(effect_entry, "count", 1) or 1))

        if name == "狐人老千":
            amount = 2 * layers
            state.next_combo_discount = max(state.next_combo_discount, amount)
            state.add_log(f"初始当前效果：狐人老千×{layers}，下一张连击牌减{amount}费")
        elif name == "伺机待发":
            amount = 2 * layers
            state.next_spell_discount = max(state.next_spell_discount, amount)
            state.add_log(f"初始当前效果：伺机待发×{layers}，下一张法术减{amount}费")
        elif name == "斯卡布斯·刀油":
            for _ in range(layers):
                state.active_card_discounts.append((2, 2))

            state.add_log(f"初始当前效果：斯卡布斯·刀油×{layers}，接下来两张牌各减{2 * layers}费")
        elif name == "锯齿骨刺":
            amount = 2 * layers
            state.next_card_discount = max(state.next_card_discount, amount)
            state.add_log(f"初始当前效果：锯齿骨刺×{layers}，下一张牌减{amount}费")


def main() -> int:
    parser = argparse.ArgumentParser(description="红龙计算器基础模型")
    parser.add_argument("--hand", default="", help="逗号分隔的手牌名称")
    parser.add_argument("--deck", default="", help="逗号分隔的牌库名称")
    parser.add_argument("--mana-crystals", type=int, default=10)
    parser.add_argument("--mana", type=int)
    parser.add_argument("--play", action="append", default=[], help="按名称依次使用卡牌，可重复传入")
    parser.add_argument("--search", action="store_true", help="执行符号化链条证明")
    parser.add_argument("--beam", action="store_true", help="使用正向束搜索（默认关闭，使用反向符号链证明）")
    parser.add_argument("--beam-width", type=int, default=4000, help="束搜索束宽，默认4000")
    parser.add_argument("--max-depth", type=int, default=100, help="符号链条最大步数")
    parser.add_argument("--max-paths", type=int, default=500000)
    parser.add_argument("--max-alex-count", type=int, default=10)
    parser.add_argument("--min-alex-count", type=int, default=1)
    parser.add_argument("--no-forward-mine", action="store_true", help="关闭正向束搜索自动挖掘（默认开启）")
    parser.add_argument("--forward-mine-width", type=int, default=1000, help="自动挖掘束搜索束宽，默认1000")
    parser.add_argument("--no-bidirectional", action="store_true", help="关闭双向符号链拼接证明（默认开启）")
    parser.add_argument("--show-limit", type=int, default=200)
    parser.add_argument("--json", action="store_true", help="输出 JSON")

    args = parser.parse_args()
    parsed_deck_names = parse_names(args.deck)
    state = create_state(
        deck_names=parsed_deck_names if args.deck.strip() else None,
        hand_names=parse_names(args.hand),
        mana_crystals=args.mana_crystals,
        mana=args.mana
    )

    for card_name in args.play:
        play_card_by_name(state, card_name)

    if args.search:
        if args.beam:
            states = beam_search_paths(
                initial_state=state,
                max_depth=args.max_depth,
                max_paths=args.max_paths,
                max_alex_count=args.max_alex_count,
                min_alex_count=args.min_alex_count,
                beam_width=args.beam_width
            )
        else:
            states = enumerate_play_paths(
                initial_state=state,
                max_depth=args.max_depth,
                max_paths=args.max_paths,
                max_alex_count=args.max_alex_count,
                min_alex_count=args.min_alex_count,
                forward_mining=not args.no_forward_mine,
                forward_beam_width=args.forward_mine_width,
                use_bidirectional=not args.no_bidirectional
            )

        if args.json:
            print(json.dumps([state_summary(item) for item in states], ensure_ascii=False, indent=2))
        else:
            print(format_paths(states, limit=args.show_limit))

        return 0

    summary = state_summary(state)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        for key, value in summary.items():
            print(f"{key}: {value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
