// 红龙贼计算器 C++ 计算核心（束搜索），与 Python red_dragon_calculator.py 的
// beam_search_paths / generate_successors 规则逐条对齐。
// 编译（MSVC）：cl /utf-8 /O2 /EHsc /std:c++17 red_dragon_core.cpp
// 用法：
//   red_dragon_core.exe --json < problem.json        # 从 stdin 读 JSON 局面，输出 JSON
//   red_dragon_core.exe --hand "鲨鱼之灵,斯卡布斯·刀油" --board "" --crystals 8 --mana 8 \
//       --min 1 --max 10 --width 3000 --depth 25 --json
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <map>
#include <set>
#include <string>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#include <shellapi.h>
#endif

using std::map;
using std::pair;
using std::string;
using std::tuple;
using std::unordered_map;
using std::vector;

static const int MAX_HAND = 10;
static const int MAX_BOARD = 7;
static const int MAX_SECRET = 5;

// ===================== 卡牌 =====================
struct Card {
    string name;
    string original_name;
    string card_type;      // minion / spell / secret / weapon / unknown
    string effect_id;
    int cost = -1;         // -1 = 无固定费用（殒命暗影）
    int temp_cost = -1;    // -1 = 无
    int health = -1;       // -1 = 无
    bool battlecry = false;
    bool combo = false;
    bool dragon = false;
    bool is_deadly_shadow = false;
    bool locked_one_cost = false;

    int current_cost() const { return temp_cost >= 0 ? temp_cost : cost; }
    bool is_spell_like() const { return card_type == "spell" || card_type == "secret"; }
    Card clone() const { return *this; }
};

struct CardDef {
    int cost;
    string card_type;
    string effect_id;
    bool battlecry;
    bool combo;
    bool dragon;
    int health;
};

static const unordered_map<string, CardDef> DB = {
    {"伪造的幸运币", {0, "spell", "fake_coin", false, false, false, -1}},
    {"幸运币", {0, "spell", "coin", false, false, false, -1}},
    {"伺机待发", {0, "spell", "preparation", false, false, false, -1}},
    {"暗影步", {0, "spell", "shadowstep", false, false, false, -1}},
    {"殒命暗影", {-1, "spell", "deadly_shadow", false, false, false, -1}},
    {"垂钓时光", {1, "spell", "gone_fishin", false, true, false, -1}},
    {"挖掘宝藏", {1, "spell", "dig_for_treasure", false, false, false, -1}},
    {"黑水弯刀", {1, "weapon", "blackwater_cutlass", false, false, false, -1}},
    {"邪恶短刀", {1, "weapon", "", false, false, false, -1}},
    {"锯齿骨刺", {2, "spell", "serrated_bone_spike", false, false, false, -1}},
    {"疾速矿锄", {2, "weapon", "quick_pick", false, false, false, -1}},
    {"异教地图", {2, "spell", "cultist_map", false, false, false, -1}},
    {"行骗", {2, "spell", "swindle", false, true, false, -1}},
    {"闪避", {2, "secret", "evasion", false, false, false, -1}},
    {"潜伏帷幕", {3, "spell", "shroud_of_concealment", false, false, false, -1}},
    {"晦鳞巢母", {3, "minion", "candlebreath_mother", true, false, false, 3}},
    {"舞动全场（ft.迦罗娜）", {3, "spell", "breakdance", false, false, false, -1}},
    {"幻觉药水", {4, "spell", "potion_of_illusion", false, false, false, -1}},
    {"幸运彗星", {1, "spell", "lucky_comet", false, false, false, -1}},
    {"战略转移", {3, "spell", "strategic_transfer", false, false, false, -1}},
    {"乐队经理精英牛头人酋长", {4, "minion", "elite_tauren_champion", true, false, false, 4}},
    {"可疑交易", {4, "spell", "dubious_purchase", false, true, false, -1}},
    {"鲨鱼之灵", {4, "minion", "spirit_of_the_shark", false, false, false, 3}},
    {"斯卡布斯·刀油", {4, "minion", "scabbs_cutterbutter", false, true, false, 3}},
    {"暗影施法者", {5, "minion", "shadowcaster", true, false, false, 4}},
    {"生命的缚誓者阿莱克丝塔萨", {9, "minion", "alexstrasza", true, false, true, 8}},
    {"赤烟·腾武", {2, "minion", "tenwu", true, false, false, 2}},
    {"狐人老千", {2, "minion", "foxy_fraud", true, false, false, 2}},
};

static Card make_card(const string& name, int cost_override = -1) {
    Card c;
    auto it = DB.find(name);
    if (it == DB.end()) {
        c.name = name;
        c.original_name = name;
        c.card_type = "unknown";
        c.effect_id = "unknown";
        return c;
    }
    const CardDef& d = it->second;
    c.name = name;
    c.original_name = name;
    c.card_type = d.card_type;
    c.effect_id = d.effect_id;
    c.cost = cost_override >= 0 ? cost_override : d.cost;
    c.battlecry = d.battlecry;
    c.combo = d.combo;
    c.dragon = d.dragon;
    c.health = d.health;
    if (name == "殒命暗影") c.is_deadly_shadow = true;
    return c;
}

static bool is_coin_name(const string& n) {
    return n == "幸运币" || n == "伪造的幸运币";
}

// ===================== 局面 =====================
struct State {
    vector<Card> hand;
    vector<Card> board;
    vector<Card> deck;
    vector<Card> secrets;
    Card weapon;
    bool has_weapon = false;
    bool deck_is_known = false;
    int mana_crystals = 10;
    int mana = 10;
    int initial_mana_crystals = 10;
    int initial_mana = 10;
    int cards_played_this_turn = 0;
    int next_spell = 0;
    int next_combo = 0;
    bool next_combo_twice = false;
    int next_card = 0;
    int next_two_cards = 0;
    int next_two_cards_count = 0;
    vector<pair<int, int>> oil_stacks;  // (剩余次数, 减费)
    int burned_cards = 0;
    int alex_play_count = 0;
    int alex_damage = 0;
    vector<string> etc_band;
    vector<string> path;

    State clone() const { return *this; }

    bool has_shark() const {
        for (const auto& c : board)
            if (c.name == "鲨鱼之灵") return true;
        return false;
    }

    int hand_size() const { return (int)hand.size(); }
    int board_size() const { return (int)board.size(); }
    bool hand_full() const { return hand_size() >= MAX_HAND; }
    bool board_full() const { return board_size() >= MAX_BOARD; }
};

// ===================== 基础规则 =====================
static bool is_spell_like(const Card& card) { return card.is_spell_like(); }

static int effective_cost(const State& s, const Card& card) {
    int base = card.current_cost();
    if (base < 0) return -1;
    if (card.locked_one_cost) return 1;  // 腾武回手：永远固定 1 费
    int discount = 0;
    if (s.next_card > 0) discount += s.next_card;
    if (s.next_two_cards_count > 0) discount += s.next_two_cards;
    for (const auto& p : s.oil_stacks)
        if (p.first > 0 && p.second > 0) discount += p.second;
    if (card.is_spell_like() && s.next_spell > 0) discount += s.next_spell;
    if (card.combo && s.next_combo > 0) discount += s.next_combo;
    int cost = base - discount;
    return cost < 0 ? 0 : cost;
}

static bool active_oil_discount(const State& s) {
    for (const auto& p : s.oil_stacks)
        if (p.first > 0 && p.second > 0) return true;
    return false;
}

static void consume_discounts(State& s, const Card& card) {
    s.next_card = 0;
    if (s.next_two_cards_count > 0) {
        s.next_two_cards_count--;
        if (s.next_two_cards_count <= 0) s.next_two_cards = 0;
    }
    vector<pair<int, int>> remain;
    for (auto& p : s.oil_stacks) {
        int r = p.first - 1;
        if (r > 0) remain.push_back({r, p.second});
    }
    s.oil_stacks = remain;
    if (card.is_spell_like()) s.next_spell = 0;
    if (card.combo) s.next_combo = 0;
}

static int minion_trigger_multiplier(const State& s, const Card& card) {
    if ((card.battlecry || card.combo) && s.has_shark()) return 2;
    return 1;
}

static void add_card_to_hand_or_burn(State& s, const Card& card) {
    if (s.hand_full()) {
        s.burned_cards++;
    } else {
        s.hand.push_back(card);
    }
}

static void transform_deadly_shadows(State& s, const Card& spell_card) {
    if (spell_card.original_name == "殒命暗影") return;
    auto it = DB.find(spell_card.original_name);
    if (it == DB.end()) return;
    for (auto& c : s.hand) {
        if (c.is_deadly_shadow) {
            Card copy = make_card(spell_card.original_name);
            copy.is_deadly_shadow = true;
            c = copy;
        }
    }
}

static void append_choice_to_last_path(State& s, const string& choice_name) {
    if (s.path.empty()) return;
    string& last = s.path.back();
    static const string full_width_close = "）";
    if (last.find("（") != string::npos && last.size() >= full_width_close.size() &&
        last.compare(last.size() - full_width_close.size(), full_width_close.size(), full_width_close) == 0) {
        last = last.substr(0, last.size() - full_width_close.size()) + "->" + choice_name + "）";
    } else {
        last = last + "（" + choice_name + "）";
    }
}

static string display_card_name(const Card& card) {
    if (card.is_deadly_shadow) return card.name + "[殒命暗影]";
    return card.name;
}

// 打出基础：扣费、移出手牌、消耗减费、记账、进区
static bool play_card_base(State& s, int hand_index, int target_friendly_index,
                           bool target_enemy_is_killed, bool enemy_target) {
    if (hand_index < 0 || hand_index >= (int)s.hand.size()) return false;
    Card card = s.hand[hand_index];
    int cost = effective_cost(s, card);
    if (cost < 0 || s.mana < cost) return false;
    if (card.card_type == "minion" && s.board_full()) return false;
    if (card.card_type == "secret" && (int)s.secrets.size() >= MAX_SECRET) return false;

    s.mana -= cost;
    s.hand.erase(s.hand.begin() + hand_index);
    consume_discounts(s, card);

    string item = display_card_name(card);
    if (target_friendly_index >= 0) {
        if (target_friendly_index < (int)s.board.size()) {
            item += "(" + s.board[target_friendly_index].name + ")";
        } else {
            item += "(无效目标)";
        }
    }
    s.path.push_back(item);

    if (card.name == "生命的缚誓者阿莱克丝塔萨") {
        s.alex_play_count++;
        if (enemy_target) {
            int mult = s.has_shark() ? 2 : 1;
            s.alex_damage += 8 * mult;
        }
    }

    if (card.card_type == "minion") {
        s.board.push_back(card);
    } else if (card.card_type == "secret") {
        s.secrets.push_back(card);
    } else if (card.card_type == "weapon") {
        s.weapon = card;
        s.has_weapon = true;
    }
    return true;
}

// 牛头人发现：从乐队中选一张，加入手牌并从乐队移除一个同名项
static vector<State> discover_fixed_choices(const State& base) {
    vector<State> out;
    if (base.etc_band.empty()) {
        out.push_back(base.clone());
        return out;
    }
    for (size_t i = 0; i < base.etc_band.size(); i++) {
        State s = base.clone();
        string choice = s.etc_band[i];
        s.etc_band.erase(s.etc_band.begin() + i);
        add_card_to_hand_or_burn(s, make_card(choice));
        append_choice_to_last_path(s, choice);
        out.push_back(s);
    }
    return out;
}

// 舞动全场：按进场顺序全部回手（1 费），手牌满则按进场顺序爆牌
static State breakdance_branch(const State& base) {
    State s = base.clone();
    vector<Card> returning = s.board;
    s.board.clear();
    int free_slots = std::max(0, MAX_HAND - s.hand_size());
    if ((int)returning.size() <= free_slots) {
        for (auto& m : returning) {
            m.temp_cost = 1;
            add_card_to_hand_or_burn(s, m);
        }
    } else {
        for (int i = 0; i < free_slots; i++) {
            returning[i].temp_cost = 1;
            add_card_to_hand_or_burn(s, returning[i]);
        }
        s.burned_cards += (int)returning.size() - free_slots;
    }
    return s;
}

// 效果结算（与 Python apply_search_effect 对齐），返回多个后继
static vector<State> apply_search_effect(const State& base, const Card& card,
                                         int target_friendly_index,
                                         bool target_enemy_is_killed,
                                         bool enemy_target) {
    int multiplier = minion_trigger_multiplier(base, card);
    vector<State> states = {base.clone()};
    for (int m = 0; m < multiplier; m++) {
        vector<State> next_states;
        for (const State& current : states) {
            const string& e = card.effect_id;
            if (e == "coin" || e == "fake_coin") {
                State ns = current.clone();
                ns.mana += 1;  // 临时法力不封顶（与 Python gain_temporary 一致）
                next_states.push_back(ns);
            } else if (e == "preparation") {
                State ns = current.clone();
                ns.next_spell += 2;
                next_states.push_back(ns);
            } else if (e == "foxy_fraud") {
                State ns = current.clone();
                ns.next_combo += 2;
                next_states.push_back(ns);
            } else if (e == "scabbs_cutterbutter") {
                State ns = current.clone();
                if (ns.cards_played_this_turn > 0) {
                    int stacks = ns.next_combo_twice ? 4 : 2;  // 幸运彗星：连击触发两次
                    ns.oil_stacks.push_back({stacks, 2});
                    ns.next_combo_twice = false;
                }
                next_states.push_back(ns);
            } else if (e == "lucky_comet") {
                // 幸运彗星：发现一张连击随从（牌库分支），设置下一张连击随从连击两次
                vector<int> combo_indexes;
                for (int i = 0; i < (int)current.deck.size(); i++) {
                    if (current.deck[i].card_type == "minion" && current.deck[i].combo)
                        combo_indexes.push_back(i);
                }
                if (!combo_indexes.empty()) {
                    for (size_t k = 0; k < combo_indexes.size() && k < 3; k++) {
                        State ns = current.clone();
                        Card card = ns.deck[combo_indexes[k]];
                        ns.deck.erase(ns.deck.begin() + combo_indexes[k]);
                        add_card_to_hand_or_burn(ns, card);
                        ns.next_combo_twice = true;
                        next_states.push_back(ns);
                    }
                } else {
                    State ns = current.clone();
                    add_card_to_hand_or_burn(ns, make_card("斯卡布斯·刀油"));
                    ns.next_combo_twice = true;
                    next_states.push_back(ns);
                }
            } else if (e == "strategic_transfer") {
                // 战略转移：所有友方随从移回手牌（保持费用状态），手牌满按进场顺序烧
                State ns = current.clone();
                vector<Card> returning = ns.board;
                ns.board.clear();
                int free_slots = std::max(0, MAX_HAND - (int)ns.hand.size());
                if ((int)returning.size() <= free_slots) {
                    for (Card& m : returning) add_card_to_hand_or_burn(ns, m);
                } else {
                    for (int i = 0; i < free_slots; i++) add_card_to_hand_or_burn(ns, returning[i]);
                    ns.burned_cards += (int)returning.size() - free_slots;
                }
                next_states.push_back(ns);
            } else if (e == "shadowstep") {
                State ns = current.clone();
                if (target_friendly_index >= 0 && target_friendly_index < (int)ns.board.size()) {
                    Card target = ns.board[target_friendly_index];
                    ns.board.erase(ns.board.begin() + target_friendly_index);
                    if (target.locked_one_cost) {
                        target.temp_cost = 1;
                    } else {
                        int base_cost = target.current_cost() >= 0 ? target.current_cost() : 0;
                        target.temp_cost = std::max(0, base_cost - 2);
                    }
                    add_card_to_hand_or_burn(ns, target);
                }
                next_states.push_back(ns);
            } else if (e == "shadowcaster") {
                State ns = current.clone();
                if (target_friendly_index >= 0 && target_friendly_index < (int)ns.board.size()) {
                    Card copied = ns.board[target_friendly_index].clone();
                    copied.temp_cost = 1;
                    copied.health = 1;
                    add_card_to_hand_or_burn(ns, copied);
                }
                next_states.push_back(ns);
            } else if (e == "tenwu") {
                State ns = current.clone();
                if (target_friendly_index >= 0 && target_friendly_index < (int)ns.board.size()) {
                    Card target = ns.board[target_friendly_index];
                    ns.board.erase(ns.board.begin() + target_friendly_index);
                    target.temp_cost = 1;
                    target.locked_one_cost = true;
                    add_card_to_hand_or_burn(ns, target);
                }
                next_states.push_back(ns);
            } else if (e == "breakdance") {
                next_states.push_back(breakdance_branch(current));
            } else if (e == "potion_of_illusion") {
                State ns = current.clone();
                vector<Card> copies;
                for (const auto& m : ns.board) {
                    Card copy = m.clone();
                    copy.temp_cost = 1;
                    copy.health = 1;
                    copies.push_back(copy);
                }
                for (const auto& c : copies) add_card_to_hand_or_burn(ns, c);
                next_states.push_back(ns);
            } else if (e == "candlebreath_mother") {
                State ns = current.clone();
                bool dragon_in_hand = false;
                for (const auto& c : ns.hand)
                    if (c.dragon) { dragon_in_hand = true; break; }
                if (dragon_in_hand) {
                    ns.mana = std::min(ns.mana_crystals, ns.mana + 2);
                }
                next_states.push_back(ns);
            } else if (e == "serrated_bone_spike") {
                if (target_enemy_is_killed) {
                    if (target_friendly_index < 0 || target_friendly_index >= (int)current.board.size()) {
                        continue;  // 无后继
                    }
                    const Card& target = current.board[target_friendly_index];
                    if (target.health < 0 || target.health > 3) {
                        continue;  // 无后继
                    }
                    State ns = current.clone();
                    ns.board.erase(ns.board.begin() + target_friendly_index);
                    ns.next_card += 2;
                    next_states.push_back(ns);
                } else {
                    next_states.push_back(current.clone());
                }
            } else if (e == "cultist_map") {
                State ns = current.clone();
                Card unknown = make_card("未知发现物");
                add_card_to_hand_or_burn(ns, unknown);
                next_states.push_back(ns);
            } else if (e == "elite_tauren_champion") {
                vector<State> disc = discover_fixed_choices(current);
                for (auto& d : disc) next_states.push_back(d);
            } else if (e == "alexstrasza") {
                next_states.push_back(current.clone());
            } else {
                next_states.push_back(current.clone());
            }
        }
        states = std::move(next_states);
    }
    for (State& rs : states) {
        if (card.is_spell_like()) transform_deadly_shadows(rs, card);
        rs.cards_played_this_turn++;
    }
    return states;
}

// ===================== 后继生成 =====================
static const std::set<string> CORE_CARD_NAMES = {
    "鲨鱼之灵", "狐人老千", "斯卡布斯·刀油", "暗影施法者",
    "乐队经理精英牛头人酋长", "晦鳞巢母", "生命的缚誓者阿莱克丝塔萨",
    "暗影步", "舞动全场（ft.迦罗娜）", "幻觉药水", "锯齿骨刺",
    "殒命暗影", "赤烟·腾武",
};
static const std::set<string> SHADOWCASTER_ALLOWED_TARGETS = {
    "暗影施法者", "斯卡布斯·刀油", "生命的缚誓者阿莱克丝塔萨", "晦鳞巢母",
};
static const std::set<string> MANA_GAIN_OR_DISCOUNT_EFFECTS = {
    "coin", "fake_coin", "preparation", "shadowstep", "foxy_fraud",
    "scabbs_cutterbutter", "serrated_bone_spike",
};
static const std::set<string> DRAW_CARD_EFFECTS = {
    "dig_for_treasure", "shroud_of_concealment", "swindle",
    "dubious_purchase", "gone_fishin", "quick_pick",
};
static const vector<vector<string>> PREFERRED_COMBO_PATTERNS = {
    {"鲨鱼之灵", "狐人老千", "斯卡布斯·刀油"},
    {"斯卡布斯·刀油", "暗影施法者", "乐队经理精英牛头人酋长"},
    {"鲨鱼之灵", "斯卡布斯·刀油", "斯卡布斯·刀油", "生命的缚誓者阿莱克丝塔萨"},
    {"生命的缚誓者阿莱克丝塔萨", "暗影施法者", "生命的缚誓者阿莱克丝塔萨",
     "生命的缚誓者阿莱克丝塔萨"},
};

static bool combo_active(const State& s) { return s.cards_played_this_turn > 0; }

static int cards_drawn_if_played(const State& s, const Card& card) {
    const string& e = card.effect_id;
    if (DRAW_CARD_EFFECTS.find(e) == DRAW_CARD_EFFECTS.end()) return 0;
    if (e == "gone_fishin" && !combo_active(s)) return 0;
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
    for (const auto& c : s.deck) {
        if (c.card_type == "minion") deck_minions++;
        if (c.is_spell_like()) deck_spells++;
    }
    int deck_total = (int)s.deck.size();
    if (e == "dubious_purchase") return std::min(3, deck_total);
    if (e == "gone_fishin") return std::min(1, deck_total);
    if (e == "dig_for_treasure") return std::min(1, deck_minions);
    if (e == "swindle") {
        int n = std::min(1, deck_spells);
        if (combo_active(s)) n += std::min(1, deck_minions);
        return n;
    }
    if (e == "shroud_of_concealment") return std::min(2, deck_minions);
    if (e == "quick_pick") return std::min(1, deck_total);
    return 0;
}

static int preferred_combo_bonus(const vector<string>& path, const string& next_name) {
    vector<string> names = path;
    names.push_back(next_name);
    int bonus = 0;
    for (const auto& pattern : PREFERRED_COMBO_PATTERNS) {
        int plen = (int)pattern.size();
        int prefix_length = std::min(plen, (int)names.size());
        bool prefix_ok = true;
        for (int k = 0; k < prefix_length; k++) {
            if (names[(int)names.size() - prefix_length + k] != pattern[k]) {
                prefix_ok = false;
                break;
            }
        }
        if (prefix_ok) bonus += prefix_length * 10;
        if ((int)names.size() >= plen) {
            bool full_ok = true;
            for (int k = 0; k < plen; k++) {
                if (names[(int)names.size() - plen + k] != pattern[k]) {
                    full_ok = false;
                    break;
                }
            }
            if (full_ok) bonus += 100;
        }
    }
    return bonus;
}

static int heuristic_score(const State& s, const Card& card, int cost,
                           int target_friendly_index, bool target_enemy_is_killed) {
    int score = 0;
    int current_cost = card.current_cost() >= 0 ? card.current_cost() : 0;
    int saved_cost = std::max(0, current_cost - cost);
    if (cost == 0 && MANA_GAIN_OR_DISCOUNT_EFFECTS.count(card.effect_id)) score += 120;
    if (card.effect_id == "coin" || card.effect_id == "fake_coin") {
        int early_bonus = std::max(0, 8 - s.cards_played_this_turn) * 10;
        int hand_pressure = std::max(0, (int)s.hand.size() - 6) * 50;
        score += early_bonus + hand_pressure;
    }
    if (saved_cost > 0) score += saved_cost * 25;
    if (active_oil_discount(s) && (current_cost == 3 || current_cost == 4)) score += 80;
    if (CORE_CARD_NAMES.count(card.name) || card.is_deadly_shadow) score += 60;
    score += preferred_combo_bonus(s.path, display_card_name(card));
    if (card.effect_id == "shadowcaster" && target_friendly_index >= 0 &&
        target_friendly_index < (int)s.board.size() &&
        SHADOWCASTER_ALLOWED_TARGETS.count(s.board[target_friendly_index].name)) {
        score += 40;
    }
    if (card.effect_id == "serrated_bone_spike" && target_enemy_is_killed) score += 30;
    return score;
}

static vector<State> generate_successors(const State& st) {
    vector<tuple<int, int, State>> ranked;
    int seq = 0;

    for (int hand_index = 0; hand_index < (int)st.hand.size(); hand_index++) {
        const Card& card = st.hand[hand_index];
        if (card.name.rfind("未知", 0) == 0) continue;
        int cost = effective_cost(st, card);
        if (cost < 0) continue;
        if (st.mana < cost) continue;
        if (card.card_type == "minion" && st.board_full()) continue;
        if (card.card_type == "secret" && (int)st.secrets.size() >= MAX_SECRET) continue;
        if (cards_drawn_if_played(st, card) > 0) continue;

        vector<int> friendly_targets;
        if (card.effect_id == "shadowstep" || card.effect_id == "shadowcaster" ||
            card.effect_id == "serrated_bone_spike" || card.effect_id == "tenwu") {
            for (int i = 0; i < (int)st.board.size(); i++) friendly_targets.push_back(i);
        } else {
            friendly_targets.push_back(-1);
        }
        vector<bool> kill_options = {false};
        if (card.effect_id == "serrated_bone_spike") kill_options = {false, true};
        vector<bool> enemy_options = {true};
        if (card.effect_id == "alexstrasza") enemy_options = {true, false};

        for (int tf : friendly_targets) {
            for (bool ek : kill_options) {
                for (bool et : enemy_options) {
                    int hscore = heuristic_score(st, card, cost, tf, ek);
                    State base = st.clone();
                    if (!play_card_base(base, hand_index, tf, ek, et)) continue;
                    vector<State> succs = apply_search_effect(base, card, tf, ek, et);
                    for (State& succ : succs) {
                        ranked.push_back({hscore, seq, succ});
                        seq++;
                    }
                }
            }
        }
    }

    std::stable_sort(ranked.begin(), ranked.end(),
                     [](const tuple<int, int, State>& a, const tuple<int, int, State>& b) {
                         if (std::get<0>(a) != std::get<0>(b)) return std::get<0>(a) < std::get<0>(b);
                         return std::get<1>(a) < std::get<1>(b);
                     });
    vector<State> out;
    out.reserve(ranked.size());
    for (auto& r : ranked) out.push_back(std::get<2>(r));
    return out;
}

// ===================== 去重键（与 Python state_key_for_dedup 对齐） =====================
static string card_key_string(const Card& c) {
    char buf[512];
    snprintf(buf, sizeof(buf), "%s|%s|%s|%s|%d|%d|%d|%d",
             c.name.c_str(), c.original_name.c_str(), c.card_type.c_str(),
             c.effect_id.c_str(), c.current_cost(), c.is_deadly_shadow ? 1 : 0,
             c.locked_one_cost ? 1 : 0, c.health);
    return string(buf);
}

static string state_key_for_dedup(const State& s) {
    string key;
    key += s.deck_is_known ? "1|" : "0|";
    vector<string> hand_keys;
    for (const auto& c : s.hand) hand_keys.push_back(card_key_string(c));
    std::sort(hand_keys.begin(), hand_keys.end());
    key += "[";
    for (const auto& k : hand_keys) key += k + ";";
    key += "]|";
    for (const auto& c : s.deck) key += card_key_string(c) + ";";
    key += "|";
    for (const auto& c : s.board) key += card_key_string(c) + ";";
    key += "|";
    for (const auto& c : s.secrets) key += card_key_string(c) + ";";
    key += "|";
    if (s.has_weapon) key += card_key_string(s.weapon);
    key += "|";
    for (const auto& n : s.etc_band) key += n + ";";
    key += "|" + std::to_string(s.cards_played_this_turn);
    key += "|" + std::to_string(s.next_spell);
    key += "|" + std::to_string(s.next_combo);
    key += "|" + std::to_string(s.next_combo_twice ? 1 : 0);
    key += "|" + std::to_string(s.next_card);
    key += "|" + std::to_string(s.next_two_cards);
    key += "|" + std::to_string(s.next_two_cards_count);
    key += "|";
    for (const auto& p : s.oil_stacks) key += std::to_string(p.first) + ":" + std::to_string(p.second) + ";";
    return key;
}

// ===================== 束搜索评分（子链评分 / 覆盖度 / 离散子链） =====================
static int count_hand_cards(const State& s, const string& name) {
    int n = 0;
    for (const auto& c : s.hand) if (c.name == name) n++;
    return n;
}
static int count_board_cards(const State& s, const string& name) {
    int n = 0;
    for (const auto& c : s.board) if (c.name == name) n++;
    return n;
}
static int count_hand_dragons(const State& s) {
    int n = 0;
    for (const auto& c : s.hand) if (c.dragon) n++;
    return n;
}

static int subchain_score(const State& s) {
    int hand_d = count_hand_dragons(s);
    int board_d = count_board_cards(s, "生命的缚誓者阿莱克丝塔萨");
    int dragons = hand_d + board_d;
    bool shark_on = s.has_shark();
    bool shark_in_hand = count_hand_cards(s, "鲨鱼之灵") > 0;
    int mother = count_hand_cards(s, "晦鳞巢母") + count_board_cards(s, "晦鳞巢母");
    int shadowcaster = count_hand_cards(s, "暗影施法者") + count_board_cards(s, "暗影施法者");
    int shadowstep = count_hand_cards(s, "暗影步");
    int dance = count_hand_cards(s, "舞动全场（ft.迦罗娜）");
    int potion = count_hand_cards(s, "幻觉药水");
    bool deadly = false;
    for (const auto& c : s.hand) if (c.is_deadly_shadow) { deadly = true; break; }
    int scabbs = count_hand_cards(s, "斯卡布斯·刀油");
    int oil = 0;
    for (const auto& p : s.oil_stacks) if (p.first > 0 && p.second > 0) oil += p.second * p.first;

    int score = oil * 2;
    if (dragons > 0 && (shark_on || shark_in_hand)) score += dragons * 16;
    if (dragons > 0 && mother > 0) score += 12;
    if (dragons > 0 && shadowcaster > 0) score += 12;
    if (dragons > 0 && (dance > 0 || potion > 0)) score += 12;
    if (board_d > 0 && shadowstep > 0) score += 12;
    if (deadly && (dance > 0 || potion > 0)) score += 15;
    if ((shark_on || shark_in_hand) && scabbs >= 2) score += 8;
    score += count_hand_cards(s, "乐队经理精英牛头人酋长") * 8;
    return score;
}

static int subchain_coverage(const State& s) {
    int hand_d = count_hand_dragons(s);
    int board_d = count_board_cards(s, "生命的缚誓者阿莱克丝塔萨");
    int dragons = hand_d + board_d;
    bool shark_on = s.has_shark();
    bool shark_in_hand = count_hand_cards(s, "鲨鱼之灵") > 0;
    int mother = count_hand_cards(s, "晦鳞巢母") + count_board_cards(s, "晦鳞巢母");
    int shadowcaster = count_hand_cards(s, "暗影施法者") + count_board_cards(s, "暗影施法者");
    int shadowstep = count_hand_cards(s, "暗影步");
    int dance = count_hand_cards(s, "舞动全场（ft.迦罗娜）");
    int potion = count_hand_cards(s, "幻觉药水");
    bool deadly = false;
    for (const auto& c : s.hand) if (c.is_deadly_shadow) { deadly = true; break; }
    int scabbs = count_hand_cards(s, "斯卡布斯·刀油");

    int coverage = 0;
    if (dragons > 0 && (shark_on || shark_in_hand)) coverage++;   // 鱼龙
    if (dragons > 0 && mother > 0) coverage++;                    // 龙晦龙
    if (dragons > 0 && shadowcaster > 0) coverage++;              // 龙暗龙
    if (dragons > 0 && (dance > 0 || potion > 0)) coverage++;     // 龙舞龙
    if (board_d > 0 && shadowstep > 0) coverage++;                // 龙步龙
    if (deadly && (dance > 0 || potion > 0)) coverage++;          // 双舞
    if ((shark_on || shark_in_hand) && scabbs >= 2) coverage++;   // 刀刀引擎
    return coverage;
}

static bool path_contains(const vector<string>& path, const vector<string>& needles) {
    size_t i = 0;
    for (const auto& item : path) {
        if (i < needles.size() && item.find(needles[i]) != string::npos) i++;
    }
    return i == needles.size();
}

static int discrete_path_score(const State& s) {
    const auto& p = s.path;
    int score = 0;
    if (path_contains(p, {"鲨鱼之灵", "生命的缚誓者"})) score += 16;
    if (path_contains(p, {"生命的缚誓者", "晦鳞巢母", "生命的缚誓者"})) score += 20;
    if (path_contains(p, {"生命的缚誓者", "暗影施法者", "生命的缚誓者"})) score += 16;
    if (path_contains(p, {"生命的缚誓者", "舞动全场", "生命的缚誓者"})) score += 16;
    if (path_contains(p, {"暗影步", "生命的缚誓者"})) score += 14;
    if (path_contains(p, {"舞动全场", "舞动全场"})) score += 18;
    if (path_contains(p, {"鲨鱼之灵", "晦鳞巢母", "生命的缚誓者"})) score += 14;
    return score;
}

// ===================== 束搜索 =====================
struct BeamPath {
    int alex_play_count = 0;
    int alex_damage = 0;
    int mana = 0;
    int initial_mana_crystals = 0;
    int initial_mana = 0;
    vector<string> path;
};

struct BeamResult {
    vector<BeamPath> paths;
    int expansions = 0;
    int reached_depth = 0;
    int max_damage = 0;
    int max_dragons = 0;
};

struct Progress {
    int last_reported = 0;
    int reported_max_count = -1;
    bool enabled = true;
};

static BeamPath path_from_state(const State& s) {
    BeamPath p;
    p.alex_play_count = s.alex_play_count;
    p.alex_damage = s.alex_damage;
    p.mana = s.mana;
    p.initial_mana_crystals = s.initial_mana_crystals;
    p.initial_mana = s.initial_mana;
    p.path = s.path;
    return p;
}

static BeamResult beam_search_paths(const State& start_in, int max_depth, int max_paths,
                                    int max_alex, int min_alex, int beam_width,
                                    Progress* prog = nullptr) {
    BeamResult res;
    min_alex = std::max(1, std::min(min_alex, max_alex));
    State start = start_in.clone();

    struct Cand {
        State s;
        string key;
        int total = 0;
        int coverage = 0;
        int mana = 0;
        int count = 0;
    };

    struct BestEntry {
        BeamPath p;
        int total = 0;
        int mana = 0;
    };
    unordered_map<int, BestEntry> best;
    {
        BestEntry e;
        e.p = path_from_state(start);
        e.total = start.alex_damage + subchain_score(start) + discrete_path_score(start);
        e.mana = start.mana;
        best[start.alex_play_count] = e;
    }

    vector<State> level = {start};
    unordered_map<string, State> seen;
    seen[state_key_for_dedup(start) + "|" + std::to_string(start.alex_play_count)] = start;
    int expansions = 0;
    int reached_depth = 0;
    int max_damage = start.alex_damage;

    for (int depth = 1; depth <= max_depth; depth++) {
        if (level.empty()) break;
        reached_depth = depth;
        vector<Cand> cands;
        unordered_map<string, size_t> cand_index;  // key -> cands 位置（保持插入顺序）

        for (const State& state : level) {
            vector<State> succs = generate_successors(state);
            for (State& succ : succs) {
                expansions++;
                if (prog && prog->enabled) {
                    if (expansions - prog->last_reported >= 20000) {
                        fprintf(stderr, "PROGRESS %d %d %d\n", expansions, (int)level.size(), depth);
                        prog->last_reported = expansions;
                    }
                    if (succ.alex_play_count > prog->reported_max_count) {
                        fprintf(stderr, "FOUND %d %d\n", succ.alex_play_count, succ.alex_damage);
                        prog->reported_max_count = succ.alex_play_count;
                    }
                }
                if (succ.alex_damage > max_damage) max_damage = succ.alex_damage;
                string key = state_key_for_dedup(succ) + "|" + std::to_string(succ.alex_play_count);
                auto prev = seen.find(key);
                if (prev != seen.end() && succ.mana <= prev->second.mana) continue;
                int total = succ.alex_damage + subchain_score(succ) + discrete_path_score(succ);
                int coverage = subchain_coverage(succ);
                auto cur = cand_index.find(key);
                if (cur != cand_index.end()) {
                    Cand& c = cands[cur->second];
                    if (succ.mana <= c.mana) continue;
                    c.s = std::move(succ);  // 更高法力替换，保持原插入位置
                    c.total = total;
                    c.coverage = coverage;
                    c.mana = c.s.mana;
                } else {
                    Cand c;
                    c.s = std::move(succ);
                    c.key = key;
                    c.total = total;
                    c.coverage = coverage;
                    c.mana = c.s.mana;
                    c.count = c.s.alex_play_count;
                    cand_index[key] = cands.size();
                    cands.push_back(std::move(c));
                }
                const State& ns = cands[cand_index[key]].s;
                auto bit = best.find(ns.alex_play_count);
                if (bit == best.end() ||
                    (total > bit->second.total) ||
                    (total == bit->second.total && ns.mana > bit->second.mana)) {
                    BestEntry e;
                    e.p = path_from_state(ns);
                    e.total = total;
                    e.mana = ns.mana;
                    best[ns.alex_play_count] = e;
                }
            }
        }

        if (cands.empty()) break;

        for (const Cand& c : cands) {
            auto prev = seen.find(c.key);
            if (prev == seen.end() || c.mana > prev->second.mana) seen[c.key] = c.s;
        }

        // 分桶：按当前龙数，避免高龙数分支挤掉正在蓄力的低龙数高分分支
        map<int, vector<const Cand*>> buckets;
        for (const Cand& c : cands) buckets[c.count].push_back(&c);

        int per_bucket = std::max(1, (int)(beam_width * 1.25 / std::max(1, (int)buckets.size())));
        vector<State> next_level;

        for (auto it = buckets.rbegin(); it != buckets.rend(); ++it) {
            auto& bstates = it->second;
            std::stable_sort(bstates.begin(), bstates.end(),
                             [](const Cand* a, const Cand* b) {
                                 if (a->total != b->total) return a->total > b->total;
                                 return a->mana > b->mana;
                             });
            vector<const Cand*> selected;
            for (int i = 0; i < per_bucket && i < (int)bstates.size(); i++) selected.push_back(bstates[i]);
            if (!bstates.empty()) {
                // 冠军：子链覆盖度优先，其次总分，其次法力
                const Cand* champ = bstates[0];
                int champ_coverage = champ->coverage;
                int champ_total = champ->total;
                int champ_mana = champ->mana;
                for (const Cand* bs : bstates) {
                    if (std::tie(bs->coverage, bs->total, bs->mana) >
                        std::tie(champ_coverage, champ_total, champ_mana)) {
                        champ = bs;
                        champ_coverage = bs->coverage;
                        champ_total = bs->total;
                        champ_mana = bs->mana;
                    }
                }
                bool found = false;
                for (const Cand* sel : selected) {
                    if (sel->key == champ->key) { found = true; break; }
                }
                if (!found) selected.push_back(champ);
            }
            for (const Cand* s : selected) next_level.push_back(s->s);
        }

        if ((int)next_level.size() > beam_width) next_level.resize(beam_width);
        level = std::move(next_level);
    }

    res.expansions = expansions;
    res.reached_depth = reached_depth;
    res.max_damage = max_damage;

    vector<BeamPath> collected;
    for (auto& kv : best) {
        if (kv.first >= min_alex && kv.first <= max_alex) collected.push_back(kv.second.p);
    }
    std::stable_sort(collected.begin(), collected.end(),
                     [](const BeamPath& a, const BeamPath& b) {
                         if (a.alex_damage != b.alex_damage) return a.alex_damage > b.alex_damage;
                         if (a.alex_play_count != b.alex_play_count) return a.alex_play_count > b.alex_play_count;
                         if (a.path.size() != b.path.size()) return a.path.size() > b.path.size();
                         if (a.mana != b.mana) return a.mana > b.mana;
                         if (a.initial_mana_crystals != b.initial_mana_crystals)
                             return a.initial_mana_crystals < b.initial_mana_crystals;
                         return a.initial_mana < b.initial_mana;
                     });
    if ((int)collected.size() > max_paths) collected.resize(max_paths);
    res.paths = std::move(collected);
    for (const auto& p : res.paths) {
        if (p.alex_play_count > res.max_dragons) res.max_dragons = p.alex_play_count;
    }
    return res;
}

// ===================== 简易 JSON =====================
struct JVal {
    enum Type { NUL, BOOL, NUM, STR, ARR, OBJ } type = NUL;
    bool b = false;
    long long num = 0;
    string str;
    vector<JVal> arr;
    vector<pair<string, JVal>> obj;

    const JVal* find(const string& k) const {
        if (type != OBJ) return nullptr;
        for (const auto& kv : obj)
            if (kv.first == k) return &kv.second;
        return nullptr;
    }
    long long get_int(const string& k, long long dflt) const {
        const JVal* v = find(k);
        if (!v || v->type != NUM) return dflt;
        return v->num;
    }
    string get_str(const string& k, const string& dflt = "") const {
        const JVal* v = find(k);
        if (!v || v->type != STR) return dflt;
        return v->str;
    }
};

static void json_skip_ws(const string& s, size_t& i) {
    while (i < s.size() && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) i++;
}

static bool json_parse_string(const string& s, size_t& i, string& out) {
    if (i >= s.size() || s[i] != '"') return false;
    i++;
    out.clear();
    while (i < s.size()) {
        char c = s[i];
        if (c == '"') { i++; return true; }
        if (c == '\\') {
            if (i + 1 >= s.size()) return false;
            char n = s[i + 1];
            if (n == '"' || n == '\\' || n == '/') { out.push_back(n); i += 2; }
            else if (n == 'n') { out.push_back('\n'); i += 2; }
            else if (n == 't') { out.push_back('\t'); i += 2; }
            else if (n == 'r') { out.push_back('\r'); i += 2; }
            else if (n == 'u') {
                // 仅支持 ASCII 转义（我方生成端 ensure_ascii=False 不会产生 \u）
                out += "?";
                i += 6;
            } else return false;
        } else {
            out.push_back(c);
            i++;
        }
    }
    return false;
}

static bool json_parse_value(const string& s, size_t& i, JVal& v);

static bool json_parse_array(const string& s, size_t& i, JVal& v) {
    v.type = JVal::ARR;
    i++;  // [
    while (true) {
        json_skip_ws(s, i);
        if (i < s.size() && s[i] == ']') { i++; return true; }
        JVal item;
        if (!json_parse_value(s, i, item)) return false;
        v.arr.push_back(item);
        json_skip_ws(s, i);
        if (i < s.size() && s[i] == ',') { i++; continue; }
        if (i < s.size() && s[i] == ']') { i++; return true; }
        return false;
    }
}

static bool json_parse_object(const string& s, size_t& i, JVal& v) {
    v.type = JVal::OBJ;
    i++;  // {
    while (true) {
        json_skip_ws(s, i);
        if (i < s.size() && s[i] == '}') { i++; return true; }
        string key;
        if (!json_parse_string(s, i, key)) return false;
        json_skip_ws(s, i);
        if (i >= s.size() || s[i] != ':') return false;
        i++;
        JVal item;
        if (!json_parse_value(s, i, item)) return false;
        v.obj.push_back({key, item});
        json_skip_ws(s, i);
        if (i < s.size() && s[i] == ',') { i++; continue; }
        if (i < s.size() && s[i] == '}') { i++; return true; }
        return false;
    }
}

static bool json_parse_value(const string& s, size_t& i, JVal& v) {
    json_skip_ws(s, i);
    if (i >= s.size()) return false;
    char c = s[i];
    if (c == '"') return json_parse_string(s, i, v.str) ? (v.type = JVal::STR, true) : false;
    if (c == '[') return json_parse_array(s, i, v);
    if (c == '{') return json_parse_object(s, i, v);
    if (c == 't') {
        if (s.compare(i, 4, "true") == 0) { v.type = JVal::BOOL; v.b = true; i += 4; return true; }
        return false;
    }
    if (c == 'f') {
        if (s.compare(i, 5, "false") == 0) { v.type = JVal::BOOL; v.b = false; i += 5; return true; }
        return false;
    }
    if (c == 'n') {
        if (s.compare(i, 4, "null") == 0) { v.type = JVal::NUL; i += 4; return true; }
        return false;
    }
    // 数字
    size_t start = i;
    while (i < s.size() && (isdigit((unsigned char)s[i]) || s[i] == '-' || s[i] == '.' || s[i] == 'e' || s[i] == 'E' || s[i] == '+')) i++;
    if (i == start) return false;
    v.type = JVal::NUM;
    v.num = atoll(s.substr(start, i - start).c_str());
    return true;
}

static bool parse_json(const string& s, JVal& root) {
    size_t i = 0;
    return json_parse_value(s, i, root);
}

static string json_escape(const string& s) {
    string out;
    out.reserve(s.size() + 8);
    for (char c : s) {
        if (c == '"' || c == '\\') { out.push_back('\\'); out.push_back(c); }
        else if (c == '\n') out += "\\n";
        else if (c == '\r') out += "\\r";
        else if (c == '\t') out += "\\t";
        else out.push_back(c);
    }
    return out;
}

static void print_json_result(const BeamResult& res, int max_alex, int min_alex) {
    printf("{\n");
    printf("  \"expansions\": %d,\n", res.expansions);
    printf("  \"depth\": %d,\n", res.reached_depth);
    printf("  \"max_damage\": %d,\n", res.max_damage);
    printf("  \"max_dragons\": %d,\n", res.max_dragons);
    printf("  \"results\": [\n");
    for (size_t i = 0; i < res.paths.size(); i++) {
        const BeamPath& p = res.paths[i];
        printf("    {\"dragons\": %d, \"damage\": %d, \"mana\": %d, \"path\": [",
               p.alex_play_count, p.alex_damage, p.mana);
        for (size_t j = 0; j < p.path.size(); j++) {
            if (j) printf(", ");
            printf("\"%s\"", json_escape(p.path[j]).c_str());
        }
        printf("]}%s\n", i + 1 < res.paths.size() ? "," : "");
    }
    printf("  ]\n}\n");
}

// ===================== 符号链证明引擎（C++ 移植，与 Python 逐条对齐） =====================
#include "red_dragon_chain_data.inc"

struct SymAction {
    std::string name;
    std::string target;
    std::vector<std::string> choices;
};

struct SymChain {
    std::string name;
    int target_alex_count = 0;
    std::vector<SymAction> actions;
};

struct DiscreteSubchain {
    std::string name;
    int min_alex_count = 0;
    std::vector<SymAction> required;
};

struct AlexTail {
    std::string name;
    std::vector<SymAction> actions;
    int gain = 0;
    std::map<std::string, int> hand_req;
    std::map<std::string, int> board_req;
    std::vector<std::string> band_req;
    bool any_friendly_minion = false;
    bool needs_deadly = false;
    std::map<std::string, int> hand_req_deadly;
};

struct Stats {
    std::map<std::string, int> counters;
    std::map<std::string, std::string> status;
    void inc(const std::string& k, int n = 1) { counters[k] += n; }
    void set(const std::string& k, const std::string& v) { status[k] = v; }
    void set_int(const std::string& k, long long v) { status[k] = std::to_string(v); }
};

static const std::string T_ALEX = "生命的缚誓者阿莱克丝塔萨";
static const std::string T_SHARK = "鲨鱼之灵";
static const std::string T_SHADOWCASTER = "暗影施法者";
static const std::string T_ETC = "乐队经理精英牛头人酋长";
static const std::string T_DANCE = "舞动全场（ft.迦罗娜）";
static const std::string T_POTION = "幻觉药水";
static const std::string T_SHADOWSTEP = "暗影步";
static const std::string T_PREP = "伺机待发";
static const std::string T_BONE = "锯齿骨刺";
static const std::string T_COIN_A = "幸运币";
static const std::string T_COIN_B = "伪造的幸运币";
static const std::string T_DEADLY = "殒命暗影";
static const std::string T_FOXY = "狐人老千";
static const std::string T_MOTHER = "晦鳞巢母";

static std::vector<std::string> split_choices(const char* s) {
    std::vector<std::string> out;
    if (!s || !*s) return out;
    std::string cur;
    for (const char* p = s; *p; p++) {
        if (*p == '|') {
            if (!cur.empty()) out.push_back(cur);
            cur.clear();
        } else {
            cur.push_back(*p);
        }
    }
    if (!cur.empty()) out.push_back(cur);
    return out;
}

static std::vector<SymChain>& symbolic_chains() {
    static std::vector<SymChain> out = [] {
        std::vector<SymChain> v;
        size_t n = sizeof(kChainEntries) / sizeof(kChainEntries[0]);
        v.reserve(n);
        for (size_t i = 0; i < n; i++) {
            SymChain c;
            c.name = kChainEntries[i].name;
            c.target_alex_count = kChainEntries[i].target_alex_count;
            c.actions.reserve(kChainEntries[i].action_count);
            for (int j = 0; j < kChainEntries[i].action_count; j++) {
                const ChainActionFlat& a = kChainActionPool[kChainEntries[i].action_start + j];
                SymAction sa;
                sa.name = a.name;
                sa.target = a.target;
                sa.choices = split_choices(a.choices);
                c.actions.push_back(std::move(sa));
            }
            v.push_back(std::move(c));
        }
        return v;
    }();
    return out;
}

static std::vector<DiscreteSubchain>& discrete_subchains() {
    static std::vector<DiscreteSubchain> out = [] {
        std::vector<DiscreteSubchain> v;
        size_t n = sizeof(kDiscreteEntries) / sizeof(kDiscreteEntries[0]);
        v.reserve(n);
        for (size_t i = 0; i < n; i++) {
            DiscreteSubchain d;
            d.name = kDiscreteEntries[i].name;
            d.min_alex_count = kDiscreteEntries[i].min_alex_count;
            d.required.reserve(kDiscreteEntries[i].action_count);
            for (int j = 0; j < kDiscreteEntries[i].action_count; j++) {
                const ChainActionFlat& a = kDiscreteActionPool[kDiscreteEntries[i].action_start + j];
                SymAction sa;
                sa.name = a.name;
                sa.target = a.target;
                sa.choices = split_choices(a.choices);
                d.required.push_back(std::move(sa));
            }
            v.push_back(std::move(d));
        }
        return v;
    }();
    return out;
}

static std::string canonical_path_item(const std::string& item) {
    std::string out = item;
    std::string needle = "[" + T_DEADLY + "]";
    size_t pos;
    while ((pos = out.find(needle)) != std::string::npos) {
        out.erase(pos, needle.size());
    }
    return out;
}

static bool discrete_action_matches(const SymAction& actual, const SymAction& required) {
    if (is_coin_name(required.name) && is_coin_name(actual.name)) return true;
    if (actual.name != required.name) return false;
    if (!required.target.empty() && actual.target != required.target) return false;
    if (!required.choices.empty()) {
        std::set<std::string> a(actual.choices.begin(), actual.choices.end());
        std::set<std::string> b(required.choices.begin(), required.choices.end());
        if (a != b) return false;
    }
    return true;
}

static bool chain_contains_discrete_subchain(const std::vector<SymAction>& actions,
                                             const DiscreteSubchain& subchain) {
    size_t j = 0;
    for (const SymAction& actual : actions) {
        if (discrete_action_matches(actual, subchain.required[j])) {
            j++;
            if (j == subchain.required.size()) return true;
        }
    }
    return false;
}

static std::string sym_action_label(const SymAction& a) {
    std::string label = a.name;
    if (!a.target.empty()) label += "(" + a.target + ")";
    if (!a.choices.empty()) {
        label += "（";
        for (size_t i = 0; i < a.choices.size(); i++) {
            if (i) label += "->";
            label += a.choices[i];
        }
        label += "）";
    }
    return label;
}

static bool chain_action_matches(const std::string& path_item, const SymAction& action) {
    std::string canonical = canonical_path_item(path_item);
    std::vector<std::string> candidates;
    if (is_coin_name(action.name)) {
        candidates = {T_COIN_A, T_COIN_B};
    } else {
        candidates = {action.name};
    }
    bool name_ok = false;
    for (const std::string& c : candidates) {
        if (canonical.size() >= c.size() && canonical.compare(0, c.size(), c) == 0) {
            name_ok = true;
            break;
        }
    }
    if (!name_ok) return false;
    if (!action.target.empty()) {
        std::string needle = "(" + action.target + ")";
        if (canonical.find(needle) == std::string::npos) return false;
    }
    for (const std::string& choice : action.choices) {
        if (canonical.find(choice) == std::string::npos) return false;
    }
    return true;
}

static bool board_has_name(const State& s, const std::string& name) {
    for (const Card& c : s.board) if (c.name == name) return true;
    return false;
}

static bool chain_action_already_satisfied(const State& initial, const State& st, const SymAction& a) {
    if (!a.target.empty() || !a.choices.empty()) return false;
    if (a.name == T_SHARK) return board_has_name(initial, T_SHARK) && board_has_name(st, T_SHARK);
    if (a.name == T_FOXY) return initial.next_combo > 0 && st.next_combo > 0;
    if (a.name == "斯卡布斯·刀油") {
        if (initial.oil_stacks.empty()) return false;
        for (const auto& p : st.oil_stacks) {
            if (p.first > 0 && p.second > 0) return true;
        }
        return false;
    }
    if (a.name == T_PREP) return initial.next_spell > 0 && st.next_spell > 0;
    if (a.name == T_BONE) return initial.next_card > 0 && st.next_card > 0;
    if (a.name == T_MOTHER) return board_has_name(initial, T_MOTHER) && board_has_name(st, T_MOTHER);
    return false;
}

static std::string state_key_for_dedup_str(const State& s) {
    return state_key_for_dedup(s);
}

static bool path_sort_better(const State& a, const State& b) {
    if (a.alex_damage != b.alex_damage) return a.alex_damage > b.alex_damage;
    if (a.alex_play_count != b.alex_play_count) return a.alex_play_count > b.alex_play_count;
    if (a.path.size() != b.path.size()) return a.path.size() > b.path.size();
    if (a.mana != b.mana) return a.mana > b.mana;
    if (a.initial_mana_crystals != b.initial_mana_crystals)
        return a.initial_mana_crystals < b.initial_mana_crystals;
    return a.initial_mana < b.initial_mana;
}

static void sort_path_states_cpp(std::vector<State>& v) {
    std::stable_sort(v.begin(), v.end(), path_sort_better);
}

static std::string path_key(const State& s) {
    std::string k;
    for (size_t i = 0; i < s.path.size(); i++) {
        if (i) k += "->";
        k += s.path[i];
    }
    return k;
}

// ---------- 符号链验证（含 CDCL 式 step_memo / DP 复用） ----------
struct StepMemoEntry {
    bool ok = false;
    std::vector<State> states;
};

static std::string sym_chain_key(const State& s, int action_index, int target_alex_count) {
    return state_key_for_dedup(s) + "|" + std::to_string(action_index) + "|"
        + std::to_string(std::max(0, target_alex_count - s.alex_play_count)) + "|"
        + std::to_string(s.mana);
}

static std::string step_key_str(const std::string& initial_key, const State& s, const SymAction& a, int mana) {
    std::string k = initial_key + "|" + state_key_for_dedup(s) + "|" + a.name + "|" + a.target + "|";
    for (const std::string& c : a.choices) k += c + ";";
    k += "|" + std::to_string(mana);
    return k;
}

static std::vector<State> validate_symbolic_chain(
    const State& initial_state,
    const SymChain& chain,
    int max_states,
    Stats* stats,
    std::unordered_map<std::string, StepMemoEntry>* step_memo,
    bool* should_stop) {
    std::vector<State> states = {initial_state.clone()};
    std::unordered_set<std::string> seen;
    int intermediate_limit = std::max(1, std::min(max_states, 80));
    std::string initial_key = state_key_for_dedup(initial_state);

    for (size_t ai = 0; ai < chain.actions.size(); ai++) {
        if (should_stop && *should_stop) return {};
        const SymAction& action = chain.actions[ai];
        std::vector<State> next_states;
        for (const State& st : states) {
            std::string ck = sym_chain_key(st, (int)ai, chain.target_alex_count);
            if (seen.count(ck)) {
                if (stats) stats->inc("符号链条DP复用");
                continue;
            }
            seen.insert(ck);
            std::string sk = step_key_str(initial_key, st, action, st.mana);
            if (step_memo) {
                auto it = step_memo->find(sk);
                if (it != step_memo->end()) {
                    if (!it->second.ok) {
                        if (stats) stats->inc("冲突学习剪枝");
                        continue;
                    }
                    if (stats) stats->inc("子链结果复用");
                    for (const State& cs : it->second.states) {
                        next_states.push_back(cs.clone());
                    }
                    if ((int)next_states.size() >= intermediate_limit) break;
                    continue;
                }
            }
            std::vector<State> matched;
            if (chain_action_already_satisfied(initial_state, st, action)) {
                matched.push_back(st.clone());
                if (stats) stats->inc("符号链条预启动跳步");
            } else {
                for (State& succ : generate_successors(st)) {
                    if (succ.path.empty()) continue;
                    if (chain_action_matches(succ.path.back(), action)) {
                        matched.push_back(std::move(succ));
                        if ((int)matched.size() >= intermediate_limit) break;
                    }
                }
            }
            if (step_memo && step_memo->size() < 60000) {
                StepMemoEntry e;
                e.ok = !matched.empty();
                e.states = matched;
                (*step_memo)[sk] = std::move(e);
            }
            for (const State& m : matched) next_states.push_back(m);
            if ((int)next_states.size() >= intermediate_limit) break;
        }
        if (next_states.empty()) {
            if (stats) stats->inc("链条失败：" + chain.name + " @ " + sym_action_label(action));
            return {};
        }
        sort_path_states_cpp(next_states);
        if ((int)next_states.size() > intermediate_limit) next_states.resize(intermediate_limit);
        states = std::move(next_states);
    }
    std::vector<State> out;
    for (const State& st : states) {
        if (st.alex_play_count >= chain.target_alex_count) out.push_back(st);
    }
    return out;
}

// ---------- 反向目标尾链（AlexTail / TailSim） ----------
static const std::set<std::string> DEADLY_COPY_SOURCE_SPELLS = {
    T_DANCE, T_POTION, T_SHADOWSTEP, T_PREP, T_BONE, T_COIN_A, T_COIN_B,
};

static std::pair<bool, std::map<std::string, int>> make_deadly_variant(
    const std::map<std::string, int>& hand_req) {
    std::string best_spell;
    int best_count = 0;
    for (const auto& kv : hand_req) {
        if (kv.first == T_DEADLY) continue;
        if (DEADLY_COPY_SOURCE_SPELLS.count(kv.first) && kv.second >= 2 && kv.second > best_count) {
            best_spell = kv.first;
            best_count = kv.second;
        }
    }
    if (best_spell.empty()) return {false, {}};
    std::map<std::string, int> variant = hand_req;
    variant[best_spell]--;
    if (variant[best_spell] <= 0) variant.erase(best_spell);
    variant[T_DEADLY]++;
    return {true, variant};
}

static AlexTail make_alex_tail(
    const std::string& name,
    std::vector<SymAction> actions,
    int gain,
    std::map<std::string, int> hand_req,
    std::map<std::string, int> board_req,
    std::vector<std::string> band_req,
    bool any_friendly_minion) {
    auto deadly = make_deadly_variant(hand_req);
    AlexTail t;
    t.name = name;
    t.actions = std::move(actions);
    t.gain = gain;
    t.hand_req = std::move(hand_req);
    t.board_req = std::move(board_req);
    t.band_req = std::move(band_req);
    t.any_friendly_minion = any_friendly_minion;
    t.needs_deadly = deadly.first;
    t.hand_req_deadly = std::move(deadly.second);
    return t;
}

struct TailSim {
    std::vector<SymAction> actions;
    std::map<std::string, int> hand;
    std::map<std::string, int> board;
    int alex_played = 0;
    std::map<std::string, int> hand_req;
    std::map<std::string, int> board_req;
    std::set<std::string> band_req;
    bool any_friendly_minion = false;
    bool prep_used = false;
    bool bone_used = false;

    TailSim clone() const { return *this; }

    void hand_need(const std::string& name) {
        int have = 0;
        auto it = hand.find(name);
        if (it != hand.end()) have = it->second;
        hand_req[name] = std::max(hand_req[name], 1 - have);
    }
    void board_need(const std::string& name) {
        int have = 0;
        auto it = board.find(name);
        if (it != board.end()) have = it->second;
        board_req[name] = std::max(board_req[name], 1 - have);
    }
    void play_minion(const std::string& name) {
        hand_need(name);
        hand[name]--;
        board[name]++;
    }
    void play_spell(const std::string& name) {
        hand_need(name);
        hand[name]--;
    }
    void add_hand(const std::string& name, int count = 1) {
        hand[name] += count;
    }
    void remove_board(const std::string& name) {
        board[name]--;
    }
    AlexTail to_tail(int index) {
        std::map<std::string, int> hr;
        for (const auto& kv : hand_req) if (kv.second > 0) hr[kv.first] = kv.second;
        std::map<std::string, int> br;
        for (const auto& kv : board_req) if (kv.second > 0) br[kv.first] = kv.second;
        std::vector<std::string> band(band_req.begin(), band_req.end());
        return make_alex_tail("反向目标尾链-" + std::to_string(index), actions, alex_played,
                              hr, br, band, any_friendly_minion);
    }
};

static int tail_direct(TailSim& sim) {
    sim.actions.push_back({T_ALEX, "", {}});
    sim.play_minion(T_ALEX);
    sim.alex_played++;
    return 1;
}

static int tail_shadowcaster(TailSim& sim, bool doubled) {
    sim.actions.push_back({T_SHADOWCASTER, T_ALEX, {}});
    sim.board_need(T_ALEX);
    sim.play_minion(T_SHADOWCASTER);
    int copies = doubled ? 2 : 1;
    if (doubled) sim.board_need(T_SHARK);
    sim.add_hand(T_ALEX, copies);
    for (int i = 0; i < copies; i++) {
        sim.actions.push_back({T_ALEX, "", {}});
        sim.play_minion(T_ALEX);
        sim.alex_played++;
    }
    return copies;
}

static int tail_shadowstep(TailSim& sim) {
    sim.actions.push_back({T_SHADOWSTEP, T_ALEX, {}});
    sim.board_need(T_ALEX);
    sim.play_spell(T_SHADOWSTEP);
    sim.remove_board(T_ALEX);
    sim.add_hand(T_ALEX);
    sim.actions.push_back({T_ALEX, "", {}});
    sim.play_minion(T_ALEX);
    sim.alex_played++;
    return 1;
}

static int tail_dance(TailSim& sim) {
    if (sim.board.empty()) return -1;
    sim.actions.push_back({T_DANCE, "", {}});
    sim.board_need(T_ALEX);
    sim.play_spell(T_DANCE);
    for (auto& kv : sim.board) {
        if (kv.second > 0) {
            sim.add_hand(kv.first, kv.second);
            kv.second = 0;
        }
    }
    sim.actions.push_back({T_ALEX, "", {}});
    sim.play_minion(T_ALEX);
    sim.alex_played++;
    return 1;
}

static int tail_potion(TailSim& sim) {
    sim.actions.push_back({T_POTION, "", {}});
    sim.board_need(T_ALEX);
    sim.play_spell(T_POTION);
    sim.add_hand(T_ALEX);
    sim.actions.push_back({T_ALEX, "", {}});
    sim.play_minion(T_ALEX);
    sim.alex_played++;
    return 1;
}

static int tail_etc(TailSim& sim, bool potion_variant) {
    std::string choice = potion_variant ? T_POTION : T_DANCE;
    std::vector<std::string> choices = {choice, T_ALEX};
    sim.actions.push_back({T_ETC, "", choices});
    sim.board_need(T_SHARK);
    sim.play_minion(T_ETC);
    sim.band_req.insert(choices.begin(), choices.end());
    sim.add_hand(choice);
    sim.add_hand(T_ALEX);
    sim.actions.push_back({T_ALEX, "", {}});
    sim.play_minion(T_ALEX);
    sim.alex_played++;
    return 1;
}

static int tail_prep(TailSim& sim) {
    if (sim.prep_used) return -1;
    sim.actions.push_back({T_PREP, "", {}});
    sim.play_spell(T_PREP);
    sim.prep_used = true;
    return 0;
}

static int tail_bone(TailSim& sim) {
    if (sim.bone_used) return -1;
    sim.actions.push_back({T_BONE, "", {}});
    sim.play_spell(T_BONE);
    sim.any_friendly_minion = true;
    sim.bone_used = true;
    return 0;
}

static std::string tail_actions_key(const std::vector<SymAction>& actions) {
    std::string k;
    for (const SymAction& a : actions) {
        k += a.name + "|" + a.target + "|";
        for (const std::string& c : a.choices) k += c + ";";
        k += "#";
    }
    return k;
}

static std::vector<AlexTail> generate_alex_tails(
    int max_gain, int max_actions = 12, int max_tails_per_gain = 150, int max_tails = 1500) {
    std::map<std::string, AlexTail> tails;
    int tail_index = 0;
    int visits = 0;
    std::map<int, int> gain_counts;

    std::function<void(TailSim, int)> rec = [&](TailSim sim, int remaining) {
        visits++;
        if (visits > 80000) return;
        if (remaining <= 0) {
            AlexTail tail = sim.to_tail(tail_index++);
            std::string key = tail_actions_key(tail.actions);
            for (const auto& kv : tail.hand_req) key += "h" + kv.first + ":" + std::to_string(kv.second);
            for (const auto& kv : tail.board_req) key += "b" + kv.first + ":" + std::to_string(kv.second);
            for (const std::string& b : tail.band_req) key += "B" + b;
            key += std::string("m") + (tail.any_friendly_minion ? "1" : "0");
            if (tails.count(key)) return;
            if (gain_counts[tail.gain] >= max_tails_per_gain) return;
            tails[key] = std::move(tail);
            gain_counts[tail.gain]++;
            return;
        }
        if ((int)tails.size() >= max_tails) return;
        if ((int)sim.actions.size() >= max_actions) return;

        struct Opt { std::function<int(TailSim&)> fn; };
        std::vector<Opt> gain_opts = {
            {[](TailSim& s) { return tail_direct(s); }},
            {[](TailSim& s) { return tail_shadowcaster(s, false); }},
            {[](TailSim& s) { return tail_shadowcaster(s, true); }},
            {[](TailSim& s) { return tail_shadowstep(s); }},
            {[](TailSim& s) { return tail_dance(s); }},
            {[](TailSim& s) { return tail_potion(s); }},
            {[](TailSim& s) { return tail_etc(s, false); }},
            {[](TailSim& s) { return tail_etc(s, true); }},
        };
        for (auto& o : gain_opts) {
            TailSim next = sim.clone();
            int gain = o.fn(next);
            if (gain <= 0 || gain > remaining) continue;
            rec(std::move(next), remaining - gain);
        }
        std::vector<Opt> prep_opts = {
            {[](TailSim& s) { return tail_prep(s); }},
            {[](TailSim& s) { return tail_bone(s); }},
        };
        for (auto& o : prep_opts) {
            TailSim next = sim.clone();
            int gain = o.fn(next);
            if (gain < 0) continue;
            rec(std::move(next), remaining);
        }
    };

    for (int target_gain = 1; target_gain <= max_gain; target_gain++) {
        rec(TailSim(), target_gain);
    }
    std::vector<AlexTail> result;
    result.reserve(tails.size());
    for (auto& kv : tails) result.push_back(std::move(kv.second));
    return result;
}

// ---------- 模板子链库（起手段 + 尾段） ----------
static bool sim_apply_template_action(TailSim& sim, const SymAction& action) {
    const std::string& name = action.name;
    if (name == T_ALEX) {
        sim.play_minion(T_ALEX);
        sim.alex_played++;
        return true;
    }
    if (name == T_SHADOWCASTER && action.target == T_ALEX) {
        sim.board_need(T_ALEX);
        sim.play_minion(T_SHADOWCASTER);
        int copies = sim.board.count(T_SHARK) && sim.board[T_SHARK] > 0 ? 2 : 1;
        sim.add_hand(T_ALEX, copies);
        return true;
    }
    if (name == T_SHADOWSTEP && action.target == T_ALEX) {
        sim.board_need(T_ALEX);
        sim.play_spell(T_SHADOWSTEP);
        sim.remove_board(T_ALEX);
        sim.add_hand(T_ALEX);
        return true;
    }
    if (name == T_DANCE) {
        sim.play_spell(T_DANCE);
        for (auto& kv : sim.board) {
            if (kv.second > 0) {
                sim.add_hand(kv.first, kv.second);
                kv.second = 0;
            }
        }
        return true;
    }
    if (name == T_POTION) {
        sim.play_spell(T_POTION);
        if (sim.board.count(T_ALEX) && sim.board[T_ALEX] > 0) sim.add_hand(T_ALEX);
        return true;
    }
    if (name == T_ETC) {
        if (!action.choices.empty()) {
            sim.board_need(T_SHARK);
            sim.play_minion(T_ETC);
            sim.band_req.insert(action.choices.begin(), action.choices.end());
            for (const std::string& c : action.choices) sim.add_hand(c);
            return true;
        }
        sim.play_minion(T_ETC);
        return true;
    }
    if (name == T_PREP) {
        sim.play_spell(T_PREP);
        return true;
    }
    if (name == T_BONE) {
        sim.play_spell(T_BONE);
        sim.any_friendly_minion = true;
        return true;
    }
    if (name == T_SHARK) {
        sim.play_minion(T_SHARK);
        return true;
    }
    auto it = DB.find(name);
    if (it == DB.end()) return false;
    if (it->second.card_type == "minion") sim.play_minion(name);
    else sim.play_spell(name);
    return true;
}

static std::vector<AlexTail> augment_setup_subchains(
    std::vector<AlexTail> setup_subchains, int max_actions = 14) {
    const std::vector<std::pair<std::string, std::string>> coin_pairs = {
        {T_COIN_A, T_COIN_A},
        {T_COIN_A, T_COIN_B},
        {T_COIN_B, T_COIN_A},
        {T_COIN_B, T_COIN_B},
    };
    std::vector<AlexTail> result = setup_subchains;
    std::set<std::string> existing_keys;
    for (const AlexTail& sub : result) {
        existing_keys.insert(tail_actions_key(sub.actions));
    }
    int index = (int)result.size() + 1;
    for (const AlexTail& sub : setup_subchains) {
        if (sub.actions.empty()) continue;
        const std::string& first_name = sub.actions[0].name;
        if (is_coin_name(first_name)) continue;
        if (first_name != T_SHARK) continue;
        if ((int)sub.actions.size() + 2 > max_actions) continue;
        for (const auto& cp : coin_pairs) {
            std::vector<SymAction> actions;
            actions.push_back({cp.first, "", {}});
            actions.push_back({cp.second, "", {}});
            for (const SymAction& a : sub.actions) actions.push_back(a);
            std::string key = tail_actions_key(actions);
            if (existing_keys.count(key)) continue;
            existing_keys.insert(key);
            std::map<std::string, int> hand_req = sub.hand_req;
            hand_req[T_COIN_A] += 2;
            result.push_back(make_alex_tail(
                "模板起手段-币币鱼-" + std::to_string(index),
                std::move(actions), sub.gain, std::move(hand_req), sub.board_req,
                sub.band_req, sub.any_friendly_minion));
            index++;
        }
    }
    return result;
}

static std::pair<std::vector<AlexTail>, std::vector<AlexTail>> build_template_subchain_library(
    int max_gain) {
    std::map<std::string, AlexTail> setup_chains;
    std::map<std::string, AlexTail> tail_chains;
    int setup_index = 0;
    int tail_index = 0;

    for (int target = 1; target <= max_gain; target++) {
        for (const SymChain& chain : symbolic_chains()) {
            if (chain.target_alex_count != target) continue;
            size_t first_alex = std::string::npos;
            for (size_t i = 0; i < chain.actions.size(); i++) {
                if (chain.actions[i].name == T_ALEX) {
                    first_alex = i;
                    break;
                }
            }
            if (first_alex == std::string::npos) continue;
            std::vector<SymAction> setup_actions(chain.actions.begin(), chain.actions.begin() + first_alex);
            std::vector<SymAction> tail_actions(chain.actions.begin() + first_alex, chain.actions.end());

            if (!setup_actions.empty()) {
                TailSim sim;
                bool ok = true;
                for (const SymAction& a : setup_actions) {
                    if (!sim_apply_template_action(sim, a)) { ok = false; break; }
                }
                if (ok) {
                    setup_index++;
                    std::string key = tail_actions_key(setup_actions);
                    if (!setup_chains.count(key)) {
                        std::map<std::string, int> hr;
                        for (const auto& kv : sim.hand_req) if (kv.second > 0) hr[kv.first] = kv.second;
                        std::map<std::string, int> br;
                        for (const auto& kv : sim.board_req) if (kv.second > 0) br[kv.first] = kv.second;
                        std::vector<std::string> band(sim.band_req.begin(), sim.band_req.end());
                        setup_chains[key] = make_alex_tail(
                            "模板起手段-" + std::to_string(setup_index), setup_actions, 0,
                            std::move(hr), std::move(br), std::move(band), sim.any_friendly_minion);
                    }
                }
            }

            TailSim sim;
            bool ok = true;
            for (const SymAction& a : tail_actions) {
                if (!sim_apply_template_action(sim, a)) { ok = false; break; }
            }
            if (ok) {
                tail_index++;
                std::string key = tail_actions_key(tail_actions) + "#" + std::to_string(sim.alex_played);
                if (!tail_chains.count(key)) {
                    std::map<std::string, int> hr;
                    for (const auto& kv : sim.hand_req) if (kv.second > 0) hr[kv.first] = kv.second;
                    std::map<std::string, int> br;
                    for (const auto& kv : sim.board_req) if (kv.second > 0) br[kv.first] = kv.second;
                    std::vector<std::string> band(sim.band_req.begin(), sim.band_req.end());
                    tail_chains[key] = make_alex_tail(
                        "模板尾段-" + std::to_string(tail_index), tail_actions, sim.alex_played,
                        std::move(hr), std::move(br), std::move(band), sim.any_friendly_minion);
                }
            }
        }
    }
    std::vector<AlexTail> setup_list;
    for (auto& kv : setup_chains) setup_list.push_back(std::move(kv.second));
    std::vector<AlexTail> tail_list;
    for (auto& kv : tail_chains) tail_list.push_back(std::move(kv.second));
    return {augment_setup_subchains(std::move(setup_list)), std::move(tail_list)};
}

// ---------- 引理前提（数学引理式：资源 / 费用 / 目标契合 / 殒命替代） ----------
static int hand_count_with_coin_equivalence(const std::map<std::string, int>& hand_counts,
                                            const std::string& name) {
    if (is_coin_name(name)) {
        int a = 0, b = 0;
        auto it = hand_counts.find(T_COIN_A);
        if (it != hand_counts.end()) a = it->second;
        it = hand_counts.find(T_COIN_B);
        if (it != hand_counts.end()) b = it->second;
        return a + b;
    }
    auto it = hand_counts.find(name);
    return it == hand_counts.end() ? 0 : it->second;
}

static bool lemma_constraint_rank_better(const State& base_state,
                                         const AlexTail& a, const AlexTail& b) {
    std::map<std::string, int> hand_counts;
    for (const Card& c : base_state.hand) hand_counts[c.name]++;
    auto rank = [&](const AlexTail& t) -> std::tuple<long long, long long, long long> {
        long long satisfied = 0;
        for (const auto& kv : t.hand_req) {
            satisfied += std::min((long long)hand_count_with_coin_equivalence(hand_counts, kv.first),
                                  (long long)kv.second);
        }
        long long total = 0;
        for (const auto& kv : t.hand_req) total += kv.second;
        if (total == 0) total = 1;
        return {satisfied, total, 0};
    };
    auto ra = rank(a);
    auto rb = rank(b);
    // -satisfied/total 更小者优先 → satisfied/total 更大者优先
    if (std::get<0>(ra) * std::get<1>(rb) != std::get<0>(rb) * std::get<1>(ra))
        return std::get<0>(ra) * std::get<1>(rb) > std::get<0>(rb) * std::get<1>(ra);
    if (a.hand_req.size() != b.hand_req.size()) return a.hand_req.size() < b.hand_req.size();
    return a.actions.size() < b.actions.size();
}

static bool hand_req_met(const std::map<std::string, int>& hand_counts,
                         const std::map<std::string, int>& req,
                         const std::string& skip_name = "") {
    for (const auto& kv : req) {
        int required = kv.second;
        if (kv.first == skip_name) required = std::max(0, required - 1);
        if (hand_count_with_coin_equivalence(hand_counts, kv.first) < required) return false;
    }
    return true;
}

static std::tuple<int, bool, bool> tail_cost_profile(const std::string& name) {
    auto it = DB.find(name);
    if (it == DB.end()) return {0, false, false};
    int base = it->second.cost;
    if (base < 0) base = 0;
    bool is_spell = it->second.card_type == "spell" || it->second.card_type == "secret";
    return {base, is_spell, it->second.combo};
}

static bool tail_mana_feasible(const State& state, const AlexTail& tail) {
    int mana = state.mana;
    int next_spell = state.next_spell;
    int next_combo = state.next_combo;
    int next_card = state.next_card;
    std::vector<std::pair<int, int>> actives;
    for (const auto& p : state.oil_stacks) {
        if (p.first > 0 && p.second > 0) actives.push_back(p);
    }
    for (const SymAction& action : tail.actions) {
        if (!DB.count(action.name)) continue;
        if (is_coin_name(action.name)) mana += 1;
        auto profile = tail_cost_profile(action.name);
        int base_cost = std::get<0>(profile);
        bool is_spell = std::get<1>(profile);
        bool is_combo = std::get<2>(profile);
        int discount = next_card;
        for (const auto& p : actives) discount += p.second;
        if (is_spell) discount += next_spell;
        if (is_combo) discount += next_combo;
        int cost = std::max(0, base_cost - discount);
        if (mana < cost) return false;
        mana -= cost;
        next_card = 0;
        std::vector<std::pair<int, int>> new_actives;
        for (const auto& p : actives) {
            if (p.first - 1 > 0) new_actives.push_back({p.first - 1, p.second});
        }
        actives = std::move(new_actives);
        if (is_spell) next_spell = 0;
        if (is_combo) next_combo = 0;
        if (action.name == T_PREP) next_spell += 2;
        else if (action.name == T_BONE) next_card += 2;
    }
    return true;
}

static bool lemma_prestart_skipped(const State& base_state, const AlexTail& lemma) {
    return !lemma.actions.empty() &&
           chain_action_already_satisfied(base_state, base_state, lemma.actions[0]);
}

static bool lemma_meets_state(const State& base_state, const AlexTail& lemma) {
    std::map<std::string, int> hand_counts;
    for (const Card& c : base_state.hand) hand_counts[c.name]++;
    std::map<std::string, int> board_counts;
    for (const Card& c : base_state.board) board_counts[c.name]++;
    std::string skip_name;
    if (lemma_prestart_skipped(base_state, lemma)) skip_name = lemma.actions[0].name;
    if (!hand_req_met(hand_counts, lemma.hand_req, skip_name)) {
        if (!(lemma.needs_deadly && hand_req_met(hand_counts, lemma.hand_req_deadly, skip_name))) {
            return false;
        }
    }
    for (const auto& kv : lemma.board_req) {
        auto it = board_counts.find(kv.first);
        if (it == board_counts.end() || it->second < kv.second) return false;
    }
    if (!lemma.band_req.empty()) {
        std::set<std::string> band(base_state.etc_band.begin(), base_state.etc_band.end());
        for (const std::string& b : lemma.band_req) {
            if (!band.count(b)) return false;
        }
    }
    if (lemma.any_friendly_minion && base_state.board.empty()) return false;
    return true;
}

static bool lemma_mana_feasible(const State& base_state, const AlexTail& lemma) {
    std::vector<SymAction> actions = lemma.actions;
    if (lemma_prestart_skipped(base_state, lemma) && !actions.empty()) {
        actions.erase(actions.begin());
    }
    if (actions.empty()) return true;
    AlexTail proxy;
    proxy.name = lemma.name;
    proxy.actions = std::move(actions);
    proxy.gain = lemma.gain;
    proxy.hand_req = lemma.hand_req;
    proxy.board_req = lemma.board_req;
    proxy.band_req = lemma.band_req;
    proxy.any_friendly_minion = lemma.any_friendly_minion;
    return tail_mana_feasible(base_state, proxy);
}

// ---------- 双向符号链证明（前向算子/引理交错 + 反向尾链拼接） ----------
static std::vector<State> bidirectional_symbolic_prove_paths(
    const State& initial_state,
    int max_alex_count,
    int min_alex_count,
    int max_paths,
    int max_chain_steps,
    int forward_depth,
    Stats* stats,
    std::unordered_map<std::string, StepMemoEntry>* step_memo,
    bool* should_stop) {
    std::vector<State> all_proved;
    std::unordered_set<std::string> seen_paths;

    int max_gain = std::min(max_alex_count, 6);
    std::vector<AlexTail> generated_tails = generate_alex_tails(max_gain);
    auto lib = build_template_subchain_library(max_gain);
    std::vector<AlexTail> setup_subchains = std::move(lib.first);
    std::vector<AlexTail> template_tails = std::move(lib.second);

    std::stable_sort(setup_subchains.begin(), setup_subchains.end(),
        [&](const AlexTail& a, const AlexTail& b) {
            return lemma_constraint_rank_better(initial_state, a, b);
        });

    std::vector<AlexTail> tails = std::move(generated_tails);
    for (AlexTail& t : template_tails) {
        if (t.gain > 0) tails.push_back(std::move(t));
    }
    std::map<std::string, AlexTail> unique_tail_map;
    for (AlexTail& t : tails) {
        unique_tail_map[tail_actions_key(t.actions)] = std::move(t);
    }
    tails.clear();
    for (auto& kv : unique_tail_map) tails.push_back(std::move(kv.second));

    std::map<int, std::vector<const AlexTail*>> tails_by_gain;
    for (const AlexTail& t : tails) tails_by_gain[t.gain].push_back(&t);

    std::vector<State> lemma_pool;
    std::unordered_set<std::string> seen_lemma_keys;
    const int max_lemma_states = 400;
    const int max_lemma_actions = 14;
    int lemma_apply_count = 0;

    auto apply_forward_lemma = [&](const State& base_state, const AlexTail& lemma) {
        if ((int)lemma_pool.size() >= max_lemma_states) return;
        if ((int)lemma.actions.size() > max_lemma_actions) return;
        if ((int)base_state.path.size() + (int)lemma.actions.size() > max_chain_steps) return;
        if (!lemma_meets_state(base_state, lemma)) return;
        if (!lemma_mana_feasible(base_state, lemma)) return;
        if (lemma.gain > 0 && base_state.alex_play_count + lemma.gain > max_alex_count) return;
        lemma_apply_count++;
        SymChain lemma_chain;
        lemma_chain.name = "前向引理-" + lemma.name;
        lemma_chain.target_alex_count = lemma.gain;
        lemma_chain.actions = lemma.actions;
        std::vector<State> lemma_states = validate_symbolic_chain(
            base_state, lemma_chain, std::max(1, max_paths - (int)all_proved.size()),
            stats, step_memo, should_stop);
        sort_path_states_cpp(lemma_states);
        for (State& ls : lemma_states) {
            std::string lk = path_key(ls);
            if (seen_lemma_keys.count(lk)) continue;
            seen_lemma_keys.insert(lk);
            lemma_pool.push_back(std::move(ls));
            if ((int)lemma_pool.size() >= max_lemma_states) break;
        }
    };

    std::vector<State> level = {initial_state.clone()};
    std::vector<State> collected = {initial_state.clone()};
    std::unordered_set<std::string> seen_frontier_keys = {
        state_key_for_dedup(initial_state)
    };
    const int forward_beam_width = 1500;

    for (int d = 1; d <= forward_depth; d++) {
        if (should_stop && *should_stop) break;
        size_t lemma_pool_before = lemma_pool.size();
        for (const State& base_state : level) {
            if ((int)lemma_pool.size() >= max_lemma_states) break;
            for (const AlexTail& lemma : setup_subchains) {
                if ((int)lemma_pool.size() >= max_lemma_states) break;
                apply_forward_lemma(base_state, lemma);
            }
        }
        for (size_t i = lemma_pool_before; i < lemma_pool.size(); i++) {
            level.push_back(lemma_pool[i]);
        }
        std::vector<State> next_level;
        std::unordered_set<std::string> next_seen;
        for (const State& st : level) {
            if (st.alex_play_count >= max_alex_count) continue;
            for (State& succ : generate_successors(st)) {
                std::string key = state_key_for_dedup(succ);
                if (seen_frontier_keys.count(key) || next_seen.count(key)) continue;
                next_seen.insert(key);
                next_level.push_back(std::move(succ));
            }
        }
        if (next_level.empty()) break;
        std::stable_sort(next_level.begin(), next_level.end(),
            [](const State& a, const State& b) {
                if (a.alex_play_count != b.alex_play_count) return a.alex_play_count > b.alex_play_count;
                return a.mana > b.mana;
            });
        if ((int)next_level.size() > forward_beam_width) next_level.resize(forward_beam_width);
        level = std::move(next_level);
        for (const auto& k : next_seen) seen_frontier_keys.insert(k);
        int keep = std::max(1, forward_beam_width / 4);
        for (int i = 0; i < keep && i < (int)level.size(); i++) {
            collected.push_back(level[i]);
        }
    }

    if (stats) {
        stats->set_int("双向前向探索状态数", (int)collected.size());
        stats->set_int("前向引理尝试次数", lemma_apply_count);
        stats->set_int("前向引理状态数", (int)lemma_pool.size());
    }

    std::vector<std::pair<State, int>> pool;
    for (const State& s : collected) pool.push_back({s, 0});
    for (const State& s : lemma_pool) pool.push_back({s, 1});
    pool.push_back({initial_state.clone(), 1});

    std::vector<std::map<std::string, int>> pool_hand_counts;
    std::vector<std::map<std::string, int>> pool_board_counts;
    std::vector<std::set<std::string>> pool_band_sets;
    std::vector<bool> pool_has_board;
    for (const auto& pr : pool) {
        const State& s = pr.first;
        std::map<std::string, int> hc, bc;
        for (const Card& c : s.hand) hc[c.name]++;
        for (const Card& c : s.board) bc[c.name]++;
        pool_hand_counts.push_back(std::move(hc));
        pool_board_counts.push_back(std::move(bc));
        pool_band_sets.emplace_back(s.etc_band.begin(), s.etc_band.end());
        pool_has_board.push_back(!s.board.empty());
    }

    auto add_state = [&](State&& chain_state) -> bool {
        std::string pk = path_key(chain_state);
        if (seen_paths.count(pk)) return false;
        seen_paths.insert(pk);
        all_proved.push_back(std::move(chain_state));
        return true;
    };

    // 1) 前向直接命中
    for (const auto& pr : pool) {
        const State& s = pr.first;
        if (s.alex_play_count >= min_alex_count) {
            add_state(s.clone());
            if ((int)all_proved.size() >= max_paths) break;
        }
    }

    // 2) 前沿拼接（反向尾链）
    auto& discrete_lib = discrete_subchains();
    for (int target = max_alex_count; target >= min_alex_count; target--) {
        if ((int)all_proved.size() >= max_paths) break;
        if (should_stop && *should_stop) break;
        std::vector<const DiscreteSubchain*> relevant;
        for (const DiscreteSubchain& sc : discrete_lib) {
            if (sc.min_alex_count <= target) relevant.push_back(&sc);
        }
        struct Cand {
            const State* state;
            const AlexTail* tail;
            int priority;
            int hits;
        };
        std::vector<Cand> candidates;
        for (size_t idx = 0; idx < pool.size(); idx++) {
            const State& st = pool[idx].first;
            int priority = pool[idx].second;
            if (st.alex_play_count >= target) continue;
            int needed = target - st.alex_play_count;
            if (needed <= 0) continue;
            auto tit = tails_by_gain.find(needed);
            if (tit == tails_by_gain.end()) continue;
            for (const AlexTail* tail : tit->second) {
                bool band_ok = tail->band_req.empty();
                if (!band_ok) {
                    band_ok = true;
                    for (const std::string& b : tail->band_req) {
                        if (!pool_band_sets[idx].count(b)) { band_ok = false; break; }
                    }
                }
                if (!band_ok) continue;
                if (tail->any_friendly_minion && !pool_has_board[idx]) continue;
                bool hand_ok = true;
                for (const auto& kv : tail->hand_req) {
                    if (hand_count_with_coin_equivalence(pool_hand_counts[idx], kv.first) < kv.second) {
                        hand_ok = false;
                        break;
                    }
                }
                if (!hand_ok) continue;
                bool board_ok = true;
                for (const auto& kv : tail->board_req) {
                    auto it = pool_board_counts[idx].find(kv.first);
                    if (it == pool_board_counts[idx].end() || it->second < kv.second) {
                        board_ok = false;
                        break;
                    }
                }
                if (!board_ok) continue;
                if (!tail_mana_feasible(st, *tail)) continue;
                int hits = 0;
                for (const DiscreteSubchain* sc : relevant) {
                    if (chain_contains_discrete_subchain(tail->actions, *sc)) hits++;
                }
                candidates.push_back({&st, tail, priority, hits});
            }
        }
        std::stable_sort(candidates.begin(), candidates.end(),
            [](const Cand& a, const Cand& b) {
                if (a.hits != b.hits) return a.hits > b.hits;
                if (a.priority != b.priority) return a.priority > b.priority;
                if (a.state->mana != b.state->mana) return a.state->mana > b.state->mana;
                if (a.tail->actions.size() != b.tail->actions.size())
                    return a.tail->actions.size() < b.tail->actions.size();
                return a.state->path.size() < b.state->path.size();
            });
        int validated_count = 0;
        for (size_t ci = 0; ci < candidates.size() && ci < 20; ci++) {
            if (should_stop && *should_stop) break;
            const State& st = *candidates[ci].state;
            const AlexTail& tail = *candidates[ci].tail;
            SymChain chain;
            chain.name = "双向拼接-" + tail.name;
            chain.target_alex_count = target;
            chain.actions = tail.actions;
            std::vector<State> chain_states = validate_symbolic_chain(
                st, chain, std::max(1, max_paths - (int)all_proved.size()),
                stats, step_memo, should_stop);
            validated_count++;
            sort_path_states_cpp(chain_states);
            for (State& cs : chain_states) {
                if (add_state(std::move(cs))) {
                    if (stats) {
                        int best = 0;
                        for (const State& p : all_proved) best = std::max(best, p.alex_play_count);
                        stats->set_int("已证明龙数", best);
                        stats->set("证明方式", "双向符号链拼接证明");
                        stats->set("当前搜索 " + std::to_string(target) + "龙", "存在");
                    }
                    fprintf(stderr, "FOUND %d %d\n", target, all_proved.back().alex_damage);
                }
                if ((int)all_proved.size() >= max_paths) break;
            }
            if ((int)all_proved.size() >= max_paths) break;
        }
        if (stats) stats->set_int("双向验证链条数", stats->counters["双向验证链条数"] + validated_count);
    }
    if (stats) {
        stats->set_int("双向生成尾链数", (int)generated_tails.size());
        stats->set_int("模板子链数（起手+尾段）", (int)setup_subchains.size() + (int)template_tails.size());
        stats->set_int("双向拼接候选尾链数", (int)tails.size());
    }
    sort_path_states_cpp(all_proved);
    if ((int)all_proved.size() > max_paths) all_proved.resize(max_paths);
    return all_proved;
}

// ---------- 反向符号链证明主驱动（含双向拼接兜底） ----------
static std::vector<State> reverse_symbolic_prove_paths_cpp(
    const State& initial,
    int max_alex_count,
    int min_alex_count,
    int max_paths,
    int max_chain_steps,
    int forward_depth,
    Stats* stats,
    bool* should_stop) {
    min_alex_count = std::max(1, std::min(min_alex_count, max_alex_count));
    std::vector<State> all_proved;
    std::unordered_set<std::string> seen_paths;
    int best_alex_count = 0;
    std::unordered_map<std::string, StepMemoEntry> step_memo;
    auto& all_chains = symbolic_chains();
    auto& discrete_lib = discrete_subchains();

    int start_alex = std::max(1, max_alex_count);
    for (int target = start_alex; target >= min_alex_count; target--) {
        if (should_stop && *should_stop) break;
        if (stats) stats->set("当前证明目标", std::to_string(target));
        if (stats) stats->set("当前搜索 " + std::to_string(target) + "龙", "搜索中");
        std::vector<const SymChain*> chains;
        for (const SymChain& c : all_chains) {
            if (c.target_alex_count == target && (int)c.actions.size() <= max_chain_steps) {
                chains.push_back(&c);
            }
        }
        std::vector<const DiscreteSubchain*> relevant;
        for (const DiscreteSubchain& sc : discrete_lib) {
            if (sc.min_alex_count <= target) relevant.push_back(&sc);
        }
        std::vector<std::pair<const SymChain*, int>> scored;
        scored.reserve(chains.size());
        for (const SymChain* c : chains) {
            int hits = 0;
            for (const DiscreteSubchain* sc : relevant) {
                if (chain_contains_discrete_subchain(c->actions, *sc)) hits++;
            }
            scored.push_back({c, hits});
        }
        std::stable_sort(scored.begin(), scored.end(),
            [](const std::pair<const SymChain*, int>& a, const std::pair<const SymChain*, int>& b) {
                if (a.second != b.second) return a.second > b.second;
                if (a.first->actions.size() != b.first->actions.size())
                    return a.first->actions.size() < b.first->actions.size();
                return a.first->name < b.first->name;
            });
        if (stats) stats->inc("符号候选链条数", (int)chains.size());
        if (stats) stats->set_int("离散子链库数", (int)discrete_lib.size());
        bool target_proved = false;
        for (size_t ci = 0; ci < scored.size(); ci++) {
            if (should_stop && *should_stop) break;
            const SymChain& chain = *scored[ci].first;
            std::vector<State> chain_states = validate_symbolic_chain(
                initial, chain, std::max(1, max_paths - (int)all_proved.size()),
                stats, &step_memo, should_stop);
            sort_path_states_cpp(chain_states);
            for (State& cs : chain_states) {
                std::string pk = path_key(cs);
                if (seen_paths.count(pk)) continue;
                seen_paths.insert(pk);
                all_proved.push_back(std::move(cs));
                best_alex_count = std::max(best_alex_count, all_proved.back().alex_play_count);
                target_proved = true;
                if (stats) {
                    stats->set_int("已证明龙数", best_alex_count);
                    stats->set("证明方式", "双向符号链条证明");
                    stats->set("当前搜索 " + std::to_string(target) + "龙", "存在");
                }
                fprintf(stderr, "FOUND %d %d\n", all_proved.back().alex_play_count,
                        all_proved.back().alex_damage);
                if ((int)all_proved.size() >= max_paths) break;
            }
            if (stats) {
                fprintf(stderr, "PROGRESS %d %d %d\n", (int)all_proved.size(),
                        (int)(scored.size() - ci - 1), (int)step_memo.size());
            }
            if ((int)all_proved.size() >= max_paths) break;
        }
        if (!target_proved && stats) {
            stats->set("当前搜索 " + std::to_string(target) + "龙", "不存在");
        }
        if ((int)all_proved.size() >= max_paths) break;
    }

    if (best_alex_count < min_alex_count && !(should_stop && *should_stop)) {
        std::vector<State> bidir = bidirectional_symbolic_prove_paths(
            initial, max_alex_count, min_alex_count, max_paths, max_chain_steps,
            forward_depth, stats, &step_memo, should_stop);
        for (State& bs : bidir) {
            std::string pk = path_key(bs);
            if (seen_paths.count(pk)) continue;
            seen_paths.insert(pk);
            all_proved.push_back(std::move(bs));
            best_alex_count = std::max(best_alex_count, all_proved.back().alex_play_count);
            if (stats) {
                stats->set_int("已证明龙数", best_alex_count);
                stats->set("证明方式", "双向符号链拼接证明");
            }
            if ((int)all_proved.size() >= max_paths) break;
        }
    }

    if (stats) {
        stats->set_int("已证明龙数", best_alex_count);
        stats->set_int("已搜索到龙数下限", min_alex_count);
    }
    sort_path_states_cpp(all_proved);
    if ((int)all_proved.size() > max_paths) all_proved.resize(max_paths);
    return all_proved;
}

static void print_symbolic_json_result(const std::vector<State>& results, const Stats& stats,
                                       int max_alex, int min_alex) {
    int max_damage = 0, max_dragons = 0;
    for (const State& s : results) {
        max_damage = std::max(max_damage, s.alex_damage);
        max_dragons = std::max(max_dragons, s.alex_play_count);
    }
    printf("{\n");
    printf("  \"mode\": \"symbolic\",\n");
    printf("  \"max_damage\": %d,\n", max_damage);
    printf("  \"max_dragons\": %d,\n", max_dragons);
    printf("  \"expansions\": %d,\n", (int)results.size());
    printf("  \"depth\": %d,\n", max_alex);
    printf("  \"min_alex\": %d,\n", min_alex);
    printf("  \"stats\": {\n");
    bool first = true;
    for (const auto& kv : stats.counters) {
        if (!first) printf(",\n");
        printf("    \"%s\": %d", json_escape(kv.first).c_str(), kv.second);
        first = false;
    }
    for (const auto& kv : stats.status) {
        if (!first) printf(",\n");
        printf("    \"%s\": \"%s\"", json_escape(kv.first).c_str(), json_escape(kv.second).c_str());
        first = false;
    }
    printf("\n  },\n");
    printf("  \"results\": [\n");
    for (size_t i = 0; i < results.size(); i++) {
        const State& p = results[i];
        printf("    {\"dragons\": %d, \"damage\": %d, \"mana\": %d, \"path\": [",
               p.alex_play_count, p.alex_damage, p.mana);
        for (size_t j = 0; j < p.path.size(); j++) {
            if (j) printf(", ");
            printf("\"%s\"", json_escape(p.path[j]).c_str());
        }
        printf("]}%s\n", i + 1 < results.size() ? "," : "");
    }
    printf("  ]\n}\n");
}

// ===================== 输入解析 =====================
static Card parse_card_with_cost(const string& part) {
    size_t colon = part.rfind(':');
    if (colon == string::npos) return make_card(part);
    string name = part.substr(0, colon);
    int cost = atoi(part.substr(colon + 1).c_str());
    return make_card(name, cost);
}

static void parse_name_list(const string& text, const string& sep, vector<string>& out) {
    size_t pos = 0;
    while (pos <= text.size()) {
        size_t comma = text.find(sep, pos);
        string part = text.substr(pos, comma == string::npos ? string::npos : comma - pos);
        if (!part.empty()) out.push_back(part);
        if (comma == string::npos) break;
        pos = comma + sep.size();
    }
}

static State state_from_json(const JVal& root) {
    State st;
    st.mana_crystals = (int)root.get_int("crystals", 10);
    st.mana = (int)root.get_int("mana", st.mana_crystals);
    st.initial_mana_crystals = st.mana_crystals;
    st.initial_mana = st.mana;
    st.deck_is_known = false;
    const JVal* dj = root.find("deck_is_known");
    if (dj && dj->type == JVal::BOOL) st.deck_is_known = dj->b;

    const JVal* hand = root.find("hand");
    if (hand && hand->type == JVal::ARR) {
        for (const auto& item : hand->arr) {
            Card c = make_card(item.get_str("name"));
            long long tc = item.get_int("temp_cost", -1);
            if (tc >= 0) c.temp_cost = (int)tc;
            if (item.find("locked") && item.find("locked")->type == JVal::BOOL)
                c.locked_one_cost = item.find("locked")->b;
            if (item.find("deadly") && item.find("deadly")->type == JVal::BOOL)
                c.is_deadly_shadow = item.find("deadly")->b;
            st.hand.push_back(c);
        }
    }
    const JVal* board = root.find("board");
    if (board && board->type == JVal::ARR) {
        for (const auto& item : board->arr) {
            Card c = make_card(item.get_str("name"));
            long long hp = item.get_int("health", -1);
            if (hp >= 0) c.health = (int)hp;
            long long tc = item.get_int("temp_cost", -1);
            if (tc >= 0) c.temp_cost = (int)tc;
            st.board.push_back(c);
        }
    }
    const JVal* secrets = root.find("secrets");
    if (secrets && secrets->type == JVal::ARR) {
        for (const auto& item : secrets->arr) {
            st.secrets.push_back(make_card(item.get_str("name")));
        }
    }
    const JVal* weapon = root.find("weapon");
    if (weapon && weapon->type == JVal::OBJ) {
        st.weapon = make_card(weapon->get_str("name"));
        st.has_weapon = true;
    }
    const JVal* band = root.find("etc_band");
    if (band && band->type == JVal::ARR) {
        for (const auto& item : band->arr) {
            if (item.type == JVal::STR) st.etc_band.push_back(item.str);
        }
    }
    const JVal* deck = root.find("deck");
    if (deck && deck->type == JVal::ARR) {
        for (const auto& item : deck->arr) {
            st.deck.push_back(make_card(item.get_str("name")));
        }
    }
    return st;
}

#ifdef _WIN32
static int run_python(const std::wstring& cmdline) {
    /* 统一入口：把 Python 脚本调用交给系统 python 解释器（宽字符命令行，避免中文路径乱码）。 */
    std::wstring full = L"python " + cmdline;
    wchar_t* mutable_cmd = &full[0];
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    ZeroMemory(&pi, sizeof(pi));
    si.cb = sizeof(si);

    if (!CreateProcessW(NULL, mutable_cmd, NULL, NULL, TRUE, 0, NULL, NULL, &si, &pi)) {
        fprintf(stderr, "python 启动失败（错误码 %lu）\n", GetLastError());
        return 1;
    }

    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return (int)code;
}
#endif

int main(int argc, char** argv) {
#ifdef _WIN32
    /* 统一入口：命令行含 --python 时，把后续参数原样交给 Python 解释器执行。
       例如：red_dragon_calculator.exe --python 代码/red_dragon_calculator.py --sync-archive */
    int wargc = 0;
    LPWSTR* wargv = CommandLineToArgvW(GetCommandLineW(), &wargc);
    int python_start = -1;

    if (wargv != NULL) {
        for (int i = 1; i < wargc; i++) {
            if (wcscmp(wargv[i], L"--python") == 0) {
                python_start = i + 1;
                break;
            }
        }
    }

    if (python_start > 0) {
        std::wstring cmd;

        for (int i = python_start; i < wargc; i++) {
            if (i > python_start) cmd += L' ';
            cmd += L'"' + std::wstring(wargv[i]) + L'"';
        }

        if (wargv != NULL) LocalFree(wargv);
        return run_python(cmd);
    }

    if (wargv != NULL) LocalFree(wargv);
#endif
    bool use_json = false;
    string hand_text, board_text, band_text;
    int crystals = 8, mana = 8, min_alex = 1, max_alex = 10, width = 5000, depth = 30, max_paths = 1000000;
    int deadly_index = -1;
    int forward_depth = 5;
    string mode = "beam";

    for (int i = 1; i < argc; i++) {
        auto next = [&](const char* flag, string* out) -> bool {
            if (strcmp(argv[i], flag) == 0 && i + 1 < argc) {
                *out = argv[i + 1];
                i++;
                return true;
            }
            return false;
        };
        string tmp;
        if (strcmp(argv[i], "--json") == 0) { use_json = true; continue; }
        if (next("--hand", &tmp)) { hand_text = tmp; continue; }
        if (next("--board", &tmp)) { board_text = tmp; continue; }
        if (next("--band", &tmp)) { band_text = tmp; continue; }
        if (next("--crystals", &tmp)) { crystals = atoi(tmp.c_str()); continue; }
        if (next("--mana", &tmp)) { mana = atoi(tmp.c_str()); continue; }
        if (next("--min", &tmp)) { min_alex = atoi(tmp.c_str()); continue; }
        if (next("--max", &tmp)) { max_alex = atoi(tmp.c_str()); continue; }
        if (next("--width", &tmp)) { width = atoi(tmp.c_str()); continue; }
        if (next("--depth", &tmp)) { depth = atoi(tmp.c_str()); continue; }
        if (next("--max-paths", &tmp)) { max_paths = atoi(tmp.c_str()); continue; }
        if (next("--deadly", &tmp)) { deadly_index = atoi(tmp.c_str()); continue; }
        if (next("--mode", &tmp)) { mode = tmp; continue; }
        if (next("--forward-depth", &tmp)) { forward_depth = atoi(tmp.c_str()); continue; }
    }

    State st;
    bool use_symbolic = false;
    if (use_json) {
        string input;
        char buf[65536];
        size_t n;
        while ((n = fread(buf, 1, sizeof(buf), stdin)) > 0) input.append(buf, n);
        JVal root;
        if (!parse_json(input, root)) {
            fprintf(stderr, "JSON 解析失败\n");
            return 1;
        }
        st = state_from_json(root);
        min_alex = (int)root.get_int("min_alex", min_alex);
        max_alex = (int)root.get_int("max_alex", max_alex);
        width = (int)root.get_int("width", width);
        depth = (int)root.get_int("depth", depth);
        max_paths = (int)root.get_int("max_paths", max_paths);
        mode = root.get_str("mode", mode);
        forward_depth = (int)root.get_int("forward_depth", forward_depth);
    } else {
        st.mana_crystals = crystals;
        st.mana = mana;
        st.initial_mana_crystals = crystals;
        st.initial_mana = mana;
        if (!hand_text.empty()) {
            size_t pos = 0;
            while (pos <= hand_text.size()) {
                size_t comma = hand_text.find(',', pos);
                string part = hand_text.substr(pos, comma == string::npos ? string::npos : comma - pos);
                if (!part.empty()) st.hand.push_back(parse_card_with_cost(part));
                if (comma == string::npos) break;
                pos = comma + 1;
            }
        }
        if (!board_text.empty()) {
            size_t pos = 0;
            while (pos <= board_text.size()) {
                size_t comma = board_text.find(',', pos);
                string part = board_text.substr(pos, comma == string::npos ? string::npos : comma - pos);
                if (!part.empty()) st.board.push_back(make_card(part));
                if (comma == string::npos) break;
                pos = comma + 1;
            }
        }
        if (!band_text.empty()) parse_name_list(band_text, ",", st.etc_band);
        if (deadly_index >= 1 && deadly_index <= (int)st.hand.size()) {
            st.hand[deadly_index - 1].is_deadly_shadow = true;
        }
    }

    use_symbolic = (mode == "symbolic" || mode == "双向符号链证明");

    if (st.etc_band.empty()) {
        st.etc_band = {"舞动全场（ft.迦罗娜）", "幻觉药水", "生命的缚誓者阿莱克丝塔萨"};
    }

    if (use_symbolic) {
        Stats stats;
        bool stop = false;
        std::vector<State> results = reverse_symbolic_prove_paths_cpp(
            st, max_alex, min_alex, max_paths, depth, forward_depth, &stats, &stop);
        print_symbolic_json_result(results, stats, max_alex, min_alex);
        return 0;
    }

    Progress prog;
    prog.enabled = true;
    BeamResult res = beam_search_paths(st, depth, max_paths, max_alex, min_alex, width, &prog);
    if (prog.enabled) {
        fprintf(stderr, "PROGRESS %d %d %d\n", res.expansions, 0, res.reached_depth);
    }
    print_json_result(res, max_alex, min_alex);
    return 0;
}
