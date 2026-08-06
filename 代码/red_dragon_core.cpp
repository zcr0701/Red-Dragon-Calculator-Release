// 红龙贼计算器 C++ 计算核心（MCTS + 束搜索模拟 + 瓶颈模型启发）。
//
// 架构（2026-08-05 重构）：
//   - 计算全部改为 MCTS + 束搜索模拟：每步决策以当前局面为根做 N 次迭代
//     （UCB1 选择 / 随机扩展 / 束搜索模拟 / 回传），选访问次数最多的子动作执行，
//     重复到游戏结束；多局并行（根并行）收集各龙数最优路径。
//   - 奖励：伤害优先（伤害×100 + 龙数；鲨鱼在场时每龙 16 伤）。
//   - 简单启发函数（完全放弃子链库）：瓶颈模型（Liebig 最小因子律），
//     可达龙数 ≈ min(① 龙源数, ② 回手容量, ③ 法力可负担轮数)，
//     束内保留评分 = 当前伤害 + 瓶颈可达龙数×16。
//
// 编译（MinGW g++）：g++ -std=c++17 -O3 -static -o red_dragon_engine.exe red_dragon_core.cpp
// 用法：red_dragon_engine.exe --json < problem.json
// 输入局面（stdin JSON）：
//   {"crystals":8,"mana":8,"hand":[{"name":"鲨鱼之灵","temp_cost":2,"locked":false,"deadly":false}],
//    "board":[...],"secrets":[...],"weapon":{...},"deck":[...],"etc_band":[...],
//    "current_effects":[{"name":"狐人老千","count":2}],
//    "min_alex":1,"max_alex":10,"depth":30,"max_paths":1000000,
//    "iterations":400,"beam_width":8,"sim_depth":8,"explore_c":1.414,
//    "games":8,"threads":4,"time_budget_sec":30.0}
// 输出（stdout）：{"mode":"mcts_beam","results":[{"dragons","damage","mana","path"}],"stats":{...}}
// 实时进度：stderr 输出 PROGRESS / FOUND 行。

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <map>
#include <memory>
#include <random>
#include <set>
#include <string>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#endif

using std::map;
using std::pair;
using std::string;
using std::tuple;
using std::unordered_map;
using std::unordered_set;
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
    {"幸运彗星", {2, "spell", "lucky_comet", false, false, false, -1}},
    {"战略转移", {1, "spell", "strategic_transfer", false, false, false, -1}},
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
    bool etc_band_provided = false;   // JSON 显式传了 etc_band（空数组=牛池已空）
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
// 领域剪枝：牌库未知时抽牌类法术不可枚举（跳过），避免虚构后继。
static const std::set<string> DRAW_CARD_EFFECTS = {
    "dig_for_treasure", "shroud_of_concealment", "swindle",
    "dubious_purchase", "gone_fishin", "quick_pick",
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

static vector<State> generate_successors(const State& st) {
    vector<State> out;
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
                    State base = st.clone();
                    if (!play_card_base(base, hand_index, tf, ek, et)) continue;
                    vector<State> succs = apply_search_effect(base, card, tf, ek, et);
                    for (State& succ : succs) out.push_back(std::move(succ));
                }
            }
        }
    }
    return out;
}

// ===================== 去重哈希（快） =====================
// 与旧 state_key_for_dedup 同语义的 64 位 FNV-1a 哈希（手牌无序、其余有序），
// 仅用于搜索去重/循环保护；哈希碰撞概率可忽略，不影响路径合法性。
static uint64_t str_hash(const string& s) {
    uint64_t h = 1469598103934665603ULL;
    for (char c : s) {
        h ^= (unsigned char)c;
        h *= 1099511628211ULL;
    }
    return h;
}

static uint64_t mix_hash(uint64_t h, uint64_t x) {
    h ^= x;
    h *= 1099511628211ULL;
    return h;
}

static uint64_t card_hash(const Card& c) {
    uint64_t h = str_hash(c.name);
    h = mix_hash(h, str_hash(c.original_name));
    h = mix_hash(h, str_hash(c.card_type));
    h = mix_hash(h, str_hash(c.effect_id));
    h = mix_hash(h, (uint64_t)c.current_cost());
    h = mix_hash(h, c.is_deadly_shadow ? 1ULL : 0ULL);
    h = mix_hash(h, c.locked_one_cost ? 1ULL : 0ULL);
    h = mix_hash(h, (uint64_t)c.health);
    return h;
}

static uint64_t state_hash(const State& s) {
    uint64_t h = 1469598103934665603ULL;
    h = mix_hash(h, s.deck_is_known ? 1ULL : 0ULL);
    vector<uint64_t> hand_keys;
    hand_keys.reserve(s.hand.size());
    for (const auto& c : s.hand) hand_keys.push_back(card_hash(c));
    std::sort(hand_keys.begin(), hand_keys.end());
    for (uint64_t k : hand_keys) h = mix_hash(h, k);
    for (const auto& c : s.deck) h = mix_hash(h, card_hash(c));
    for (const auto& c : s.board) h = mix_hash(h, card_hash(c));
    for (const auto& c : s.secrets) h = mix_hash(h, card_hash(c));
    if (s.has_weapon) h = mix_hash(h, card_hash(s.weapon));
    for (const auto& n : s.etc_band) h = mix_hash(h, str_hash(n));
    h = mix_hash(h, (uint64_t)s.cards_played_this_turn);
    h = mix_hash(h, (uint64_t)s.next_spell);
    h = mix_hash(h, (uint64_t)s.next_combo);
    h = mix_hash(h, s.next_combo_twice ? 1ULL : 0ULL);
    h = mix_hash(h, (uint64_t)s.next_card);
    h = mix_hash(h, (uint64_t)s.next_two_cards);
    h = mix_hash(h, (uint64_t)s.next_two_cards_count);
    for (const auto& p : s.oil_stacks) h = mix_hash(h, (uint64_t)p.first * 31 + (uint64_t)p.second);
    return h;
}

// ===================== 简易 JSON =====================
struct JVal {
    enum Type { NUL, BOOL, NUM, STR, ARR, OBJ } type = NUL;
    bool b = false;
    long long num = 0;
    double dnum = 0.0;
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
    double get_double(const string& k, double dflt) const {
        const JVal* v = find(k);
        if (!v || v->type != NUM) return dflt;
        return v->dnum;
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
    size_t start = i;
    while (i < s.size() && (isdigit((unsigned char)s[i]) || s[i] == '-' || s[i] == '.' || s[i] == 'e' || s[i] == 'E' || s[i] == '+')) i++;
    if (i == start) return false;
    string numstr = s.substr(start, i - start);
    v.type = JVal::NUM;
    v.num = atoll(numstr.c_str());
    v.dnum = atof(numstr.c_str());
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

// ===================== 路径匹配标签（--verify 用） =====================
// 去除 [殒命暗影] 标记，两种硬币视为同一张
static string canonical_action(string item) {
    string out = item;
    string needle = "[殒命暗影]";
    size_t pos;
    while ((pos = out.find(needle)) != string::npos) out.erase(pos, needle.size());
    if (is_coin_name(out)) return "幸运币";
    return out;
}

// ===================== MCTS + 束搜索模拟 =====================
struct SearchParams {
    int min_alex = 1;
    int max_alex = 10;
    int depth = 30;
    int max_paths = 1000000;
    int iterations = 400;       // 每步决策的 MCTS 迭代次数 N
    int beam_width = 8;         // 模拟专用束宽 B_sim
    int sim_depth = 8;          // 束搜索模拟深度上限（树随 MCTS 逐步加深）
    double explore_c = 1.41421356;  // UCB1 探索常数 C
    int games = 8;              // MCTS 游戏局数（根并行，硬上限）
    int threads = 4;
    double time_budget_sec = 30.0;  // 总时间预算（秒），0 = 不限时
};

// 奖励：伤害×100 + 龙数。伤害优先，龙数破平。
static double reward_of(const State& s) {
    return s.alex_damage * 100.0 + s.alex_play_count;
}

// ---------- 子链覆盖度（旧 beam 冠军判据：状态侧已凑齐的子链骨架数） ----------
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

// ===================== 瓶颈启发（Liebig 最小因子律） =====================
// 红龙 OTK 每轮循环 = 打出 1 条龙 + 1 次回手。
// 可达龙数 ≈ min(① 龙源数, ② 回手容量, ③ 法力可负担轮数)，
// 不取决于资源总和，而取决于最紧的那条约束。
static double bottleneck_dragons(const State& s) {
    int hand_dragons = 0, board_dragons = 0, shadowcaster = 0, scabbs = 0;
    int shark_in_hand = 0, single_returns = 0, whole_returns = 0, deadly = 0;
    int cheapest_dragon = -1;
    for (const auto& c : s.hand) {
        const string& n = c.name;
        if (c.dragon) {
            hand_dragons++;
            int cost = effective_cost(s, c);
            if (cost >= 0 && (cheapest_dragon < 0 || cost < cheapest_dragon)) cheapest_dragon = cost;
        } else if (n == "暗影施法者") {
            shadowcaster++;
        } else if (n == "斯卡布斯·刀油") {
            scabbs++;
        } else if (n == "鲨鱼之灵") {
            shark_in_hand++;
        } else if (n == "暗影步" || n == "赤烟·腾武") {
            single_returns++;
        } else if (n == "舞动全场（ft.迦罗娜）" || n == "幻觉药水" || n == "战略转移") {
            whole_returns++;
        }
        if (c.is_deadly_shadow) deadly++;
    }
    for (const auto& c : s.board) {
        const string& n = c.name;
        if (n == "生命的缚誓者阿莱克丝塔萨") board_dragons++;
        else if (n == "暗影施法者") shadowcaster++;
        else if (n == "斯卡布斯·刀油") scabbs++;
    }
    bool shark = s.has_shark() || shark_in_hand > 0;

    // ① 龙源数：手牌龙 + 场上龙（回手后重打）+ 暗施可复制龙（鲨鱼时 ×2）
    double sources = (double)(hand_dragons + board_dragons + shadowcaster * (shark ? 2 : 1));

    // ② 回手容量：单体回手 + 整场回手×3；刀油+单体回手 ≈ 无限（+15 封顶）；殒命打五折
    double capacity = (double)single_returns + (double)whole_returns * 3.0;
    if (scabbs > 0 && single_returns > 0) capacity += 15.0;
    if (deadly > 0 && s.cards_played_this_turn > 0) capacity += 0.5;

    // ③ 法力可负担轮数：阈值型资源；考虑手牌刀油先打出带来的减费潜力
    if (cheapest_dragon < 0 && board_dragons == 0 && shadowcaster == 0) return 0.0;  // 无龙源
    if (cheapest_dragon < 0) cheapest_dragon = 9;
    int per_scabbs = shark ? 4 : 2;
    int eff_dragon = std::max(0, cheapest_dragon - scabbs * per_scabbs);
    if (s.mana < eff_dragon) return 0.0;                                            // 阈值：打不起第一条龙
    int rounds = 1 + (s.mana - eff_dragon) / std::max(1, eff_dragon + 1);           // 每轮 ≈ 龙费 + 回手费(约1)
    double mana = (double)rounds;
    return std::min(sources, std::min(capacity, mana));
}

// 时间预算：跨线程共享的原子停止标志 + 起始时间
struct Budget {
    std::atomic<bool>* stop = nullptr;
    std::chrono::steady_clock::time_point* t0 = nullptr;
    double budget_sec = 0.0;

    bool over() const {
        if (!stop || budget_sec <= 0.0) return false;
        if (stop->load()) return true;
        double sec = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - *t0).count();
        if (sec >= budget_sec) {
            stop->store(true);
            return true;
        }
        return false;
    }
};

static bool path_sort_better(const State& a, const State& b) {
    if (a.alex_damage != b.alex_damage) return a.alex_damage > b.alex_damage;
    if (a.alex_play_count != b.alex_play_count) return a.alex_play_count > b.alex_play_count;
    if (a.path.size() != b.path.size()) return a.path.size() > b.path.size();
    if (a.mana != b.mana) return a.mana > b.mana;
    if (a.initial_mana_crystals != b.initial_mana_crystals)
        return a.initial_mana_crystals < b.initial_mana_crystals;
    return a.initial_mana < b.initial_mana;
}

static void add_best(const State& s, unordered_map<int, State>& best, int min_alex) {
    if (s.alex_play_count < min_alex) return;
    auto it = best.find(s.alex_play_count);
    if (it == best.end() || path_sort_better(s, it->second)) best[s.alex_play_count] = s;
}

// 束搜索模拟：从给定状态快速搜到深度上限，返回途中发现的最高奖励状态（含完整路径）
// 束内保留评分 = 当前伤害 + 瓶颈可达龙数×16（简单启发函数，无子链库）；
// 并按当前龙数分桶，保住正在蓄力的低龙数分支。
static State beam_simulate(const State& start, const SearchParams& p,
                           int* expansions, const Budget* budget = nullptr) {
    struct Cand { double score; State s; };
    vector<Cand> level;
    State best = start;
    double best_reward = reward_of(start);
    double best_score = (double)start.alex_damage + bottleneck_dragons(start) * 16.0;
    level.push_back({best_score, start});
    unordered_set<uint64_t> seen;
    seen.insert(state_hash(start));
    int sim_max = std::max(1, std::min(p.depth - (int)start.path.size(), p.sim_depth));

    for (int d = 0; d < sim_max; d++) {
        if (budget && budget->over()) break;
        vector<Cand> next;
        for (const Cand& c : level) {
            if (c.s.alex_play_count >= p.max_alex) continue;
            for (State& succ : generate_successors(c.s)) {
                if (expansions) (*expansions)++;
                uint64_t key = state_hash(succ);
                if (seen.count(key)) continue;  // 循环保护
                seen.insert(key);
                double r = reward_of(succ);
                double sc = (double)succ.alex_damage + bottleneck_dragons(succ) * 16.0;
                if (r > best_reward || (r == best_reward && sc > best_score)) {
                    best_reward = r;
                    best_score = sc;
                    best = succ;
                }
                next.push_back({sc, std::move(succ)});
            }
        }
        if (next.empty()) break;
        // 分桶：按当前龙数分组，避免高龙数分支挤掉蓄力中的低龙数高分分支
        map<int, vector<Cand*>> buckets;
        for (Cand& c : next) buckets[c.s.alex_play_count].push_back(&c);
        int per_bucket = std::max(1, (int)(p.beam_width * 1.2 / std::max(1, (int)buckets.size())));
        vector<Cand> kept;
        for (auto it = buckets.rbegin(); it != buckets.rend(); ++it) {
            auto& b = it->second;
            std::stable_sort(b.begin(), b.end(),
                             [](const Cand* a, const Cand* b) { return a->score > b->score; });
            for (int i = 0; i < per_bucket && i < (int)b.size(); i++) kept.push_back(std::move(*b[i]));
        }
        if ((int)kept.size() > p.beam_width) {
            std::stable_sort(kept.begin(), kept.end(),
                             [](const Cand& a, const Cand& b) { return a.score > b.score; });
            kept.resize(p.beam_width);
        }
        level = std::move(kept);
    }
    return best;
}

struct MctsNode {
    State state;
    vector<State> succs;
    vector<MctsNode*> children;  // 与 succs 平行，nullptr = 尚未尝试
    vector<int> untried;
    int visits = 0;
    double total = 0.0;
    State best_state;            // 该节点（及其模拟）发现的最佳状态
    double best_reward = -1.0;
    bool succs_ready = false;
    bool terminal = false;
    MctsNode* parent = nullptr;
};

static void ensure_succs(MctsNode* node, int* expansions) {
    if (node->succs_ready) return;
    node->succs = generate_successors(node->state);
    node->succs_ready = true;
    node->terminal = node->succs.empty();
    if (expansions) (*expansions) += (int)node->succs.size();
    node->children.assign(node->succs.size(), nullptr);
    node->untried.clear();
    for (int i = 0; i < (int)node->succs.size(); i++) node->untried.push_back(i);
}

struct StepOutcome {
    State chosen;
    vector<State> harvested;
    int expansions = 0;
    int simulations = 0;
};

// 单步 MCTS：从 root 跑 N 次迭代（UCB1 选择 / 随机扩展 / 束搜索模拟 / 回传），
// 选择访问次数最多的子动作执行，并收集模拟中发现的高奖励路径。
static StepOutcome mcts_step(const State& root_state, const SearchParams& p,
                             std::mt19937& rng, const Budget& budget) {
    StepOutcome out;
    vector<std::unique_ptr<MctsNode>> arena;
    arena.push_back(std::make_unique<MctsNode>());
    arena.back()->state = root_state.clone();
    MctsNode* root = arena.back().get();
    ensure_succs(root, &out.expansions);
    if (root->terminal) {
        out.chosen = root_state.clone();
        return out;
    }

    for (int it = 0; it < p.iterations; it++) {
        if ((it & 127) == 0 && budget.over()) break;  // 时间预算内可中断
        // 1) 选择：沿 UCB1 下降，直到非完全展开节点或终止状态
        MctsNode* node = root;
        while (!node->terminal && node->untried.empty()) {
            MctsNode* best_child = nullptr;
            double best_val = -1.0e18;
            for (MctsNode* ch : node->children) {
                if (!ch) continue;
                double ucb;
                if (ch->visits == 0) {
                    ucb = 1.0e18;
                } else {
                    // 奖励上限估计（每龙最多 16 伤）：max_alex*16*100 + max_alex + 余量
                    double max_r = p.max_alex * 1600.0 + p.max_alex + 100.0;
                    double q = (ch->total / ch->visits) / max_r;
                    ucb = q + p.explore_c * std::sqrt(std::log((double)node->visits) / (double)ch->visits);
                }
                if (ucb > best_val) { best_val = ucb; best_child = ch; }
            }
            if (!best_child) break;
            node = best_child;
        }

        // 2) 扩展 + 3) 模拟（束搜索）
        MctsNode* back_start = node;
        double reward;
        if (node->terminal) {
            reward = reward_of(node->state);
        } else if (!node->untried.empty()) {
            std::uniform_int_distribution<int> u(0, (int)node->untried.size() - 1);
            int pos = u(rng);
            int idx = node->untried[pos];
            node->untried[pos] = node->untried.back();
            node->untried.pop_back();
            arena.push_back(std::make_unique<MctsNode>());
            MctsNode* child = arena.back().get();
            child->state = node->succs[idx].clone();
            child->parent = node;
            node->children[idx] = child;
            ensure_succs(child, &out.expansions);
            State sim_best = beam_simulate(child->state, p, &out.expansions, &budget);
            reward = reward_of(sim_best);
            child->best_reward = reward;
            child->best_state = sim_best;
            back_start = child;
            out.simulations++;
        } else {
            reward = reward_of(node->state);
        }

        // 4) 回传
        for (MctsNode* n = back_start; n; n = n->parent) {
            n->visits++;
            n->total += reward;
        }
    }

    // 最终动作选择：访问次数最多（次按平均价值），鲁棒优先
    MctsNode* best = nullptr;
    for (MctsNode* ch : root->children) {
        if (!ch) continue;
        if (!best || ch->visits > best->visits ||
            (ch->visits == best->visits &&
             ch->total / ch->visits > best->total / best->visits)) {
            best = ch;
        }
    }
    out.chosen = best ? best->state.clone() : root_state.clone();
    for (const auto& np : arena) {
        if (np->best_reward >= 0.0 && !np->best_state.path.empty()) {
            out.harvested.push_back(np->best_state);
        }
    }
    return out;
}

struct ThreadOut {
    unordered_map<int, State> best;   // 各龙数最优路径
    vector<State> game_paths;         // 每局逐步走出的路径（用于子链学习）
    int expansions = 0;
    int simulations = 0;
    int games_played = 0;
    int reached_depth = 0;
    double work_sec = 0.0;            // 该线程实际计算耗时（秒）
};

// 单局游戏：每步以当前状态为根重跑 MCTS，选最优动作前进，直到无法继续/达目标
static void mcts_game(const State& start, const SearchParams& p,
                      std::mt19937& rng, ThreadOut& out, const Budget& budget) {
    State st = start.clone();
    add_best(st, out.best, p.min_alex);
    out.game_paths.push_back(st);
    unordered_set<uint64_t> seen;
    seen.insert(state_hash(st));

    for (int step = 0; step < p.depth; step++) {
        if (budget.over()) break;
        if (st.alex_play_count >= p.max_alex) break;
        if (generate_successors(st).empty()) break;  // 无可行动作 = 终局
        StepOutcome so = mcts_step(st, p, rng, budget);
        out.expansions += so.expansions;
        out.simulations += so.simulations;
        for (const State& h : so.harvested) add_best(h, out.best, p.min_alex);
        if (so.chosen.path.size() <= st.path.size()) break;  // 无进展保护
        st = so.chosen;
        uint64_t key = state_hash(st);
        if (seen.count(key)) break;  // 循环保护
        seen.insert(key);
        add_best(st, out.best, p.min_alex);
        out.game_paths.push_back(st);
    }
    out.reached_depth = std::max(out.reached_depth, (int)st.path.size());
}

// ---------- 旧 beam 的子链/离散路径评分（宽束模拟沿用，经验证能挖出 10 龙/160 伤） ----------
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

// 根级宽束模拟：与旧 beam 同机制（宽束全深度 + 分桶 + 覆盖度冠军，
// 总分 = 伤害 + 子链分 + 离散路径分，键含龙数），保证深线（10 龙/160 伤）
// 能被发现；与 MCTS 游戏并行，共享时间预算。
static void wide_beam_pass(const State& start_in, const SearchParams& p,
                           const Budget& budget, ThreadOut& out) {
    int beam_width = std::max(1500, std::min(3000, p.beam_width * 200));
    State start = start_in.clone();
    struct Cand {
        State s;
        uint64_t key = 0;
        int total = 0;
        double bottleneck = 0.0;
        int mana = 0;
        int count = 0;
    };
    auto dedup_key = [](const State& s) {
        return mix_hash(state_hash(s), (uint64_t)s.alex_play_count);
    };
    vector<State> level = {start};
    unordered_map<uint64_t, State> seen;
    seen[dedup_key(start)] = start;
    add_best(start, out.best, p.min_alex);

    for (int depth = 1; depth <= p.depth; depth++) {
        if (budget.over()) break;
        vector<Cand> cands;
        unordered_map<uint64_t, size_t> cand_index;
        for (const State& state : level) {
            for (State& succ : generate_successors(state)) {
                out.expansions++;
                uint64_t key = dedup_key(succ);
                auto prev = seen.find(key);
                if (prev != seen.end() && succ.mana <= prev->second.mana) continue;
                int total = succ.alex_damage + subchain_score(succ) + discrete_path_score(succ);
                double bottleneck = bottleneck_dragons(succ);
                auto cur = cand_index.find(key);
                if (cur != cand_index.end()) {
                    Cand& c = cands[cur->second];
                    if (succ.mana <= c.mana) continue;
                    c.s = std::move(succ);  // 更高法力替换，保持原插入位置
                    c.total = total;
                    c.bottleneck = bottleneck;
                    c.mana = c.s.mana;
                } else {
                    Cand c;
                    c.s = std::move(succ);
                    c.key = key;
                    c.total = total;
                    c.bottleneck = bottleneck;
                    c.mana = c.s.mana;
                    c.count = c.s.alex_play_count;
                    cand_index[key] = cands.size();
                    cands.push_back(std::move(c));
                }
                const State& ns = cands[cand_index[key]].s;
                add_best(ns, out.best, p.min_alex);
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
                // 冠军：瓶颈潜力优先，其次总分，其次法力
                const Cand* champ = bstates[0];
                for (const Cand* bs : bstates) {
                    if (std::tie(bs->bottleneck, bs->total, bs->mana) >
                        std::tie(champ->bottleneck, champ->total, champ->mana)) {
                        champ = bs;
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
        out.reached_depth = std::max(out.reached_depth, depth);
    }
}

struct MctsResult {
    unordered_map<int, State> best_by_dragons;
    vector<State> game_paths;
    int expansions = 0;
    int simulations = 0;
    int games_played = 0;
    int reached_depth = 0;
    double wall_sec = 0.0;
    double mcts_work_sec = 0.0;
    double wide_work_sec = 0.0;
    int mcts_expansions = 0;
    int wide_expansions = 0;
};

struct Progress {
    bool enabled = true;
};

struct WorkerArgs {
    const State* start = nullptr;
    const SearchParams* p = nullptr;
    ThreadOut* out = nullptr;
    Progress* prog = nullptr;
    const Budget* budget = nullptr;
    int target = 0;
    int tid = 0;
};

#ifdef _WIN32
static DWORD WINAPI worker_entry(LPVOID param) {
#else
static void* worker_entry(void* param) {
#endif
    WorkerArgs* a = static_cast<WorkerArgs*>(param);
    auto t_start = std::chrono::steady_clock::now();
    std::mt19937 rng((std::random_device{}()) ^
                     (std::uint64_t)std::chrono::high_resolution_clock::now()
                         .time_since_epoch().count() ^
                     (std::uint64_t)a->tid);
    ThreadOut& o = *a->out;
    Budget budget = *a->budget;
    for (int g = 0; g < a->target; g++) {
        if (budget.stop->load()) break;
        mcts_game(*a->start, *a->p, rng, o, budget);
        o.games_played++;
        if (a->prog && a->prog->enabled) {
            fprintf(stderr, "PROGRESS %d %d %d\n",
                    o.expansions, o.simulations, o.reached_depth);
        }
    }
    o.work_sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - t_start).count();
#ifdef _WIN32
    return 0;
#else
    return nullptr;
#endif
}

#ifdef _WIN32
static DWORD WINAPI wide_worker_entry(LPVOID param) {
#else
static void* wide_worker_entry(void* param) {
#endif
    WorkerArgs* a = static_cast<WorkerArgs*>(param);
    auto t_start = std::chrono::steady_clock::now();
    wide_beam_pass(*a->start, *a->p, *a->budget, *a->out);
    a->out->work_sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - t_start).count();
    if (a->prog && a->prog->enabled) {
        fprintf(stderr, "PROGRESS %d %d %d\n",
                a->out->expansions, a->out->simulations, a->out->reached_depth);
    }
#ifdef _WIN32
    return 0;
#else
    return nullptr;
#endif
}

static MctsResult run_mcts_search(const State& start, const SearchParams& p, Progress* prog) {
    MctsResult res;
    auto t0 = std::chrono::steady_clock::now();
    int games_target = std::max(1, p.games);
    // 游戏线程 + 1 个根级宽束模拟线程；WaitForMultipleObjects 上限 64
    int game_threads = std::max(1, std::min(p.threads, std::min(games_target, 31)));
    std::atomic<bool> stop{false};
    Budget budget;
    budget.stop = &stop;
    budget.t0 = &t0;
    budget.budget_sec = p.time_budget_sec;

    vector<ThreadOut> outs(game_threads + 1);  // 最后一个是宽束模拟通道
    int base = games_target / game_threads;
    int extra = games_target % game_threads;
    vector<WorkerArgs> args;
    args.reserve(game_threads);
    for (int t = 0; t < game_threads; t++) {
        WorkerArgs a;
        a.start = &start;
        a.p = &p;
        a.out = &outs[t];
        a.prog = prog;
        a.budget = &budget;
        a.target = base + (t < extra ? 1 : 0);
        a.tid = t;
        args.push_back(a);
    }
    WorkerArgs wide_arg;
    wide_arg.start = &start;
    wide_arg.p = &p;
    wide_arg.out = &outs[game_threads];
    wide_arg.prog = prog;
    wide_arg.budget = &budget;
    wide_arg.target = 0;
    wide_arg.tid = 99;

#ifdef _WIN32
    vector<HANDLE> handles;
    handles.reserve(game_threads + 1);
    for (int t = 0; t < game_threads; t++) {
        HANDLE h = CreateThread(nullptr, 0, worker_entry, &args[t], 0, nullptr);
        if (h) handles.push_back(h);
    }
    HANDLE hw = CreateThread(nullptr, 0, wide_worker_entry, &wide_arg, 0, nullptr);
    if (hw) handles.push_back(hw);
    if (!handles.empty()) {
        WaitForMultipleObjects((DWORD)handles.size(), handles.data(), TRUE, INFINITE);
        for (HANDLE h : handles) CloseHandle(h);
    }
#else
    // 非 Windows 平台退化为顺序执行
    for (int t = 0; t < game_threads; t++) worker_entry(&args[t]);
    wide_worker_entry(&wide_arg);
#endif

    int prev_max = -1;
    for (int t = 0; t < game_threads + 1; t++) {
        res.expansions += outs[t].expansions;
        res.simulations += outs[t].simulations;
        res.games_played += outs[t].games_played;
        res.reached_depth = std::max(res.reached_depth, outs[t].reached_depth);
        if (t < game_threads) {
            res.mcts_work_sec = std::max(res.mcts_work_sec, outs[t].work_sec);
            res.mcts_expansions += outs[t].expansions;
        } else {
            res.wide_work_sec = outs[t].work_sec;
            res.wide_expansions = outs[t].expansions;
        }
        for (const auto& kv : outs[t].best) add_best(kv.second, res.best_by_dragons, p.min_alex);
        for (const State& s : outs[t].game_paths) res.game_paths.push_back(s);
    }
    res.wall_sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    // FOUND 实时上报（龙数创新高）
    for (const auto& kv : res.best_by_dragons) {
        if (kv.first > prev_max) {
            prev_max = kv.first;
            if (prog && prog->enabled) {
                fprintf(stderr, "FOUND %d %d\n", kv.first, kv.second.alex_damage);
            }
        }
    }
    return res;
}

// ===================== 输出 =====================
static void print_json_result(const MctsResult& res, const SearchParams& p) {
    vector<State> results;
    results.reserve(res.best_by_dragons.size());
    for (const auto& kv : res.best_by_dragons) results.push_back(kv.second);
    std::stable_sort(results.begin(), results.end(), path_sort_better);
    if ((int)results.size() > p.max_paths) results.resize(p.max_paths);
    int max_damage = 0, max_dragons = 0;
    for (const State& s : results) {
        max_damage = std::max(max_damage, s.alex_damage);
        max_dragons = std::max(max_dragons, s.alex_play_count);
    }
    printf("{\n");
    printf("  \"mode\": \"mcts_beam\",\n");
    printf("  \"max_damage\": %d,\n", max_damage);
    printf("  \"max_dragons\": %d,\n", max_dragons);
    printf("  \"expansions\": %d,\n", res.expansions);
    printf("  \"depth\": %d,\n", res.reached_depth);
    printf("  \"min_alex\": %d,\n", p.min_alex);
    printf("  \"stats\": {\n");
    printf("    \"迭代次数/步\": %d,\n", p.iterations);
    printf("    \"模拟束宽\": %d,\n", p.beam_width);
    printf("    \"模拟深度\": %d,\n", p.sim_depth);
    printf("    \"探索常数\": %.3f,\n", p.explore_c);
    printf("    \"搜索局数\": %d,\n", res.games_played);
    printf("    \"展开节点\": %d,\n", res.expansions);
    printf("    \"模拟次数\": %d,\n", res.simulations);
    printf("    \"总耗时(秒)\": %.1f,\n", res.wall_sec);
    printf("    \"MCTS耗时(秒)\": %.1f,\n", res.mcts_work_sec);
    printf("    \"宽束耗时(秒)\": %.1f,\n", res.wide_work_sec);
    printf("    \"MCTS展开\": %d,\n", res.mcts_expansions);
    printf("    \"宽束展开\": %d,\n", res.wide_expansions);
    if (res.wall_sec > 0.0) {
        printf("    \"展开/秒\": %.0f,\n", res.expansions / res.wall_sec);
        printf("    \"模拟/秒\": %.0f,\n", res.simulations / res.wall_sec);
    }
    printf("    \"启发函数\": \"瓶颈模型(min 龙源/回手/法力)\"\n");
    printf("  },\n");
    printf("  \"results\": [\n");
    for (size_t i = 0; i < results.size(); i++) {
        const State& pst = results[i];
        printf("    {\"dragons\": %d, \"damage\": %d, \"mana\": %d, \"path\": [",
               pst.alex_play_count, pst.alex_damage, pst.mana);
        for (size_t j = 0; j < pst.path.size(); j++) {
            if (j) printf(", ");
            printf("\"%s\"", json_escape(pst.path[j]).c_str());
        }
        printf("]}%s\n", i + 1 < results.size() ? "," : "");
    }
    printf("  ]\n}\n");
}

// ===================== 输入解析 =====================
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
        st.etc_band_provided = true;
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
    const JVal* effects = root.find("current_effects");
    if (effects && effects->type == JVal::ARR) {
        for (const auto& item : effects->arr) {
            const string name = item.get_str("name");
            int layers = (int)item.get_int("count", 1);
            if (layers <= 0) layers = 1;
            st.cards_played_this_turn = std::max(st.cards_played_this_turn, 1);
            if (name == "狐人老千") {
                st.next_combo = std::max(st.next_combo, 2 * layers);
            } else if (name == "伺机待发") {
                st.next_spell = std::max(st.next_spell, 2 * layers);
            } else if (name == "锯齿骨刺") {
                st.next_card = std::max(st.next_card, 2 * layers);
            } else if (name == "斯卡布斯·刀油") {
                for (int i = 0; i < layers; i++) st.oil_stacks.push_back({2, 2});
            }
        }
    }
    return st;
}

// ---------- 路径验证（--verify / replay_path）：逐动作重放，报告卡在哪一步 ----------
// 回溯匹配：同一卡名可能有多个目标实例（如多张红龙），逐个尝试，任一实例组合走通即通过
static bool verify_rec(const State& st, const vector<string>& replay, size_t i,
                       State& final_state, int* attempts) {
    if (i >= replay.size()) {
        final_state = st;
        return true;
    }
    string want = canonical_action(replay[i]);
    vector<State> succs = generate_successors(st);
    for (State& succ : succs) {
        if (succ.path.empty()) continue;
        if (canonical_action(succ.path.back()) != want) continue;
        if (verify_rec(succ, replay, i + 1, final_state, attempts)) return true;
        if (attempts && ++(*attempts) > 500000) return false;  // 回溯预算保护
    }
    if (attempts) {
        fprintf(stderr,
                "VERIFY dead-end at step %d: want=%s mana=%d dragons=%d damage=%d\n",
                (int)i, want.c_str(), st.mana, st.alex_play_count, st.alex_damage);
    }
    return false;
}

static int verify_path(const State& start, const vector<string>& replay,
                       State& final_state, int* fail_step) {
    int attempts = 0;
    bool ok = verify_rec(start, replay, 0, final_state, &attempts);
    if (!ok && fail_step) *fail_step = attempts;
    return ok ? 1 : 0;
}

int main(int argc, char** argv) {
    bool use_json = false;
    bool use_verify = false;
    SearchParams p;

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
        if (strcmp(argv[i], "--verify") == 0) { use_verify = true; continue; }
        if (strcmp(argv[i], "--mode") == 0 && i + 1 < argc) { i++; continue; }  // 兼容旧 payload，忽略
        if (next("--min", &tmp)) { p.min_alex = atoi(tmp.c_str()); continue; }
        if (next("--max", &tmp)) { p.max_alex = atoi(tmp.c_str()); continue; }
        if (next("--depth", &tmp)) { p.depth = atoi(tmp.c_str()); continue; }
        if (next("--max-paths", &tmp)) { p.max_paths = atoi(tmp.c_str()); continue; }
        if (next("--iterations", &tmp)) { p.iterations = atoi(tmp.c_str()); continue; }
        if (next("--beam-width", &tmp)) { p.beam_width = atoi(tmp.c_str()); continue; }
        if (next("--sim-depth", &tmp)) { p.sim_depth = atoi(tmp.c_str()); continue; }
        if (next("--explore-c", &tmp)) { p.explore_c = atof(tmp.c_str()); continue; }
        if (next("--games", &tmp)) { p.games = atoi(tmp.c_str()); continue; }
        if (next("--threads", &tmp)) { p.threads = atoi(tmp.c_str()); continue; }
        if (next("--time-budget", &tmp)) { p.time_budget_sec = atof(tmp.c_str()); continue; }
    }

    if (!use_json) {
        fprintf(stderr, "用法: red_dragon_engine.exe --json [--verify] < problem.json\n");
        return 1;
    }

    string input;
    char buf[65536];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), stdin)) > 0) input.append(buf, n);
    JVal root;
    if (!parse_json(input, root)) {
        fprintf(stderr, "JSON 解析失败\n");
        return 1;
    }
    State st = state_from_json(root);
    p.min_alex = (int)root.get_int("min_alex", p.min_alex);
    p.max_alex = (int)root.get_int("max_alex", p.max_alex);
    p.depth = (int)root.get_int("depth", p.depth);
    p.max_paths = (int)root.get_int("max_paths", p.max_paths);
    p.iterations = (int)root.get_int("iterations", p.iterations);
    p.beam_width = (int)root.get_int("beam_width", p.beam_width);
    p.sim_depth = (int)root.get_int("sim_depth", p.sim_depth);
    p.explore_c = root.get_double("explore_c", p.explore_c);
    p.games = (int)root.get_int("games", p.games);
    p.threads = (int)root.get_int("threads", p.threads);
    p.time_budget_sec = root.get_double("time_budget_sec", p.time_budget_sec);
    p.min_alex = std::max(1, std::min(p.min_alex, p.max_alex));
    p.iterations = std::max(1, p.iterations);
    p.beam_width = std::max(1, p.beam_width);
    p.sim_depth = std::max(1, p.sim_depth);
    p.games = std::max(1, p.games);
    p.threads = std::max(1, p.threads);

    if (!st.etc_band_provided && st.etc_band.empty()) {
        st.etc_band = {"舞动全场（ft.迦罗娜）", "幻觉药水", "生命的缚誓者阿莱克丝塔萨"};
    }

    // 路径验证模式：不搜索，只按 replay_path 重放
    if (use_verify) {
        const JVal* rp = root.find("replay_path");
        vector<string> replay;
        if (rp && rp->type == JVal::ARR) {
            for (const auto& item : rp->arr)
                if (item.type == JVal::STR) replay.push_back(item.str);
        }
        State final_state;
        int fail_step = -1;
        int ok = verify_path(st, replay, final_state, &fail_step);
        printf("{\n");
        printf("  \"ok\": %s,\n", ok ? "true" : "false");
        printf("  \"fail_step\": %d,\n", fail_step);
        printf("  \"dragons\": %d,\n", final_state.alex_play_count);
        printf("  \"damage\": %d,\n", final_state.alex_damage);
        printf("  \"mana\": %d,\n", final_state.mana);
        printf("  \"path\": [");
        for (size_t i = 0; i < final_state.path.size(); i++) {
            if (i) printf(", ");
            printf("\"%s\"", json_escape(final_state.path[i]).c_str());
        }
        printf("]\n}\n");
        return ok ? 0 : 1;
    }

    Progress prog;
    prog.enabled = true;
    MctsResult res = run_mcts_search(st, p, &prog);
    if (prog.enabled) {
        fprintf(stderr, "PROGRESS %d %d %d\n", res.expansions, res.simulations, res.reached_depth);
    }

    print_json_result(res, p);
    return 0;
}
