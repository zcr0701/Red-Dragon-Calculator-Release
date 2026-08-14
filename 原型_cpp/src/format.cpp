#include "format.h"

#include "engine.h"
#include "search.h"

#include <algorithm>
#include <cstdio>
#include <set>
#include <sstream>
#include <string>

namespace rdc {
namespace {

std::string join_strs(const std::vector<std::string>& items,
                      const std::string& sep) {
    std::string out;
    for (size_t i = 0; i < items.size(); ++i) {
        if (i > 0) out += sep;
        out += items[i];
    }
    return out;
}

std::string json_escape(const std::string& s) {
    std::string out;
    for (unsigned char ch : s) {
        switch (ch) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (ch < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", ch);
                    out += buf;
                } else {
                    out += (char)ch;
                }
        }
    }
    return out;
}

std::string json_str(const std::string& s) {
    return "\"" + json_escape(s) + "\"";
}

std::string json_str_list(const std::vector<std::string>& items) {
    std::string out = "[";
    for (size_t i = 0; i < items.size(); ++i) {
        if (i > 0) out += ", ";
        out += json_str(items[i]);
    }
    out += "]";
    return out;
}

std::string py_list_repr(const std::vector<std::string>& items) {
    std::string out = "[";
    for (size_t i = 0; i < items.size(); ++i) {
        if (i > 0) out += ", ";
        out += "'" + items[i] + "'";
    }
    out += "]";
    return out;
}

std::string py_bool(bool v) {
    return v ? "True" : "False";
}

std::string card_summary(const Card& c) {
    // {"name": ..., "cost": ..., "health": ..., "is_deadly_shadow": ...}
    std::string out = "{";
    out += "'name': '" + c.name + "', ";
    out += "'cost': ";
    out += c.current_cost().has_value()
               ? std::to_string(*c.current_cost())
               : "None";
    out += ", 'health': ";
    out += c.health.has_value() ? std::to_string(*c.health) : "None";
    out += ", 'is_deadly_shadow': " + py_bool(c.is_deadly_shadow);
    out += "}";
    return out;
}

}  // namespace

std::string compress_path_steps(const std::vector<std::string>& path,
                                int max_chunk_size) {
    std::vector<std::string> compressed;
    size_t index = 0;
    while (index < path.size()) {
        int best_chunk_size = 0;
        int best_repeat_count = 1;
        int max_size =
            (int)std::min(max_chunk_size, (int)((path.size() - index) / 2));
        for (int chunk_size = 1; chunk_size <= max_size; ++chunk_size) {
            int repeat_count = 1;
            size_t cursor = index + chunk_size;
            while (cursor + chunk_size <= path.size()) {
                bool same = true;
                for (int i = 0; i < chunk_size; ++i) {
                    if (path[cursor + i] != path[index + i]) {
                        same = false;
                        break;
                    }
                }
                if (!same) {
                    break;
                }
                repeat_count += 1;
                cursor += chunk_size;
            }
            if (repeat_count > 1 &&
                chunk_size * repeat_count >
                    best_chunk_size * best_repeat_count) {
                best_chunk_size = chunk_size;
                best_repeat_count = repeat_count;
            }
        }
        if (best_repeat_count > 1) {
            std::vector<std::string> chunk(path.begin() + index,
                                           path.begin() + index + best_chunk_size);
            compressed.push_back("（" + join_strs(chunk, " -> ") + "）×" +
                                 std::to_string(best_repeat_count));
            index += (size_t)best_chunk_size * best_repeat_count;
        } else {
            compressed.push_back(path[index]);
            index += 1;
        }
    }
    return join_strs(compressed, " -> ");
}

std::string format_path_text(const std::vector<std::string>& path) {
    if (path.empty()) {
        return "无可用出牌";
    }
    return compress_path_steps(path);
}

std::string format_paths(const std::vector<GameState>& states, int limit,
                         int non_alex_limit) {
    std::vector<GameState> unique;
    std::set<std::string> seen_paths;
    for (const auto& st : states) {
        std::string key = join_strs(st.path, "\x1f");
        if (seen_paths.count(key)) {
            continue;
        }
        seen_paths.insert(key);
        unique.push_back(st);
    }

    std::vector<GameState> alex_states;
    std::vector<GameState> non_alex_states;
    for (const auto& st : unique) {
        if (st.alex_play_count > 0) {
            alex_states.push_back(st);
        } else {
            non_alex_states.push_back(st);
        }
    }
    sort_path_states(alex_states);
    sort_path_states(non_alex_states);

    int shown_alex = std::min(limit, (int)alex_states.size());
    int remain = std::max(0, limit - shown_alex);
    int shown_non =
        std::min(non_alex_limit, std::min(remain, (int)non_alex_states.size()));

    std::vector<std::string> lines;
    lines.push_back("共计算出 " + std::to_string((int)unique.size()) +
                    " 条不重复终局路径");
    lines.push_back("其中可打出红龙路径 " +
                    std::to_string((int)alex_states.size()) +
                    " 条，未打出红龙路径 " +
                    std::to_string((int)non_alex_states.size()) + " 条");
    lines.push_back("显示红龙路径前 " + std::to_string(shown_alex) +
                    " 条；未打出红龙路径最多仅保留 " +
                    std::to_string(shown_non) + " 条：");
    lines.push_back("");

    int index = 1;
    if (shown_alex > 0) {
        lines.push_back("可打出红龙路径：");
    }
    for (int i = 0; i < shown_alex; ++i) {
        const auto& st = alex_states[i];
        std::string alex_text =
            " | 红龙次数：" + std::to_string(st.alex_play_count) +
            " | 伤害：" + std::to_string(st.alex_damage) + "点";
        lines.push_back(
            std::to_string(index) + ". " + format_path_text(st.path) +
            " | 需求：" + std::to_string(st.initial_mana_crystals) + "水晶 / " +
            std::to_string(st.initial_mana) + "法力 | 剩余法力：" +
            std::to_string(st.mana) + alex_text);
        index += 1;
    }

    if (shown_non > 0) {
        if (shown_alex > 0) {
            lines.push_back("");
        }
        lines.push_back("未打出红龙备用路径：");
    }
    for (int i = 0; i < shown_non; ++i) {
        const auto& st = non_alex_states[i];
        lines.push_back(
            std::to_string(index) + ". " + format_path_text(st.path) +
            " | 需求：" + std::to_string(st.initial_mana_crystals) + "水晶 / " +
            std::to_string(st.initial_mana) + "法力 | 剩余法力：" +
            std::to_string(st.mana));
        index += 1;
    }
    return join_strs(lines, "\n");
}

std::string state_summary_json(const GameState& s) {
    std::ostringstream out;
    out << "{\n";
    out << "  \"mana_crystals\": " << s.mana_crystals << ",\n";
    out << "  \"mana\": " << s.mana << ",\n";
    out << "  \"initial_mana_crystals\": " << s.initial_mana_crystals << ",\n";
    out << "  \"initial_mana\": " << s.initial_mana << ",\n";
    out << "  \"hand\": " << json_str_list([&]() {
        std::vector<std::string> names;
        for (const auto& c : s.hand) names.push_back(c.name);
        return names;
    }()) << ",\n";
    out << "  \"hand_detail\": [\n";
    for (size_t i = 0; i < s.hand.size(); ++i) {
        const auto& c = s.hand[i];
        out << "    {\"name\": " << json_str(c.name) << ", \"cost\": ";
        out << (c.current_cost().has_value()
                    ? std::to_string(*c.current_cost())
                    : "null");
        out << ", \"health\": ";
        out << (c.health.has_value() ? std::to_string(*c.health) : "null");
        out << ", \"is_deadly_shadow\": "
            << (c.is_deadly_shadow ? "true" : "false") << "}";
        if (i + 1 < s.hand.size()) out << ",";
        out << "\n";
    }
    out << "  ],\n";
    out << "  \"deck\": " << json_str_list([&]() {
        std::vector<std::string> names;
        for (const auto& c : s.deck) names.push_back(c.name);
        return names;
    }()) << ",\n";
    out << "  \"deck_is_known\": " << (s.deck_is_known ? "true" : "false")
        << ",\n";
    out << "  \"board\": " << json_str_list([&]() {
        std::vector<std::string> names;
        for (const auto& c : s.board) names.push_back(c.name);
        return names;
    }()) << ",\n";
    out << "  \"secrets\": " << json_str_list([&]() {
        std::vector<std::string> names;
        for (const auto& c : s.secrets) names.push_back(c.name);
        return names;
    }()) << ",\n";
    out << "  \"weapon\": "
        << (s.weapon.has_value() ? json_str(s.weapon->name) : "null") << ",\n";
    out << "  \"etc_band_remaining\": "
        << json_str_list(s.etc_band_remaining) << ",\n";
    out << "  \"burned_cards\": " << s.burned_cards << ",\n";
    out << "  \"alex_play_count\": " << s.alex_play_count << ",\n";
    out << "  \"alex_damage\": " << s.alex_damage << ",\n";
    out << "  \"path\": " << json_str_list(s.path) << ",\n";
    out << "  \"next_spell_discount\": " << s.next_spell_discount << ",\n";
    out << "  \"next_combo_discount\": " << s.next_combo_discount << ",\n";
    out << "  \"next_card_discount\": " << s.next_card_discount << ",\n";
    out << "  \"next_two_cards_discount\": " << s.next_two_cards_discount
        << ",\n";
    out << "  \"next_two_cards_discount_count\": "
        << s.next_two_cards_discount_count << ",\n";
    out << "  \"active_card_discounts\": [";
    for (size_t i = 0; i < s.active_card_discounts.size(); ++i) {
        if (i > 0) out << ", ";
        out << "[" << s.active_card_discounts[i].first << ", "
            << s.active_card_discounts[i].second << "]";
    }
    out << "],\n";
    out << "  \"log\": " << json_str_list(s.log) << "\n";
    out << "}";
    return out.str();
}

std::string results_json(const std::vector<GameState>& states) {
    std::ostringstream out;
    out << "[";
    for (size_t i = 0; i < states.size(); ++i) {
        if (i > 0) out << ",\n";
        out << state_summary_json(states[i]);
    }
    out << "]";
    return out.str();
}

std::string state_summary_text(const GameState& s) {
    std::vector<std::string> hand_names, deck_names, board_names, secret_names;
    for (const auto& c : s.hand) hand_names.push_back(c.name);
    for (const auto& c : s.deck) deck_names.push_back(c.name);
    for (const auto& c : s.board) board_names.push_back(c.name);
    for (const auto& c : s.secrets) secret_names.push_back(c.name);

    std::vector<std::string> detail;
    for (const auto& c : s.hand) detail.push_back(card_summary(c));

    std::vector<std::string> lines;
    lines.push_back("mana_crystals: " + std::to_string(s.mana_crystals));
    lines.push_back("mana: " + std::to_string(s.mana));
    lines.push_back("initial_mana_crystals: " +
                    std::to_string(s.initial_mana_crystals));
    lines.push_back("initial_mana: " + std::to_string(s.initial_mana));
    lines.push_back("hand: " + py_list_repr(hand_names));
    lines.push_back("hand_detail: [" + join_strs(detail, ", ") + "]");
    lines.push_back("deck: " + py_list_repr(deck_names));
    lines.push_back("deck_is_known: " + py_bool(s.deck_is_known));
    lines.push_back("board: " + py_list_repr(board_names));
    lines.push_back("secrets: " + py_list_repr(secret_names));
    lines.push_back("weapon: " +
                    (s.weapon.has_value() ? "'" + s.weapon->name + "'" : "None"));
    lines.push_back("etc_band_remaining: " + py_list_repr(s.etc_band_remaining));
    lines.push_back("burned_cards: " + std::to_string(s.burned_cards));
    lines.push_back("alex_play_count: " + std::to_string(s.alex_play_count));
    lines.push_back("alex_damage: " + std::to_string(s.alex_damage));
    lines.push_back("path: " + py_list_repr(s.path));
    lines.push_back("next_spell_discount: " +
                    std::to_string(s.next_spell_discount));
    lines.push_back("next_combo_discount: " +
                    std::to_string(s.next_combo_discount));
    lines.push_back("next_card_discount: " +
                    std::to_string(s.next_card_discount));
    lines.push_back("next_two_cards_discount: " +
                    std::to_string(s.next_two_cards_discount));
    lines.push_back("next_two_cards_discount_count: " +
                    std::to_string(s.next_two_cards_discount_count));
    std::vector<std::string> discount_pairs;
    for (const auto& item : s.active_card_discounts) {
        discount_pairs.push_back("(" + std::to_string(item.first) + ", " +
                                 std::to_string(item.second) + ")");
    }
    lines.push_back("active_card_discounts: [" +
                    join_strs(discount_pairs, ", ") + "]");
    lines.push_back("log: " + py_list_repr(s.log));
    return join_strs(lines, "\n");
}

}  // namespace rdc
