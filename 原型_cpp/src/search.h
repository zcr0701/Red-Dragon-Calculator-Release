#pragma once

#include "model.h"

#include <atomic>
#include <string>
#include <vector>

namespace rdc {

std::vector<int> playable_card_indexes(const GameState& s);
std::vector<GameState> generate_successors(const GameState& s);

std::string state_key_for_dedup(const GameState& s);
void sort_path_states(std::vector<GameState>& states);

// 正向束搜索（与 Python beam_search_paths 一致）。
std::vector<GameState> beam_search_paths(const GameState& initial, int max_depth,
                                         int max_paths, int max_alex_count,
                                         int min_alex_count, int beam_width,
                                         double time_budget_seconds = 0.0,
                                         const std::atomic<bool>* stop_flag =
                                             nullptr);

}  // namespace rdc
