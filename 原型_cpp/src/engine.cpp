#include "engine.h"

#include "cards.h"

#include <algorithm>
#include <stdexcept>

namespace rdc {
namespace {

void effect_etc(GameState& s, int discover_choice_index) {
    if (s.etc_band_remaining.empty()) {
        s.add_log("牛头人酋长：乐队中已没有可选牌");
        return;
    }
    discover_choice_index = std::max(
        0, std::min(discover_choice_index, (int)s.etc_band_remaining.size() - 1));
    std::string chosen_name = s.etc_band_remaining[discover_choice_index];
    s.etc_band_remaining.erase(s.etc_band_remaining.begin() + discover_choice_index);
    hand_add(s, make_card(chosen_name));
    s.add_log("牛头人酋长选择：" + chosen_name);
}

std::optional<Card> discover_from_deck(GameState& s, int choice_index) {
    if (s.deck.empty()) {
        s.add_log("牌库为空，无法发现");
        return std::nullopt;
    }
    int choice_count = std::min(3, (int)s.deck.size());
    choice_index = std::max(0, std::min(choice_index, choice_count - 1));
    Card chosen = s.deck[choice_index];
    s.deck.erase(s.deck.begin() + choice_index);
    hand_add(s, chosen);
    s.add_log("发现：" + chosen.name);
    return chosen;
}

// 单局出牌路径使用的效果结算（搜索路径另有分支版，见 search.cpp）。
void apply_effect_single(GameState& s, Card& card, std::optional<int> target_friendly,
                         bool target_enemy_is_killed, int discover_choice_index,
                         bool enemy_target) {
    const std::string& e = card.effect_id;

    if (e == "shadowstep") {
        if (!target_friendly.has_value() ||
            *target_friendly >= (int)s.board.size()) {
            s.add_log("暗影步缺少有效友方随从目标");
            return;
        }
        Card target = s.board[*target_friendly];
        s.board.erase(s.board.begin() + *target_friendly);
        if (target.locked_one_cost) {
            target.temp_cost = 1;
        } else {
            int base = target.current_cost().value_or(0);
            target.temp_cost = std::max(0, base - 2);
        }
        hand_add(s, target);
    } else if (e == "fake_coin" || e == "coin") {
        gain_temporary_mana(s, 1);
    } else if (e == "preparation") {
        s.next_spell_discount += 2;
        s.add_log("下一张法术减2费");
    } else if (e == "foxy_fraud") {
        s.next_combo_discount += 2;
        s.add_log("下一张连击牌减2费");
    } else if (e == "scabbs_cutterbutter") {
        if (combo_active(s)) {
            s.active_card_discounts.emplace_back(2, 2);
            s.add_log("本回合下两张牌减2费");
        }
    } else if (e == "swindle") {
        draw_first_by_type(s, "spell");
        if (combo_active(s)) {
            draw_first_by_type(s, "minion");
        }
    } else if (e == "dig_for_treasure") {
        auto drawn = draw_first_by_type(s, "minion");
        if (drawn.has_value() && has_tag(*drawn, "pirate")) {
            hand_add(s, make_card("幸运币"));
        }
    } else if (e == "gone_fishin") {
        if (combo_active(s)) {
            draw_top_card(s);
        }
        s.add_log("探底效果已简化为记录，不自动重排牌库");
    } else if (e == "serrated_bone_spike") {
        if (target_enemy_is_killed) {
            if (target_friendly.has_value() &&
                *target_friendly < (int)s.board.size()) {
                Card killed = s.board[*target_friendly];
                s.board.erase(s.board.begin() + *target_friendly);
                s.add_log("锯齿骨刺击杀目标：" + killed.name);
            }
            s.next_card_discount += 2;
            s.add_log("锯齿骨刺击杀目标，下一张牌减2费");
        }
    } else if (e == "quick_pick") {
        draw_top_card(s);
    } else if (e == "cultist_map") {
        discover_from_deck(s, discover_choice_index);
    } else if (e == "shroud_of_concealment") {
        draw_first_by_type(s, "minion");
        draw_first_by_type(s, "minion");
    } else if (e == "candlebreath_mother") {
        bool has_dragon = false;
        for (const auto& c : s.hand) {
            if (has_tag(c, "dragon")) {
                has_dragon = true;
                break;
            }
        }
        if (has_dragon) {
            restore_mana(s, 2);
        } else {
            s.add_log("手牌中没有龙牌，晦鳞巢母不复原水晶");
        }
    } else if (e == "elite_tauren_champion") {
        effect_etc(s, discover_choice_index);
    } else if (e == "potion_of_illusion") {
        std::vector<Card> copies;
        for (const auto& minion : s.board) {
            copies.push_back(make_one_one_copy(minion));
        }
        for (const auto& c : copies) {
            hand_add(s, c);
        }
    } else if (e == "breakdance") {
        std::vector<Card> returning = s.board;  // 按进场顺序
        s.board.clear();
        for (auto& minion : returning) {
            minion.temp_cost = 1;
            hand_add(s, minion);
        }
    } else if (e == "alexstrasza") {
        s.add_log(enemy_target ? "红龙目标：敌方" : "红龙目标：友方");
    } else if (e == "dubious_purchase") {
        for (int i = 0; i < 3; ++i) {
            draw_top_card(s);
        }
        if (combo_active(s)) {
            s.add_log("可疑交易连击：随机消灭一个敌方随从");
        }
    } else if (e == "shadowcaster") {
        if (!target_friendly.has_value() ||
            *target_friendly >= (int)s.board.size()) {
            s.add_log("暗影施法者缺少有效友方随从目标");
            return;
        }
        hand_add(s, make_one_one_copy(s.board[*target_friendly]));
    } else if (e == "tenwu") {
        if (!target_friendly.has_value() ||
            *target_friendly >= (int)s.board.size()) {
            s.add_log("赤烟·腾武缺少有效友方随从目标");
            return;
        }
        Card target = s.board[*target_friendly];
        s.board.erase(s.board.begin() + *target_friendly);
        target.temp_cost = 1;
        target.locked_one_cost = true;
        hand_add(s, target);
        s.add_log("赤烟·腾武回手：" + target.name + "（本回合固定1费）");
    }
    // evasion 等无结算效果
}

void apply_card_effect(GameState& s, Card& card, std::optional<int> target_friendly,
                       bool target_enemy_is_killed, int discover_choice_index,
                       bool enemy_target) {
    int times = 1;
    if (has_tag(card, "battlecry") || has_tag(card, "combo")) {
        times = minion_trigger_multiplier(s, card);
    }
    for (int i = 0; i < times; ++i) {
        apply_effect_single(s, card, target_friendly, target_enemy_is_killed,
                            discover_choice_index, enemy_target);
    }
}

}  // namespace

bool is_spell_like(const Card& c) {
    return c.card_type == "spell" || c.card_type == "secret";
}

bool has_tag(const Card& c, const std::string& tag) {
    for (const auto& t : c.tags) {
        if (t == tag) {
            return true;
        }
    }
    return false;
}

std::optional<int> effective_cost(const GameState& s, const Card& c) {
    std::optional<int> base = c.current_cost();
    if (!base.has_value()) {
        return std::nullopt;
    }
    int discount = 0;
    if (s.next_card_discount > 0) {
        discount += s.next_card_discount;
    }
    if (s.next_two_cards_discount_count > 0) {
        discount += s.next_two_cards_discount;
    }
    for (const auto& item : s.active_card_discounts) {
        if (item.first > 0 && item.second > 0) {
            discount += item.second;
        }
    }
    if (is_spell_like(c) && s.next_spell_discount > 0) {
        discount += s.next_spell_discount;
    }
    if (has_tag(c, "combo") && s.next_combo_discount > 0) {
        discount += s.next_combo_discount;
    }
    if (c.locked_one_cost) {
        return 1;
    }
    return std::max(0, *base - discount);
}

bool combo_active(const GameState& s) {
    return s.cards_played_this_turn > 0;
}

int minion_trigger_multiplier(const GameState& s, const Card& c) {
    if (c.card_type == "minion" && s.has_shark()) {
        return 2;
    }
    return 1;
}

int alex_damage_amount(const GameState& s) {
    return 8 * (s.has_shark() ? 2 : 1);
}

bool active_oil_discount(const GameState& s) {
    for (const auto& item : s.active_card_discounts) {
        if (item.first > 0 && item.second > 0) {
            return true;
        }
    }
    return false;
}

void consume_discounts(GameState& s, const Card& c) {
    s.next_card_discount = 0;
    if (s.next_two_cards_discount_count > 0) {
        s.next_two_cards_discount_count -= 1;
        if (s.next_two_cards_discount_count <= 0) {
            s.next_two_cards_discount = 0;
        }
    }
    if (!s.active_card_discounts.empty()) {
        std::vector<std::pair<int, int>> remaining;
        for (const auto& item : s.active_card_discounts) {
            int next_count = item.first - 1;
            if (next_count > 0) {
                remaining.emplace_back(next_count, item.second);
            }
        }
        s.active_card_discounts = std::move(remaining);
    }
    if (is_spell_like(c)) {
        s.next_spell_discount = 0;
    }
    if (has_tag(c, "combo")) {
        s.next_combo_discount = 0;
    }
}

void transform_deadly_shadows(GameState& s, const Card& spell) {
    if (spell.original_name == "殒命暗影") {
        return;
    }
    for (auto& card : s.hand) {
        if (!card.is_deadly_shadow) {
            continue;
        }
        auto copied = make_deadly_shadow_copy_from_spell(spell);
        if (!copied.has_value()) {
            continue;
        }
        card = *copied;
        s.add_log("殒命暗影变形为：" + card.name);
    }
}

void gain_temporary_mana(GameState& s, int amount) {
    int old = s.mana;
    s.mana += amount;
    s.add_log("获得临时法力：" + std::to_string(old) + " -> " + std::to_string(s.mana));
}

void restore_mana(GameState& s, int amount) {
    int old = s.mana;
    s.mana = std::min(s.mana + amount, s.mana_crystals);
    s.add_log("复原法力：" + std::to_string(old) + " -> " + std::to_string(s.mana));
}

bool hand_add(GameState& s, const Card& c) {
    if ((int)s.hand.size() >= MAX_HAND_SIZE) {
        s.burned_cards += 1;
        s.add_log("爆牌：" + c.name);
        return false;
    }
    s.hand.push_back(c);
    s.add_log("加入手牌：" + c.name);
    return true;
}

std::optional<Card> draw_top_card(GameState& s) {
    if (s.deck.empty()) {
        s.add_log("牌库为空，无法抽牌");
        return std::nullopt;
    }
    Card card = s.deck.front();
    s.deck.erase(s.deck.begin());
    hand_add(s, card);
    return card;
}

std::optional<Card> draw_first_by_type(GameState& s, const std::string& type) {
    for (size_t i = 0; i < s.deck.size(); ++i) {
        if (s.deck[i].card_type != type) {
            continue;
        }
        Card found = s.deck[i];
        s.deck.erase(s.deck.begin() + i);
        hand_add(s, found);
        return found;
    }
    s.add_log("牌库中没有可抽取的" + type);
    return std::nullopt;
}

Card make_one_one_copy(const Card& c) {
    Card copied = c;
    copied.temp_cost = 1;
    copied.health = 1;
    return copied;
}

std::optional<Card> make_deadly_shadow_copy_from_spell(const Card& spell) {
    if (!card_exists(spell.original_name)) {
        return std::nullopt;
    }
    Card copied = make_card(spell.original_name);
    copied.is_deadly_shadow = true;
    return copied;
}

std::string display_card_name(const Card& c) {
    if (c.is_deadly_shadow) {
        return c.name + "[殒命暗影]";
    }
    return c.name;
}

std::string card_cost_health_label(const Card& c) {
    std::string text = display_card_name(c);
    std::optional<int> cost = c.current_cost();
    text += cost.has_value() ? ("[" + std::to_string(*cost) + "费") : "[*费";
    if (c.card_type == "minion" && c.health.has_value()) {
        text += "," + std::to_string(*c.health) + "血";
    }
    text += "]";
    return text;
}

int cards_drawn_if_played(const GameState& s, const Card& c) {
    const std::string& e = c.effect_id;
    if (!is_draw_card_effect(e)) {
        return 0;
    }
    if (e == "gone_fishin" && !combo_active(s)) {
        return 0;
    }
    if (!s.deck_is_known) {
        if (e == "dubious_purchase") return 3;
        if (e == "dig_for_treasure") return 1;
        if (e == "shroud_of_concealment") return 2;
        if (e == "swindle") return combo_active(s) ? 2 : 1;
        if (e == "gone_fishin") return 1;
        if (e == "quick_pick") return 1;
        return 1;
    }
    int deck_minions = 0, deck_spells = 0;
    for (const auto& c2 : s.deck) {
        if (c2.card_type == "minion") ++deck_minions;
        if (is_spell_like(c2)) ++deck_spells;
    }
    int deck_total = (int)s.deck.size();
    if (e == "dubious_purchase") return std::min(3, deck_total);
    if (e == "gone_fishin") return std::min(1, deck_total);
    if (e == "dig_for_treasure") return std::min(1, deck_minions);
    if (e == "swindle") {
        int n = std::min(1, deck_spells);
        if (combo_active(s)) {
            n += std::min(1, deck_minions);
        }
        return n;
    }
    if (e == "shroud_of_concealment") return std::min(2, deck_minions);
    if (e == "quick_pick") return std::min(1, deck_total);
    return 0;
}

bool play_card(GameState& s, int hand_index, std::optional<int> target_friendly,
               bool target_enemy_is_killed, int discover_choice_index,
               bool enemy_target) {
    if (hand_index < 0 || hand_index >= (int)s.hand.size()) {
        s.add_log("无效的手牌序号");
        return false;
    }
    Card card = s.hand[hand_index];
    std::string card_display_name = display_card_name(card);
    std::optional<int> cost = effective_cost(s, card);
    if (!cost.has_value()) {
        s.add_log("无法使用：" + card_display_name + " 当前没有费用");
        return false;
    }
    if (s.mana < *cost) {
        s.add_log("法力不足，无法使用：" + card_display_name + "，需要" +
                  std::to_string(*cost) + "，当前" + std::to_string(s.mana));
        return false;
    }
    if (card.card_type == "minion" && (int)s.board.size() >= MAX_BOARD_SIZE) {
        s.add_log("随从栏已满，无法使用：" + card_display_name);
        return false;
    }
    if (card.card_type == "secret" && (int)s.secrets.size() >= MAX_SECRET_SIZE) {
        s.add_log("奥秘栏已满，无法使用：" + card_display_name);
        return false;
    }

    s.mana -= *cost;
    s.hand.erase(s.hand.begin() + hand_index);
    consume_discounts(s, card);
    s.add_log("使用：" + card_display_name + "，费用：" + std::to_string(*cost) +
              "，剩余法力：" + std::to_string(s.mana));

    if (card.card_type == "minion") {
        s.board.push_back(card);
    } else if (card.card_type == "secret") {
        s.secrets.push_back(card);
    } else if (card.card_type == "weapon") {
        s.weapon = card;
    }

    if (card.name == "生命的缚誓者阿莱克丝塔萨") {
        s.alex_play_count += 1;
        if (enemy_target) {
            s.alex_damage += alex_damage_amount(s);
        }
    }

    apply_card_effect(s, card, target_friendly, target_enemy_is_killed,
                      discover_choice_index, enemy_target);

    if (is_spell_like(card)) {
        s.last_spell_original_name = card.original_name;
        transform_deadly_shadows(s, card);
    }
    s.cards_played_this_turn += 1;
    return true;
}

bool play_card_by_name(GameState& s, const std::string& name,
                       std::optional<int> target_friendly,
                       bool target_enemy_is_killed, int discover_choice_index,
                       bool enemy_target) {
    for (size_t i = 0; i < s.hand.size(); ++i) {
        if (s.hand[i].name == name) {
            return play_card(s, (int)i, target_friendly, target_enemy_is_killed,
                             discover_choice_index, enemy_target);
        }
    }
    s.add_log("手牌中没有：" + name);
    return false;
}

GameState create_state(const std::vector<std::string>& deck_names, bool deck_known,
                       const std::vector<std::string>& hand_names,
                       int mana_crystals, std::optional<int> mana) {
    mana_crystals = std::max(0, std::min(mana_crystals, MAX_MANA_CRYSTALS));
    int mana_value = mana.value_or(mana_crystals);

    GameState s;
    s.deck_is_known = deck_known;
    for (const auto& name : deck_names) {
        s.deck.push_back(make_card(name));
    }
    for (const auto& name : hand_names) {
        s.hand.push_back(make_card(name));
    }
    s.mana_crystals = mana_crystals;
    s.mana = mana_value;
    s.initial_mana_crystals = mana_crystals;
    s.initial_mana = mana_value;
    s.etc_band_remaining = etc_band();
    return s;
}

}  // namespace rdc
