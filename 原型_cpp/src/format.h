#pragma once

#include "model.h"

#include <string>
#include <vector>

namespace rdc {

std::string compress_path_steps(const std::vector<std::string>& path,
                                int max_chunk_size = 10);
std::string format_path_text(const std::vector<std::string>& path);
std::string format_paths(const std::vector<GameState>& states, int limit = 200,
                         int non_alex_limit = 10);

std::string state_summary_json(const GameState& s);
std::string results_json(const std::vector<GameState>& states);
std::string state_summary_text(const GameState& s);

}  // namespace rdc
