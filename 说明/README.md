# 炉石红龙贼计算器（MCTS + 束搜索模拟版）

本项目是红龙贼（牛转 / 牛舞）击杀手牌计算器，2026-08-05 精简为两层结构：

- **C++ 只负责计算**：`代码/red_dragon_core.cpp` 编译为 `red_dragon_engine.exe`，
  stdin 读 JSON 局面，stdout 回 JSON 结果（MCTS + 束搜索模拟 + 动态子链库）。
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
│   ├── red_dragon_core.cpp     # C++ 计算核心（MCTS + 束搜索模拟 + 动态子链库）
│   ├── subchain_library.json   # 动态子链库（计算完成后自动更新，可删除重建）
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
- 右侧选择计算参数后点「开始计算」（MCTS + 束搜索模拟）：
  - 迭代次数/步（默认 400，UCB1 每步决策的迭代数 N）、模拟束宽（默认 8，B_sim）、
    模拟深度（默认 8）、探索常数 C（默认 1.414，可按奖励范围 0.5~2 调整）；
  - 搜索局数（默认 8，根并行上限）、时间预算秒数（默认 30，0 = 不限时）、
    最大深度（默认 30）、最少/最多龙数、最大路径数；
- 每次计算完成后，C++ 会把发现路径的连续子链写回
  `代码/subchain_library.json`（价值 = 所在路径龙数×10），下次计算自动加载加权；
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
  "iterations": 400, "beam_width": 8, "sim_depth": 8, "explore_c": 1.414,
  "games": 8, "threads": 4, "time_budget_sec": 30.0,
  "library_path": "代码/subchain_library.json"
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

## 计算模式说明（MCTS + 束搜索模拟）

- 每步决策以当前局面为根运行 N 次 MCTS 迭代：UCB1 选择 → 随机扩展 →
  束搜索快速模拟（模拟束宽 B_sim，按子链库给后继动作加权）→ 回传奖励；
- 奖励 = **伤害优先**（伤害×100 + 龙数，鲨鱼在场每龙 16 伤）；
- 迭代结束后选择访问次数最多的子动作执行，进入下一状态，直到游戏结束；
- 多局根并行（默认 4 线程），时间预算到时自动停止并返回当前最优路径；
- 另开一个**根级宽束模拟线程**（宽束全深度、按龙数分桶 + 覆盖度冠军），
  保证 25 步以上的深线（如 10 龙/160 伤）能被发现；
- **独立子链库**：内置短子链 + 离散长距离子链种子（各自独立评分），
  束搜索模拟按“当前路径后缀命中子链前缀”的综合加权给动作打分；
- **动态更新**：计算完成后把发现路径的连续子链提取进库并更新对应价值，
  持久化到 `subchain_library.json`，跨次计算持续学习。

搜索算法细节见 `说明/计算流程.md`。
