# 红龙计算器 C++ 原型（束搜索版）

把 Python 版红龙贼出牌路径计算器（`代码/red_dragon_calculator.py`）的核心逻辑移植为
C++17 命令行程序。按用户要求，**只保留正向束搜索**，不包含穷举/符号链证明模式。

## 瀑布式开发记录

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| 1 需求分析 | 通读 6 个 Python 文件，梳理 OCR 识别→手牌重建→路径计算→存档的完整链路 | 原型范围锁定为“计算器核心 CLI”（卡牌模型 + 效果引擎 + 束搜索），不包含 OCR/存档/GUI |
| 2 概要设计 | 模块划分与 Python 一一对应 | model / cards / engine / search / format / main 六个模块 |
| 3 详细设计与编码 | 移植 26 张卡牌、全部效果、搜索分支、束搜索算法 | `src/` 下约 2300 行 C++17，零警告编译 |
| 4 测试验证 | 与 Python 原版逐字对拍 | 6/6 基础用例通过；高难实战局面（8水晶）最优线 7龙/104伤 与 Python 一致 |
| 5 交付 | 构建脚本、本文档、对比测试脚本 | 本目录 |

## 目录结构

```
原型_cpp/
├── build.bat          一键构建（优先 PATH 中的 g++/w64devkit，回退 MSVC）
├── test_compare.ps1   与 Python 原版对拍测试（beam 模式）
├── src/
│   ├── model.h         Card / GameState / 常量（对应 Python dataclass）
│   ├── cards.*         卡牌数据库与集合常量（对应 CARD_DATABASE / ETC_BAND）
│   ├── engine.*        效果引擎与单局出牌（对应 play_card / effect_*）
│   ├── search.*        束搜索（对应 generate_successors / beam_search_paths）
│   ├── format.*        路径压缩/文本/JSON 输出（对应 format_paths）
│   └── main.cpp        CLI 入口
└── build/              构建产物（已 gitignore）
```

## 构建

本机 MSVC 存在但 Windows SDK 头文件缺失（`Windows Kits\10` 目录为空），因此使用便携版
MinGW-w64（[w64devkit](https://github.com/skeeto/w64devkit)，已下载解压到
`%LOCALAPPDATA%\w64devkit`）。`build.bat` 按以下顺序寻找编译器：

1. `PATH` 中的 `g++`；
2. `%LOCALAPPDATA%\w64devkit\w64devkit\bin\g++.exe`；
3. MSVC `cl`（需完整 VS C++ 工作负载 + Windows SDK）。

```bat
build.bat
```

## 用法

与 Python CLI 参数对齐：

```bat
build\red_dragon_calc.exe --hand "鲨鱼之灵,幸运币,幸运币,幸运币,生命的缚誓者阿莱克丝塔萨" ^
  --mana-crystals 10 --mana 10 --search --beam-width 300 --max-depth 12
```

常用参数：

- `--hand` / `--deck`：手牌/牌库卡名（逗号分隔）；不传 `--deck` 表示牌库未知；
- `--mana-crystals` / `--mana`：水晶/当前法力；
- `--play 卡名`：按名称依次打出（可重复），用于先模拟到某局面再搜索；
- `--search`：执行束搜索（`--beam` 为兼容旧 CLI 的同义参数）；
- `--beam-width N`：束宽，默认 3000；`--max-depth N`：步数上限，默认 100；
- `--max-paths` / `--max-alex-count` / `--min-alex-count`：路径/龙数上下限；
- `--show-limit N`：文本输出条数；`--json`：输出 JSON；`--time-budget N`：秒数预算
  （0 = 不限时，默认不限时）。

## 与 Python 的对应关系

| Python | C++ |
| --- | --- |
| `CardDef` / `CardInstance` / `GameState` | `rdc::CardDef` / `rdc::Card` / `rdc::GameState` |
| `CARD_DATABASE` / `ETC_BAND` | `cards.cpp` 卡牌表 / `etc_band()` |
| `play_card` / `apply_card_effect` / 各 `effect_*` | `engine.cpp` 同名单函数 |
| `generate_successors` / `heuristic_score` | `search.cpp` 同名函数 |
| `beam_search_paths`（含子链/覆盖度/离散子链评分） | `beam_search_paths` |
| `sort_path_states` / `compress_path_steps` / `format_paths` | `format.cpp` 同名函数 |

## 测试结果

`test_compare.ps1` 将 Python 原版与 C++ 版在同一局面（beam 模式、相同束宽/深度）下对拍，
比较“共计算出”之后的全部输出：

- 6 个基础用例（硬币、刀油连击、暗影步回手、牛头人发现、殒命暗影变形、已知牌库抽牌）：
  **6/6 逐字一致**；
- 高难实战局面（8 水晶、11 张手牌，含 3 刀油 + 殒命暗影 + 舞动全场）：双方均找到
  **7 条路径，最优 7龙/104伤**，前 6 条除同分平局路径外逐字一致。

## 已知差异（原型范围说明）

1. **只有束搜索**：Python 默认的“反向符号链证明”（约 3500 行）与穷举模式未移植，
   因此复杂局面的搜索深度/效率不如 Python 默认模式；复杂局面建议加大 `--beam-width`
   与 `--max-depth`。
2. **无存档/公式表持久化**：不读写 `存档/`，计算结果只在内存中返回。
3. **同分平局**：束搜索在“评分与剩余法力完全相同”的多条路径之间如何取舍由遍历顺序
   决定，C++ 与 Python 可能保留不同的等价路径（龙数/伤害/法力相同）。
4. `--play` 模拟与搜索共用同一效果引擎，效果结算顺序与 Python 一致（鲨鱼双倍战吼、
   连击/减费叠加等已对拍验证）。
