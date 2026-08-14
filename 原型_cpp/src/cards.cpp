#include "cards.h"

#include <stdexcept>

namespace rdc {
namespace {

std::vector<std::string> tags(std::initializer_list<const char*> list) {
    return std::vector<std::string>(list.begin(), list.end());
}

CardDef def(const char* name, int cost, const char* type, const char* effect_id,
            std::vector<std::string> tag_list, int health) {
    CardDef d;
    d.name = name;
    d.cost = cost;
    d.card_type = type;
    d.effect_id = effect_id;
    d.tags = std::move(tag_list);
    d.health = health;
    return d;
}

const std::vector<CardDef>& build_database() {
    // 与 Python CARD_DATABASE 逐条对应（描述文本省略，不影响计算逻辑）。
    static const std::vector<CardDef> db = {
        def("暗影步", 0, "spell", "shadowstep", {}, -1),
        def("伪造的幸运币", 0, "spell", "fake_coin", {}, -1),
        def("幸运币", 0, "spell", "coin", {}, -1),
        def("伺机待发", 0, "spell", "preparation", {}, -1),
        def("殒命暗影", -1, "spell", "deadly_shadow", {}, -1),
        def("黑水弯刀", 1, "weapon", "blackwater_cutlass", tags({"tradeable"}), -1),
        def("邪恶短刀", 1, "weapon", "", {}, -1),
        def("垂钓时光", 1, "spell", "gone_fishin", tags({"combo"}), -1),
        def("挖掘宝藏", 1, "spell", "dig_for_treasure", {}, -1),
        def("闪避", 2, "secret", "evasion", tags({"secret"}), -1),
        def("狐人老千", 2, "minion", "foxy_fraud", tags({"battlecry", "pirate"}), 2),
        def("赤烟·腾武", 2, "minion", "tenwu", tags({"battlecry"}), 2),
        def("行骗", 2, "spell", "swindle", tags({"combo"}), -1),
        def("锯齿骨刺", 2, "spell", "serrated_bone_spike", {}, -1),
        def("疾速矿锄", 2, "weapon", "quick_pick", {}, -1),
        def("异教地图", 2, "spell", "cultist_map", {}, -1),
        def("潜伏帷幕", 3, "spell", "shroud_of_concealment", {}, -1),
        def("晦鳞巢母", 3, "minion", "candlebreath_mother", tags({"battlecry"}), 3),
        def("鲨鱼之灵", 4, "minion", "spirit_of_the_shark", {}, 3),
        def("斯卡布斯·刀油", 4, "minion", "scabbs_cutterbutter", tags({"combo"}), 3),
        def("乐队经理精英牛头人酋长", 4, "minion", "elite_tauren_champion",
            tags({"battlecry"}), 4),
        def("幻觉药水", 4, "spell", "potion_of_illusion", {}, -1),
        def("舞动全场（ft.迦罗娜）", 3, "spell", "breakdance", {}, -1),
        def("生命的缚誓者阿莱克丝塔萨", 9, "minion", "alexstrasza",
            tags({"battlecry", "dragon"}), 8),
        def("可疑交易", 4, "spell", "dubious_purchase", tags({"combo"}), -1),
        def("暗影施法者", 5, "minion", "shadowcaster", tags({"battlecry"}), 4),
    };
    return db;
}

}  // namespace

const std::vector<CardDef>& card_database() {
    return build_database();
}

const std::vector<std::string>& etc_band() {
    static const std::vector<std::string> band = {
        "舞动全场（ft.迦罗娜）",
        "幻觉药水",
        "生命的缚誓者阿莱克丝塔萨",
    };
    return band;
}

bool card_exists(const std::string& name) {
    for (const auto& d : card_database()) {
        if (d.name == name) {
            return true;
        }
    }
    return false;
}

Card make_card(const std::string& name) {
    for (const auto& d : card_database()) {
        if (d.name != name) {
            continue;
        }
        Card c;
        c.name = d.name;
        c.cost = d.cost;
        c.original_name = d.name;
        c.card_type = d.card_type;
        c.effect_id = d.effect_id;
        c.tags = d.tags;
        c.no_fixed_cost = d.cost < 0;
        c.temp_cost = std::nullopt;
        c.is_deadly_shadow = (d.effect_id == "deadly_shadow");
        c.health = d.health < 0 ? std::nullopt : std::optional<int>(d.health);
        return c;
    }
    throw std::runtime_error("未知卡牌：" + name);
}

Card make_card_runtime(const std::string& name, int cost) {
    Card c = make_card(name);
    c.cost = cost;
    c.temp_cost = std::nullopt;
    return c;
}

bool is_coin_name(const std::string& name) {
    return name == "幸运币" || name == "伪造的幸运币";
}

bool is_core_card(const std::string& name) {
    static const std::vector<std::string> core = {
        "鲨鱼之灵", "狐人老千", "斯卡布斯·刀油", "暗影施法者",
        "乐队经理精英牛头人酋长", "晦鳞巢母", "生命的缚誓者阿莱克丝塔萨",
        "暗影步", "舞动全场（ft.迦罗娜）", "幻觉药水", "锯齿骨刺",
        "殒命暗影", "赤烟·腾武",
    };
    for (const auto& n : core) {
        if (n == name) {
            return true;
        }
    }
    return false;
}

bool is_shadowcaster_allowed_target(const std::string& name) {
    return name == "暗影施法者" || name == "斯卡布斯·刀油" ||
           name == "生命的缚誓者阿莱克丝塔萨" || name == "晦鳞巢母";
}

bool is_mana_gain_or_discount_effect(const std::string& effect_id) {
    return effect_id == "coin" || effect_id == "fake_coin" ||
           effect_id == "preparation" || effect_id == "shadowstep" ||
           effect_id == "foxy_fraud" || effect_id == "scabbs_cutterbutter" ||
           effect_id == "serrated_bone_spike";
}

bool is_draw_card_effect(const std::string& effect_id) {
    return effect_id == "dig_for_treasure" || effect_id == "shroud_of_concealment" ||
           effect_id == "swindle" || effect_id == "dubious_purchase" ||
           effect_id == "gone_fishin" || effect_id == "quick_pick";
}

const std::vector<std::vector<std::string>>& preferred_combo_patterns() {
    static const std::vector<std::vector<std::string>> patterns = {
        {"鲨鱼之灵", "狐人老千", "斯卡布斯·刀油"},
        {"斯卡布斯·刀油", "暗影施法者", "乐队经理精英牛头人酋长"},
        {"鲨鱼之灵", "斯卡布斯·刀油", "斯卡布斯·刀油", "生命的缚誓者阿莱克丝塔萨"},
        {"生命的缚誓者阿莱克丝塔萨", "暗影施法者", "生命的缚誓者阿莱克丝塔萨",
         "生命的缚誓者阿莱克丝塔萨"},
    };
    return patterns;
}

}  // namespace rdc
