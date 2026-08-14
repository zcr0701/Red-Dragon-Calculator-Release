#pragma once

#include "model.h"

#include <string>
#include <vector>

namespace rdc {

const std::vector<CardDef>& card_database();
const std::vector<std::string>& etc_band();

Card make_card(const std::string& name);
Card make_card_runtime(const std::string& name, int cost);
bool card_exists(const std::string& name);

bool is_coin_name(const std::string& name);
bool is_core_card(const std::string& name);
bool is_shadowcaster_allowed_target(const std::string& name);
bool is_mana_gain_or_discount_effect(const std::string& effect_id);
bool is_draw_card_effect(const std::string& effect_id);
const std::vector<std::vector<std::string>>& preferred_combo_patterns();

}  // namespace rdc
