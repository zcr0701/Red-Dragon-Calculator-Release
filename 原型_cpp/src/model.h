#pragma once

#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace rdc {

// 与 Python red_dragon_calculator.py 的 CardDef / CardInstance / GameState 对应。
// cost / health 用 -1 表示“无”（None）。

constexpr int MAX_HAND_SIZE = 10;
constexpr int MAX_BOARD_SIZE = 7;
constexpr int MAX_SECRET_SIZE = 5;
constexpr int MAX_MANA_CRYSTALS = 10;

struct CardDef {
    std::string name;
    int cost = -1;
    std::string card_type;  // minion / spell / secret / weapon
    std::string effect_id;
    std::vector<std::string> tags;
    int health = -1;
};

struct Card {
    std::string name;
    int cost = -1;
    std::string original_name;
    std::string card_type;
    std::string effect_id;
    std::vector<std::string> tags;
    bool no_fixed_cost = false;
    std::optional<int> temp_cost;
    bool is_deadly_shadow = false;
    std::optional<int> health;
    bool locked_one_cost = false;

    std::optional<int> current_cost() const {
        if (temp_cost.has_value()) {
            return std::max(0, *temp_cost);
        }
        if (cost < 0) {
            return std::nullopt;
        }
        return cost;
    }
};

struct GameState {
    std::vector<Card> deck;
    bool deck_is_known = false;
    std::vector<Card> hand;
    std::vector<Card> board;
    std::vector<Card> secrets;
    std::optional<Card> weapon;
    int mana_crystals = 10;
    int mana = 10;
    int initial_mana_crystals = 10;
    int initial_mana = 10;
    int cards_played_this_turn = 0;
    int next_spell_discount = 0;
    int next_combo_discount = 0;
    int next_card_discount = 0;
    int next_two_cards_discount = 0;
    int next_two_cards_discount_count = 0;
    std::vector<std::pair<int, int>> active_card_discounts;  // (剩余次数, 减费量)
    bool hero_immune_this_turn = false;
    std::string last_spell_original_name;
    int burned_cards = 0;
    int alex_play_count = 0;
    int alex_damage = 0;
    std::vector<std::string> etc_band_remaining;
    std::vector<std::string> path;
    std::vector<std::string> log;
    bool record_log = true;

    GameState clone() const { return *this; }

    bool has_shark() const {
        for (const auto& c : board) {
            if (c.name == "鲨鱼之灵") {
                return true;
            }
        }
        return false;
    }

    void add_log(const std::string& text) {
        if (record_log) {
            log.push_back(text);
        }
    }
};

}  // namespace rdc
