#include "search.h"

#include "cards.h"
#include "engine.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <iostream>
#include <map>
#include <set>
#include <string>
#include <tuple>
#include <unordered_map>
#include <utility>

namespace rdc {
namespace {

// ---------- 手牌 / 战场计数辅助 ----------

int count_hand_cards(const GameState& s, const std::string& name) {
    int n = 0;
    for (const auto& c : s.hand) {
        if (c.name == name) ++n;
    }
    return n;
}

int count_board_cards(const GameState& s, const std::string& name) {
    int n = 0;
    for (const auto& c : s.board) {
        if (c.name == name) ++n;
    }
    return n;
}

int count_hand_dragons(const GameState& s) {
    int n = 0;
    for (const auto& c : s.hand) {
        if (has_tag(c, "dragon")) ++n;
    }
    return n;
}

// ---------- 路径 / 评分 ----------

std::string join_items(const std::vector<std::string>& items,
                       const std::string& sep) {
    std::string out;
    for (size_t i = 0; i < items.size(); ++i) {
        if (i > 0) out += sep;
        out += items[i];
    }
    return out;
}

int preferred_combo_bonus(const std::vector<std::string>& path,
                          const std::string& next_name) {
    std::vector<std::string> names = path;
    names.push_back(next_name);
    int bonus = 0;
    for (const auto& pattern : preferred_combo_patterns()) {
        int prefix_length =
            std::min((int)pattern.size(), (int)names.size());
        bool prefix_ok = true;
        for (int i = 0; i < prefix_length; ++i) {
            if (names[names.size() - prefix_length + i] != pattern[i]) {
                prefix_ok = false;
                break;
            }
        }
        if (prefix_ok) {
            bonus += prefix_length * 10;
        }
        if ((int)names.size() >= (int)pattern.size()) {
            bool full_ok = true;
            for (int i = 0; i < (int)pattern.size(); ++i) {
                if (names[names.size() - pattern.size() + i] != pattern[i]) {
                    full_ok = false;
                    break;
                }
            }
            if (full_ok) {
                bonus += 100;
            }
        }
    }
    return bonus;
}

int heuristic_score(const GameState& s, const Card& card, int cost,
                    std::optional<int> target_friendly,
                    bool target_enemy_is_killed) {
    int score = 0;
    int current_cost = card.current_cost().value_or(0);
    int saved_cost = std::max(0, current_cost - cost);

    if (cost == 0 && is_mana_gain_or_discount_effect(card.effect_id)) {
        score += 120;
    }
    if (card.effect_id == "coin" || card.effect_id == "fake_coin") {
        int early_bonus = std::max(0, 8 - s.cards_played_this_turn) * 10;
        int hand_pressure = std::max(0, (int)s.hand.size() - 6) * 50;
        score += early_bonus + hand_pressure;
    }
    if (saved_cost > 0) {
        score += saved_cost * 25;
    }
    if (active_oil_discount(s) && (current_cost == 3 || current_cost == 4)) {
        score += 80;
    }
    if (is_core_card(card.name) || card.is_deadly_shadow) {
        score += 60;
    }

    std::string display = display_card_name(card);
    size_t paren = display.find('(');
    std::string base = paren == std::string::npos ? display : display.substr(0, paren);
    score += preferred_combo_bonus(s.path, base);

    if (card.effect_id == "shadowcaster" && target_friendly.has_value() &&
        *target_friendly < (int)s.board.size() &&
        is_shadowcaster_allowed_target(s.board[*target_friendly].name)) {
        score += 40;
    }
    if (card.effect_id == "serrated_bone_spike" && target_enemy_is_killed) {
        score += 30;
    }
    return score;
}

// ---------- 搜索分支辅助（与 Python 搜索路径一致） ----------

void append_choice_to_last_path(GameState& s, const std::string& choice) {
    if (s.path.empty()) {
        return;
    }
    std::string& last = s.path.back();
    size_t open = last.find("（");
    bool ends_with_close =
        last.size() >= 3 &&
        last.compare(last.size() - 3, 3, "）") == 0;
    if (open != std::string::npos && ends_with_close) {
        // “）”为 3 字节 UTF-8 字符，整体移除后追加新选择。
        last = last.substr(0, last.size() - 3) + "->" + choice + "）";
    } else {
        last += "（" + choice + "）";
    }
}

Card unknown_card(const std::string& label) {
    Card c;
    c.name = "未知" + label;
    c.cost = -1;
    c.original_name = c.name;
    c.card_type = "unknown";
    c.effect_id = "unknown";
    return c;
}

void add_unknown_cards_to_hand(GameState& s, int count, const std::string& label) {
    for (int i = 0; i < count; ++i) {
        hand_add(s, unknown_card(label));
    }
}

std::vector<int> minion_draw_choices(const GameState& s, int limit) {
    std::vector<int> indexes;
    for (size_t i = 0; i < s.deck.size(); ++i) {
        if (s.deck[i].card_type == "minion") {
            indexes.push_back((int)i);
            if ((int)indexes.size() >= limit) {
                break;
            }
        }
    }
    return indexes;
}

GameState draw_specific_minion_branch(const GameState& s, int deck_index,
                                      const std::string& label) {
    GameState ns = s.clone();
    Card card = ns.deck[deck_index];
    ns.deck.erase(ns.deck.begin() + deck_index);
    hand_add(ns, card);
    ns.add_log(label + "抽到随从：" + card.name);
    return ns;
}

std::vector<GameState> draw_minion_branches(const GameState& s, int count,
                                            const std::string& label, int limit) {
    std::vector<GameState> states;
    states.push_back(s.clone());
    for (int draw_index = 0; draw_index < count; ++draw_index) {
        std::vector<GameState> next;
        for (const auto& current : states) {
            std::vector<int> choices = minion_draw_choices(current, limit);
            if (choices.empty()) {
                GameState ns = current.clone();
                ns.add_log(label + "没有可抽随从");
                next.push_back(std::move(ns));
                continue;
            }
            for (int idx : choices) {
                next.push_back(draw_specific_minion_branch(current, idx, label));
            }
        }
        states = std::move(next);
    }
    return states;
}

GameState draw_top_cards_branch(const GameState& s, int count,
                                const std::string& label) {
    GameState ns = s.clone();
    for (int i = 0; i < count; ++i) {
        auto drawn = draw_top_card(ns);
        if (drawn.has_value()) {
            ns.add_log(label + "抽到：" + drawn->name);
        }
    }
    return ns;
}

GameState draw_first_by_type_branch(const GameState& s, const std::string& type,
                                    const std::string& label) {
    GameState ns = s.clone();
    auto drawn = draw_first_by_type(ns, type);
    if (drawn.has_value()) {
        ns.add_log(label + "抽到：" + drawn->name);
    }
    return ns;
}

std::vector<GameState> discover_fixed_choices(const GameState& s,
                                              const std::vector<std::string>& names,
                                              const std::string& label) {
    std::vector<GameState> states;
    if (names.empty()) {
        GameState ns = s.clone();
        ns.add_log(label + "：没有可选牌");
        states.push_back(std::move(ns));
        return states;
    }
    for (const auto& name : names) {
        GameState ns = s.clone();
        hand_add(ns, make_card(name));
        for (size_t i = 0; i < ns.etc_band_remaining.size(); ++i) {
            if (ns.etc_band_remaining[i] == name) {
                ns.etc_band_remaining.erase(ns.etc_band_remaining.begin() + i);
                break;
            }
        }
        append_choice_to_last_path(ns, name);
        ns.add_log(label + "选择：" + name);
        states.push_back(std::move(ns));
    }
    return states;
}

std::vector<GameState> discover_deck_count_only(const GameState& s,
                                                const std::string& label) {
    GameState ns = s.clone();
    add_unknown_cards_to_hand(ns, 1, "发现牌");
    ns.add_log(label + "：发现简化为加入1张未知牌");
    return {std::move(ns)};
}

std::vector<GameState> breakdance_search_branches(const GameState& s) {
    GameState ns = s.clone();
    int free_slots = std::max(0, MAX_HAND_SIZE - (int)ns.hand.size());
    std::vector<Card> returning = ns.board;  // 按进场顺序
    ns.board.clear();

    if ((int)returning.size() <= free_slots) {
        for (auto& minion : returning) {
            minion.temp_cost = 1;
            hand_add(ns, minion);
        }
        return {std::move(ns)};
    }

    // 手牌满：先进场的优先占位，后进场的被消灭。
    for (int i = 0; i < free_slots; ++i) {
        returning[i].temp_cost = 1;
        hand_add(ns, returning[i]);
    }
    for (int i = free_slots; i < (int)returning.size(); ++i) {
        ns.burned_cards += 1;
        ns.add_log("爆牌：" + returning[i].name);
    }
    return {std::move(ns)};
}

std::vector<GameState> apply_search_effect(GameState state, const Card& card,
                                           std::optional<int> target_friendly,
                                           bool target_enemy_is_killed,
                                           bool enemy_target) {
    int multiplier = 1;
    if (has_tag(card, "battlecry") || has_tag(card, "combo")) {
        multiplier = minion_trigger_multiplier(state, card);
    }

    std::vector<GameState> states;
    states.push_back(std::move(state));

    for (int m = 0; m < multiplier; ++m) {
        std::vector<GameState> next;
        for (auto& current : states) {
            const std::string& e = card.effect_id;

            if (e == "coin" || e == "fake_coin") {
                GameState ns = current.clone();
                gain_temporary_mana(ns, 1);
                next.push_back(std::move(ns));
            } else if (e == "preparation") {
                GameState ns = current.clone();
                ns.next_spell_discount += 2;
                next.push_back(std::move(ns));
            } else if (e == "foxy_fraud") {
                GameState ns = current.clone();
                ns.next_combo_discount += 2;
                next.push_back(std::move(ns));
            } else if (e == "scabbs_cutterbutter") {
                GameState ns = current.clone();
                if (combo_active(ns)) {
                    ns.active_card_discounts.emplace_back(2, 2);
                }
                next.push_back(std::move(ns));
            } else if (e == "shadowstep") {
                GameState ns = current.clone();
                if (target_friendly.has_value() &&
                    *target_friendly < (int)ns.board.size()) {
                    Card target = ns.board[*target_friendly];
                    ns.board.erase(ns.board.begin() + *target_friendly);
                    if (target.locked_one_cost) {
                        target.temp_cost = 1;
                    } else {
                        int base = target.current_cost().value_or(0);
                        target.temp_cost = std::max(0, base - 2);
                    }
                    hand_add(ns, target);
                }
                next.push_back(std::move(ns));
            } else if (e == "shadowcaster") {
                GameState ns = current.clone();
                if (target_friendly.has_value() &&
                    *target_friendly < (int)ns.board.size()) {
                    hand_add(ns, make_one_one_copy(ns.board[*target_friendly]));
                }
                next.push_back(std::move(ns));
            } else if (e == "tenwu") {
                GameState ns = current.clone();
                if (target_friendly.has_value() &&
                    *target_friendly < (int)ns.board.size()) {
                    Card target = ns.board[*target_friendly];
                    ns.board.erase(ns.board.begin() + *target_friendly);
                    target.temp_cost = 1;
                    target.locked_one_cost = true;
                    hand_add(ns, target);
                }
                next.push_back(std::move(ns));
            } else if (e == "breakdance") {
                auto branches = breakdance_search_branches(current);
                next.insert(next.end(), branches.begin(), branches.end());
            } else if (e == "potion_of_illusion") {
                GameState ns = current.clone();
                std::vector<Card> copies;
                for (const auto& minion : ns.board) {
                    copies.push_back(make_one_one_copy(minion));
                }
                for (const auto& c : copies) {
                    hand_add(ns, c);
                }
                next.push_back(std::move(ns));
            } else if (e == "candlebreath_mother") {
                GameState ns = current.clone();
                bool has_dragon = false;
                for (const auto& c : ns.hand) {
                    if (has_tag(c, "dragon")) {
                        has_dragon = true;
                        break;
                    }
                }
                if (has_dragon) {
                    restore_mana(ns, 2);
                }
                next.push_back(std::move(ns));
            } else if (e == "serrated_bone_spike") {
                GameState ns = current.clone();
                if (target_enemy_is_killed) {
                    if (!target_friendly.has_value() ||
                        *target_friendly >= (int)ns.board.size()) {
                        continue;  // 无效目标分支：丢弃
                    }
                    Card target = ns.board[*target_friendly];
                    if (!target.health.has_value() || *target.health > 3) {
                        continue;
                    }
                    ns.board.erase(ns.board.begin() + *target_friendly);
                    ns.add_log("锯齿骨刺击杀：" + target.name);
                    ns.next_card_discount += 2;
                }
                next.push_back(std::move(ns));
            } else if (e == "dig_for_treasure") {
                auto branches = draw_minion_branches(current, 1, "挖掘宝藏", 6);
                next.insert(next.end(), branches.begin(), branches.end());
            } else if (e == "shroud_of_concealment") {
                auto branches = draw_minion_branches(current, 2, "潜伏帷幕", 6);
                next.insert(next.end(), branches.begin(), branches.end());
            } else if (e == "swindle") {
                GameState ns = draw_first_by_type_branch(current, "spell", "行骗");
                if (combo_active(ns)) {
                    auto branches = draw_minion_branches(ns, 1, "行骗连击", 6);
                    next.insert(next.end(), branches.begin(), branches.end());
                } else {
                    next.push_back(std::move(ns));
                }
            } else if (e == "dubious_purchase") {
                next.push_back(draw_top_cards_branch(current, 3, "可疑交易"));
            } else if (e == "gone_fishin") {
                GameState ns = current.clone();
                if (combo_active(ns)) {
                    add_unknown_cards_to_hand(ns, 1, "抽牌");
                }
                next.push_back(std::move(ns));
            } else if (e == "cultist_map") {
                auto branches = discover_deck_count_only(current, "异教地图");
                next.insert(next.end(), branches.begin(), branches.end());
            } else if (e == "elite_tauren_champion") {
                auto branches =
                    discover_fixed_choices(current, current.etc_band_remaining,
                                           "牛头人酋长");
                next.insert(next.end(), branches.begin(), branches.end());
            } else if (e == "alexstrasza") {
                GameState ns = current.clone();
                ns.add_log(enemy_target ? "红龙目标：敌方" : "红龙目标：友方");
                next.push_back(std::move(ns));
            } else {
                next.push_back(current.clone());
            }
        }
        states = std::move(next);
    }

    for (auto& st : states) {
        if (is_spell_like(card)) {
            st.last_spell_original_name = card.original_name;
            transform_deadly_shadows(st, card);
        }
        st.cards_played_this_turn += 1;
    }
    return states;
}

std::optional<std::pair<GameState, Card>> play_card_base_for_search(
    const GameState& s, int hand_index, std::optional<int> target_friendly,
    bool enemy_target) {
    if (hand_index < 0 || hand_index >= (int)s.hand.size()) {
        return std::nullopt;
    }
    GameState ns = s.clone();
    Card card = ns.hand[hand_index];
    std::string card_display_name = display_card_name(card);
    std::optional<int> cost = effective_cost(ns, card);
    if (!cost.has_value()) {
        return std::nullopt;
    }
    if (ns.mana < *cost) {
        return std::nullopt;
    }
    if (card.card_type == "minion" && (int)ns.board.size() >= MAX_BOARD_SIZE) {
        return std::nullopt;
    }
    if (card.card_type == "secret" && (int)ns.secrets.size() >= MAX_SECRET_SIZE) {
        return std::nullopt;
    }

    ns.mana -= *cost;
    ns.hand.erase(ns.hand.begin() + hand_index);
    consume_discounts(ns, card);

    std::string target_text;
    if (target_friendly.has_value()) {
        if (*target_friendly >= 0 && *target_friendly < (int)ns.board.size()) {
            target_text = "(" + ns.board[*target_friendly].name + ")";
        } else {
            target_text = "(无效目标)";
        }
    }
    ns.path.push_back(card_display_name + target_text);
    ns.add_log("使用：" + card_display_name + "，费用：" + std::to_string(*cost) +
               "，剩余法力：" + std::to_string(ns.mana));

    if (card.name == "生命的缚誓者阿莱克丝塔萨") {
        ns.alex_play_count += 1;
        if (enemy_target) {
            ns.alex_damage += alex_damage_amount(ns);
        }
    }

    if (card.card_type == "minion") {
        ns.board.push_back(card);
    } else if (card.card_type == "secret") {
        ns.secrets.push_back(card);
    } else if (card.card_type == "weapon") {
        ns.weapon = card;
    }

    return std::make_pair(std::move(ns), std::move(card));
}

std::vector<std::optional<int>> target_friendly_options(const GameState& s,
                                                        const Card& card) {
    if (card.effect_id == "shadowstep" || card.effect_id == "shadowcaster" ||
        card.effect_id == "serrated_bone_spike" || card.effect_id == "tenwu") {
        std::vector<std::optional<int>> options;
        for (int i = 0; i < (int)s.board.size(); ++i) {
            options.push_back(i);
        }
        return options;
    }
    return {std::nullopt};
}

std::vector<bool> target_enemy_kill_options(const Card& card) {
    if (card.effect_id == "serrated_bone_spike") {
        return {false, true};
    }
    return {false};
}

std::vector<bool> target_enemy_options(const Card& card) {
    if (card.effect_id == "alexstrasza") {
        return {true, false};  // 先打脸分支
    }
    return {true};
}

}  // namespace

// ---------- 公共接口 ----------

std::vector<int> playable_card_indexes(const GameState& s) {
    std::vector<int> indexes;
    for (size_t i = 0; i < s.hand.size(); ++i) {
        const Card& card = s.hand[i];
        if (card.name.rfind("未知", 0) == 0) {
            continue;
        }
        std::optional<int> cost = effective_cost(s, card);
        if (!cost.has_value() || s.mana < *cost) {
            continue;
        }
        if (card.card_type == "minion" && (int)s.board.size() >= MAX_BOARD_SIZE) {
            continue;
        }
        if (card.card_type == "secret" &&
            (int)s.secrets.size() >= MAX_SECRET_SIZE) {
            continue;
        }
        indexes.push_back((int)i);
    }
    return indexes;
}

std::vector<GameState> generate_successors(const GameState& s) {
    struct Item {
        int score;
        int seq;
        GameState state;
    };
    std::vector<Item> items;
    int sequence = 0;

    for (int hand_index : playable_card_indexes(s)) {
        const Card& card = s.hand[hand_index];
        std::optional<int> cost = effective_cost(s, card);
        if (!cost.has_value()) {
            continue;
        }
        if (cards_drawn_if_played(s, card) > 0) {
            continue;  // 抽牌法术在搜索中禁用
        }
        for (auto target_friendly : target_friendly_options(s, card)) {
            for (bool target_enemy_is_killed : target_enemy_kill_options(card)) {
                for (bool enemy_target : target_enemy_options(card)) {
                    auto base = play_card_base_for_search(
                        s, hand_index, target_friendly, enemy_target);
                    if (!base.has_value()) {
                        continue;
                    }
                    int score = heuristic_score(s, card, *cost, target_friendly,
                                                target_enemy_is_killed);
                    auto successors = apply_search_effect(
                        std::move(base->first), base->second, target_friendly,
                        target_enemy_is_killed, enemy_target);
                    for (auto& successor : successors) {
                        items.push_back(
                            Item{score, sequence++, std::move(successor)});
                    }
                }
            }
        }
    }

    std::sort(items.begin(), items.end(),
              [](const Item& a, const Item& b) {
                  if (a.score != b.score) return a.score < b.score;
                  return a.seq < b.seq;
              });
    std::vector<GameState> out;
    out.reserve(items.size());
    for (auto& item : items) {
        out.push_back(std::move(item.state));
    }
    return out;
}

namespace {

std::string padded_int(int value) {
    if (value < 0) {
        return "--";
    }
    char buf[16];
    std::snprintf(buf, sizeof(buf), "%02d", value);
    return buf;
}

std::string card_key(const Card& c) {
    std::string k = c.name;
    k += '|';
    k += c.original_name;
    k += '|';
    k += c.card_type;
    k += '|';
    k += c.effect_id;
    k += '|';
    k += padded_int(c.current_cost().value_or(-1));
    k += '|';
    k += c.is_deadly_shadow ? '1' : '0';
    k += '|';
    k += c.locked_one_cost ? '1' : '0';
    k += '|';
    k += padded_int(c.health.value_or(-1));
    return k;
}

std::string join_sorted_keys(const std::vector<Card>& cards) {
    std::vector<std::string> keys;
    for (const auto& c : cards) {
        keys.push_back(card_key(c));
    }
    std::sort(keys.begin(), keys.end());
    return join_items(keys, ",");
}

std::string join_ordered_keys(const std::vector<Card>& cards) {
    std::vector<std::string> keys;
    for (const auto& c : cards) {
        keys.push_back(card_key(c));
    }
    return join_items(keys, ",");
}

}  // namespace

std::string state_key_for_dedup(const GameState& s) {
    std::string k;
    k += s.deck_is_known ? '1' : '0';
    k += '|';
    k += join_sorted_keys(s.hand);
    k += '|';
    k += join_ordered_keys(s.deck);
    k += '|';
    k += join_ordered_keys(s.board);
    k += '|';
    k += join_ordered_keys(s.secrets);
    k += '|';
    k += s.weapon.has_value() ? card_key(*s.weapon) : "N";
    k += '|';
    k += join_items(s.etc_band_remaining, ",");
    k += '|';
    k += std::to_string(s.cards_played_this_turn);
    k += '|';
    k += std::to_string(s.next_spell_discount);
    k += '|';
    k += std::to_string(s.next_combo_discount);
    k += '|';
    k += std::to_string(s.next_card_discount);
    k += '|';
    k += std::to_string(s.next_two_cards_discount);
    k += '|';
    k += std::to_string(s.next_two_cards_discount_count);
    k += '|';
    for (size_t i = 0; i < s.active_card_discounts.size(); ++i) {
        if (i > 0) k += ';';
        k += std::to_string(s.active_card_discounts[i].first) + "," +
             std::to_string(s.active_card_discounts[i].second);
    }
    return k;
}

void sort_path_states(std::vector<GameState>& states) {
    std::sort(states.begin(), states.end(), [](const GameState& a,
                                               const GameState& b) {
        auto ka = std::make_tuple(-a.alex_damage, -a.alex_play_count,
                                  -(int)a.path.size(), -a.mana,
                                  a.initial_mana_crystals, a.initial_mana);
        auto kb = std::make_tuple(-b.alex_damage, -b.alex_play_count,
                                  -(int)b.path.size(), -b.mana,
                                  b.initial_mana_crystals, b.initial_mana);
        return ka < kb;
    });
}

namespace {

// ---------- 束搜索评分（与 Python 一致） ----------

int subchain_score(const GameState& state) {
    int hand_dragons = count_hand_dragons(state);
    int board_dragons = count_board_cards(state, "生命的缚誓者阿莱克丝塔萨");
    int dragons = hand_dragons + board_dragons;
    bool shark_on = state.has_shark();
    int shark_in_hand = count_hand_cards(state, "鲨鱼之灵");
    int mother_any = count_hand_cards(state, "晦鳞巢母") +
                     count_board_cards(state, "晦鳞巢母");
    int shadowcaster_any = count_hand_cards(state, "暗影施法者") +
                           count_board_cards(state, "暗影施法者");
    int shadowstep_any = count_hand_cards(state, "暗影步");
    int dance_any = count_hand_cards(state, "舞动全场（ft.迦罗娜）");
    int potion_any = count_hand_cards(state, "幻觉药水");
    bool deadly_ready = false;
    for (const auto& c : state.hand) {
        if (c.is_deadly_shadow) {
            deadly_ready = true;
            break;
        }
    }
    int scabbs_in_hand = count_hand_cards(state, "斯卡布斯·刀油");
    int oil_value = 0;
    for (const auto& item : state.active_card_discounts) {
        if (item.first > 0 && item.second > 0) {
            oil_value += item.second * item.first;
        }
    }
    int score = oil_value * 2;
    if (dragons > 0 && (shark_on || shark_in_hand > 0)) {
        score += dragons * 16;
    }
    if (dragons > 0 && mother_any > 0) score += 12;
    if (dragons > 0 && shadowcaster_any > 0) score += 12;
    if (dragons > 0 && (dance_any > 0 || potion_any > 0)) score += 12;
    if (board_dragons > 0 && shadowstep_any > 0) score += 12;
    if (deadly_ready && (dance_any > 0 || potion_any > 0)) score += 15;
    if ((shark_on || shark_in_hand > 0) && scabbs_in_hand >= 2) score += 8;
    score += count_hand_cards(state, "乐队经理精英牛头人酋长") * 8;
    return score;
}

int subchain_coverage(const GameState& state) {
    int hand_dragons = count_hand_dragons(state);
    int board_dragons = count_board_cards(state, "生命的缚誓者阿莱克丝塔萨");
    int dragons = hand_dragons + board_dragons;
    bool shark_on = state.has_shark();
    int shark_in_hand = count_hand_cards(state, "鲨鱼之灵");
    int mother_any = count_hand_cards(state, "晦鳞巢母") +
                     count_board_cards(state, "晦鳞巢母");
    int shadowcaster_any = count_hand_cards(state, "暗影施法者") +
                           count_board_cards(state, "暗影施法者");
    int shadowstep_any = count_hand_cards(state, "暗影步");
    int dance_any = count_hand_cards(state, "舞动全场（ft.迦罗娜）");
    int potion_any = count_hand_cards(state, "幻觉药水");
    bool deadly_ready = false;
    for (const auto& c : state.hand) {
        if (c.is_deadly_shadow) {
            deadly_ready = true;
            break;
        }
    }
    int scabbs_in_hand = count_hand_cards(state, "斯卡布斯·刀油");
    int coverage = 0;
    if (dragons > 0 && (shark_on || shark_in_hand > 0)) ++coverage;
    if (dragons > 0 && mother_any > 0) ++coverage;
    if (dragons > 0 && shadowcaster_any > 0) ++coverage;
    if (dragons > 0 && (dance_any > 0 || potion_any > 0)) ++coverage;
    if (board_dragons > 0 && shadowstep_any > 0) ++coverage;
    if (deadly_ready && (dance_any > 0 || potion_any > 0)) ++coverage;
    if ((shark_on || shark_in_hand > 0) && scabbs_in_hand >= 2) ++coverage;
    return coverage;
}

bool contains_subsequence(const std::vector<std::string>& path,
                          const std::vector<std::string>& required) {
    size_t cursor = 0;
    for (const auto& needle : required) {
        bool found = false;
        while (cursor < path.size()) {
            if (path[cursor].find(needle) != std::string::npos) {
                found = true;
                ++cursor;
                break;
            }
            ++cursor;
        }
        if (!found) {
            return false;
        }
    }
    return true;
}

int discrete_path_score(const GameState& state) {
    const auto& path = state.path;
    int score = 0;
    if (contains_subsequence(path, {"鲨鱼之灵", "生命的缚誓者"})) score += 16;
    if (contains_subsequence(path, {"生命的缚誓者", "晦鳞巢母",
                                    "生命的缚誓者"})) score += 20;
    if (contains_subsequence(path, {"生命的缚誓者", "暗影施法者",
                                    "生命的缚誓者"})) score += 16;
    if (contains_subsequence(path, {"生命的缚誓者", "舞动全场",
                                    "生命的缚誓者"})) score += 16;
    if (contains_subsequence(path, {"暗影步", "生命的缚誓者"})) score += 14;
    if (contains_subsequence(path, {"舞动全场", "舞动全场"})) score += 18;
    if (contains_subsequence(path, {"鲨鱼之灵", "晦鳞巢母",
                                    "生命的缚誓者"})) score += 14;
    return score;
}

int total_score(const GameState& s) {
    return s.alex_damage + subchain_score(s) + discrete_path_score(s);
}

std::tuple<int, int, int> champion_key(const GameState& s) {
    return std::make_tuple(subchain_coverage(s), total_score(s), s.mana);
}

std::string dedup_key_with_alex(const GameState& s) {
    return state_key_for_dedup(s) + "\x1f" + std::to_string(s.alex_play_count);
}

// ---------- 正向搜索核心（beam 与穷举共用） ----------

struct BestEntry {
    GameState state;
    int score = 0;
};

struct ScoredState {
    std::string key;
    GameState state;
    int score = 0;
};

std::vector<GameState> forward_search_paths(const GameState& initial,
                                            int max_depth, int max_paths,
                                            int max_alex_count,
                                            int min_alex_count, int beam_width,
                                            double time_budget_seconds,
                                            const std::atomic<bool>* stop_flag) {
    GameState start = initial.clone();
    start.record_log = false;
    min_alex_count = std::max(1, std::min(min_alex_count, max_alex_count));

    std::unordered_map<int, BestEntry> best;
    best[start.alex_play_count] =
        BestEntry{start.clone(), total_score(start)};

    std::vector<GameState> level;
    level.push_back(start);

    std::unordered_map<std::string, GameState> seen;
    seen[dedup_key_with_alex(start)] = start;

    auto deadline = time_budget_seconds > 0
                        ? std::chrono::steady_clock::now() +
                              std::chrono::duration<double>(time_budget_seconds)
                        : std::chrono::steady_clock::time_point::max();
    bool budget_hit = false;

    for (int depth = 1; depth <= max_depth; ++depth) {
        if (level.empty()) {
            break;
        }
        if (stop_flag && stop_flag->load()) {
            break;
        }
        if (std::chrono::steady_clock::now() >= deadline) {
            budget_hit = true;
            break;
        }
        std::unordered_map<std::string, GameState> next_seen;
        std::vector<ScoredState> scored;

        for (const auto& state : level) {
            if (stop_flag && stop_flag->load()) {
                break;
            }
            if (std::chrono::steady_clock::now() >= deadline) {
                budget_hit = true;
                break;
            }
            for (auto& successor : generate_successors(state)) {
                int sc = total_score(successor);
                std::string key = dedup_key_with_alex(successor);
                auto prev = seen.find(key);
                if (prev != seen.end() && successor.mana <= prev->second.mana) {
                    continue;
                }
                auto cur = next_seen.find(key);
                if (cur != next_seen.end() &&
                    successor.mana <= cur->second.mana) {
                    continue;
                }
                next_seen.emplace(key, successor);
                scored.push_back(ScoredState{key, successor, sc});

                auto cur_best = best.find(scored.back().state.alex_play_count);
                if (cur_best == best.end() || sc > cur_best->second.score ||
                    (sc == cur_best->second.score &&
                     scored.back().state.mana > cur_best->second.state.mana)) {
                    best[scored.back().state.alex_play_count] =
                        BestEntry{scored.back().state.clone(), sc};
                }
            }
        }

        if (budget_hit) {
            break;
        }

        if (next_seen.empty()) {
            break;
        }

        for (const auto& item : next_seen) {
            auto prev = seen.find(item.first);
            if (prev == seen.end() || item.second.mana > prev->second.mana) {
                seen[item.first] = item.second;
            }
        }

        // 按当前龙数分桶，避免高龙数分支挤掉低龙数高潜力分支。
        std::unordered_map<int, std::vector<ScoredState>> buckets;
        for (auto& item : scored) {
            buckets[item.state.alex_play_count].push_back(std::move(item));
        }
        std::vector<int> bucket_keys;
        for (const auto& item : buckets) {
            bucket_keys.push_back(item.first);
        }
        std::sort(bucket_keys.begin(), bucket_keys.end(),
                  std::greater<int>());
        int per_bucket = std::max(
            1, (int)(beam_width * 1.25 / std::max(1, (int)bucket_keys.size())));
        std::vector<GameState> new_level;

        for (int bucket_key : bucket_keys) {
            auto& vec = buckets[bucket_key];
            std::sort(vec.begin(), vec.end(),
                      [](const ScoredState& a, const ScoredState& b) {
                if (a.score != b.score) return a.score > b.score;
                return a.state.mana > b.state.mana;
            });
            int take = std::min(per_bucket, (int)vec.size());
            std::vector<ScoredState> selected(vec.begin(),
                                              vec.begin() + take);
            if (!vec.empty()) {
                const ScoredState* champion = &vec[0];
                for (const auto& cand : vec) {
                    if (champion_key(cand.state) >
                        champion_key(champion->state)) {
                        champion = &cand;
                    }
                }
                bool present = false;
                for (const auto& sel : selected) {
                    if (sel.key == champion->key) {
                        present = true;
                        break;
                    }
                }
                if (!present) {
                    selected.push_back(*champion);
                }
            }
            for (auto& sel : selected) {
                new_level.push_back(std::move(sel.state));
            }
        }

        if ((int)new_level.size() > beam_width) {
            new_level.resize(beam_width);
        }
        level = std::move(new_level);
    }

    std::vector<GameState> out;
    for (const auto& item : best) {
        if (item.first >= min_alex_count && item.first <= max_alex_count) {
            out.push_back(item.second.state);
        }
    }
    sort_path_states(out);
    if ((int)out.size() > max_paths) {
        out.resize(max_paths);
    }
    if (budget_hit) {
        std::cerr << "提示：搜索达到时间预算，返回当前最优结果"
                     "（可用 --time-budget 加大预算）\n";
    }
    return out;
}

}  // namespace

std::vector<GameState> beam_search_paths(const GameState& initial, int max_depth,
                                         int max_paths, int max_alex_count,
                                         int min_alex_count, int beam_width,
                                         double time_budget_seconds,
                                         const std::atomic<bool>* stop_flag) {
    return forward_search_paths(initial, max_depth, max_paths, max_alex_count,
                                min_alex_count, beam_width,
                                time_budget_seconds, stop_flag);
}

}  // namespace rdc
