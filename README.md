# 炉石红龙贼计算器

本项目是从 TRAE 对话中迁移过来的红龙贼（牛转 / 牛舞）计算器项目，用 git 管理版本和分支。

## 项目结构

```text
炉石传说/
├── red_dragon_calculator.py    # 红龙路径计算核心：卡牌定义、状态模型、符号链、路径枚举
├── rebuild_hand.py             # OCR 文本 → 手牌/牌库/战场/当前效果 重建逻辑
├── ocr_region_gui.py           # OCR 图形界面（PyQt5），截图区域 + 计算入口
├── ocr_interface.py            # OCR 底层封装（PaddleOCR + mss 截图）
├── card_config.json            # 卡牌名称修正配置
├── docs/
│   └── 红龙贼-对话导出.md       # 原始 TRAE 对话导出（迁移依据）
├── 参考数据/
│   ├── 六随红龙公式表(2026.4.22更新).xlsx   # 六随红龙公式表（公式表 sheet，115 行）
│   ├── 红龙贼-牛转牛舞公式.xlsx            # 牛转/牛舞公式（简洁版 + 精修版）
│   └── 截图/                              # OCR 测试截图
├── git使用指南.md              # Git 使用教程（配合本项目的分支工作流）
└── README.md
```

## 运行方式

### 命令行（不需要 GUI 依赖）

```powershell
# 红龙路径计算：给定手牌，搜索可达路径
python red_dragon_calculator.py --hand "狐人老千,斯卡布斯·刀油,鲨鱼之灵,晦鳞巢母" --search

# 从 OCR 文本重建手牌（支持 --text / --file）
python rebuild_hand.py --text "手牌中(9)`n0 狐人老千`n4 鲨鱼之灵"
```

主要参数见 `python red_dragon_calculator.py --help`。

### 图形界面（需要 PyQt5 + PaddleOCR）

```powershell
python ocr_region_gui.py
```

界面支持框选截图区域、选择牛头人酋长剩余卡池（舞动全场 / 幻觉药水 / 生命缚誓者阿莱克丝塔萨）、查看初始状态摘要。计算结果会写入 `logs/red_dragon_all_paths_时间戳.txt`。

## 迁移时已知的状态（截至对话导出 2026-08-03）

- 计算器目前主要依赖"反向符号链模板"证明，已禁用慢速对象化正向搜索。
- 已确认成立：基础 2/3/4 龙路径、狐人老千[0费] 场景下的药水五龙路径、"战场已有鲨鱼之灵"预启动 4 龙路径（`公式预启动鱼在场步刀伺骨四龙链`）。
- 未确认：预启动局面的 5 龙 / 6 龙尚未被现有模板证明，不排除存在其他药水/回手/费用排列。
- 对话中提到的临时验证脚本（`formula_regression_probe.py`、`validate_new_formula_table.py`、`probe_*.py` 等）在 TRAE 临时目录中已丢失，未随迁移恢复；后续如需可重新编写。

## Git 分支约定

- `main`：稳定里程碑，只合并验证过的版本。
- `dev`：日常修改都在这个分支上，改完想留档时再提交，不必每次修改都提交。
- 详见 [git使用指南.md](git使用指南.md)。
