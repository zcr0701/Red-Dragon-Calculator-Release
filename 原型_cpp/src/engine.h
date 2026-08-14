#pragma once

#include "model.h"

#include <optional>
#include <string>
#include <vector>

namespace rdc {

bool is_spell_like(const Card& c);
bool has_tag(const Card& c, const std::string& tag);
std::optional<int> effective_cost(const GameState& s, const Card& c);
bool combo_active(const GameState& s);
int minion_trigger_multiplier(const GameState& s, const Card& c);
int alex_damage_amount(const GameState& s);
bool active_oil_discount(const GameState& s);

void consume_discounts(GameState& s, const Card& c);
void transform_deadly_shadows(GameState& s, const Card& spell);
void gain_temporary_mana(GameState& s, int amount);
void restore_mana(GameState& s, int amount);

bool hand_add(GameState& s, const Card& c);  // 手牌满则爆牌计数
std::optional<Card> draw_top_card(GameState& s);
std::optional<Card> draw_first_by_type(GameState& s, const std::string& type);

Card make_one_one_copy(const Card& c);
std::optional<Card> make_deadly_shadow_copy_from_spell(const Card& spell);
std::string display_card_name(const Card& c);
std::string card_cost_health_label(const Card& c);

int cards_drawn_if_played(const GameState& s, const Card& c);

bool play_card(GameState& s, int hand_index,
               std::optional<int> target_friendly = std::nullopt,
               bool target_enemy_is_killed = false,
               int discover_choice_index = 0,
               bool enemy_target = true);
bool play_card_by_name(GameState& s, const std::string& name,
                       std::optional<int> target_friendly = std::nullopt,
                       bool target_enemy_is_killed = false,
                       int discover_choice_index = 0,
                       bool enemy_target = true);

GameState create_state(const std::vector<std::string>& deck_names, bool deck_known,
                       const std::vector<std::string>& hand_names,
                       int mana_crystals, std::optional<int> mana);

}  // namespace rdc
