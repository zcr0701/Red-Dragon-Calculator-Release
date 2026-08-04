#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

#include "cards.h"
#include "engine.h"
#include "format.h"
#include "search.h"

#include <chrono>
#include <cstdio>
#include <iostream>
#include <optional>
#include <string>
#include <vector>

namespace {

// Windows：命令行参数为 UTF-16，需转成 UTF-8，避免 MinGW 运行时按 ANSI 码页
// 转换导致中文卡名乱码。
std::vector<std::string> windows_wide_args() {
    int count = 0;
    LPWSTR* wargv = CommandLineToArgvW(GetCommandLineW(), &count);
    std::vector<std::string> out;
    if (!wargv) {
        return out;
    }
    for (int i = 0; i < count; ++i) {
        int len = WideCharToMultiByte(CP_UTF8, 0, wargv[i], -1, nullptr, 0,
                                      nullptr, nullptr);
        std::string s(len > 0 ? len - 1 : 0, '\0');
        if (len > 1) {
            WideCharToMultiByte(CP_UTF8, 0, wargv[i], -1, s.data(), len,
                                nullptr, nullptr);
        }
        out.push_back(s);
    }
    LocalFree(wargv);
    return out;
}

std::vector<std::string> parse_names(const std::string& text) {
    std::vector<std::string> names;
    std::string current;
    for (char ch : text) {
        if (ch == ',') {
            size_t start = current.find_first_not_of(" \t");
            size_t end = current.find_last_not_of(" \t");
            if (start != std::string::npos) {
                names.push_back(current.substr(start, end - start + 1));
            }
            current.clear();
        } else {
            current += ch;
        }
    }
    size_t start = current.find_first_not_of(" \t");
    size_t end = current.find_last_not_of(" \t");
    if (start != std::string::npos) {
        names.push_back(current.substr(start, end - start + 1));
    }
    return names;
}

struct Args {
    std::string hand;
    std::string deck;
    int mana_crystals = 10;
    std::optional<int> mana;
    std::vector<std::string> play;
    bool search = false;
    bool beam = false;
    bool json = false;
    int beam_width = 3000;
    int max_depth = 100;
    int max_paths = 1000000;
    int max_alex_count = 10;
    int min_alex_count = 1;
    int show_limit = 200;
    double time_budget = 0.0;  // 0 = 不限时
    bool time_budget_set = false;
};

int parse_int(const std::string& text, const std::string& name) {
    try {
        size_t pos = 0;
        int value = std::stoi(text, &pos);
        if (pos != text.size()) {
            throw std::invalid_argument("trailing");
        }
        return value;
    } catch (...) {
        std::cerr << "[错误] 参数 " << name << " 必须是整数，收到：" << text
                  << "\n";
        std::exit(1);
    }
}

void usage() {
    std::cout
        << "红龙计算器 C++ 原型（与 red_dragon_calculator.py CLI 对齐）\n\n"
        << "用法:\n"
        << "  red_dragon_calc [选项]\n\n"
        << "选项:\n"
        << "  --hand \"卡1,卡2,...\"            手牌卡名（逗号分隔）\n"
        << "  --deck \"卡1,卡2,...\"            牌库卡名；不传表示牌库未知\n"
        << "  --mana-crystals N                 水晶数，默认 10\n"
        << "  --mana N                          当前法力，默认等于水晶数\n"
        << "  --play 卡名                       按名称依次打出（可重复传入）\n"
        << "  --search                          执行束搜索\n"
        << "  --beam                            与 --search 相同（兼容原 CLI）\n"
        << "  --beam-width N                    束宽，默认 3000\n"
        << "  --max-depth N                     最大步数，默认 100\n"
        << "  --max-paths N                     路径上限，默认 1000000\n"
        << "  --max-alex-count N                搜索龙数上限，默认 10\n"
        << "  --min-alex-count N                搜索龙数下限，默认 1\n"
        << "  --show-limit N                    文本输出路径数，默认 200\n"
        << "  --json                            输出 JSON\n"
        << "  -h, --help                        显示帮助\n";
}

}  // namespace

int main(int argc, char** argv) {
    (void)argc;
    (void)argv;
#ifdef _WIN32
    SetConsoleOutputCP(CP_UTF8);
    SetConsoleCP(CP_UTF8);
#endif

    Args args;
#ifdef _WIN32
    std::vector<std::string> raw = windows_wide_args();
    if (raw.size() > 0) {
        raw.erase(raw.begin());  // 去掉程序名
    }
#else
    std::vector<std::string> raw(argv + 1, argv + argc);
#endif

    auto need_value = [&](size_t& i, const std::string& name) -> std::string {
        if (i + 1 >= raw.size()) {
            std::cerr << "[错误] 参数 " << name << " 缺少取值\n";
            std::exit(1);
        }
        return raw[++i];
    };

    for (size_t i = 0; i < raw.size(); ++i) {
        const std::string& arg = raw[i];
        if (arg == "-h" || arg == "--help") {
            usage();
            return 0;
        } else if (arg == "--hand") {
            args.hand = need_value(i, arg);
        } else if (arg == "--deck") {
            args.deck = need_value(i, arg);
        } else if (arg == "--mana-crystals") {
            args.mana_crystals = parse_int(need_value(i, arg), arg);
        } else if (arg == "--mana") {
            args.mana = parse_int(need_value(i, arg), arg);
        } else if (arg == "--play") {
            args.play.push_back(need_value(i, arg));
        } else if (arg == "--search") {
            args.search = true;
        } else if (arg == "--beam") {
            args.beam = true;
        } else if (arg == "--json") {
            args.json = true;
        } else if (arg == "--beam-width") {
            args.beam_width = parse_int(need_value(i, arg), arg);
        } else if (arg == "--max-depth") {
            args.max_depth = parse_int(need_value(i, arg), arg);
        } else if (arg == "--max-paths") {
            args.max_paths = parse_int(need_value(i, arg), arg);
        } else if (arg == "--max-alex-count") {
            args.max_alex_count = parse_int(need_value(i, arg), arg);
        } else if (arg == "--min-alex-count") {
            args.min_alex_count = parse_int(need_value(i, arg), arg);
        } else if (arg == "--show-limit") {
            args.show_limit = parse_int(need_value(i, arg), arg);
        } else if (arg == "--time-budget") {
            args.time_budget = std::stod(need_value(i, arg));
            args.time_budget_set = true;
        } else {
            std::cerr << "[错误] 未知参数：" << arg << "\n";
            usage();
            return 1;
        }
    }

    try {
        std::vector<std::string> deck_names = parse_names(args.deck);
        std::vector<std::string> hand_names = parse_names(args.hand);
        rdc::GameState state = rdc::create_state(
            deck_names, !args.deck.empty(), hand_names, args.mana_crystals,
            args.mana);

        for (const auto& name : args.play) {
            rdc::play_card_by_name(state, name);
        }

        if (args.search) {
            double budget = args.time_budget_set ? args.time_budget : 0.0;
            auto t0 = std::chrono::steady_clock::now();
            std::vector<rdc::GameState> states = rdc::beam_search_paths(
                state, args.max_depth, args.max_paths, args.max_alex_count,
                args.min_alex_count, args.beam_width, budget);
            auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                          std::chrono::steady_clock::now() - t0)
                          .count();

            if (args.json) {
                std::cout << rdc::results_json(states) << "\n";
            } else {
                std::cout << rdc::format_paths(states, args.show_limit) << "\n";
                char buf[64];
                std::snprintf(buf, sizeof(buf), "计算总耗时：%.1f 秒",
                              ms / 1000.0);
                std::cout << buf << "\n";
            }
            return 0;
        }

        if (args.json) {
            std::cout << rdc::state_summary_json(state) << "\n";
        } else {
            std::cout << rdc::state_summary_text(state) << "\n";
        }
    } catch (const std::exception& e) {
        std::cerr << "[错误] " << e.what() << "\n";
        return 1;
    }
    return 0;
}
