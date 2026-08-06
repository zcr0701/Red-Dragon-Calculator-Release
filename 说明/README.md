# 炉石红龙贼计算器（纯束宽搜索版）

本项目是红龙贼（牛转 / 牛舞）击杀手牌计算器，2026-08-05 精简为两层结构：

- **C++ 只负责计算**：`代码/red_dragon_core.cpp` 编译为 `red_dragon_engine.exe`，
  stdin 读 JSON 局面，stdout 回 JSON 结果（纯束宽搜索 + 启发函数）。
- **Python 只负责 GUI 与日志读取**：PyQt5 图形界面（`main.py`）+
  基于 hslog 读取本机 `Power.log` 的对局快照（`powerlog_reader.py`）。
- Python 把场面状态（手牌 / 战场 / 水晶 / 法力 / 当前效果等）序列化成 JSON
  传给 C++，C++ 把搜索结果（龙数 / 伤害 / 路径）返回给 GUI 展示。

旧版 Python 计算引擎、OCR 界面、HDT 插件、局面存档同步等模块已删除
（均在 git 历史中，`git log` 可找回）。

## 项目结构

```text
炉石传说/
├── 代码/
│   ├── main.py                 # PyQt5 GUI 主入口（Python 入口）
│   ├── powerlog_reader.py      # Power.log 实时读取器（hslog 解析 → 对局快照）
│   ├── engine.py               # 快照 → JSON → C++ exe → 结果解析（薄封装）
│   ├── red_dragon_core.cpp     # C++ 计算核心（纯束宽搜索 + 瓶颈模型启发）
│   ├── red_dragon_engine.exe   # 编译产物（计算核心）
│   ├── build_engine.bat        # 重新编译 C++ 核心（MinGW g++）
│   ├── card_id_map.json        # CardID → 中文卡名（日志读取用）
│   └── logs/                   # 搜索结果输出目录
├── 说明/
│   ├── README.md               # 本文档
│   ├── 搜索算法调研.md
│   └── 计算流程.md
├── 存档/                       # 历史局面数据（只读参考）
├── 参考数据/                   # 公式表与截图
└── work/                       # 历史实验/测试脚本（未精简）
```

## 运行方式

### GUI（主入口）

```powershell
python 代码/main.py
```

- 顶部状态条显示当前读取的 Power.log 路径与对局状态；窗口每 1 秒增量刷新。
- 左侧展示手牌、战场随从、牌库数、武器、奥秘、当前效果；
- 「殒命暗影位置」可手动勾选并填手牌序号（日志 `GHOSTLY` 自动识别会合并）；
- 牛头人卡池可勾选剩余选项：**舞动全场 / 幻觉药水 / 红龙 / 战略转移 / 赤烟·腾武**，
  最多勾选 3 张，默认 舞/药/龙（取消勾选 = 这张已被选走）；
- 底部「手动输入」面板：手牌栏“费用 卡名”、随从栏“费用 卡名 血量”、
  当前效果“狐人老千 2”（数量=叠加层数）、殒命暗影写“* 殒命暗影”，
  支持简称（刀油/狐/鱼/龙/舞/药/转），点「解析并应用」后直接计算；
- 右侧选择计算参数后点「开始计算」（纯束宽搜索）：
  - 时间预算秒数（默认 3，硬时限 ≤3 秒，大部分 1~2 秒出结果）、最大深度（默认 100）、
    最少/最多龙数、最大路径数；
- 单路宽束（默认束宽 1100 + H6 组合加权启发）：小局面深线（3 龙/48 伤）与大局面深度
  （10 龙/152~160 伤）折中；`wide_width` / `wide_widths` / `heuristic` 可调；
- 结果按伤害排序展示路径，并自动写入 `代码/logs/red_dragon_all_paths_时间戳.txt`。

自测参数：

```powershell
python 代码/main.py --smoke      # 打开窗口 2.5 秒后自动关闭
python 代码/main.py --demo       # 启动即载入示例局面（无游戏也能试计算）
python 代码/main.py --selftest   # 打开窗口并跑一次示例计算后退出
```

### 重新编译 C++ 核心

```powershell
代码\build_engine.bat
```

需要 MinGW g++（本机在 `D:\mingw64\bin\g++.exe`）。

### 命令行调用 C++ 核心（JSON 接口）

```powershell
Get-Content 局面.json | 代码\red_dragon_engine.exe --json
```

输入 JSON 示例：

```json
{
  "crystals": 8, "mana": 8,
  "hand": [{"name": "鲨鱼之灵", "temp_cost": 1},
           {"name": "斯卡布斯·刀油", "temp_cost": 1}],
  "board": [], "secrets": [], "weapon": null, "deck": [],
  "etc_band": ["舞动全场（ft.迦罗娜）", "幻觉药水", "生命的缚誓者阿莱克丝塔萨"],
  "min_alex": 1, "max_alex": 10, "depth": 30,
  "max_paths": 1000000, "mode": "mcts_beam",
  "iterations": 50, "beam_width": 8, "sim_depth": 8, "explore_c": 1.414,
  "games": 8, "threads": 4, "time_budget_sec": 2.0,
  "wide_widths": [2400, 600]
}
```

stdout 输出 JSON 结果（`results` 里每条含 `dragons` / `damage` / `mana` / `path`），
stderr 输出 `PROGRESS` / `FOUND` 实时进度。

## 日志读取前置条件（hslog）

- `%LOCALAPPDATA%\Blizzard\Hearthstone\log.config` 已开启
  `[Power] FilePrinting=True Verbose=true`；
- 炉石安装目录（`F:\Hearthstone` 等）`Logs` 下每个会话目录里有 `Power.log`；
- 依赖：`pip install hslog`（自动带 hearthstone、aniso8601）。

命令行单独测试日志读取：

```powershell
python 代码/powerlog_reader.py --once    # 打印最新对局快照（JSON）
python 代码/powerlog_reader.py --watch   # 持续跟随最新对局
```

读取内容与机制：

- **手牌 / 场面 / 水晶 / 法力**：字段级精确（CardID → 中文名、当前费用含
  刀油/伺机/腾武减费、随从血量）；法力 = `RESOURCES - RESOURCES_USED + TEMP_RESOURCES`；
- **当前效果**：按“打出事件 + 消耗规则”计数（伺机待发/狐人老千/刀油/骨刺），
  中途启动时用附加效果实体兜底补种；计算时把当前效果（如 狐人老千×2）传给
  C++ 核心（手牌按基础费用传入，由 C++ 按效果折扣，避免双重折扣）；
- **殒命暗影**：按 `GHOSTLY` tag 标记手牌位置；
- **本机玩家判定**：优先用
  `%LOCALAPPDATA%\Blizzard\Hearthstone\Cache\Offline\offlineData_*` 的账号
  hi/lo 匹配 `CREATE_GAME` 的 `GameAccountId`。

已知边界：`deck` 只含“已揭示”的牌库卡（Power.log 不直接给出整副牌）；
对局结束后快照标记 `game_over`；单行解析失败自动跳过并计数 `line_errors`。

## 计算模式说明（纯束宽搜索）

- 从初始局面做全深度束宽搜索（按龙数分桶 + 启发值冠军兜底，键含龙数去重），
  时间预算到时返回各龙数最优路径；**无 MCTS / 无子链库**，代码精简；
- 启发函数默认 **H6 组合加权** = 0.6×瓶颈模型 + 资源求和 + 路径里程碑；
- 瓶颈模型（Liebig 最小因子律）：可达龙数 ≈ min(① 龙源数, ② 回手容量, ③ 法力可负担轮数)，
  束内保留评分 = 当前伤害 + 启发函数值；
- 硬时限 ≤3 秒（宽束循环内细粒度中断），大部分局面 1~2 秒出结果。

搜索算法细节见 `说明/计算流程.md`。
