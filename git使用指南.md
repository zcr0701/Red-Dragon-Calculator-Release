# Git 使用指南（给本项目用）

这个项目已经配好了两个分支：

- `main`：稳定版。只有完成一个里程碑（比如"验证到 5 龙"）才合并进来。
- `dev`：日常工作分支。你大量修改、补公式的时候都待在这个分支，**想留个档再提交，不用每次修改都提交**。

Git 的基本心智模型：

```text
工作区（你正在改的文件）
   │  git add        （把某个文件放进"暂存区"）
   ▼
暂存区（准备提交的改动）
   │  git commit     （把暂存区打包成一条历史记录）
   ▼
版本库（历史记录，可以随时回去）
```

## 最常用的命令（先记住这 7 个）

### 1. 查看状态

```powershell
git status
```

告诉你：哪些文件改了、哪些还没提交。**开始任何操作前先看它**。

### 2. 看具体改了什么

```powershell
git diff
```

只显示你改了哪些行（还没暂存的改动）。

### 3. 把改动加入暂存区

```powershell
git add red_dragon_calculator.py   # 只加入一个文件
git add -A                          # 加入所有改动（新文件/修改/删除都算）
```

你想"挑着提交"就只 `git add` 需要的文件，不想提交的改完先放着。

### 4. 提交（打一个存档点）

```powershell
git commit -m "补上预启动四龙链模板"
```

`-m` 后面是这次提交的说明，写清楚"做了什么"即可。

### 5. 看历史

```powershell
git log --oneline
```

一行一条历史记录。加 `-5` 只看最近 5 条：`git log --oneline -5`。

### 6. 切换分支

```powershell
git switch dev     # 切到 dev 分支
git switch main    # 切回 main 分支
```

切分支前，未提交的改动会跟着你走；所以**重要改动先 commit 再切分支**。

### 7. 合并分支

```powershell
git switch main          # 先回到 main
git merge dev            # 把 dev 的提交合并进 main
```

合并后 main 和 dev 内容一样，可以继续在 dev 上改下一轮。

## 回溯（你本来就关心的能力）

### 撤销"还没提交"的修改（恢复到上次提交的样子）

```powershell
git restore red_dragon_calculator.py   # 放弃这个文件的未提交改动
git restore .                          # 放弃所有未提交改动（小心，不可恢复）
```

### 撤销"已经提交"的一次提交

```powershell
git revert <提交编号>
```

`<提交编号>` 用 `git log --oneline` 查。`git revert` 不会删历史，而是新增一条"反着做"的提交，安全、可追踪，推荐用它。

### 回到过去的某个版本看看

```powershell
git checkout <提交编号>
```

看完想回来：

```powershell
git switch dev
```

## 本项目推荐的工作流

```text
1. 改代码（随便改，不用急着提交）
2. 跑一下，确认这次改动有效果
3. git status / git diff 看看改了什么
4. git add 需要的文件（不想提交的跳过）
5. git commit -m "说明"
6. 攒够一个里程碑 → 合并到 main
```

常用的组合拳（提交所有改动，只适合"全部都要留档"时用）：

```powershell
git add -A
git commit -m "说明"
```

## 分支的完整玩法（进阶，用得上再看）

```powershell
git branch               # 列出所有分支，* 号是当前所在
git branch 新分支名       # 从当前位置新建分支
git switch -c 新分支名    # 新建并立刻切过去
git branch -d 旧分支名    # 删除已合并的分支
git merge --abort        # 合并冲突时放弃合并
```

**冲突**：两个分支改了同一处代码时，合并会冲突。git 会在文件里标出 `<<<<<<<` 和 `>>>>>>>` 两个版本，你手动选择保留哪个、删掉标记，然后 `git add` 该文件并 `git commit` 完成合并。

## 两个小提醒

- 每次提交前 `git status` 扫一眼，确认没有把 `logs/`、`__pycache__/` 之类的东西提交进去（这些已被 `.gitignore` 自动排除）。
- 提交说明写中文没问题，写得具体一点，一个月后看历史能想起来"当时干了啥"。

## 提交 vs 上传（push）——为什么不会"每次修改都上传"

这是本项目最重要的一条约定，单独说清楚：

- `git commit` **只在本地存档**，像游戏存档一样，不会把任何东西传到网上。
- 本仓库已配置远程仓库 `origin` → `https://github.com/zcr0701/red-dragon-calculator.git`（GitHub 私有仓库）。
- 在 GitHub 上建好远程仓库之后，**只有手动运行 `git push` 才会把本地提交推送上去**；不 push 就一直只存在本机。
- 因此：你大量修改、弥补公式的时候，文件改了就改了，**不想留档就不用提交**；改到某个程度想存一个档，再 `git add` + `git commit`。提交频率完全由你决定。

配合 Codex 使用的约定：**Codex 只在你明确要求提交（或完成一个里程碑）时才执行 `git add` / `git commit`**，平时的中间修改都留在工作区里不动，不会每次修改都提交或上传。

### 什么时候"上传"（push）

```powershell
git push              # 在 dev 上：把 dev 的本地提交推送到 GitHub
git switch main       # 想同步 main 时先切过去
git push              # 在 main 上：把 main 的本地提交推送到 GitHub
```

两个分支都设了上游跟踪（`-u`），所以直接在对应分支上 `git push` 就行，不用写完整分支名。

## 本仓库已配置的内容

- 两个分支：`main`（稳定里程碑）和 `dev`（日常工作），平时待在 `dev`。
- 两个命令别名（图省事）：

  ```powershell
  git st    # 等同 git status -s，紧凑显示改动
  git lg    # 等同 git log --graph --oneline --all，图形化历史
  ```

- `.gitignore` 已排除 `__pycache__/`、`logs/`、`*.log`、编辑器/系统文件，提交时不会误带这些。
- 已配置远程仓库（GitHub 私有仓库 `zcr0701/red-dragon-calculator`）：commit 是本地存档，`git push` 才上传；想公开可在 GitHub 网页上把仓库改为 Public。
