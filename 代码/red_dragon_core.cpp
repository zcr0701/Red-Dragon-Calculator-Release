// 红龙贼计算器 C++ 计算核心（纯束宽搜索 + 瓶颈模型启发）。
//
// 架构（2026-08-06 纯束宽重构，彻底移除 MCTS）：
//   - 并行多路宽束搜索（默认束宽 {2400, 600}）：2400 保小局面深线（如 3 龙/48 伤），
//     600 保大局面 2 秒时限内走深（如 10 龙/152 伤）；共享时间预算，合并各龙数最优路径。
//   - 简单启发函数（无子链库）：瓶颈模型（Liebig 最小因子律），
//     可达龙数 ≈ min(① 龙源数, ② 回手容量, ③ 法力可负担轮数)，
//     束内保留评分 = 当前伤害 + 启发函数值；按龙数分桶 + 启发值冠军兜底。
//   - 默认 2 秒时间预算（硬时限 ≤3 秒，宽束循环内细粒度中断），大部分局面 1~2 秒出结果。
//
// 编译（MinGW g++）：g++ -std=c++17 -O3 -static -o red_dragon_engine.exe red_dragon_core.cpp
// 用法：red_dragon_engine.exe --json < problem.json
// 输入局面（stdin JSON）：
//   {"crystals":8,"mana":8,"hand":[{"name":"鲨鱼之灵","temp_cost":2,"locked":false,"deadly":false}],
//    "board":[...],"secrets":[...],"weapon":{...},"deck":[...],"etc_band":[...],
//    "current_effects":[{"name":"狐人老千","count":2}],
//    "min_alex":1,"max_alex":10,"depth":30,"max_paths":1000000,
//    "threads":4,"time_budget_sec":2.0,"heuristic":-1,"wide_widths":[2400,600]}
// 输出（stdout）：{"mode":"beam","results":[{"dragons","damage","mana","path"}],"stats":{...}}
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

static uint64_t str_hash(const string& s);
static uint64_t mix_hash(uint64_t h, uint64_t x);

// 字符串驻留池（索引版）：索引稳定（vector 不因增长失效）。
// 静态初始化预驻留全部 DB 卡名/类型/效果 + 固定未知名；快照输入名在解析期（单线程）驻留；
// 束搜索线程只读池（pre-reserve 防重分配），比较全部是 O(1) uint16 比较。
static vector<std::shared_ptr<const std::string>> g_intern_pool;
static vector<uint64_t> g_intern_hash;  // 每个驻留串的 str_hash（复现原 static_hash 语义，保搜索行为不变）
static unordered_map<string, uint16_t> g_intern_idx_map;
static std::atomic<bool> g_intern_lock{false};

static uint16_t intern_idx(const string& name) {
    while (g_intern_lock.exchange(true, std::memory_order_acquire)) {
        // 自旋等待（临界区极短，仅首次插入才分配）
    }
    uint16_t idx = 0;
    auto it = g_intern_idx_map.find(name);
    if (it == g_intern_idx_map.end()) {
        idx = (uint16_t)g_intern_pool.size();
        g_intern_pool.push_back(std::make_shared<const std::string>(name));
        g_intern_hash.push_back(str_hash(name));
        g_intern_idx_map.emplace(name, idx);
    } else {
        idx = it->second;
    }
    g_intern_lock.store(false, std::memory_order_release);
    return idx;
}

static const string& intern_str(uint16_t idx) {
    static const string empty;
    return idx < g_intern_pool.size() ? *g_intern_pool[idx] : empty;
}

// 兼容旧接口（部分非热路径调用方仍按 shared_ptr 取用）
static std::shared_ptr<const std::string> intern_name(const string& name) {
    return g_intern_pool[intern_idx(name)];
}

// 热路径卡名/类型/效果驻留索引：uint16 比较 O(1)、Card 体积骤减。
static const uint16_t N_SHARK = intern_idx("鲨鱼之灵");
static const uint16_t N_MOTHER = intern_idx("晦鳞巢母");
static const uint16_t N_CASTER = intern_idx("暗影施法者");
static const uint16_t N_STEP = intern_idx("暗影步");
static const uint16_t N_DANCE = intern_idx("舞动全场（ft.迦罗娜）");
static const uint16_t N_POTION = intern_idx("幻觉药水");
static const uint16_t N_SCABBS = intern_idx("斯卡布斯·刀油");
static const uint16_t N_ETC = intern_idx("乐队经理精英牛头人酋长");
static const uint16_t N_DRAGON = intern_idx("生命的缚誓者阿莱克丝塔萨");
static const uint16_t N_TENWU = intern_idx("赤烟·腾武");
static const uint16_t N_TRANSFER = intern_idx("战略转移");
static const uint16_t N_DEADLY = intern_idx("殒命暗影");
static const uint16_t N_SWINDLE = intern_idx("行骗");
static const uint16_t N_DIG = intern_idx("挖掘宝藏");
static const uint16_t N_CURTAIN = intern_idx("潜伏帷幕");
static const uint16_t N_QUICKDRAW = intern_idx("持枪要挟");
static const uint16_t N_FISHIN = intern_idx("垂钓时光");
static const uint16_t N_FOX = intern_idx("狐人老千");
static const uint16_t N_BONE = intern_idx("锯齿骨刺");
// 卡牌类型
static const uint16_t N_T_MINION = intern_idx("minion");
static const uint16_t N_T_SPELL = intern_idx("spell");
static const uint16_t N_T_SECRET = intern_idx("secret");
static const uint16_t N_T_WEAPON = intern_idx("weapon");
static const uint16_t N_T_UNKNOWN = intern_idx("unknown");
// 卡牌效果（effect_id）
static const uint16_t N_E_NONE = intern_idx("unknown");
static const uint16_t N_E_COIN = intern_idx("coin");
static const uint16_t N_E_FAKE_COIN = intern_idx("fake_coin");
static const uint16_t N_E_PREPARATION = intern_idx("preparation");
static const uint16_t N_E_PLAGUE = intern_idx("plague_of_madness");
static const uint16_t N_E_LUCKY_COMET = intern_idx("lucky_comet");
static const uint16_t N_E_FOXY = intern_idx("foxy_fraud");
static const uint16_t N_E_SCABBS_CUT = intern_idx("scabbs_cutterbutter");
static const uint16_t N_E_TRANSFER = intern_idx("strategic_transfer");
static const uint16_t N_E_STEP = intern_idx("shadowstep");
static const uint16_t N_E_CASTER = intern_idx("shadowcaster");
static const uint16_t N_E_TENWU = intern_idx("tenwu");
static const uint16_t N_E_DANCE = intern_idx("breakdance");
static const uint16_t N_E_POTION = intern_idx("potion_of_illusion");
static const uint16_t N_E_MOTHER = intern_idx("candlebreath_mother");
static const uint16_t N_E_REHYDRATE = intern_idx("rehydrate");
static const uint16_t N_E_SWINDLE = intern_idx("swindle");
static const uint16_t N_E_FISHIN = intern_idx("gone_fishin");
static const uint16_t N_E_BONE = intern_idx("serrated_bone_spike");
static const uint16_t N_E_GAMBLER = intern_idx("gambler_hunter");
static const uint16_t N_E_CULTIST = intern_idx("cultist_map");
static const uint16_t N_E_ETC = intern_idx("elite_tauren_champion");
static const uint16_t N_E_QUICKDRAW = intern_idx("discover_quickdraw");
static const uint16_t N_E_MISFIRE = intern_idx("misfire");
static const uint16_t N_E_DEHYDRATE = intern_idx("dehydrate");
static const uint16_t N_E_POCKET_SAND = intern_idx("pocket_sand");
static const uint16_t N_E_LAY_DOWN = intern_idx("lay_down_the_law");
static const uint16_t N_E_ALEX = intern_idx("alexstrasza");

// ===================== 卡牌 =====================
struct Card {
    uint16_t name_idx = 0;      // 驻留池索引（比较 O(1)）
    uint16_t original_idx = 0;
    uint16_t type_idx = 0;      // minion/spell/secret/weapon/unknown
    uint16_t effect_idx = 0;    // 效果 ID（shadowcaster 等）
    int8_t cost = -1;        // -1 = 无固定费用（殒命暗影）；炉石费用 ≤10，int8 足够
    int8_t temp_cost = -1;   // -1 = 无
    int8_t health = -1;      // -1 = 无；随从血量 ≤30，int8 足够
    bool battlecry = false;
    bool combo = false;
    bool dragon = false;
    bool is_deadly_shadow = false;
    bool locked_one_cost = false;
    bool entered_hand_this_turn = false;  // 快枪判定：本回合进入手牌（含回手/发现/复制）
    bool is_mini_copy = false;            // 暗影施法者/幻觉药水制造的 1/1 复制（回手不还原）

    const string& name() const { return intern_str(name_idx); }
    const string& original_name() const { return intern_str(original_idx); }
    const string& card_type_str() const { return intern_str(type_idx); }
    const string& effect_id_str() const { return intern_str(effect_idx); }
    int current_cost() const { return temp_cost >= 0 ? temp_cost : cost; }
    bool is_spell_like() const { return type_idx == N_T_SPELL || type_idx == N_T_SECRET; }
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
    {"疯狂之灾祸", {1, "spell", "plague_of_madness", false, false, false, -1}},
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
    {"“赤烟”腾武", {2, "minion", "tenwu", true, false, false, 2}},  // 官方名（日志/卡图）别名
    {"押注猎手", {3, "minion", "gambler_hunter", false, true, false, 4}},  // 快枪或连击：获取一张幸运币
    {"狐人老千", {2, "minion", "foxy_fraud", true, false, false, 2}},  // 战吼：下一张连击牌减 2 费（非连击牌）
    {"持枪要挟", {1, "spell", "discover_quickdraw", false, false, false, -1}},  // 发现一张另一职业的快枪牌
    // ===== 持枪要挟发现牌池（另一职业快枪牌，牌池固定）=====
    // 已建模（按 可能机制 打分：补水5 > 脱水4 > 误炸3 > 袋底藏沙2 > 不许乱动1）：
    //   补水（快枪：复原两个法力水晶 → +2 法力，上限水晶）
    //   脱水（快枪：法力值消耗为 1 → 本回合进入手牌时按 1 费）
    //   误炸（快枪可选目标造成 3/2/1 点伤害 → 与 OTK 无关，按普通法术）
    //   袋底藏沙（快枪敌方下一张 +1 费，只影响对方 → 按普通法术）
    //   不许乱动（变 1/1 + 1 伤 → 与 OTK 无关，按普通法术）
    // 未建模（未知机制，先纳入备注忽略）：
    //   农场小助手（战吼发现亡灵 + 快枪减 2 费）、银蛇（突袭剧毒快枪免疫）、
    //   热浪来袭（快枪全体 2 伤）、和善的银行职员（发现法术）、
    //   亮石旋岩虫（吸血快枪 5 伤）、列车难题（弃牌召矿车）
    {"补水", {2, "spell", "rehydrate", false, false, false, -1}},
    {"脱水", {3, "spell", "dehydrate", false, false, false, -1}},
    {"误炸", {2, "spell", "misfire", false, false, false, -1}},
    {"袋底藏沙", {2, "spell", "pocket_sand", false, false, false, -1}},
    {"不许乱动", {2, "spell", "lay_down_the_law", false, false, false, -1}},
    {"农场小助手", {3, "minion", "farm_hand", false, false, false, 3}},
    {"银蛇", {3, "minion", "silver_serpent", false, false, false, 3}},
    {"热浪来袭", {2, "spell", "heat_wave", false, false, false, -1}},
    {"和善的银行职员", {3, "minion", "benevolent_banker", false, false, false, 4}},
    {"亮石旋岩虫", {4, "minion", "glowstone_gyreworm", false, false, false, 4}},
    {"列车难题", {3, "spell", "trolley_problem", false, false, false, -1}},
};

// 持枪要挟已建模的发现池（按优先级打分排序；未建模牌见上方备注）
static const vector<string> QUICKDRAW_MODELED_POOL = {
    "补水", "脱水", "误炸", "袋底藏沙", "不许乱动",
};
static const unordered_map<string, int> QUICKDRAW_POOL_SCORE = {
    {"补水", 5},
    {"脱水", 4},
    {"误炸", 3},
    {"袋底藏沙", 2},
    {"不许乱动", 1},
};
// 其余未建模快枪牌按类型分成两个杂牌分支（置入未知杂牌，按数量加权）：
//   其他快枪牌·随从（4 张）：农场小助手/银蛇/和善的银行职员/亮石旋岩虫 → 随从杂牌（占随从栏）
//   其他快枪牌·法术（2 张）：热浪来袭/列车难题 → 法术杂牌（不占随从栏）
static const string QUICKDRAW_OTHER_MINION_NAME = "其他快枪牌·随从";
static const int QUICKDRAW_OTHER_MINION_COUNT = 4;
static const string QUICKDRAW_OTHER_SPELL_NAME = "其他快枪牌·法术";
static const int QUICKDRAW_OTHER_SPELL_COUNT = 2;

// 预驻留：DB 全部卡名/类型/效果 + 固定未知名，保证束搜索期间驻留池不再增长
// （搜索线程只读 g_intern_pool，pre-reserve 防重分配；输入快照名在解析期驻留）。
static const bool g_preintern_done = []() {
    g_intern_pool.reserve(1024);
    for (const auto& kv : DB) {
        intern_idx(kv.first);
        intern_idx(kv.second.card_type);
        intern_idx(kv.second.effect_id);
    }
    static const char* const extras[] = {
        "未知随从", "未知法术", "未知抽牌", "未知杂牌",
        "未知快枪牌随从", "未知快枪牌法术", "敌方随从",
        "幸运币", "伪造的幸运币", "补水", "脱水", "误炸",
        "袋底藏沙", "不许乱动", "其他快枪牌·随从", "其他快枪牌·法术",
        "赤烟·腾武", "“赤烟”腾武", "阿莱克斯塔萨", "阿莱克丝塔萨",
        "生命的缚誓者阿莱克斯塔萨", "舞动全场（ft.迦罗娜）", "幻觉药水",
        "生命的缚誓者阿莱克丝塔萨", "战略转移", "乐队经理精英牛头人酋长",
        "殒命暗影", "行骗", "挖掘宝藏", "潜伏帷幕", "持枪要挟", "垂钓时光",
    };
    for (const char* s : extras) intern_idx(s);
    return true;
}();

// 误炸快枪分支上限（3 点 + 2 点 + 1 点可击杀最多 3 个随从，组合数防止爆炸）
static const size_t MAX_MISFIRE_BRANCHES = 80;

// ===================== 抽牌属性 =====================
// 抽牌属性：卡牌会抽牌时记录“抽什么类型的牌、几张”：
//   - random=随机：发现（选 3 张随机牌抽 1 张）或未知类型的普通抽牌，抽到的牌类型不可知；
//   - minion=随从 / spell=法术：抽牌类型已知。
// 只用于辅助“是否考虑展开该路径”的判断（generate_successors / cards_drawn_if_played），
// 不模拟具体抽到哪张牌（牌库未知时抽牌结果不可建模）。
struct DrawSpec {
    string type;              // "random" / "minion" / "spell"
    int count = 0;            // 张数
    bool conditional = false; // 有条件才抽（预留；当前无此类抽牌卡）
};

// 基础抽牌（无条件部分）；DRAW_ATTR_COMBO 为连击触发时追加的部分。
static const unordered_map<string, vector<DrawSpec>> DRAW_ATTR_BASE = {
    {"dig_for_treasure", {{"minion", 1}}},           // 挖掘宝藏：抽 1 张随从牌
    {"cultist_map", {{"random", 1}}},                // 异教地图：从牌库发现（选 3 张随机抽 1）→ 随机
    {"swindle", {{"spell", 1}}},                     // 行骗：抽 1 张法术牌
    {"shroud_of_concealment", {{"minion", 2}}},      // 潜伏帷幕：抽 2 张随从牌
    {"dubious_purchase", {{"random", 3}}},           // 可疑交易：抽 3 张随机牌
};
static const unordered_map<string, vector<DrawSpec>> DRAW_ATTR_COMBO = {
    {"gone_fishin", {{"random", 1}}},  // 垂钓时光：基础不抽（探底），连击才抽 1 张
    {"swindle", {{"minion", 1}}},      // 行骗：连击再抽 1 张随从牌
};

static uint64_t str_hash(const string& s);
static uint64_t mix_hash(uint64_t h, uint64_t x);

static Card make_card(const string& name, int cost_override = -1) {
    Card c;
    string normalized = name;
    if (name == "“赤烟”腾武") normalized = "赤烟·腾武";  // 统一规范名
    if (normalized == "阿莱克斯塔萨" || normalized == "阿莱克丝塔萨" ||
        normalized == "生命的缚誓者阿莱克斯塔萨") {
        normalized = "生命的缚誓者阿莱克丝塔萨";  // 红龙常用误写（斯/丝）
    }
    auto it = DB.find(normalized);
    if (it == DB.end()) {
        c.name_idx = intern_idx(normalized);
        c.original_idx = c.name_idx;
        c.type_idx = N_T_UNKNOWN;
        c.effect_idx = N_E_NONE;
        return c;
    }
    const CardDef& d = it->second;
    c.name_idx = intern_idx(normalized);
    c.original_idx = c.name_idx;
    c.type_idx = intern_idx(d.card_type);
    c.effect_idx = intern_idx(d.effect_id);
    c.cost = (int8_t)(cost_override >= 0 ? cost_override : d.cost);
    c.battlecry = d.battlecry;
    c.combo = d.combo;
    c.dragon = d.dragon;
    c.health = (int8_t)d.health;
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
    vector<Card> enemy_board;  // 敌方随从（只需血量，用于锯齿骨刺击杀目标判定）
    vector<Card> deck;
    vector<Card> secrets;
    Card weapon;
    bool has_weapon = false;
    bool deck_is_known = false;
    bool branch_expand = true;  // W-B：抽随从卡分支是否直接展开进束宽搜索（主窗口“W-B机制”勾选框）
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
    int sp_cost_inc = 0;  // 敌方法术增费（[spcost+1]/[spcost+2]），只作用于法术
    vector<pair<int, int>> oil_stacks;  // (剩余次数, 减费)
    int burned_cards = 0;
    int alex_play_count = 0;
    int alex_damage = 0;
    vector<string> etc_band;
    bool etc_band_provided = false;   // JSON 显式传了 etc_band（空数组=牛池已空）
    string forced_discover_choice;    // 可能分支机制：强制持枪要挟发现某张牌（空=全部展开）
    string forced_draw_choice;        // WhatIF 同级分支：强制抽随从卡抽到某张随从（空=全部展开）
    int fork_damage = 0;              // 最近一次分支卡（行骗/挖掘/潜伏/垂钓/持枪）打出时的伤害
                                      // = 该分叉点之前的伤害；用于“后续造成伤害为0则不显示分支”
    int quickdraw_choice = -1;        // 路径中第一张持枪要挟的发现牌（QUICKDRAW_MODELED_POOL 下标；-1=无）
    bool used_quickdraw = false;      // 路径中是否打出过持枪要挟（原版=不含持枪的最优线）
    bool used_draw_branch = false;    // 路径中是否打出过抽随从分支卡（行骗/挖掘宝藏/潜伏帷幕/垂钓时光）
    string last_draw_key;             // 最近一次抽随从分支卡抽到的随从集（如 “狐人老千”/“刀、狐”，空=未抽）
    vector<string> dredge_bottom;     // 垂钓时光探底已知牌（牌库底 3 张中已知的部分，≤3；缺位=未知杂牌）
    std::shared_ptr<vector<string>> path_buf;  // 路径共享存储（克隆 O(1)，写时复制）

    const vector<string>& path() const {
        static const vector<string> empty;
        return path_buf ? *path_buf : empty;
    }
    vector<string>& path_mut() {
        if (!path_buf) {
            path_buf = std::make_shared<vector<string>>();
        } else if (path_buf.use_count() > 1) {
            path_buf = std::make_shared<vector<string>>(*path_buf);
        }
        return *path_buf;
    }

    State clone() const { return *this; }  // path_buf 共享，克隆 O(1)

    // 克隆时直接按目标容量各分配一次（避免"拷贝后 reserve"的二次重分配），
    // 后续 push_back 不触发重分配；path_buf 共享，克隆 O(1)。
    State clone_reserved() const {
        State c;
        c.hand.reserve(std::max((size_t)MAX_HAND, hand.size()));
        c.hand.assign(hand.begin(), hand.end());
        c.board.reserve(std::max((size_t)MAX_BOARD, board.size()));
        c.board.assign(board.begin(), board.end());
        c.enemy_board.reserve(enemy_board.size() + 1);
        c.enemy_board.assign(enemy_board.begin(), enemy_board.end());
        c.deck.reserve(std::max((size_t)16, deck.size()));
        c.deck.assign(deck.begin(), deck.end());
        c.secrets.reserve(std::max((size_t)MAX_SECRET, secrets.size()));
        c.secrets.assign(secrets.begin(), secrets.end());
        c.oil_stacks.reserve(oil_stacks.size() + 1);
        c.oil_stacks.assign(oil_stacks.begin(), oil_stacks.end());
        c.etc_band.reserve(etc_band.size() + 2);
        c.etc_band.assign(etc_band.begin(), etc_band.end());
        c.weapon = weapon;
        c.has_weapon = has_weapon;
        c.deck_is_known = deck_is_known;
        c.branch_expand = branch_expand;
        c.mana_crystals = mana_crystals;
        c.mana = mana;
        c.initial_mana_crystals = initial_mana_crystals;
        c.initial_mana = initial_mana;
        c.cards_played_this_turn = cards_played_this_turn;
        c.next_spell = next_spell;
        c.next_combo = next_combo;
        c.next_combo_twice = next_combo_twice;
        c.next_card = next_card;
        c.next_two_cards = next_two_cards;
        c.next_two_cards_count = next_two_cards_count;
        c.sp_cost_inc = sp_cost_inc;
        c.burned_cards = burned_cards;
        c.alex_play_count = alex_play_count;
        c.alex_damage = alex_damage;
        c.etc_band_provided = etc_band_provided;
        c.forced_discover_choice = forced_discover_choice;
        c.forced_draw_choice = forced_draw_choice;
        c.fork_damage = fork_damage;
        c.quickdraw_choice = quickdraw_choice;
        c.used_quickdraw = used_quickdraw;
        c.used_draw_branch = used_draw_branch;
        c.last_draw_key = last_draw_key;
        c.dredge_bottom.reserve(dredge_bottom.size() + 1);
        c.dredge_bottom.assign(dredge_bottom.begin(), dredge_bottom.end());
        c.path_buf = path_buf;
        return c;
    }

    bool has_shark() const {
        for (const auto& c : board)
            if (c.name_idx == N_SHARK) return true;
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
    // 脱水快枪：本回合进入手牌时法力值消耗为 1
    if (card.effect_idx == N_E_DEHYDRATE && card.entered_hand_this_turn) base = 1;
    int discount = 0;
    if (s.next_card > 0) discount += s.next_card;
    if (s.next_two_cards_count > 0) discount += s.next_two_cards;
    for (const auto& p : s.oil_stacks)
        if (p.first > 0 && p.second > 0) discount += p.second;
    if (card.is_spell_like() && s.next_spell > 0) discount += s.next_spell;
    if (card.combo && s.next_combo > 0) discount += s.next_combo;
    int cost = base - discount;
    if (card.is_spell_like() && s.sp_cost_inc > 0) cost += s.sp_cost_inc;
    return cost < 0 ? 0 : cost;
}

// 同名同状态手牌：打出效果与结果完全相同（手牌位置不影响状态哈希），
// 展开时只保留第一张，避免为重复卡各克隆一次状态。
// 仅当该差异会影响打出结果时才不合并：押注猎手的快枪（本回合入牌）与
// 战略转移可还原时的 1/1 复制身份。
static bool same_playable_card(const State& s, const Card& a, const Card& b) {
    if (a.name_idx != b.name_idx || a.current_cost() != b.current_cost() ||
        a.is_deadly_shadow != b.is_deadly_shadow ||
        a.locked_one_cost != b.locked_one_cost)
        return false;
    if (a.effect_idx == N_E_GAMBLER &&
        a.entered_hand_this_turn != b.entered_hand_this_turn)
        return false;
    bool transfer_around = false;
    for (const auto& c : s.hand)
        if (c.name_idx == N_TRANSFER) { transfer_around = true; break; }
    if (!transfer_around) {
        for (const auto& n : s.etc_band)
            if (n == "战略转移") { transfer_around = true; break; }
    }
    if (transfer_around && a.is_mini_copy != b.is_mini_copy) return false;
    return true;
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
    if (card.effect_idx == N_E_TENWU) return 1;  // 腾武是单体回手，鲨鱼不重复触发（避免回两张）
    if ((card.battlecry || card.combo) && s.has_shark()) return 2;
    return 1;
}

static void add_card_to_hand_or_burn(State& s, const Card& card) {
    Card added = card;
    added.entered_hand_this_turn = true;  // 搜索中的入牌都算“本回合进入手牌”（快枪判定）
    if (s.hand_full()) {
        s.burned_cards++;
    } else {
        s.hand.push_back(added);
    }
}

static void transform_deadly_shadows(State& s, const Card& spell_card) {
    if (spell_card.original_idx == N_DEADLY) return;
    auto it = DB.find(spell_card.original_name());
    if (it == DB.end()) return;
    for (auto& c : s.hand) {
        if (c.is_deadly_shadow) {
            Card copy = make_card(spell_card.original_name());
            copy.is_deadly_shadow = true;
            c = copy;
        }
    }
}

static void append_choice_to_last_path(State& s, const string& choice_name) {
    if (s.path().empty()) return;
    string& last = s.path_mut().back();
    static const string full_width_close = "）";
    if (last.find("（") != string::npos && last.size() >= full_width_close.size() &&
        last.compare(last.size() - full_width_close.size(), full_width_close.size(), full_width_close) == 0) {
        last = last.substr(0, last.size() - full_width_close.size()) + "->" + choice_name + "）";
    } else {
        last = last + "（" + choice_name + "）";
    }
}

static string display_card_name(const Card& card) {
    if (card.is_deadly_shadow) return card.name() + "[殒命暗影]";
    return card.name();
}

// 打出基础：扣费、移出手牌、消耗减费、记账、进区
static bool play_card_base(State& s, int hand_index, int target_friendly_index,
                           bool target_enemy_is_killed, bool enemy_target) {
    if (hand_index < 0 || hand_index >= (int)s.hand.size()) return false;
    Card card = s.hand[hand_index];
    int cost = effective_cost(s, card);
    if (cost < 0 || s.mana < cost) return false;
    // 杂牌（unknown）可能是随从：打出会占格子，按随从处理
    if ((card.type_idx == N_T_MINION || card.type_idx == N_T_UNKNOWN) && s.board_full()) return false;
        if (card.type_idx == N_T_SECRET) {
            if ((int)s.secrets.size() >= MAX_SECRET) return false;
            for (const auto& sec : s.secrets)
                if (sec.name() == card.name()) return false;  // 每种奥秘只能装备一个
        }

    s.mana -= cost;
    s.hand.erase(s.hand.begin() + hand_index);
    consume_discounts(s, card);

    string item = display_card_name(card);
    if (target_friendly_index >= 0) {
        if (target_friendly_index < (int)s.board.size()) {
            // 指向性操作统一标注目标在 board 上的顺序（1 起）：
            // 卡名（目标名Nnd），如 暗影步（斯卡布斯·刀油3nd）
            item += "（" + s.board[target_friendly_index].name()
                  + std::to_string(target_friendly_index + 1) + "nd）";
        } else {
            item += "（无效目标）";
        }
    } else if (target_friendly_index < -1) {
        // 敌方随从目标（编码：-2 = 第0个敌方随从）：同样带敌方 board 序号
        int eidx = -target_friendly_index - 2;
        if (eidx >= 0 && eidx < (int)s.enemy_board.size()) {
            item += "（" + s.enemy_board[eidx].name()
                  + std::to_string(eidx + 1) + "nd）";
        } else {
            item += "（敌方随从）";
        }
    }
    s.path_mut().push_back(item);

    if (card.name_idx == N_DRAGON) {
        s.alex_play_count++;
        if (enemy_target) {
            int mult = s.has_shark() ? 2 : 1;
            s.alex_damage += 8 * mult;
        }
    }

    if (card.type_idx == N_T_MINION || card.type_idx == N_T_UNKNOWN) {
        s.board.push_back(card);
    } else if (card.type_idx == N_T_SECRET) {
        s.secrets.push_back(card);
    } else if (card.type_idx == N_T_WEAPON) {
        s.weapon = card;
        s.has_weapon = true;
    }
    return true;
}

// 牛头人发现：从乐队中选一张，加入手牌并从乐队移除一个同名项
static vector<State> discover_fixed_choices(const State& base) {
    vector<State> out;
    if (base.etc_band.empty()) {
        out.push_back(base.clone_reserved());
        return out;
    }
    for (size_t i = 0; i < base.etc_band.size(); i++) {
        State s = base.clone_reserved();
        string choice = s.etc_band[i];
        s.etc_band.erase(s.etc_band.begin() + i);
        add_card_to_hand_or_burn(s, make_card(choice));
        append_choice_to_last_path(s, choice);
        out.push_back(s);
    }
    return out;
}

// 舞动全场：按进场顺序全部回手（1 费），手牌满则按进场顺序爆牌。
// 回手会清除随从身上的附魔（暗施/药水 1/1 复制、buff 等还原为原始身材），
// 仅保留殒命暗影变形标记；费用统一变为 1。
static State breakdance_branch(const State& base) {
    State s = base.clone_reserved();
    vector<Card> returning = s.board;
    s.board.clear();
    auto revert_to_original = [](Card& m) {
        Card orig = make_card(m.name());
        orig.is_deadly_shadow = m.is_deadly_shadow;
        orig.temp_cost = 1;
        m = orig;
    };
    int free_slots = std::max(0, MAX_HAND - s.hand_size());
    if ((int)returning.size() <= free_slots) {
        for (Card& m : returning) {
            revert_to_original(m);
            add_card_to_hand_or_burn(s, m);
        }
    } else {
        for (int i = 0; i < free_slots; i++) {
            Card& m = returning[i];
            revert_to_original(m);
            add_card_to_hand_or_burn(s, m);
        }
        s.burned_cards += (int)returning.size() - free_slots;
    }
    return s;
}

// 效果结算：单分支效果原地修改（零克隆，对应“增量状态”优化）；
// 仅牛头人发现等真多分支效果走克隆路径。
static bool apply_effect_inplace(State& s, uint16_t e, const Card& card,
                                 int target_friendly_index, bool target_enemy_is_killed) {
    if (e == N_E_COIN || e == N_E_FAKE_COIN) {
        s.mana += 1;  // 临时法力不封顶（与 Python gain_temporary 一致）
    } else if (e == N_E_PREPARATION) {
        s.next_spell += 2;
    } else if (e == N_E_PLAGUE) {
        // 疯狂之灾祸：双方各装备一把 2/2 剧毒刀。武器攻击不计入 OTK 伤害，
        // 仅作为 1 费法术（连击触发器 / 殒命暗影变形源 / 杂牌）参与搜索。
    } else if (e == N_E_LUCKY_COMET) {
        // 幸运彗星：将一张类型为随从的杂牌置入手牌；获得一次性效果——
        // 下一张连击随从的连击触发两次（仅当连击能触发时生效并消耗，跨回合保留）。
        Card junk = make_card("未知随从");
        junk.type_idx = N_T_MINION;
        junk.entered_hand_this_turn = true;
        add_card_to_hand_or_burn(s, junk);
        s.next_combo_twice = true;
    } else if (e == N_E_FOXY) {
        // 战吼：下一张连击牌减 2 费。不需要本回合先出牌触发——
        // 即使作为本回合第一张牌打出也生效（鲨鱼双战吼 → 下一张连击牌 -4）。
        s.next_combo += 2;
    } else if (e == N_E_SCABBS_CUT) {
        if (s.cards_played_this_turn > 0) {
            // 连击：接下来两张牌各减 2（幸运彗星双触发 = 两次各推 {2,2}）
            s.oil_stacks.push_back({2, 2});
        }
    } else if (e == N_E_TRANSFER) {
        // 战略转移：所有友方随从移回手牌，一律还原为原始版本
        // （费用恢复原始消耗、身材恢复原始身材；暗施/药水 1/1 复制、腾武锁 1 费同样还原）
        vector<Card> returning = s.board;
        s.board.clear();
        int free_slots = std::max(0, MAX_HAND - (int)s.hand.size());
        auto revert_to_original = [](Card& m) {
            Card orig = make_card(m.name());
            orig.is_deadly_shadow = m.is_deadly_shadow;
            m = orig;
        };
        if ((int)returning.size() <= free_slots) {
            for (Card& m : returning) {
                revert_to_original(m);
                add_card_to_hand_or_burn(s, m);
            }
        } else {
            for (int i = 0; i < free_slots; i++) {
                Card& m = returning[i];
                revert_to_original(m);
                add_card_to_hand_or_burn(s, m);
            }
            s.burned_cards += (int)returning.size() - free_slots;
        }
    } else if (e == N_E_STEP) {
        if (target_friendly_index >= 0 && target_friendly_index < (int)s.board.size()) {
            Card target = s.board[target_friendly_index];
            s.board.erase(s.board.begin() + target_friendly_index);
            // 暗影步：无论此前受何影响（舞动 1 费 / 暗施·药水 1/1 复制 / 腾武锁 1 费），
            // 一律还原为原始版本（原始身材），再按原始费用减 2
            Card orig = make_card(target.name());
            orig.is_deadly_shadow = target.is_deadly_shadow;
            orig.temp_cost = std::max(0, (orig.current_cost() >= 0 ? orig.current_cost() : 0) - 2);
            target = orig;
            add_card_to_hand_or_burn(s, target);
        }
    } else if (e == N_E_CASTER) {
        if (target_friendly_index >= 0 && target_friendly_index < (int)s.board.size()) {
            Card copied = s.board[target_friendly_index].clone();
            copied.temp_cost = 1;
            copied.health = 1;
            copied.is_mini_copy = true;
            add_card_to_hand_or_burn(s, copied);
        }
    } else if (e == N_E_TENWU) {
        if (target_friendly_index >= 0 && target_friendly_index < (int)s.board.size()) {
            Card target = s.board[target_friendly_index];
            s.board.erase(s.board.begin() + target_friendly_index);
            // 腾武回手：费用变为 1，但不再强制锁定——可再被刀油/骨刺/伺机等减费
            target.temp_cost = 1;
            add_card_to_hand_or_burn(s, target);
        }
    } else if (e == N_E_DANCE) {
        State ns = breakdance_branch(s);
        s = std::move(ns);
    } else if (e == N_E_POTION) {
        vector<Card> copies;
        for (const auto& m : s.board) {
            Card copy = m.clone();
            copy.temp_cost = 1;
            copy.health = 1;
            copy.is_mini_copy = true;
            copies.push_back(copy);
        }
        for (const auto& c : copies) add_card_to_hand_or_burn(s, c);
    } else if (e == N_E_MOTHER) {
        bool dragon_in_hand = false;
        for (const auto& c : s.hand)
            if (c.dragon) { dragon_in_hand = true; break; }
        if (dragon_in_hand) {
            s.mana = std::min(s.mana_crystals, s.mana + 2);
        }
    } else if (e == N_E_REHYDRATE) {
        // 补水快枪：本回合进入手牌时复原两个法力水晶
        if (card.entered_hand_this_turn) {
            s.mana = std::min(s.mana_crystals, s.mana + 2);
        }
    } else if (e == N_E_SWINDLE) {
        if (s.deck_is_known) {
            // 行骗：不连击抽 1 张法术；连击再抽 1 张随从（即连击共抽法术+随从 2 张）。
            // 牌库已知时按实际牌库精确模拟（移出牌库进手牌/满手烧毁）：
            // 牌库里还有随从（如红龙）就抽得到，不受“随从组集齐=随从已抽完”启发影响
            // （该启发只用于牌库未知的兜底，见 cards_drawn_if_played）。
            bool combo = s.cards_played_this_turn > 0;  // 本张行骗的连击状态
            for (auto it = s.deck.begin(); it != s.deck.end(); ++it) {
                if (!it->is_spell_like()) continue;
                Card drawn = *it;
                s.deck.erase(it);
                add_card_to_hand_or_burn(s, drawn);
                break;
            }
            if (combo) {
                for (auto it = s.deck.begin(); it != s.deck.end(); ++it) {
                    bool minion = it->type_idx == N_T_MINION || it->type_idx == N_T_UNKNOWN;
                    if (!minion) continue;
                    Card drawn = *it;
                    s.deck.erase(it);
                    add_card_to_hand_or_burn(s, drawn);
                    break;
                }
            }
        } else {
            // 牌库未知：行骗保底抽 1 张法术（必然发生，不能当成免费连击触发器）。
            // 把该法术作为“未知法术”杂牌置入手牌（满手按进场顺序烧毁）；
            // 连击的随从部分不模拟（牌库是否还有随从未知，避免虚构抽牌后继）。
            Card unknown_spell = make_card("未知法术");
            unknown_spell.type_idx = N_T_SPELL;
            add_card_to_hand_or_burn(s, unknown_spell);
        }
    } else if (e == N_E_FISHIN) {
        // 垂钓时光：连击才抽 1 张牌（不连击只有探底，不抽牌）。
        // 牌库已知：按实际牌库抽第一张（移出牌库进手牌/满手烧毁）；
        // 牌库未知：把抽到的牌作为“未知抽牌”杂牌置入手牌（不能当免费连击触发器）。
        if (s.cards_played_this_turn > 0) {
            if (s.deck_is_known && !s.deck.empty()) {
                Card drawn = s.deck.front();
                s.deck.erase(s.deck.begin());
                add_card_to_hand_or_burn(s, drawn);
            } else if (!s.deck_is_known) {
                Card unknown = make_card("未知抽牌");
                add_card_to_hand_or_burn(s, unknown);
            }
        }
    } else if (e == N_E_BONE) {
        if (target_enemy_is_killed) {
            if (target_friendly_index < -1) {
                // 敌方随从目标（编码 -2 起）：血量 <= 3 必死，下一张牌减 2 费（不抽牌）
                int eidx = -target_friendly_index - 2;
                if (eidx < 0 || eidx >= (int)s.enemy_board.size()) return false;
                const Card& target = s.enemy_board[eidx];
                if (target.health < 0 || target.health > 3) return false;
                s.enemy_board.erase(s.enemy_board.begin() + eidx);
                s.next_card += 2;
            } else {
                if (target_friendly_index < 0 || target_friendly_index >= (int)s.board.size()) {
                    return false;  // 无后继
                }
                const Card& target = s.board[target_friendly_index];
                if (target.health < 0 || target.health > 3) {
                    return false;  // 无后继
                }
                s.board.erase(s.board.begin() + target_friendly_index);
                s.next_card += 2;
            }
        }
    } else if (e == N_E_GAMBLER) {
        // 押注猎手：快枪或连击 → 获取一张幸运币（两者不叠加）
        bool quick_draw = card.entered_hand_this_turn;
        bool triggered = s.cards_played_this_turn > 0 || quick_draw;
        if (triggered) {
            add_card_to_hand_or_burn(s, make_card("幸运币"));
        }
    } else if (e == N_E_CULTIST) {
        // 异教地图：从牌库中“发现”一张牌（保底抽 1 张）。
        // 牌库已知：按实际牌库抽第一张（移出牌库进手牌/满手烧毁）；
        // 牌库未知：无法可靠建模抽到的牌，搜索不再展开该动作（见 generate_successors）。
        if (s.deck_is_known && !s.deck.empty()) {
            Card drawn = s.deck.front();
            s.deck.erase(s.deck.begin());
            add_card_to_hand_or_burn(s, drawn);
        }
    }
    // alexstrasza / 未知效果：无操作
    return true;
}

static vector<State> apply_search_effect(State base, const Card& card,
                                         int target_friendly_index,
                                         bool target_enemy_is_killed,
                                         bool enemy_target) {
    (void)enemy_target;
    int multiplier = minion_trigger_multiplier(base, card);
    if (base.next_combo_twice && card.combo && base.cards_played_this_turn > 0) {
        // 幸运彗星：下一张连击随从的连击触发两次（总额外触发一次，
        // 不与鲨鱼倍率叠加）；仅当连击真正触发时才消耗（一次性，跨回合保留）。
        multiplier = 2;
        base.next_combo_twice = false;
    }
    uint16_t e = card.effect_idx;
    if (e == N_E_ETC) {
        // 牛头人发现：真多分支效果，克隆每个乐队选择
        vector<State> states;
        if (multiplier == 1) {
            states.push_back(std::move(base));
        } else {
            states.push_back(base.clone_reserved());
            states.push_back(base.clone_reserved());
        }
        for (int m = 0; m < multiplier; m++) {
            vector<State> next_states;
            for (const State& current : states) {
                vector<State> disc = discover_fixed_choices(current);
                for (auto& d : disc) next_states.push_back(std::move(d));
            }
            states = std::move(next_states);
        }
        for (State& rs : states) {
            if (card.is_spell_like()) transform_deadly_shadows(rs, card);
            rs.cards_played_this_turn++;
        }
        return states;
    }
    if (e == N_E_QUICKDRAW) {
        // 持枪要挟：发现一张另一职业快枪牌（牌池固定，展开 5 张已建模牌；
        // 其余未建模牌按类型拆成“其他快枪牌·随从（4）”/“其他快枪牌·法术（2）”
        // 两个杂牌分支，数量加权 4/11、2/11）。
        // 发现牌本回合进入手牌 → 快枪可用。
        // forced_discover_choice 非空时（可能分支机制单独计算）只展开该牌。
        vector<State> states;
        const bool forced = !base.forced_discover_choice.empty();
        for (size_t ci = 0; ci < QUICKDRAW_MODELED_POOL.size(); ci++) {
            const string& choice = QUICKDRAW_MODELED_POOL[ci];
            if (forced && choice != base.forced_discover_choice) continue;
            State s = base.clone_reserved();
            if (s.quickdraw_choice < 0) s.quickdraw_choice = (int)ci;
            s.used_quickdraw = true;
            s.fork_damage = base.alex_damage;
            add_card_to_hand_or_burn(s, make_card(choice));
            // 路径标注“（X）”：持枪要挟只是把快枪牌置入手牌，X 由玩家后续打出
            if (!s.path().empty()) {
                s.path_mut().back() += "（" + choice + "）";
            }
            states.push_back(std::move(s));
        }
        // 其余快枪牌·随从（农场小助手/银蛇/和善的银行职员/亮石旋岩虫）：
        // 视作随从杂牌（占随从栏，身材未知），按数量加权 4/11
        if (!forced || base.forced_discover_choice == QUICKDRAW_OTHER_MINION_NAME) {
            State s = base.clone_reserved();
            if (s.quickdraw_choice < 0)
                s.quickdraw_choice = (int)QUICKDRAW_MODELED_POOL.size();  // 下标 5 = 其他快枪牌·随从
            s.used_quickdraw = true;
            s.fork_damage = base.alex_damage;
            Card junk = make_card("未知快枪牌随从");
            junk.type_idx = N_T_MINION;
            add_card_to_hand_or_burn(s, junk);
            if (!s.path().empty()) {
                s.path_mut().back() += "（" + QUICKDRAW_OTHER_MINION_NAME + "）";
            }
            states.push_back(std::move(s));
        }
        // 其余快枪牌·法术（热浪来袭/列车难题）：
        // 视作法术杂牌（不占随从栏），按数量加权 2/11
        if (!forced || base.forced_discover_choice == QUICKDRAW_OTHER_SPELL_NAME) {
            State s = base.clone_reserved();
            if (s.quickdraw_choice < 0)
                s.quickdraw_choice = (int)QUICKDRAW_MODELED_POOL.size() + 1;  // 下标 6 = 其他快枪牌·法术
            s.used_quickdraw = true;
            s.fork_damage = base.alex_damage;
            Card junk = make_card("未知快枪牌法术");
            junk.type_idx = N_T_SPELL;
            add_card_to_hand_or_burn(s, junk);
            if (!s.path().empty()) {
                s.path_mut().back() += "（" + QUICKDRAW_OTHER_SPELL_NAME + "）";
            }
            states.push_back(std::move(s));
        }
        for (State& rs : states) {
            if (card.is_spell_like()) transform_deadly_shadows(rs, card);
            rs.cards_played_this_turn++;
        }
        return states;
    }
    if (e == N_E_MISFIRE && card.entered_hand_this_turn) {
        // 误炸快枪：依次选择 3 个目标造成 3/2/1 点伤害（同一目标可重复命中），
        // 只腾随从栏格子建模：命中血量 <= 对应伤害的己方随从即死亡，最多清 3 格；
        // 非击杀伤害（打在 8/8 龙上不致死）与 OTK 无关，不建模。
        vector<State> states;
        states.push_back(base.clone_reserved());  // 无目标：当作普通法术打出
        const auto& b = base.board;
        const size_t n = b.size();
        bool capped = false;

        // ann = 标注顺序（= 伤害结算顺序 3/2/1 的目标 board 序号）；
        // idxs 只用于从 board 移除（与标注顺序无关）。
        auto kill_branch = [&](const vector<size_t>& idxs, const vector<size_t>& ann) {
            State s = base.clone_reserved();
            vector<size_t> sorted = idxs;
            std::sort(sorted.begin(), sorted.end(), std::greater<size_t>());
            for (size_t x : sorted) s.board.erase(s.board.begin() + x);
            if (!s.path().empty()) {
                string names;
                for (size_t x : ann) {
                    if (!names.empty()) names += "、";
                    names += b[x].name() + std::to_string(x + 1);  // 带 board 序号
                }
                s.path_mut().back() += "（" + names + "）";
            }
            states.push_back(std::move(s));
        };

        // 1 杀：3 点命中血量 <= 3 的随从
        for (size_t i = 0; i < n; i++) {
            if (b[i].health >= 0 && b[i].health <= 3) {
                kill_branch({i}, {i});
                if (states.size() >= MAX_MISFIRE_BRANCHES) { capped = true; break; }
            }
        }

        // 2 杀：3 点 + 2 点（两随从较小血量 <= 2、较大 <= 3）。
        // 标注按伤害顺序：较大血量吃 3 点、较小血量吃 2 点（先写吃 3 点的）。
        if (!capped) {
            for (size_t i = 0; i < n && !capped; i++) {
                for (size_t j = i + 1; j < n; j++) {
                    int hi = std::max(b[i].health, b[j].health);
                    int lo = std::min(b[i].health, b[j].health);
                    if (b[i].health >= 0 && b[j].health >= 0 && lo <= 2 && hi <= 3) {
                        size_t lower = (b[i].health <= b[j].health) ? i : j;
                        size_t higher = (lower == i) ? j : i;
                        kill_branch({i, j}, {higher, lower});
                        if (states.size() >= MAX_MISFIRE_BRANCHES) { capped = true; break; }
                    }
                }
            }
        }

        // 3 杀：3 + 2 + 1（三随从血量排序后小<=1、中<=2、大<=3）。
        // 标注按伤害顺序：最大血量吃 3 点、中间吃 2 点、最小吃 1 点。
        if (!capped) {
            for (size_t i = 0; i < n && !capped; i++) {
                for (size_t j = i + 1; j < n && !capped; j++) {
                    for (size_t k = j + 1; k < n; k++) {
                        if (b[i].health < 0 || b[j].health < 0 || b[k].health < 0) continue;
                        vector<size_t> ids = {i, j, k};
                        std::sort(ids.begin(), ids.end(),
                                  [&](size_t x, size_t y) { return b[x].health < b[y].health; });
                        if (b[ids[0]].health <= 1 && b[ids[1]].health <= 2 && b[ids[2]].health <= 3) {
                            kill_branch(ids, {ids[2], ids[1], ids[0]});
                            if (states.size() >= MAX_MISFIRE_BRANCHES) { capped = true; break; }
                        }
                    }
                }
            }
        }

        for (State& rs : states) {
            if (card.is_spell_like()) transform_deadly_shadows(rs, card);
            rs.cards_played_this_turn++;
        }
        return states;
    }
    // 脱水：造成 4 点伤害（基础效果，快枪仅减费为 1）→ 可击杀血量 <= 4 的己方随从腾格子
    if (e == N_E_DEHYDRATE) {
        vector<State> states;
        states.push_back(base.clone_reserved());  // 无目标
        for (size_t i = 0; i < base.board.size(); i++) {
            const Card& target = base.board[i];
            if (target.health >= 0 && target.health <= 4) {
                State s = base.clone_reserved();
                s.board.erase(s.board.begin() + i);
                if (!s.path().empty()) {
                    s.path_mut().back() += "（" + target.name() + std::to_string(i + 1) + "）";
                }
                states.push_back(std::move(s));
            }
        }
        for (State& rs : states) {
            if (card.is_spell_like()) transform_deadly_shadows(rs, card);
            rs.cards_played_this_turn++;
        }
        return states;
    }
    // 袋底藏沙：造成 3 点伤害（基础效果）→ 可击杀血量 <= 3 的己方随从腾格子
    if (e == N_E_POCKET_SAND) {
        vector<State> states;
        states.push_back(base.clone_reserved());  // 无目标
        for (size_t i = 0; i < base.board.size(); i++) {
            const Card& target = base.board[i];
            if (target.health >= 0 && target.health <= 3) {
                State s = base.clone_reserved();
                s.board.erase(s.board.begin() + i);
                if (!s.path().empty()) {
                    s.path_mut().back() += "（" + target.name() + std::to_string(i + 1) + "）";
                }
                states.push_back(std::move(s));
            }
        }
        for (State& rs : states) {
            if (card.is_spell_like()) transform_deadly_shadows(rs, card);
            rs.cards_played_this_turn++;
        }
        return states;
    }
    // 不许乱动：快枪时先变 1/1 再造成 1 点伤害 → 可击杀任意己方随从腾格子
    if (e == N_E_LAY_DOWN && card.entered_hand_this_turn) {
        vector<State> states;
        states.push_back(base.clone_reserved());  // 无目标
        for (size_t i = 0; i < base.board.size(); i++) {
            const Card& target = base.board[i];
            if (target.health >= 0) {
                State s = base.clone_reserved();
                s.board.erase(s.board.begin() + i);
                if (!s.path().empty()) {
                    s.path_mut().back() += "（" + target.name() + std::to_string(i + 1) + "）";
                }
                states.push_back(std::move(s));
            }
        }
        for (State& rs : states) {
            if (card.is_spell_like()) transform_deadly_shadows(rs, card);
            rs.cards_played_this_turn++;
        }
        return states;
    }
    // 普通单分支效果（幸运彗星同样走此路径：置入随从杂牌 + 一次性连击双触发标记）
    for (int m = 0; m < multiplier; m++) {
        if (!apply_effect_inplace(base, e, card, target_friendly_index, target_enemy_is_killed)) {
            return {};  // 无后继（如骨刺击杀无效目标）
        }
    }
    if (card.is_spell_like()) transform_deadly_shadows(base, card);
    base.cards_played_this_turn++;
    vector<State> out;
    out.push_back(std::move(base));
    return out;
}

// ===================== 后继生成 =====================
// 抽牌类卡仍可打出（触发连击/腾手牌格）；牌库未知时只模拟“保底抽到”的部分
// （如行骗必抽 1 张法术），其余不枚举，避免虚构抽牌后继。
// 会抽牌的卡由抽牌属性表（DRAW_ATTR_BASE / DRAW_ATTR_COMBO）标记，见上方定义。

static bool combo_active(const State& s) { return s.cards_played_this_turn > 0; }

// 随从表：红龙 OTK 的核心随从组（手牌+战场视为已抽到）。
// ① {刀油, 鲨鱼之灵, 腾武, 牛头人酋长, 晦鳞巢母}
// ② {刀油, 鲨鱼之灵, 狐人老千, 暗影施法者, 牛头人酋长, 晦鳞巢母}
// 任一组集齐后，牌库视作已无随从：抽随从的法术按“不抽牌”处理，
// 可打出腾格子（避免虚构抽牌后继）。
static bool combo_minion_set_complete(const State& s) {
    bool scabbs = false, shark = false, tenwu = false, etc = false, mother = false;
    bool foxy = false, caster = false;
    auto mark = [&](uint16_t n) {
        if (n == N_SCABBS) scabbs = true;
        else if (n == N_SHARK) shark = true;
        else if (n == N_TENWU) tenwu = true;
        else if (n == N_ETC) etc = true;
        else if (n == N_MOTHER) mother = true;
        else if (n == N_FOX) foxy = true;
        else if (n == N_CASTER) caster = true;
    };
    for (const auto& c : s.hand) mark(c.name_idx);
    for (const auto& c : s.board) mark(c.name_idx);
    if (scabbs && shark && tenwu && etc && mother) return true;              // ①
    if (scabbs && shark && foxy && caster && etc && mother) return true;     // ②
    return false;
}

// 抽随从卡分支池：牌库剩余可能抽到的组合随从（按优先级排序，供束宽搜索直接展开分支）。
// 默认勾选 鱼/狐/刀/暗/牛/晦，腾武默认不勾（玩家手动勾选，程序不会自动改），故分支池不含腾武。
static const vector<string> COMBO_MINION_POOL = {
    "鲨鱼之灵",        // 鱼 6
    "斯卡布斯·刀油",   // 刀 5
    "乐队经理精英牛头人酋长",  // 牛 4
    "暗影施法者",       // 暗 3
    "晦鳞巢母",        // 晦 2
    "狐人老千",        // 狐 1
};

static vector<string> combo_missing_minions(const State& s) {
    vector<string> missing;
    auto have = [&](const string& n) {
        for (const auto& c : s.hand) if (c.name() == n) return true;
        for (const auto& c : s.board) if (c.name() == n) return true;
        return false;
    };
    for (const string& n : COMBO_MINION_POOL) {
        if (!have(n)) missing.push_back(n);
    }
    return missing;
}

// 组合取 k 个的所有组合（用于潜伏帷幕抽 2 张时展开分支），最多 MAX 个。
static vector<vector<string>> combo_combinations(const vector<string>& pool, int k,
                                                  size_t max_count = 32) {
    vector<vector<string>> out;
    // 防御：k 超出池大小（如潜伏帷幕抽 2 张但缺失池只剩 1 个随从）无合法组合，
    // 直接返回空，由调用方按“抽剩余全部”兜底，避免越界访问 pool[idx]。
    if (k <= 0 || k > (int)pool.size()) return out;
    vector<size_t> idx(k, 0);
    for (size_t i = 0; i < idx.size(); i++) idx[i] = i;
    while (true) {
        vector<string> comb;
        for (size_t i = 0; i < idx.size(); i++) comb.push_back(pool[idx[i]]);
        out.push_back(std::move(comb));
        if (out.size() >= max_count) break;
        size_t i = idx.size();
        while (i-- > 0) {
            if (idx[i] < pool.size() - (idx.size() - i)) { idx[i]++; break; }
        }
        if (i == SIZE_MAX) break;
        for (size_t j = i + 1; j < idx.size(); j++) idx[j] = idx[j - 1] + 1;
    }
    return out;
}

// 纯随机抽牌（发现/未知类型）且牌库未知时无法建模抽到的牌：
// 若卡牌本身是法术且其抽牌为无条件随机，则禁止作为免费填充牌展开
// （异教地图/持枪要挟/可疑交易；垂钓时光是连击才抽、锯齿骨刺不抽牌只减费、
// 疾速矿锄不触发抽牌可当杂牌打出，均不受此限制）。
static bool card_is_unmodelable_random_draw(const Card& card) {
    auto base_it = DRAW_ATTR_BASE.find(card.effect_id_str());
    if (base_it == DRAW_ATTR_BASE.end()) return false;
    if (DRAW_ATTR_COMBO.find(card.effect_id_str()) != DRAW_ATTR_COMBO.end()) return false;  // 连击追加 → 可展开
    if (!card.is_spell_like()) return false;  // 武器/随从等还有装备/战吼等其他效果
    for (const auto& d : base_it->second) {
        if (d.type != "random" || d.conditional) return false;
    }
    return true;
}

static int cards_drawn_if_played(const State& s, const Card& card) {
    // 按抽牌属性汇总本次实际会抽的条目：基础抽牌 + 连击追加（连击时才生效）
    auto base_it = DRAW_ATTR_BASE.find(card.effect_id_str());
    auto combo_it = DRAW_ATTR_COMBO.find(card.effect_id_str());
    if (base_it == DRAW_ATTR_BASE.end() && combo_it == DRAW_ATTR_COMBO.end()) return 0;
    vector<const DrawSpec*> specs;
    if (base_it != DRAW_ATTR_BASE.end())
        for (const auto& d : base_it->second) specs.push_back(&d);
    if (combo_it != DRAW_ATTR_COMBO.end() && combo_active(s))
        for (const auto& d : combo_it->second) specs.push_back(&d);
    if (specs.empty()) return 0;
    // 随从组已集齐 → 牌库视作无随从（仅牌库未知时的兜底启发）
    bool no_minions_left = combo_minion_set_complete(s);
    if (!s.deck_is_known) {
        int n = 0;
        for (const DrawSpec* d : specs) {
            if (d->type == "minion" && no_minions_left) continue;
            n += d->count;
        }
        return n;
    }
    // 牌库已知时以实际牌库为准：按抽牌类型统计可得张数
    int deck_total = (int)s.deck.size();
    int deck_minions = 0, deck_spells = 0;
    for (const auto& c : s.deck) {
    if (c.type_idx == N_T_MINION) deck_minions++;
        if (c.is_spell_like()) deck_spells++;
    }
    int n = 0;
    for (const DrawSpec* d : specs) {
        int avail = d->type == "minion" ? deck_minions :
                    d->type == "spell"  ? deck_spells : deck_total;
        n += std::min(d->count, avail);
    }
    return n;
}

static vector<State> generate_successors(const State& st) {
    vector<State> out;
    for (int hand_index = 0; hand_index < (int)st.hand.size(); hand_index++) {
        const Card& card = st.hand[hand_index];
        bool duplicate = false;
        for (int j = 0; j < hand_index; j++) {
            if (same_playable_card(st, st.hand[j], card)) {
                duplicate = true;
                break;
            }
        }
        if (duplicate) continue;
        int cost = effective_cost(st, card);
        if (cost < 0) continue;
        if (st.mana < cost) continue;
        // 杂牌（unknown）可能是随从：打出占格子
    if ((card.type_idx == N_T_MINION || card.type_idx == N_T_UNKNOWN) && st.board_full()) continue;
    if (card.type_idx == N_T_SECRET) {
            if ((int)st.secrets.size() >= MAX_SECRET) continue;
            bool dup_secret = false;
            for (const auto& sec : st.secrets)
                if (sec.name() == card.name()) { dup_secret = true; break; }
            if (dup_secret) continue;  // 每种奥秘只能装备一个
        }
        // 抽牌属性辅助“是否考虑展开该路径”：牌库未知时——
        // 抽随从卡（挖掘宝藏/潜伏帷幕）以及连击追加抽随从（行骗连击）
        // 直接按“组合剩余随从池”展开分支，让所有可能抽到的随从都参与束宽搜索
        // （显示可能的最高伤路径）；无此类卡在手中时这里是常数级判断，不影响搜索性能。
        if (!st.deck_is_known && st.branch_expand) {
            int draw_count = 0;
    auto base_draw_it = DRAW_ATTR_BASE.find(card.effect_id_str());
            if (base_draw_it != DRAW_ATTR_BASE.end()) {
                for (const auto& d : base_draw_it->second)
                    if (d.type == "minion") draw_count += d.count;
            }
            // 连击追加的随从抽牌（行骗：连击再抽 1 张随从）：
            // 只有连击真正触发时才展开；不连击打出行骗抽不了随从。
    auto combo_draw_it = DRAW_ATTR_COMBO.find(card.effect_id_str());
            if (combo_draw_it != DRAW_ATTR_COMBO.end() && combo_active(st)) {
                for (const auto& d : combo_draw_it->second)
                    if (d.type == "minion") draw_count += d.count;
            }
            // 保底抽的法术（行骗无条件抽 1 张法术）：展开分支时同样置入“未知法术”杂牌，
            // 与常规路径一致（牌库未知时该法术必然发生，抽到具体哪张不可建模）。
            int base_junk_spells = 0;
            if (base_draw_it != DRAW_ATTR_BASE.end()) {
                for (const auto& d : base_draw_it->second)
                    if (d.type == "spell") base_junk_spells += d.count;
            }

            if (draw_count > 0) {
                vector<string> missing = combo_missing_minions(st);

                if (!missing.empty()) {
                    vector<vector<string>> drawn_sets;

                    const bool forced = !st.forced_draw_choice.empty();

                    if (forced && draw_count == 1) {
                        // WhatIF 同级分支：强制行骗/挖掘宝藏抽到指定随从
                        if (std::find(missing.begin(), missing.end(),
                                      st.forced_draw_choice) != missing.end()) {
                            drawn_sets.push_back({st.forced_draw_choice});
                        }
                    } else if (forced) {
                        // 潜伏帷幕抽 2 张：只保留包含指定随从的组合。
                        // 缺失池不足抽取张数时（如只剩 1 个随从），直接抽剩余全部——
                        // 与常规路径的 missing < draw_count 处理一致，且避免组合越界。
                        if ((int)missing.size() <= draw_count) {
                            if (std::find(missing.begin(), missing.end(),
                                          st.forced_draw_choice) != missing.end()) {
                                drawn_sets.push_back(missing);
                            }
                        } else {
                            auto combs = combo_combinations(missing, draw_count);
                            for (auto& comb : combs) {
                                if (std::find(comb.begin(), comb.end(),
                                              st.forced_draw_choice) != comb.end()) {
                                    drawn_sets.push_back(std::move(comb));
                                }
                            }
                        }
                    } else if ((int)missing.size() < draw_count) {
                        // 剩余随从不足抽取张数：只抽剩余的全部（单个分支）
                        State base = st.clone_reserved();
                        if (play_card_base(base, hand_index, -1, false, false)) {
                            base.fork_damage = base.alex_damage;
                            for (const string& mn : missing) {
                                add_card_to_hand_or_burn(base, make_card(mn));
                            }
                            base.last_draw_key = "";
                            for (size_t k = 0; k < missing.size(); k++) {
                                if (k) base.last_draw_key += "、";
                                base.last_draw_key += missing[k];
                            }
                            for (int k = 0; k < base_junk_spells; k++) {
                                Card unknown_spell = make_card("未知法术");
                                unknown_spell.type_idx = N_T_SPELL;
                                add_card_to_hand_or_burn(base, unknown_spell);
                            }
                            base.used_draw_branch = true;  // 抽到具体随从：不进正常线
                            // 分支展开跳过 apply_search_effect：补上施放法术的
                            // 殒命暗影变形与本回合已出牌计数（连击状态）。
                            if (card.is_spell_like()) transform_deadly_shadows(base, card);
                            base.cards_played_this_turn++;
                            out.push_back(std::move(base));
                        }
                        continue;
                    } else if (draw_count == 1) {
                        for (const string& mn : missing) drawn_sets.push_back({mn});
                    } else {
                        drawn_sets = combo_combinations(missing, draw_count);
                    }

                    if (forced && drawn_sets.empty()) {
                        // 强制牌不在缺失池：退回常规全部展开（兜底）
                        if ((int)missing.size() < draw_count) {
                            State base = st.clone_reserved();
                            if (play_card_base(base, hand_index, -1, false, false)) {
                                base.fork_damage = base.alex_damage;
                                for (const string& mn : missing) {
                                    add_card_to_hand_or_burn(base, make_card(mn));
                                }
                                base.last_draw_key = "";
                                for (size_t k = 0; k < missing.size(); k++) {
                                    if (k) base.last_draw_key += "、";
                                    base.last_draw_key += missing[k];
                                }
                                for (int k = 0; k < base_junk_spells; k++) {
                                    Card unknown_spell = make_card("未知法术");
                                    unknown_spell.type_idx = N_T_SPELL;
                                    add_card_to_hand_or_burn(base, unknown_spell);
                                }
                                base.used_draw_branch = true;  // 抽到具体随从：不进正常线
                                if (card.is_spell_like()) transform_deadly_shadows(base, card);
                                base.cards_played_this_turn++;
                                out.push_back(std::move(base));
                            }
                            continue;
                        }
                        if (draw_count == 1) {
                            for (const string& mn : missing) drawn_sets.push_back({mn});
                        } else {
                            drawn_sets = combo_combinations(missing, draw_count);
                        }
                    }

                    for (const auto& drawn : drawn_sets) {
                        State base = st.clone_reserved();
                        if (!play_card_base(base, hand_index, -1, false, false)) continue;
                        base.fork_damage = base.alex_damage;

                        if (!base.path().empty()) {
                            string note = "（";
                            for (size_t di = 0; di < drawn.size(); di++) {
                                if (di) note += "、";
                                note += drawn[di];
                            }
                            note += "）";
                            base.path_mut().back() += note;
                        }

                        for (const string& mn : drawn) {
                            add_card_to_hand_or_burn(base, make_card(mn));
                        }
                        base.last_draw_key = "";
                        for (size_t k = 0; k < drawn.size(); k++) {
                            if (k) base.last_draw_key += "、";
                            base.last_draw_key += drawn[k];
                        }
                        for (int k = 0; k < base_junk_spells; k++) {
                            Card unknown_spell = make_card("未知法术");
                            unknown_spell.type_idx = N_T_SPELL;
                            add_card_to_hand_or_burn(base, unknown_spell);
                        }
                        base.used_draw_branch = true;  // 抽到具体随从：不进正常线
                        // 分支展开跳过 apply_search_effect：补上施放法术的
                        // 殒命暗影变形与本回合已出牌计数（连击状态）。
                        if (card.is_spell_like()) transform_deadly_shadows(base, card);
                        base.cards_played_this_turn++;

                        out.push_back(std::move(base));
                    }

                    // 正常线（与 V1.2.1 逻辑一致）：抽随从卡当“抽杂牌”打出——
                    // 最终结果唯一确定，不依赖具体抽到什么/置入什么进手牌。
                    // 行骗保底抽 1 张法术杂牌；垂钓时光连击抽 1 张未知杂牌；
                    // 挖掘宝藏/潜伏帷幕不模拟抽牌（无后继杂牌）。
                    // 该后继不标记 used_draw_branch → 可进入正常线（original）。
                    // 强制抽牌搜索（WhatIF 同级分支）不加此后继，保证结果确为该抽取。
                    if (!forced) {
                        State junk = st.clone_reserved();
                        if (play_card_base(junk, hand_index, -1, false, false)) {
                            if (card.effect_idx == N_E_SWINDLE) {
                                Card unknown_spell = make_card("未知法术");
                                unknown_spell.type_idx = N_T_SPELL;
                                add_card_to_hand_or_burn(junk, unknown_spell);
                            } else if (card.effect_idx == N_E_FISHIN && combo_active(st)) {
                                Card unknown = make_card("未知抽牌");
                                add_card_to_hand_or_burn(junk, unknown);
                            }
                            if (card.is_spell_like()) transform_deadly_shadows(junk, card);
                            junk.cards_played_this_turn++;
                            out.push_back(std::move(junk));
                        }
                    }

                    continue;  // 跳过下方常规展开
                }
            }
        }
        // 垂钓时光探底分叉（WhatIF/Branch）：阅读器追踪的牌库底已知牌作为分叉选项。
        // 打出垂钓时光 = 从底 3 张选 1 张入手（连击额外抽 1 张）；每个已知底牌一个分支，
        // 不足 3 张时补“未知杂牌”分支；选中后其余已知牌留在牌库底（下次垂钓时光再用）。
        if (card.effect_idx == N_E_FISHIN && st.branch_expand && !st.dredge_bottom.empty()) {
            const bool forced = !st.forced_draw_choice.empty();
            vector<string> opts;
            for (const string& n : st.dredge_bottom) {
                if (std::find(opts.begin(), opts.end(), n) == opts.end()) opts.push_back(n);
            }
            if ((int)st.dredge_bottom.size() < 3) opts.push_back("未知杂牌");

            if (forced) {
                // WhatIF 强制抽到某张：只保留该分支（不在底池时无分支，正常线兜底）
                if (st.forced_draw_choice == "未知杂牌") {
                    opts = {"未知杂牌"};
                } else if (std::find(opts.begin(), opts.end(),
                                     st.forced_draw_choice) != opts.end()) {
                    opts = {st.forced_draw_choice};
                } else {
                    opts.clear();
                }
            }

            for (const string& pick : opts) {
                State base = st.clone_reserved();
                if (!play_card_base(base, hand_index, -1, false, false)) continue;
                base.fork_damage = base.alex_damage;
                if (!apply_effect_inplace(base, N_E_FISHIN, card, -1, false)) continue;
                if (!base.path().empty()) base.path_mut().back() += "（" + pick + "）";
                if (pick == "未知杂牌") {
                    Card junk = make_card("未知抽牌");
                    add_card_to_hand_or_burn(base, junk);
                } else {
                    add_card_to_hand_or_burn(base, make_card(pick));
                    // 选中后其余已知牌留在牌库底
                    base.dredge_bottom.clear();
                    for (const string& n : st.dredge_bottom) {
                        if (n != pick) base.dredge_bottom.push_back(n);
                    }
                }
                base.last_draw_key = pick;
                base.used_draw_branch = true;  // 抽到具体底牌：不进正常线
                if (card.is_spell_like()) transform_deadly_shadows(base, card);
                base.cards_played_this_turn++;
                out.push_back(std::move(base));
            }
            // 正常线（V1.2.1）：垂钓时光按“抽杂牌”打出，结果唯一确定（不进分叉）
            if (!forced) {
                State junk = st.clone_reserved();
                if (play_card_base(junk, hand_index, -1, false, false)) {
                    apply_effect_inplace(junk, N_E_FISHIN, card, -1, false);
                    if (card.is_spell_like()) transform_deadly_shadows(junk, card);
                    junk.cards_played_this_turn++;
                    out.push_back(std::move(junk));
                }
            }
            continue;  // 跳过下方常规展开（本卡已单独处理）
        }
        // 纯随机法术抽牌（异教地图等）仍禁止，避免虚构抽牌后继
        // （无论是否展开抽随从卡分支都生效；抽随从分支开关只影响 minion 抽牌卡）
        if (!st.deck_is_known && card_is_unmodelable_random_draw(card)) continue;
        // 其余抽牌类卡按“不抽牌”打出（触发连击/腾手牌格），避免虚构抽牌后继；
        // 随从表判空后更可直接作为普通法术使用（draw 效果本身不模拟）。

        vector<int> friendly_targets;
        if (card.effect_idx == N_E_STEP || card.effect_idx == N_E_CASTER ||
            card.effect_idx == N_E_BONE || card.effect_idx == N_E_TENWU) {
            if (card.effect_idx == N_E_TENWU) {
                // 腾武不能以腾武为目标（自回环非法）；目标顺序由路径标注
                for (int i = 0; i < (int)st.board.size(); i++) {
                    if (st.board[i].name_idx == N_TENWU) continue;
                    friendly_targets.push_back(i);
                }
            } else {
                for (int i = 0; i < (int)st.board.size(); i++) friendly_targets.push_back(i);
            }
        } else {
            friendly_targets.push_back(-1);
        }
        vector<bool> kill_options = {false};
        if (card.effect_idx == N_E_BONE) kill_options = {false, true};
        vector<bool> enemy_options = {true};
        if (card.effect_idx == N_E_ALEX) enemy_options = {true, false};

        for (int tf : friendly_targets) {
            for (bool ek : kill_options) {
                for (bool et : enemy_options) {
                    State base = st.clone_reserved();
                    if (!play_card_base(base, hand_index, tf, ek, et)) continue;
                    vector<State> succs = apply_search_effect(std::move(base), card, tf, ek, et);
                    for (State& succ : succs) out.push_back(std::move(succ));
                }
            }
        }
        // 锯齿骨刺（编码负目标：-2 起）：
        //   - 血量 <= 3：击杀分支，移除并抽 2
        //   - 血量 > 3：不击杀分支视作杂牌（仅消耗腾手牌，不模拟伤害）
        if (card.effect_idx == N_E_BONE) {
            for (int ei = 0; ei < (int)st.enemy_board.size(); ei++) {
                const Card& target = st.enemy_board[ei];
                State base = st.clone_reserved();

                if (target.health >= 0 && target.health <= 3) {
                    if (!play_card_base(base, hand_index, -2 - ei, true, false)) continue;
                    vector<State> succs = apply_search_effect(std::move(base), card, -2 - ei, true, false);
                    for (State& succ : succs) out.push_back(std::move(succ));
                } else {
                    // 不击杀：视作杂牌动作（仅消耗腾手牌）
                    if (!play_card_base(base, hand_index, -2 - ei, false, false)) continue;
                    vector<State> succs = apply_search_effect(std::move(base), card, -2 - ei, false, false);
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
    // 与原 static_hash 完全同语义：str_hash(名字/原名/类型/效果) 的 mix，
    // 保证束宽去重键不变、搜索行为与压缩前逐位一致。
    uint64_t h = mix_hash(g_intern_hash[c.name_idx], g_intern_hash[c.original_idx]);
    h = mix_hash(h, g_intern_hash[c.type_idx]);
    h = mix_hash(h, g_intern_hash[c.effect_idx]);
    h = mix_hash(h, (uint64_t)c.current_cost());
    h = mix_hash(h, c.is_deadly_shadow ? 1ULL : 0ULL);
    h = mix_hash(h, c.locked_one_cost ? 1ULL : 0ULL);
    h = mix_hash(h, (uint64_t)c.health);
    return h;
}

static uint64_t state_hash(const State& s) {
    uint64_t h = 1469598103934665603ULL;
    h = mix_hash(h, s.deck_is_known ? 1ULL : 0ULL);
    // 手牌无序：固定数组 + 插入排序，避免每次哈希的堆分配与 std::sort 开销
    uint64_t hand_keys[MAX_HAND];
    int nk = 0;
    for (const auto& c : s.hand) hand_keys[nk++] = card_hash(c);
    for (int i = 1; i < nk; i++) {
        uint64_t key = hand_keys[i];
        int j = i - 1;
        while (j >= 0 && hand_keys[j] > key) {
            hand_keys[j + 1] = hand_keys[j];
            j--;
        }
        hand_keys[j + 1] = key;
    }
    for (int i = 0; i < nk; i++) h = mix_hash(h, hand_keys[i]);
    for (const auto& c : s.deck) h = mix_hash(h, card_hash(c));
    for (const auto& c : s.board) h = mix_hash(h, card_hash(c));
    for (const auto& c : s.enemy_board) h = mix_hash(h, card_hash(c));
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
    h = mix_hash(h, (uint64_t)s.sp_cost_inc);
    for (const auto& p : s.oil_stacks) h = mix_hash(h, (uint64_t)p.first * 31 + (uint64_t)p.second);
    for (const auto& n : s.dredge_bottom) h = mix_hash(h, str_hash(n));
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
    // 重放匹配：去掉指向性目标后缀（如 暗影施法者（斯卡布斯·刀油3nd）→ 暗影施法者），
    // 让 --verify 回溯自动尝试所有合法目标选择
    size_t lp = out.rfind("（");
    // 注意：尾部 "nd）" 中全角 ） 占 3 字节，整体 5 字节，不能按 3 字节比较
    static const string target_suffix = "nd）";
    if (lp != string::npos && out.size() >= lp + target_suffix.size() &&
        out.compare(out.size() - target_suffix.size(), target_suffix.size(), target_suffix) == 0) {
        out = out.substr(0, lp);
    }
    return out;
}

// 精确动作匹配（branch_prefix 分支点重放）：只规范化硬币/殒命标记，
// 保留指向性目标后缀（如 暗影施法者（斯卡布斯·刀油2nd）），
// 保证分支搜索从主路径的同一状态继续。
static string canonical_action_exact(string item) {
    string out = item;
    string needle = "[殒命暗影]";
    size_t pos;
    while ((pos = out.find(needle)) != string::npos) out.erase(pos, needle.size());
    if (is_coin_name(out)) return "幸运币";
    return out;
}

// ===================== 纯束宽搜索 =====================
struct SearchParams {
    int min_alex = 1;
    int max_alex = 10;
    int depth = 30;
    int max_paths = 1000000;
    int threads = 4;
    double time_budget_sec = 3.0;   // 总时间预算（秒）；<=0 = 不限时，按 束宽×最大深度 跑完
    int heuristic = 6;          // 组合加权（0.6×瓶颈 + 资源求和 + 路径里程碑，实测最优）
    int wide_width = 0;         // >0 = 单宽束通道固定宽度
    vector<int> wide_widths;    // 多宽束并行组合；空 = 默认 {1100,2000}
    vector<int> heuristics;     // 各宽束通道的启发函数；空 = 默认 {6,2}
    int inner_threads = 1;      // 宽束通道内部的并行展开线程数（大局面通道给 3）
    int only_best_damage = 0;   // 只计算最高伤害：找到最高伤后剪掉无法超越它的分支
    int lethal_threshold = -1;  // 精确截断：>0 时搜到 伤害≥阈值（敌方血量+护甲）即停
};

// ---------- 子链覆盖度（旧 beam 冠军判据：状态侧已凑齐的子链骨架数） ----------
static int count_hand_cards(const State& s, const string& name) {
    int n = 0;
    for (const auto& c : s.hand) if (c.name() == name) n++;
    return n;
}
static int count_hand_cards(const State& s, uint16_t name) {
    int n = 0;
    for (const auto& c : s.hand) if (c.name_idx == name) n++;
    return n;
}
static int count_board_cards(const State& s, const string& name) {
    int n = 0;
    for (const auto& c : s.board) if (c.name() == name) n++;
    return n;
}
static int count_board_cards(const State& s, uint16_t name) {
    int n = 0;
    for (const auto& c : s.board) if (c.name_idx == name) n++;
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
struct BottleneckParts {
    double sources = 0.0;     // ① 龙源数
    double capacity = 0.0;    // ② 回手容量
    double mana_rounds = 0.0; // ③ 法力可负担轮数
};

static BottleneckParts bottleneck_parts(const State& s) {
    BottleneckParts r;
    int hand_dragons = 0, board_dragons = 0, shadowcaster = 0, scabbs = 0;
    int shark_in_hand = 0, single_returns = 0, whole_returns = 0, deadly = 0;
    int cheapest_dragon = -1;
    for (const auto& c : s.hand) {
        if (c.dragon) {
            hand_dragons++;
            int cost = effective_cost(s, c);
            if (cost >= 0 && (cheapest_dragon < 0 || cost < cheapest_dragon)) cheapest_dragon = cost;
        } else if (c.name_idx == N_CASTER) {
            shadowcaster++;
        } else if (c.name_idx == N_SCABBS) {
            scabbs++;
        } else if (c.name_idx == N_SHARK) {
            shark_in_hand++;
        } else if (c.name_idx == N_STEP || c.name_idx == N_TENWU) {
            single_returns++;
        } else if (c.name_idx == N_DANCE || c.name_idx == N_POTION || c.name_idx == N_TRANSFER) {
            whole_returns++;
        }
        if (c.is_deadly_shadow) deadly++;
    }
    for (const auto& c : s.board) {
        if (c.name_idx == N_DRAGON) board_dragons++;
        else if (c.name_idx == N_CASTER) shadowcaster++;
        else if (c.name_idx == N_SCABBS) scabbs++;
    }
    bool shark = s.has_shark() || shark_in_hand > 0;

    // ① 龙源数：手牌龙 + 场上龙（回手后重打）+ 暗施可复制龙（鲨鱼时 ×2）
    r.sources = (double)(hand_dragons + board_dragons + shadowcaster * (shark ? 2 : 1));

    // ② 回手容量：单体回手 + 整场回手×3；刀油+单体回手 ≈ 无限（+15 封顶）；殒命打五折
    r.capacity = (double)single_returns + (double)whole_returns * 3.0;
    if (scabbs > 0 && single_returns > 0) r.capacity += 15.0;
    if (deadly > 0 && s.cards_played_this_turn > 0) r.capacity += 0.5;
    for (const auto& c : s.enemy_board)
        if (c.health >= 0 && c.health <= 3) { r.capacity += 1.0; break; }  // 骨刺可击杀敌方随从→抽2

    // ③ 法力可负担轮数：阈值型资源；考虑手牌刀油先打出带来的减费潜力
    if (cheapest_dragon < 0 && board_dragons == 0 && shadowcaster == 0) return r;  // 无龙源
    if (cheapest_dragon < 0) cheapest_dragon = 9;
    int per_scabbs = shark ? 4 : 2;
    int eff_dragon = std::max(0, cheapest_dragon - scabbs * per_scabbs);
    if (s.mana < eff_dragon) return r;                                              // 阈值：打不起第一条龙
    r.mana_rounds = 1 + (s.mana - eff_dragon) / std::max(1, eff_dragon + 1);        // 每轮 ≈ 龙费 + 回手费(约1)
    return r;
}

static double bottleneck_dragons(const State& s) {
    BottleneckParts p = bottleneck_parts(s);
    return std::min(p.sources, std::min(p.capacity, p.mana_rounds));
}

// 启发函数分发（实现见"启发函数库"，此处前向声明供 beam_simulate 使用）
static double heuristic_value(const State& s, int h);

// 时间预算：每通道独立（宽束通道可给不同时限），原子停止标志 + 起始时间
struct Budget {
    mutable std::atomic<bool> stop{false};
    std::chrono::steady_clock::time_point t0{};
    double budget_sec = 0.0;

    bool over() const {
        if (budget_sec <= 0.0) return false;
        if (stop.load()) return true;
        double sec = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - t0).count();
        if (sec >= budget_sec) {
            stop.store(true);
            return true;
        }
        return false;
    }
};

static bool path_sort_better(const State& a, const State& b) {
    if (a.alex_damage != b.alex_damage) return a.alex_damage > b.alex_damage;
    if (a.alex_play_count != b.alex_play_count) return a.alex_play_count > b.alex_play_count;
    if (a.path().size() != b.path().size()) return a.path().size() > b.path().size();
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

// 持枪要挟按发现牌分组的最优路径（供分支显示：每个 补水/脱水/误炸/… 各保留一条最高伤路径）
static void add_best_choice(const State& s, unordered_map<int, State>& best) {
    if (s.quickdraw_choice < 0) return;
    auto it = best.find(s.quickdraw_choice);
    if (it == best.end() || path_sort_better(s, it->second)) best[s.quickdraw_choice] = s;
}

// 抽随从分支卡按抽到随从集分组的最优路径（供 WhatIF 同级分支显示：
// 行骗(狐)/行骗(牛) 各保留一条最高伤路径）
static void add_best_draw(const State& s, unordered_map<string, State>& best) {
    if (s.last_draw_key.empty()) return;
    auto it = best.find(s.last_draw_key);
    if (it == best.end() || path_sort_better(s, it->second)) best[s.last_draw_key] = s;
}

// 原版：不含持枪要挟的最优路径（不考虑分支节点，即之前的计算逻辑）
static void add_best_no_qd(const State& s, State& best) {
    // 正常线 = 既不打持枪要挟、也不打抽随从分支卡（行骗/挖掘宝藏/潜伏帷幕/垂钓时光）：
    // 直接不考虑这类可能带有分支的抽卡（禁抽），WhatIF 分支计算才展开它们。
    if (s.used_quickdraw || s.used_draw_branch) return;
    if (best.path().empty() || path_sort_better(s, best)) best = s;
}

// 束搜索模拟：从给定状态快速搜到深度上限，返回途中发现的最高奖励状态（含完整路径）
// 束内保留评分 = 当前伤害 + 瓶颈可达龙数×16（简单启发函数，无子链库）；
// 并按当前龙数分桶，保住正在蓄力的低龙数分支。
// ---------- 旧 beam 的子链/离散路径评分（宽束模拟沿用，经验证能挖出 10 龙/160 伤） ----------
static int subchain_score(const State& s) {
    int hand_d = 0, board_d = 0;
    int shark_in_hand = 0, shark_on = 0;
    int mother = 0, shadowcaster = 0;
    int shadowstep = 0, dance = 0, potion = 0, scabbs = 0, etc_count = 0;
    bool deadly = false;
    int oil = 0;
    for (const auto& c : s.hand) {
        if (c.dragon) hand_d++;
        if (c.is_deadly_shadow) deadly = true;
        if (c.name_idx == N_SHARK) shark_in_hand++;
        else if (c.name_idx == N_MOTHER) mother++;
        else if (c.name_idx == N_CASTER) shadowcaster++;
        else if (c.name_idx == N_STEP) shadowstep++;
        else if (c.name_idx == N_DANCE) dance++;
        else if (c.name_idx == N_POTION) potion++;
        else if (c.name_idx == N_SCABBS) scabbs++;
        else if (c.name_idx == N_ETC) etc_count++;
    }
    for (const auto& c : s.board) {
        if (c.name_idx == N_DRAGON) board_d++;
        else if (c.name_idx == N_MOTHER) mother++;
        else if (c.name_idx == N_CASTER) shadowcaster++;
        else if (c.name_idx == N_SHARK) shark_on++;
    }
    int dragons = hand_d + board_d;
    bool shark = shark_on > 0 || shark_in_hand > 0;
    for (const auto& p : s.oil_stacks) if (p.first > 0 && p.second > 0) oil += p.second * p.first;

    int score = oil * 2;
    for (const auto& c : s.enemy_board)
        if (c.health >= 0 && c.health <= 3) { score += 10; break; }  // 骨刺击杀敌方随从抽2
    if (dragons > 0 && shark) score += dragons * 16;
    // ===== 精准蓄力维度（一个漏解对应一个精确状态特征，杜绝全局加分误伤） =====
    // ① 复制蓄力（175430 第 20 步等）：场上有鱼 + 场上有龙（可被复制）
    //    + 手牌有复制器（暗施/幻）且可打出 + 手牌没有费用≤当前法力的龙（无龙可打）
    //    → +16。严格检查"无可打出龙"：174341 早期手牌有 9 费龙时不触发。
    if (shark_on > 0 && board_d > 0) {
        bool playable_dragon = false;
        for (const auto& c : s.hand) {
            if (c.dragon) {
                int cc = effective_cost(s, c);
                if (cc >= 0 && cc <= s.mana) { playable_dragon = true; break; }
            }
        }
        if (!playable_dragon) {
            for (const auto& c : s.hand) {
                if (c.name_idx == N_CASTER || c.name_idx == N_POTION) {
                    int cc = effective_cost(s, c);
                    if (cc >= 0 && cc <= s.mana) { score += 16; break; }
                }
            }
        }
    }
    // ② 弹回蓄力（174341 第 18 步等）：龙数≤1 + 手牌 1 费龙 + 手牌 1 费鱼
    //    （舞动/暗影步弹回后的爆发前兆）→ +18。
    //    早期层不可能同时出现 1 费龙和 1 费鱼（减费需要舞动等操作），不会误伤前中期。
    if (s.alex_play_count <= 1) {
        bool fish1 = false, dragon1 = false;
        for (const auto& c : s.hand) {
            int cc = c.current_cost();
            if (cc != 1) continue;
            if (c.dragon) dragon1 = true;
            else if (c.name_idx == N_SHARK) fish1 = true;
        }
        if (fish1 && dragon1) score += 18;
    }
    // ③ 直出蓄力（170618 第 11 步、174341 原鱼龙线等）：场上有鱼 + 手牌有可打出的龙 → +16。
    // 鱼在场时下一只龙必吃满 16 伤；不看鱼是否在手（舞后鱼已下场即就绪）。
    if (shark_on > 0) {
        for (const auto& c : s.hand) {
            if (c.dragon) {
                int cc = effective_cost(s, c);
                if (cc >= 0 && cc <= s.mana) {
                    score += 16;
                }
                break;
            }
        }
    }
    // ④ 鱼龙优先级（150953 等）：先鱼后龙几乎总优于先龙后鱼（龙鱼更优 <0.01%）。
    //    a) 惩罚“刚打出龙、鲨鱼在手可打出却不在场”的状态——这一口龙白少 8 伤；
    //    b) 奖励“鱼龙同场”状态——鲨鱼在场时龙必吃满 16，暗施还能借双战吼再复制两条龙。
    if (shark_on == 0 && shark_in_hand > 0) {
        const auto& p = s.path();
        if (!p.empty() && p.back().find("生命的缚誓者") != string::npos) {
            for (const auto& c : s.hand) {
                int cc = effective_cost(s, c);
                if (c.name_idx == N_SHARK && cc >= 0 && cc <= s.mana) {
                    score -= 24;
                    break;
                }
            }
        }
    }
    if (shark_on > 0 && board_d > 0) {
        score += 24;
    }
    // ⑤ 分叉卡独立评估（行骗/挖掘宝藏/潜伏帷幕/持枪要挟/垂钓时光各有用途时段）：
    //    a) 抽随从卡只在组合未齐时有价值（路径前期凑齐随从）；
    //    b) 持枪要挟只在随从栏接近满时有价值（后期找误炸清格子）；
    //    c) 垂钓时光只在已知牌库底有高价值牌时有价值（殒命/暗影步/持枪/关键随从/抽随从卡）。
    //    各维度独立触发，避免“无脑给分叉卡加分”误导搜索、拖慢复杂局面。
    {
        bool has_draw_card = false;
        for (const auto& c : s.hand) {
            if (c.name_idx == N_SWINDLE || c.name_idx == N_DIG || c.name_idx == N_CURTAIN) {
                int cc = effective_cost(s, c);
                if (cc >= 0 && cc <= s.mana) { has_draw_card = true; break; }
            }
        }
        if (has_draw_card && !combo_minion_set_complete(s)) score += 12;
    }
    // 持枪要挟评估（严格限定后期）：已下过 ≥2 龙（第二轮后）、随从栏满/差一格、
    // 且手里有可打出的随从被卡住 → 才给“找误炸清格”的加分；其他状态一律不加，
    // 避免给揣着持枪的早期状态乱加分（150953 等价线曾因此被挤出束宽）。
    {
        bool has_qd = false;
        for (const auto& c : s.hand) {
            if (c.name_idx == N_QUICKDRAW) {
                int cc = effective_cost(s, c);
                if (cc >= 0 && cc <= s.mana) { has_qd = true; break; }
            }
        }
        if (has_qd && s.alex_play_count >= 2) {
            int free_slots = MAX_BOARD - (int)s.board.size();
            if (free_slots <= 1) {
                bool stuck_minion = false;
                for (const auto& c : s.hand) {
        if (c.type_idx == N_T_MINION || c.type_idx == N_T_UNKNOWN) {
                        int cc = effective_cost(s, c);
                        if (cc >= 0 && cc <= s.mana) { stuck_minion = true; break; }
                    }
                }
                if (stuck_minion) score += 8;
            }
        }
    }
    {
        bool has_fishin = false;
        for (const auto& c : s.hand) {
            if (c.name_idx == N_FISHIN) {
                int cc = effective_cost(s, c);
                if (cc >= 0 && cc <= s.mana) { has_fishin = true; break; }
            }
        }
        if (has_fishin) {
            for (const auto& n : s.dredge_bottom) {
                if (n == "殒命暗影" || n == "暗影步" || n == "持枪要挟" ||
                    n == "行骗" || n == "挖掘宝藏" || n == "潜伏帷幕" ||
                    n == "鲨鱼之灵" || n == "狐人老千" || n == "斯卡布斯·刀油" ||
                    n == "暗影施法者" || n == "乐队经理精英牛头人酋长" ||
                    n == "晦鳞巢母" || n == "赤烟·腾武" || n == "锯齿骨刺") {
                    score += 8;
                }
            }
        }
    }
    if (dragons > 0 && mother > 0) score += 12;
    if (dragons > 0 && shadowcaster > 0) score += 12;
    if (dragons > 0 && (dance > 0 || potion > 0)) score += 12;
    if (board_d > 0 && shadowstep > 0) score += 12;
    if (deadly && (dance > 0 || potion > 0)) score += 15;
    if (shark && scabbs >= 2) score += 8;
    score += etc_count * 8;
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
    const auto& p = s.path();
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

// ===================== 启发函数库（对比实验用） =====================
// heuristic 取值：
//   0 无启发（基线，仅按当前伤害排序）
//   1 瓶颈模型（min 龙源/回手/法力，当前默认）
//   2 资源求和（combo_progress 式：各资源加权相加）
//   3 伤害上界（仅龙源数 ×16，乐观上限）
//   4 法力阈值（仅法力可负担轮数 ×16）
//   5 回手容量（min(龙源, 回手) ×16，忽略法力）
//   6 组合加权（0.6×瓶颈 + 资源求和 + 路径里程碑）
//   7 瓶颈平方（凸函数，强烈偏好完整循环）
//   8 路径里程碑（鱼…龙 / 龙…晦…龙 / 双舞 等）
//   9 几何平均瓶颈（三块板都高的水桶）
//  10 阈值阶梯（每多打 1 龙奖励递增：16/48/96/160…）
//  11 行动自由度（可打手牌数 + 余费，避免死手）
//  12 回收放大瓶颈（舞动/药水让龙源可复用，再取瓶颈）
//  13 协同密度（已凑齐的搭档对数量）
//  14 容量管理（手牌/战场留空位奖励，爆满惩罚）
//  15 soft-min 瓶颈（调和平均，平滑版瓶颈）
static double heuristic_value(const State& s, int h) {
    BottleneckParts bp = bottleneck_parts(s);
    switch (h) {
        case 0: return 0.0;
        case 1: return bottleneck_dragons(s) * 16.0;
        case 2: return (double)subchain_score(s);
        case 3: return bp.sources * 16.0;
        case 4: return bp.mana_rounds * 16.0;
        case 5: {
            return std::min(bp.sources, bp.capacity) * 16.0;
        }
        case 6: {
            double b6 = std::min(bp.sources, std::min(bp.capacity, bp.mana_rounds));
            return 0.6 * b6 * 16.0
                 + (double)subchain_score(s)
                 + (double)discrete_path_score(s);
        }
        case 7: { double b = bottleneck_dragons(s); return b * b * 5.0; }
        case 8: return (double)discrete_path_score(s);
        case 9: {  // 几何平均：短板不再一票否决，三块板都高才高分
            double geo = std::pow(bp.sources * bp.capacity * bp.mana_rounds, 1.0 / 3.0);
            return geo * 16.0;
        }
        case 10: {  // 阈值阶梯：每完成一条龙奖励累加（1龙16、2龙48、3龙96…）
            int n = (int)std::floor(bottleneck_dragons(s));
            return (double)(n * (n + 1) / 2) * 16.0;
        }
        case 11: {  // 行动自由度：可打手牌数×10 + 余费×2
            int playable = 0;
            for (const auto& c : s.hand) {
                int cost = effective_cost(s, c);
                if (cost >= 0 && cost <= s.mana) playable++;
            }
            return (double)playable * 10.0 + (double)s.mana * 2.0;
        }
        case 12: {  // 回收放大：舞动/药水/转移让场上龙可复用，放大龙源后再取瓶颈
            int hand_d = 0, board_d = 0;
            int shadowcaster = 0, shark_in_hand = 0;
            int whole_returns = 0, single_returns = 0, scabbs = 0, deadly = 0;
            int cheapest = -1;
            for (const auto& c : s.hand) {
                if (c.dragon) {
                    hand_d++;
                    int cost = effective_cost(s, c);
                    if (cost >= 0 && (cheapest < 0 || cost < cheapest)) cheapest = cost;
                } else if (c.name_idx == N_CASTER) shadowcaster++;
                else if (c.name_idx == N_SCABBS) scabbs++;
                else if (c.name_idx == N_SHARK) shark_in_hand++;
                else if (c.name_idx == N_STEP || c.name_idx == N_TENWU) single_returns++;
                else if (c.name_idx == N_DANCE || c.name_idx == N_POTION || c.name_idx == N_TRANSFER) whole_returns++;
                if (c.is_deadly_shadow) deadly++;
            }
            for (const auto& c : s.board) {
                if (c.name_idx == N_DRAGON) board_d++;
                else if (c.name_idx == N_CASTER) shadowcaster++;
            }
            bool shark = s.has_shark() || shark_in_hand > 0;
            double sources = (double)(hand_d + board_d + shadowcaster * (shark ? 2 : 1));
            sources *= 1.0 + (double)whole_returns * 0.5;  // 整场回手让龙源可复用
            double capacity = (double)single_returns + (double)whole_returns * 3.0;
            if (scabbs > 0 && single_returns > 0) capacity += 15.0;
            if (deadly > 0 && s.cards_played_this_turn > 0) capacity += 0.5;
            return std::min(sources, std::min(capacity, bp.mana_rounds)) * 16.0;
        }
        case 13: {  // 协同密度：已凑齐的搭档对
            bool shark_avail = s.has_shark() || count_hand_cards(s, N_SHARK) > 0;
            int dragons = count_hand_dragons(s) + count_board_cards(s, N_DRAGON);
            int mother = count_hand_cards(s, N_MOTHER) + count_board_cards(s, N_MOTHER);
            int shadowcaster = count_hand_cards(s, N_CASTER) + count_board_cards(s, N_CASTER);
            int shadowstep = count_hand_cards(s, N_STEP);
            int dance_potion = count_hand_cards(s, N_DANCE) + count_hand_cards(s, N_POTION);
            int scabbs = count_hand_cards(s, N_SCABBS) + count_board_cards(s, N_SCABBS);
            int foxy = count_hand_cards(s, N_FOX);
            int pairs = 0;
            if (dragons > 0 && shark_avail) pairs++;
            if (dragons > 0 && mother > 0) pairs++;
            if (dragons > 0 && shadowcaster > 0) pairs++;
            if (dragons > 0 && dance_potion > 0) pairs++;
            if (dragons > 0 && shadowstep > 0) pairs++;
            if (shark_avail && scabbs >= 2) pairs++;
            if (foxy > 0 && scabbs > 0) pairs++;
            return (double)pairs * 10.0 + (double)dragons * 8.0;
        }
        case 14: {  // 容量管理：留空位奖励，爆满惩罚
            double score = (double)(std::max(0, 10 - (int)s.hand.size())) * 4.0;
            score += (double)(std::max(0, 7 - (int)s.board.size())) * 2.0;
            if (s.hand_full()) score -= 30.0;
            if (s.board_full()) score -= 40.0;
            return score + bottleneck_dragons(s) * 8.0;
        }
        case 15: {  // soft-min（调和平均）：平滑版瓶颈
            double sum = 0.0;
            if (bp.sources > 0) sum += 1.0 / bp.sources; else sum += 1e9;
            if (bp.capacity > 0) sum += 1.0 / bp.capacity; else sum += 1e9;
            if (bp.mana_rounds > 0) sum += 1.0 / bp.mana_rounds; else sum += 1e9;
            return (3.0 / sum) * 16.0;
        }
        default: return bottleneck_dragons(s) * 16.0;
    }
}

// 根级宽束模拟：与旧 beam 同机制（宽束全深度 + 分桶 + 启发值冠军，
// 总分 = 伤害 + 启发函数值，键含龙数），保证深线（10 龙/160 伤）
// 能被发现；多路宽束并行，共享时间预算。
struct ThreadOut {
    unordered_map<int, State> best;   // 各龙数最优路径
    unordered_map<int, State> best_by_choice;  // 持枪要挟按发现牌分组的最优路径
    unordered_map<string, State> best_by_draw;  // 抽随从分支卡按抽到随从集分组的最优路径
    State best_no_qd;                 // 原版：不含持枪要挟的最优路径
    int expansions = 0;
    int reached_depth = 0;
    double work_sec = 0.0;            // 该线程实际计算耗时（秒）
};

// 宽束通道并行展开：后继生成 + 打分是主要开销，分片到多线程近线性扩展
struct Cand {
    State s;
    uint64_t key = 0;
    double total = 0.0;
    double hval = 0.0;
    int mana = 0;
    int count = 0;
};

// 只计算最高伤害：剩余动作里最多还能打出的龙数上界（宽松安全上界，宁多勿少）。
// 每条龙最多 16 伤；上限 = 当前可用的龙源（手牌/场上/暗施复制）+ 回手/复制/发现潜力，
// 再与剩余步数取小，并 +2 裕量兜底（牛/殒命/幻等变数）。
static int max_extra_dragon_plays(const State& s, int remaining) {
    int plays = 0;
    bool shark = false;
    int sc = 0, wr = 0, sr = 0, po = 0, etc = 0, dl = 0;
    for (const auto& c : s.hand) {
        if (c.dragon) plays++;
        else if (c.name_idx == N_CASTER) sc++;
        else if (c.name_idx == N_DANCE) wr++;
        else if (c.name_idx == N_POTION) po++;
        else if (c.name_idx == N_ETC) etc++;
        else if (c.name_idx == N_STEP || c.name_idx == N_TENWU) sr++;
        else if (c.name_idx == N_SHARK) shark = true;
        else if (c.name_idx == N_DEADLY) dl++;
    }
    for (const auto& c : s.board) {
        if (c.name_idx == N_DRAGON) plays++;
        else if (c.name_idx == N_CASTER) sc++;
        else if (c.name_idx == N_SHARK) shark = true;
    }
    plays += sc * (shark ? 2 : 1);   // 暗施复制龙（鲨鱼双倍）
    plays += wr * 7;                 // 舞动全场每张最多弹回 7 个随从
    plays += po * 7;                 // 幻觉药水最多复制 7 个场上随从
    plays += sr;                     // 暗影步/腾武各回手 1 个
    plays += etc * 3;                // 牛头人最多从乐队拿 3 张
    plays += dl * 2;                 // 殒命暗影可变形为回手/复制法术
    return std::min(remaining, plays + 2);
}

struct BeamRawCand {
    uint64_t key;
    State s;
    double total;
    double hval;
    int mana;
    int count;
};

struct BeamSliceArgs {
    const vector<State>* level;
    const SearchParams* p;
    const Budget* budget;
    vector<BeamRawCand>* out;
    int begin;
    int end;
    std::atomic<long long>* total_exp;
    std::atomic<int>* best_damage = nullptr;
};

// 并行合并分片：同 key 必落在同片（key % shards），seen/候选去重互不冲突
struct BeamMergeArgs {
    const vector<BeamRawCand>* raws;
    const SearchParams* p;
    unordered_map<uint64_t, int>* seen;  // key -> 已见过的最高法力（无需存整状态）
    vector<Cand>* cands;
    unordered_map<int, State>* best;
    unordered_map<int, State>* best_choice;
    unordered_map<string, State>* best_draw;
    State* best_no_qd;
    std::atomic<int>* best_damage = nullptr;
};

static void merge_shard_work(BeamMergeArgs* a) {
    unordered_map<uint64_t, size_t> cand_index;
    for (const BeamRawCand& rc : *a->raws) {
        auto prev = a->seen->find(rc.key);
        if (prev != a->seen->end() && rc.mana <= prev->second) continue;
        (*a->seen)[rc.key] = rc.mana;
        auto cur = cand_index.find(rc.key);
        if (cur != cand_index.end()) {
            Cand& c = (*a->cands)[cur->second];
            if (rc.mana <= c.mana) continue;
            c.s = rc.s;
            c.total = rc.total;
            c.hval = rc.hval;
            c.mana = rc.mana;
        } else {
            Cand c;
            c.s = rc.s;
            c.key = rc.key;
            c.total = rc.total;
            c.hval = rc.hval;
            c.mana = rc.mana;
            c.count = rc.count;
            cand_index[rc.key] = a->cands->size();
            a->cands->push_back(std::move(c));
        }
        add_best((*a->cands)[cand_index[rc.key]].s, *a->best, a->p->min_alex);
        add_best_choice(rc.s, *a->best_choice);
        add_best_draw(rc.s, *a->best_draw);
        add_best_no_qd(rc.s, *a->best_no_qd);
        if (a->best_damage) {
            int d = rc.s.alex_damage;
            int cur = a->best_damage->load();
            while (d > cur && !a->best_damage->compare_exchange_weak(cur, d)) {
            }
        }
    }
}

#ifdef _WIN32
static DWORD WINAPI beam_merge_worker(LPVOID param) {
#else
static void* beam_merge_worker(void* param) {
#endif
    BeamMergeArgs* a = static_cast<BeamMergeArgs*>(param);
    merge_shard_work(a);
#ifdef _WIN32
    return 0;
#else
    return nullptr;
#endif
}

#ifdef _WIN32
static DWORD WINAPI beam_slice_worker(LPVOID param) {
#else
static void* beam_slice_worker(void* param) {
#endif
    BeamSliceArgs* a = static_cast<BeamSliceArgs*>(param);
    long long local = 0;
    for (int i = a->begin; i < a->end; i++) {
        if (a->p->lethal_threshold > 0 && a->best_damage &&
            a->best_damage->load() >= a->p->lethal_threshold) {
            // 精确截断：已达成 伤害 ≥ 敌方血量+护甲，不再展开本层剩余状态
            a->total_exp->fetch_add(local);
            return 0;
        }
        for (State& succ : generate_successors((*a->level)[i])) {
            local++;
            if ((local & 511) == 0 && a->budget->over()) {
                a->total_exp->fetch_add(local);
                return 0;
            }
            BeamRawCand rc;
            rc.key = mix_hash(state_hash(succ), (uint64_t)succ.alex_play_count);
            rc.hval = a->p->heuristic >= 0 ? heuristic_value(succ, a->p->heuristic)
                                           : (double)subchain_score(succ) + (double)discrete_path_score(succ);
            rc.total = (double)succ.alex_damage + rc.hval;
            if (a->best_damage) {
                int d = succ.alex_damage;
                int cur = a->best_damage->load();
                while (d > cur && !a->best_damage->compare_exchange_weak(cur, d)) {
                }
                if (a->p->only_best_damage) {
                    // 只计算最高伤害：当前伤害 + 剩余步数/龙源上界仍超不过已知最高伤则剪掉
                    int remaining = a->p->depth - (int)succ.path().size();
                    if (remaining < 0) remaining = 0;
                    int bound = succ.alex_damage + max_extra_dragon_plays(succ, remaining) * 16;
                    if (bound < a->best_damage->load()) continue;
                }
            }
            rc.mana = succ.mana;
            rc.count = succ.alex_play_count;
            rc.s = std::move(succ);
            a->out->push_back(std::move(rc));
        }
    }
    a->total_exp->fetch_add(local);
#ifdef _WIN32
    return 0;
#else
    return nullptr;
#endif
}

static void wide_beam_pass(const State& start_in, const SearchParams& p,
                           const Budget& budget, ThreadOut& out,
                           std::atomic<int>* best_damage = nullptr) {
    // 宽束通道宽度：默认至少 2400（经验值：1600 会漏掉 3 龙/48 伤这类深线，
    // 旧 beam 3000 稳定挖出 10 龙/160 伤），束宽参数调大时随之上限 3000。
    int beam_width = p.wide_width > 0
        ? p.wide_width
        : 2400;
    State start = start_in.clone_reserved();
    auto dedup_key = [](const State& s) {
        return mix_hash(state_hash(s), (uint64_t)s.alex_play_count);
    };
    vector<State> level = {start};
    int shards = std::max(1, std::min(4, p.inner_threads));
    vector<unordered_map<uint64_t, int>> seen_shards(shards);
    seen_shards[dedup_key(start) % shards][dedup_key(start)] = start.mana;
    add_best(start, out.best, p.min_alex);
    add_best_no_qd(start, out.best_no_qd);

    for (int depth = 1; depth <= p.depth; depth++) {
        if (budget.over()) break;
        if (p.lethal_threshold > 0 && best_damage &&
            best_damage->load() >= p.lethal_threshold) {
            break;  // 精确截断：已找到 伤害 ≥ 敌方血量+护甲 的斩杀线
        }
        if (best_damage && p.only_best_damage &&
            best_damage->load() >= p.max_alex * 16) {
            break;  // 已达理论上限（每条龙最多 16 伤），不可能再提升
        }
        // 并行展开：本层状态分片到 inner_threads 线程（后继生成 + 打分近线性扩展）
        int inner = shards;
        vector<vector<BeamRawCand>> raw_buckets(inner);
        std::atomic<long long> total_exp{0};
        int nstates = (int)level.size();
        int per = (nstates + inner - 1) / inner;
        // 预分配：每个分片约处理 nstates/inner 个状态，每个状态平均 3~8 个后继，
        // 避免 push_back 反复扩容搬移（BeamRawCand 内含 State，扩容代价高）
        for (int t = 0; t < inner; t++) {
            raw_buckets[t].reserve((size_t)(per) * 6 + 16);
        }
        vector<BeamSliceArgs> args(inner);
        for (int t = 0; t < inner; t++) {
            args[t].level = &level;
            args[t].p = &p;
            args[t].budget = &budget;
            args[t].out = &raw_buckets[t];
            args[t].begin = t * per;
            args[t].end = std::min(nstates, (t + 1) * per);
            args[t].total_exp = &total_exp;
            args[t].best_damage = best_damage;
        }
#ifdef _WIN32
        if (inner == 1) {
            beam_slice_worker(&args[0]);
        } else {
            vector<HANDLE> ths;
            ths.reserve(inner);
            for (int t = 0; t < inner; t++) {
                HANDLE h = CreateThread(nullptr, 0, beam_slice_worker, &args[t], 0, nullptr);
                if (h) ths.push_back(h);
            }
            if (!ths.empty()) {
                WaitForMultipleObjects((DWORD)ths.size(), ths.data(), TRUE, INFINITE);
                for (HANDLE h : ths) CloseHandle(h);
            }
        }
#else
        for (int t = 0; t < inner; t++) beam_slice_worker(&args[t]);
#endif
        out.expansions += (int)total_exp.load();

        // 按 key 分片到合并线程：同 key 必同片，去重/最优互不冲突，近线性扩展
        vector<vector<BeamRawCand>> shard_raws(shards);
        for (int t = 0; t < inner; t++) {
            for (BeamRawCand& rc : raw_buckets[t]) {
                shard_raws[rc.key % shards].push_back(std::move(rc));
            }
        }
        vector<vector<Cand>> shard_cands(shards);
        vector<unordered_map<int, State>> shard_best(shards);
        vector<unordered_map<int, State>> shard_best_choice(shards);
        vector<unordered_map<string, State>> shard_best_draw(shards);
        vector<State> shard_best_no_qd(shards);
        vector<BeamMergeArgs> margs(shards);
        for (int sh = 0; sh < shards; sh++) {
            margs[sh].raws = &shard_raws[sh];
            margs[sh].p = &p;
            margs[sh].seen = &seen_shards[sh];
            margs[sh].cands = &shard_cands[sh];
            margs[sh].best = &shard_best[sh];
            margs[sh].best_choice = &shard_best_choice[sh];
            margs[sh].best_draw = &shard_best_draw[sh];
            margs[sh].best_no_qd = &shard_best_no_qd[sh];
            margs[sh].best_damage = best_damage;
        }
#ifdef _WIN32
        if (shards == 1) {
            merge_shard_work(&margs[0]);
        } else {
            vector<HANDLE> ths;
            ths.reserve(shards);
            for (int sh = 0; sh < shards; sh++) {
                HANDLE h = CreateThread(nullptr, 0, beam_merge_worker, &margs[sh], 0, nullptr);
                if (h) ths.push_back(h);
            }
            if (!ths.empty()) {
                WaitForMultipleObjects((DWORD)ths.size(), ths.data(), TRUE, INFINITE);
                for (HANDLE h : ths) CloseHandle(h);
            }
        }
#else
        for (int sh = 0; sh < shards; sh++) merge_shard_work(&margs[sh]);
#endif
        vector<Cand> cands;
        size_t total_c = 0;
        for (int sh = 0; sh < shards; sh++) total_c += shard_cands[sh].size();
        cands.reserve(total_c);
        for (int sh = 0; sh < shards; sh++) {
            for (Cand& c : shard_cands[sh]) cands.push_back(std::move(c));
            for (auto& kv : shard_best[sh]) add_best(kv.second, out.best, p.min_alex);
            for (auto& kv : shard_best_choice[sh]) add_best_choice(kv.second, out.best_by_choice);
            for (auto& kv : shard_best_draw[sh]) add_best_draw(kv.second, out.best_by_draw);
            add_best_no_qd(shard_best_no_qd[sh], out.best_no_qd);
        }
        if (cands.empty()) break;
        // 分桶：按当前龙数，避免高龙数分支挤掉正在蓄力的低龙数高分分支
        map<int, vector<const Cand*>> buckets;
        for (const Cand& c : cands) buckets[c.count].push_back(&c);
        // 每桶配额放宽（1.8×），并多保留启发值冠军，避免深线（如 102558 的 112 伤
        // 法力农场线）被“总分高但同质”的分支挤掉。
        int per_bucket = std::max(1, (int)(beam_width * 1.8 / std::max(1, (int)buckets.size())));
        vector<const Cand*> all_selected;
        all_selected.reserve(std::min((size_t)(beam_width * 2), cands.size()));
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
                // 冠军（最多 3 个去重）：启发值优先，其次总分，其次法力——
                // 让“启发值高但当前伤害低”的深线分支有机会保留。
                vector<const Cand*> champs(bstates);
                std::stable_sort(champs.begin(), champs.end(),
                                 [](const Cand* a, const Cand* b) {
                                     if (a->hval != b->hval) return a->hval > b->hval;
                                     if (a->total != b->total) return a->total > b->total;
                                     return a->mana > b->mana;
                                 });
                for (int i = 0; i < 3 && i < (int)champs.size(); i++) {
                    const Cand* champ = champs[i];
                    bool found = false;
                    for (const Cand* sel : selected) {
                        if (sel->key == champ->key) { found = true; break; }
                    }
                    if (!found) selected.push_back(champ);
                }
            }
            for (const Cand* s : selected) all_selected.push_back(s);
        }
        vector<State> next_level;
        // 全局按总分取前 beam_width（不整桶砍掉低龙数蓄力桶），
        // 再给每个龙数桶保底 beam_width/(2N) 个名额，避免"总分高但同质"分支挤掉必要延续。
        std::stable_sort(all_selected.begin(), all_selected.end(),
                         [](const Cand* a, const Cand* b) {
                             if (a->total != b->total) return a->total > b->total;
                             return a->mana > b->mana;
                         });
        int nb = (int)buckets.size();
        int floor_pb = std::max(1, beam_width / std::max(1, 2 * nb));
        vector<const Cand*> keep;
        keep.reserve(beam_width);
        size_t top_n = std::min((size_t)beam_width, all_selected.size());
        for (size_t i = 0; i < top_n; i++) keep.push_back(all_selected[i]);
        map<int, int> in_keep;
        for (const Cand* c : keep) in_keep[c->count]++;
        for (auto& kv : buckets) {
            int need = floor_pb - in_keep[kv.first];
            if (need <= 0) continue;
            for (const Cand* c : kv.second) {
                if (need <= 0) break;
                if (std::find(keep.begin(), keep.end(), c) != keep.end()) continue;
                keep.pop_back();
                keep.push_back(c);
                need--;
                std::stable_sort(keep.begin(), keep.end(),
                                 [](const Cand* a, const Cand* b) {
                                     if (a->total != b->total) return a->total > b->total;
                                     return a->mana > b->mana;
                                 });
            }
        }
        next_level.reserve(keep.size());
        for (const Cand* s : keep) next_level.push_back(s->s);
        level = std::move(next_level);
        out.reached_depth = std::max(out.reached_depth, depth);
    }
}

struct BeamResult {
    unordered_map<int, State> best_by_dragons;
    unordered_map<int, State> best_by_choice;  // 持枪要挟按发现牌分组的最优路径
    unordered_map<string, State> best_by_draw;  // 抽随从分支卡按抽到随从集分组的最优路径
    State best_no_qd;                 // 原版：不含持枪要挟的最优路径
    int expansions = 0;
    int reached_depth = 0;
    double wall_sec = 0.0;
    double wide_work_sec = 0.0;
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
    std::atomic<int>* best_damage = nullptr;  // 跨通道共享：已发现的最高伤害
    int ww = 0;   // 宽束通道专用：本次模拟的束宽（0 = 用 p->wide_width）
    int heur = -1;  // 宽束通道专用：本次模拟的启发函数（<0 = 用 p->heuristic）
    int inner = 1;  // 宽束通道内部并行展开线程数
    int tid = 0;
};

#ifdef _WIN32
static DWORD WINAPI wide_worker_entry(LPVOID param) {
#else
static void* wide_worker_entry(void* param) {
#endif
    WorkerArgs* a = static_cast<WorkerArgs*>(param);
    auto t_start = std::chrono::steady_clock::now();
    SearchParams pp = *a->p;
    pp.wide_width = a->ww > 0 ? a->ww : pp.wide_width;
    if (a->heur >= 0) pp.heuristic = a->heur;
    pp.inner_threads = a->inner;
    wide_beam_pass(*a->start, pp, *a->budget, *a->out, a->best_damage);
    a->out->work_sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - t_start).count();
    if (a->prog && a->prog->enabled) {
        fprintf(stderr, "PROGRESS %d %d %d\n",
                a->out->expansions, 0, a->out->reached_depth);
    }
#ifdef _WIN32
    return 0;
#else
    return nullptr;
#endif
}

// 纯束宽主搜索：并行多路宽束（默认 {2400,600}），共享时间预算，合并各龙数最优路径。
// 3 秒硬时限内束宽越小走得越深（十龙 600 宽 1.5s 出 10 龙/152 伤，2400 宽 3.6s 只出
// 7 龙/96）；2400 保小局面深线（如 3 龙/48 伤），并行多个宽度兼得两者。
static BeamResult run_beam_search(const State& start, const SearchParams& p, Progress* prog) {
    BeamResult res;
    auto t0 = std::chrono::steady_clock::now();
    // 只计算最高伤害：跨通道共享最高伤，用于剪枝与提前停止
    std::atomic<int> shared_best{0};
    // 默认四通道：H6/1100（8水晶十龙深线）、H1/1500（4水晶十龙/紧线）、
    // H2/1100（96 伤线）、H2/3000（6水晶紧 48 伤线）
    static const int DEFAULT_WIDE_WIDTHS[] = {1100, 1500, 1100, 3000};
    static const int DEFAULT_HEURISTICS[] = {6, 1, 2, 2};
    int wide_count = p.wide_width > 0 ? 1 : (int)std::max(p.wide_widths.size(), p.heuristics.size());
    if (p.wide_width <= 0 && p.wide_widths.empty() && p.heuristics.empty()) wide_count = 2;
    wide_count = std::max(1, std::min(wide_count, std::max(1, p.threads)));
    // 每通道独立预算：默认四通道里最宽的 H2/3000（紧 48 线）提前停，
    // 把最后一段 CPU 让给 H1/1500（4 水晶十龙线，余量最紧）
    vector<Budget> ch_budgets(wide_count);
    for (int w = 0; w < wide_count; w++) {
        ch_budgets[w].t0 = t0;
        ch_budgets[w].budget_sec = p.time_budget_sec;
    }

    vector<ThreadOut> outs(wide_count);
    vector<WorkerArgs> wide_args;
    wide_args.reserve(wide_count);
    for (int w = 0; w < wide_count; w++) {
        WorkerArgs a;
        a.start = &start;
        a.p = &p;
        a.out = &outs[w];
        a.prog = prog;
        a.ww = p.wide_width > 0 ? p.wide_width
              : (p.wide_widths.empty() ? DEFAULT_WIDE_WIDTHS[std::min(w, 1)] : p.wide_widths[w]);
        a.heur = p.heuristics.empty() ? DEFAULT_HEURISTICS[std::min(w, 1)] : p.heuristics[w];
        // 仅默认四通道的 H2/3000（紧 48 线）给 2s 上限；用户自设的大束宽（H6 等）不限额
        if (a.heur == 2 && a.ww >= 2500)
            ch_budgets[w].budget_sec = std::min(ch_budgets[w].budget_sec, 2.0);
        a.budget = &ch_budgets[w];
        a.best_damage = &shared_best;
        // 榨干 CPU：把总线程数按通道均分，多余线程作为各通道内部的并行展开线程
        // （8 核 → 4 通道 × 2 内线程；16 线程 → 4 通道 × 4 内线程，内部上限 4）
        a.inner = std::max(1, std::min(4, p.threads / wide_count));
        a.tid = 90 + w;
        wide_args.push_back(a);
    }

#ifdef _WIN32
    vector<HANDLE> handles;
    handles.reserve(wide_count);
    for (int w = 0; w < wide_count; w++) {
        HANDLE hw = CreateThread(nullptr, 0, wide_worker_entry, &wide_args[w], 0, nullptr);
        if (hw) handles.push_back(hw);
    }
    if (!handles.empty()) {
        WaitForMultipleObjects((DWORD)handles.size(), handles.data(), TRUE, INFINITE);
        for (HANDLE h : handles) CloseHandle(h);
    }
#else
    for (int w = 0; w < wide_count; w++) wide_worker_entry(&wide_args[w]);
#endif

    int prev_max = -1;
    for (int t = 0; t < wide_count; t++) {
        res.expansions += outs[t].expansions;
        res.reached_depth = std::max(res.reached_depth, outs[t].reached_depth);
        res.wide_work_sec = std::max(res.wide_work_sec, outs[t].work_sec);
        res.wide_expansions += outs[t].expansions;
        for (const auto& kv : outs[t].best) add_best(kv.second, res.best_by_dragons, p.min_alex);
        for (const auto& kv : outs[t].best_by_choice)
            add_best_choice(kv.second, res.best_by_choice);
        for (const auto& kv : outs[t].best_by_draw)
            add_best_draw(kv.second, res.best_by_draw);
        add_best_no_qd(outs[t].best_no_qd, res.best_no_qd);
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
static void print_json_result(const BeamResult& res, const SearchParams& p) {
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
    printf("  \"mode\": \"beam\",\n");
    printf("  \"max_damage\": %d,\n", max_damage);
    printf("  \"max_dragons\": %d,\n", max_dragons);
    printf("  \"expansions\": %d,\n", res.expansions);
    printf("  \"depth\": %d,\n", res.reached_depth);
    printf("  \"min_alex\": %d,\n", p.min_alex);
    printf("  \"stats\": {\n");
    printf("    \"展开节点\": %d,\n", res.expansions);
    printf("    \"总耗时(秒)\": %.1f,\n", res.wall_sec);
    printf("    \"宽束耗时(秒)\": %.1f,\n", res.wide_work_sec);
    printf("    \"宽束展开\": %d,\n", res.wide_expansions);
    if (res.wall_sec > 0.0) {
        printf("    \"展开/秒\": %.0f,\n", res.expansions / res.wall_sec);
    }
    static const char* H_NAMES[] = {
        "H0无启发(基线)", "H1瓶颈模型", "H2资源求和", "H3伤害上界",
        "H4法力阈值", "H5回手容量", "H6组合加权", "H7瓶颈平方", "H8路径里程碑",
        "H9几何平均瓶颈", "H10阈值阶梯", "H11行动自由度", "H12回收放大瓶颈",
        "H13协同密度", "H14容量管理", "H15软瓶颈(调和)",
    };
    const char* hname = (p.heuristic >= 0 && p.heuristic <= 15)
                            ? H_NAMES[p.heuristic]
                            : "现状(模拟瓶颈+宽束资源/里程碑)";
    printf("    \"模式\": \"纯束宽\",\n");
    printf("    \"启发函数\": \"%s\"\n", hname);
    printf("  },\n");
    printf("  \"results\": [\n");
    for (size_t i = 0; i < results.size(); i++) {
        const State& pst = results[i];
        printf("    {\"dragons\": %d, \"damage\": %d, \"mana\": %d, \"path\": [",
               pst.alex_play_count, pst.alex_damage, pst.mana);
        for (size_t j = 0; j < pst.path().size(); j++) {
            if (j) printf(", ");
            printf("\"%s\"", json_escape(pst.path()[j]).c_str());
        }
        printf("]}%s\n", i + 1 < results.size() ? "," : "");
    }
    printf("  ],\n");
    // 抽随从分支卡按抽到随从集分组的最优路径（WhatIF 同级分支：
    // 行骗(狐)/行骗(牛) 等各保留一条最高伤路径）
    vector<const State*> draw_choices;
    for (const auto& kv : res.best_by_draw) draw_choices.push_back(&kv.second);
    std::stable_sort(
        draw_choices.begin(), draw_choices.end(),
        [](const State* a, const State* b) {
            if (a->alex_damage != b->alex_damage) return a->alex_damage > b->alex_damage;
            return a->last_draw_key < b->last_draw_key;
        });
    printf("  \"draw_branches\": [\n");
    for (size_t i = 0; i < draw_choices.size(); i++) {
        const State& pst = *draw_choices[i];
        printf("    {\"card\": \"%s\", \"damage\": %d, \"dragons\": %d, \"mana_left\": %d, \"fork_damage\": %d, \"path\": [",
               json_escape(pst.last_draw_key).c_str(),
               pst.alex_damage, pst.alex_play_count, pst.mana, pst.fork_damage);
        for (size_t j = 0; j < pst.path().size(); j++) {
            if (j) printf(", ");
            printf("\"%s\"", json_escape(pst.path()[j]).c_str());
        }
        printf("]}%s\n", i + 1 < draw_choices.size() ? "," : "");
    }
    printf("  ],\n");
    // 持枪要挟按发现牌分组的最优路径（可能分支显示；按牌池优先级 补水>脱水>误炸>… 排序，
    // 最后附“其他快枪牌·随从/·法术”两个杂牌分支）
    vector<const State*> choices;
    for (size_t ci = 0; ci <= QUICKDRAW_MODELED_POOL.size() + 1; ci++) {
        auto it = res.best_by_choice.find((int)ci);
        if (it != res.best_by_choice.end()) choices.push_back(&it->second);
    }
    printf("  \"quickdraw_branches\": [\n");
    for (size_t i = 0; i < choices.size(); i++) {
        const State& pst = *choices[i];
        string card_name;
        if (pst.quickdraw_choice < (int)QUICKDRAW_MODELED_POOL.size()) {
            card_name = QUICKDRAW_MODELED_POOL[pst.quickdraw_choice];
        } else if (pst.quickdraw_choice == (int)QUICKDRAW_MODELED_POOL.size()) {
            card_name = QUICKDRAW_OTHER_MINION_NAME;
        } else {
            card_name = QUICKDRAW_OTHER_SPELL_NAME;
        }
        printf("    {\"card\": \"%s\", \"damage\": %d, \"dragons\": %d, \"mana_left\": %d, \"fork_damage\": %d, \"path\": [",
               json_escape(card_name).c_str(),
               pst.alex_damage, pst.alex_play_count, pst.mana, pst.fork_damage);
        for (size_t j = 0; j < pst.path().size(); j++) {
            if (j) printf(", ");
            printf("\"%s\"", json_escape(pst.path()[j]).c_str());
        }
        printf("]}%s\n", i + 1 < choices.size() ? "," : "");
    }
    printf("  ],\n");
    // 原版：不含持枪要挟的最优路径（不考虑分支节点，即之前的计算逻辑）
    if (res.best_no_qd.path().empty()) {
        printf("  \"original\": null\n}\n");
    } else {
        const State& pst = res.best_no_qd;
        printf("  \"original\": {\"dragons\": %d, \"damage\": %d, \"mana\": %d, \"path\": [",
               pst.alex_play_count, pst.alex_damage, pst.mana);
        for (size_t j = 0; j < pst.path().size(); j++) {
            if (j) printf(", ");
            printf("\"%s\"", json_escape(pst.path()[j]).c_str());
        }
        printf("]}\n}\n");
    }
}

// ===================== 输入解析 =====================
static State state_from_json(const JVal& root) {
    State st;
    st.mana_crystals = (int)root.get_int("crystals", 10);
    st.mana = (int)root.get_int("mana", st.mana_crystals);
    st.initial_mana_crystals = st.mana_crystals;
    st.initial_mana = st.mana;
    // 本回合已出牌数（连击判定）：真实对局由阅读器实时统计传入；
    // 缺省 0 —— 连击不能是第一张出的牌（首张牌不能凭空触发连击）。
    st.cards_played_this_turn = (int)root.get_int("cards_played_this_turn", 0);
    if (st.cards_played_this_turn < 0) st.cards_played_this_turn = 0;
    st.deck_is_known = false;
    const JVal* dj = root.find("deck_is_known");
    if (dj && dj->type == JVal::BOOL) st.deck_is_known = dj->b;
    const JVal* be = root.find("branch_expand");
    if (be && be->type == JVal::BOOL) st.branch_expand = be->b;

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
            // 战场只可能是随从：法术/奥秘/武器误入（如日志/输入错位）直接丢弃
        if (c.is_spell_like() || c.type_idx == N_T_WEAPON) continue;
            long long hp = item.get_int("health", -1);
            if (hp >= 0) c.health = (int)hp;
            long long tc = item.get_int("temp_cost", -1);
            if (tc >= 0) c.temp_cost = (int)tc;
            if (item.find("mini") && item.find("mini")->type == JVal::BOOL)
                c.is_mini_copy = item.find("mini")->b;
            st.board.push_back(c);
        }
    }
    const JVal* enemy_board = root.find("enemy_board");
    if (enemy_board && enemy_board->type == JVal::ARR) {
        for (const auto& item : enemy_board->arr) {
            Card c = make_card(item.get_str("name", "敌方随从"));
            long long hp = item.get_int("health", -1);
            if (hp >= 0) c.health = (int)hp;
            st.enemy_board.push_back(c);
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
        // 牛池最多三张：防御性截断，避免错误输入把乐队撑大
        if (st.etc_band.size() > 3) st.etc_band.resize(3);
    }
    const JVal* qd = root.find("discover_quickdraw_choice");
    if (qd && qd->type == JVal::STR) st.forced_discover_choice = qd->str;
    const JVal* fdc = root.find("forced_draw_choice");
    if (fdc && fdc->type == JVal::STR) st.forced_draw_choice = fdc->str;
    const JVal* deck = root.find("deck");
    if (deck && deck->type == JVal::ARR) {
        for (const auto& item : deck->arr) {
            st.deck.push_back(make_card(item.get_str("name")));
        }
    }
    // 垂钓时光探底已知牌：牌库底 3 张中已知的部分（阅读器自动追踪）；
    // 缺位视为“未知杂牌”，作为垂钓时光的分叉选项。
    const JVal* db = root.find("dredge_bottom");
    if (db && db->type == JVal::ARR) {
        for (const auto& item : db->arr) {
            if (item.type != JVal::STR) continue;
            string n = item.str;
            if (n.empty() || n == "未知杂牌" || n == "未知抽牌") continue;
            st.dredge_bottom.push_back(n);
        }
        if (st.dredge_bottom.size() > 3) st.dredge_bottom.resize(3);
    }
    const JVal* effects = root.find("current_effects");
    if (effects && effects->type == JVal::ARR) {
        for (const auto& item : effects->arr) {
            const string name = item.get_str("name");
            int layers = (int)item.get_int("count", 1);
            if (layers <= 0) layers = 1;
            if (name == "狐人老千") {
                // 狐人老千是战吼：下一张连击牌减 2 费，不需要本回合先出牌。
                // 该效果是本回合打出狐后留下的（阅读器回合切换即过期），
                // 本回合已出牌数置 1 仅作一致性兜底。
                st.cards_played_this_turn = std::max(st.cards_played_this_turn, 1);
                st.next_combo = std::max(st.next_combo, 2 * layers);
            } else if (name == "伺机待发") {
                st.cards_played_this_turn = std::max(st.cards_played_this_turn, 1);
                st.next_spell = std::max(st.next_spell, 2 * layers);
            } else if (name == "锯齿骨刺") {
                st.cards_played_this_turn = std::max(st.cards_played_this_turn, 1);
                st.next_card = std::max(st.next_card, 2 * layers);
            } else if (name == "斯卡布斯·刀油") {
                st.cards_played_this_turn = std::max(st.cards_played_this_turn, 1);
                for (int i = 0; i < layers; i++) st.oil_stacks.push_back({2, 2});
            } else if (name == "幸运彗星") {
                // 彗星效果跨回合保留：下一张连击随从触发两次（由 current_effects
                // 传入，引擎内打出彗星同样设置）。不视为本回合已出过牌——
                // 连击不能是第一张出的牌，首张连击牌不能凭空触发连击。
                st.next_combo_twice = true;
            } else if (name == "[spcost+1]") {
                // 敌方法术增费：异教低阶牧师（+1）/音箱践踏者（+2），叠加生效
                st.sp_cost_inc += layers;
            } else if (name == "[spcost+2]") {
                st.sp_cost_inc += layers * 2;
            }
        }
    }
    return st;
}

// ---------- 路径验证（--verify / replay_path）：逐动作重放，报告卡在哪一步 ----------
// 回溯匹配：同一卡名可能有多个目标实例（如多张红龙），逐个尝试，任一实例组合走通即通过
// exact=true（branch_prefix 分支点重放）：必须精确匹配目标后缀
// （如 暗影施法者（斯卡布斯·刀油2nd）），保证分支搜索从主路径的同一状态继续；
// exact=false（--verify 通用重放）：去掉目标后缀自动尝试所有合法目标。
static bool verify_rec(const State& st, const vector<string>& replay, size_t i,
                       State& final_state, int* attempts, bool exact = false) {
    if (i >= replay.size()) {
        final_state = st;
        return true;
    }
    string want = exact ? canonical_action_exact(replay[i]) : canonical_action(replay[i]);
    vector<State> succs = generate_successors(st);
    for (State& succ : succs) {
        if (succ.path().empty()) continue;
        string got = exact ? canonical_action_exact(succ.path().back())
                           : canonical_action(succ.path().back());
        if (got != want) continue;
        fprintf(stderr, "TRACE step %d: %s | mana=%d hand=%d board=%d burned=%d dragons=%d dmg=%d\n",
                (int)i, succ.path().back().c_str(), succ.mana, succ.hand_size(),
                succ.board_size(), succ.burned_cards, succ.alex_play_count, succ.alex_damage);
        fprintf(stderr, "  HAND:");
        for (const auto& hc : succ.hand)
            fprintf(stderr, " [%s cost=%d hp=%d]", hc.name().c_str(), hc.current_cost(), hc.health);
        fprintf(stderr, "\n  BOARD:");
        for (const auto& bc : succ.board)
            fprintf(stderr, " [%s cost=%d hp=%d]", bc.name().c_str(), bc.current_cost(), bc.health);
        fprintf(stderr, "\n");
        if (verify_rec(succ, replay, i + 1, final_state, attempts, exact)) return true;
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
                       State& final_state, int* fail_step, bool exact = false) {
    int attempts = 0;
    bool ok = verify_rec(start, replay, 0, final_state, &attempts, exact);
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
        if (next("--threads", &tmp)) { p.threads = atoi(tmp.c_str()); continue; }
        if (next("--time-budget", &tmp)) { p.time_budget_sec = atof(tmp.c_str()); continue; }
        if (next("--heuristic", &tmp)) { p.heuristic = atoi(tmp.c_str()); continue; }
        if (next("--wide-width", &tmp)) { p.wide_width = atoi(tmp.c_str()); continue; }
        if (next("--wide-widths", &tmp)) {
            p.wide_widths.clear();
            size_t pos = 0;
            while (pos <= tmp.size()) {
                size_t comma = tmp.find(',', pos);
                string part = tmp.substr(pos, comma == string::npos ? string::npos : comma - pos);
                if (!part.empty()) p.wide_widths.push_back(atoi(part.c_str()));
                if (comma == string::npos) break;
                pos = comma + 1;
            }
            continue;
        }
        if (next("--heuristics", &tmp)) {
            p.heuristics.clear();
            size_t pos = 0;
            while (pos <= tmp.size()) {
                size_t comma = tmp.find(',', pos);
                string part = tmp.substr(pos, comma == string::npos ? string::npos : comma - pos);
                if (!part.empty()) p.heuristics.push_back(atoi(part.c_str()));
                if (comma == string::npos) break;
                pos = comma + 1;
            }
            continue;
        }
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
    p.threads = (int)root.get_int("threads", p.threads);
    p.time_budget_sec = root.get_double("time_budget_sec", p.time_budget_sec);
    p.heuristic = (int)root.get_int("heuristic", p.heuristic);
    p.only_best_damage = (int)root.get_int("only_best_damage", 0);
    p.lethal_threshold = (int)root.get_int("lethal_threshold", -1);
    p.wide_width = (int)root.get_int("wide_width", p.wide_width);
    const JVal* wws = root.find("wide_widths");
    if (wws && wws->type == JVal::ARR) {
        p.wide_widths.clear();
        for (const auto& v : wws->arr)
            if (v.type == JVal::NUM) p.wide_widths.push_back((int)v.num);
    }
    const JVal* hs = root.find("heuristics");
    if (hs && hs->type == JVal::ARR) {
        p.heuristics.clear();
        for (const auto& v : hs->arr)
            if (v.type == JVal::NUM) p.heuristics.push_back((int)v.num);
    }
    p.min_alex = std::max(1, std::min(p.min_alex, p.max_alex));
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
        for (size_t i = 0; i < final_state.path().size(); i++) {
            if (i) printf(", ");
            printf("\"%s\"", json_escape(final_state.path()[i]).c_str());
        }
        printf("]\n}\n");
        return ok ? 0 : 1;
    }

    // 可能分支优化：branch_prefix 非空时先重放前缀到“分支点”（持枪要挟打出前一刻），
    // 再从此状态开始搜索——各分支只回溯到分支点往后计算，不再重复搜索整个前缀。
    const JVal* bp = root.find("branch_prefix");
    vector<string> branch_prefix;
    if (bp && bp->type == JVal::ARR) {
        for (const auto& item : bp->arr)
            if (item.type == JVal::STR) branch_prefix.push_back(item.str);
    }

    State search_start = st;

    if (!branch_prefix.empty()) {
        State final_state;
        int fail_step = -1;
        // 分支点重放用精确目标匹配：保证从主路径同一状态继续搜索
        if (verify_path(st, branch_prefix, final_state, &fail_step, /*exact=*/true)) {
            search_start = final_state;  // 前缀重放成功：从分支点继续
        }
        // 重放失败则退回从初始状态搜索（功能兜底，结果不变）
    }

    Progress prog;
    prog.enabled = true;
    BeamResult res = run_beam_search(search_start, p, &prog);
    if (prog.enabled) {
        fprintf(stderr, "PROGRESS %d %d %d\n", res.expansions, res.wide_expansions, res.reached_depth);
    }

    print_json_result(res, p);
    return 0;
}
