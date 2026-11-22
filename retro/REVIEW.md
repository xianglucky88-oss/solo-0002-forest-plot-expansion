# ridge-demo 同一轮复盘：发布影响清单与归因说明

本目录只做**复盘**：不修改 `plotledger/` 下任何归约器代码，不新增、不虚构访问，
不把「不可达 / 未安排 / 未记录」当成实访。所有输入改动均为既有记录的字段更正。

## 0. 复现方式（可重放）

```sh
# 1) 原入口：仓库两份合法样例与反例（在仓库根目录执行）
python3 -m plotledger preview examples/baseline.json
python3 -m plotledger publish examples/baseline.json /tmp/demo.sqlite original
python3 -m plotledger publish examples/changed.json  /tmp/demo.sqlite revised
python3 -m plotledger replay /tmp/demo.sqlite original
python3 -m plotledger replay /tmp/demo.sqlite revised
python3 -m plotledger preview examples/invalid.json   # 状态 2：fractions must add to one: p1

# 2) 复盘产物（脚本与数据分目录；驱动脚本以 -I 运行）
rm -f retro/ledger/retro.sqlite
python3 -I retro/tools/build_bundles.py    # 生成 7 个更正组合并预览，拒绝信息来自冻结入口
python3 -I retro/tools/run_replay.py       # 合法组合发布入库，拒绝组合只 preview 不入库
python3 -I retro/tools/analyze.py          # Fraction 精确差额分析，从库内成果读取
```

产物：

| 路径 | 内容 |
| --- | --- |
| `retro/input/baseline.json` | 与 `examples/baseline.json` 逐字节相同的复盘起点 |
| `retro/bundles/{D,F,S,DF,DS,FS,DFS}.json` | 三个原子更正及其全部子集组合 |
| `retro/bundles/_preview_matrix.json` | 七个组合经 CLI 预览的退出码与拒绝原文 |
| `retro/bundles/run_log.json` | 发布/拒绝流水（含每版哈希与 replay 状态） |
| `retro/bundles/hashes.json` | 版名、source/result SHA-256、history |
| `retro/bundles/impact_matrix.json` | 对基线差额、边际差额、v3 事实路径 |
| `retro/bundles/invariant_check.json` | 数值不变 / 实访引用变化的逐项证明 |
| `retro/ledger/retro.sqlite` | 六个不可变版（拒绝组合从未写入） |

`run_replay.py` 拒绝覆盖已存在的 ledger；重复演示先删除该文件即可。

## 1. 三个原子更正（职责边界清晰）

| 代号 | 责任组 | 内容（仅改既有记录，无新增访问/样木） |
| --- | --- | --- |
| **D** | 设计组（权重与地类份额） | p2 pine 份额 `1 → 1/2`，新增 mixed 段 p2b 份额 `1/2`；p2 权重 `2 → 4` ha（新 mixed 半区仍代表 2 ha，乘子 5→10）；p3 权重 `6 → 4` ha 抵消 +2 ha，使 North 设计总面积不变（p3 不可达，不进入任何密度） |
| **F** | 外业组（访问记录） | 把**既有零树实访 v3** 从 (p2, pine) 迁到 (p2, mixed)；ID、`visited` 状态、空树清单不变，不新增/删除任何访问 |
| **S** | 设计组（可独立套用） | South 的 p4 份额 pine `1/2 → 3/8`、mixed `1/2 → 5/8`；不触碰任何访问 |

访问 ID 集合在全部七个组合中恒为 `{v1,v2,v3,v4,v5}`，样木 ID 集合也与基线一致
（脚本已断言）。v4 仍为 `inaccessible`、v5 仍为 `not_scheduled`，没有被提升为实访。

## 2. 允许的发布组合与拒绝原因

| 组合 | 预览/发布结果 | 入库版名 | 拒绝原因（CLI 原文，退出码 2） |
| --- | --- | --- | --- |
| D | 接受 | `E1-D` | — |
| S | 接受 | `E2-S` | — |
| DF | 接受 | `E3-DF` | — |
| DS | 接受 | `E4-DS` | — |
| DFS | 接受 | `E5-DFS` | — |
| **F** | **拒绝** | 不入库 | `unknown or duplicate plot-cover visit: v3` |
| **FS** | **拒绝** | 不入库 | `unknown or duplicate plot-cover visit: v3` |

拒绝机制（`plotledger/census.py:13`）：访问引用的 `(plot, cover)` 必须在设计份额中
具有**严格正**份额。F 把 v3 指向 p2/mixed，而该份额只由 D 创建；S 作用于 South 的 p4，
无法为 North 的 p2 提供份额，因此 FS 与 F 同样被拒。这满足「至少一边单独套用会被现有
校验拒绝」：F 单独不可发布，且任何不含 D 的 F 组合都不可发布。

顺序事实（合法性，而非数值）：只有 **D 先于或同时于 F** 才有合法中间态；F 先行在第一步
即被拒，不存在「F 已发布、D 待补」的版。最终 DFS 文档与补丁应用顺序无关（原子补丁互不
覆盖同一标量字段）。

## 3. 差额归因（三类，依据 `impact_matrix.json`）

### 3.1 可单独归因（存在独立合法对照，差额在任何上下文中恒定）

**S（E0→E2，且 E1→E4、E3→E5 复算结果完全相同）**，只影响 South：

- South/pine：design_ha `4 → 3`，unobserved_ha `4 → 3`（各 −1）
- South/mixed：design_ha `4 → 5`，unobserved_ha `4 → 5`（各 +1）
- 密度字段全程为 `null`、visits 全程为 `[]`——S 不产生也不修复任何密度。

**D 的设计面积效应（E0→E1）**，只改面积、不改任何密度（无实访在新份额上落地）：

- North/pine：design_ha `11 → 9`、unobserved_ha `6 → 4`（各 −2）
- North/mixed：design_ha `1 → 3`、unobserved_ha `0 → 2`（各 +2）
- 该效应在 E4-DS、E3-DF、E5-DFS 中逐字段复现，归属 D 无歧义。

### 3.2 随应用顺序变化（条件边际；反向顺序没有合法中间态）

**F 的边际效应只在 D 之后可测量**（E1→E3 与 E4→E5，数值完全相同）：

- North/mixed：visited_ha `1 → 3`、unobserved_ha `2 → 0`；株数密度 `100 → 100/3`、
  断面积密度 `9/4 → 3/4`、30 cm 径阶密度 `100 → 100/3`；visits `[v2] → [v2,v3]`
- North/pine：visited_ha `5 → 3`、unobserved_ha `4 → 6`；株数密度 `40 → 200/3`、
  断面积密度 `1/4 → 5/12`，径阶 `{10:20,20:20} → {10:100/3,20:100/3}`；
  visits `[v1,v3] → [v1]`

反向顺序（F 先行）在输入校验阶段即被拒，因此「F 相对基线的边际」**没有可发布的
中间版可测**。一旦 D 已就位，F 的边际数值与上下文无关（E1→E3 = E4→E5）。
已发布的最终成果数值不随顺序变化（E5-DFS 只有一个确定哈希）；顺序只决定中间态是否合法。

### 3.3 只能共同归因（无法分解到单一补丁的部分）

- **v3 实访引用的落地本身**：v3 出现在 North/mixed 的 `visits` 中，以「D 创建 p2/mixed
  正份额」与「F 迁移 v3」同时成立为前提。单独的 D 只产生 2 ha 未观测面积，单独的 F
  不可发布；该实访支撑是两者共同产物，不能记在任一组名下。
- **F 的独立数值贡献**：不存在合法的 F-only 版，F 对基线的差额在本系统内不可单独识别
  （见第 5 节）。

## 4. 数值不变但实访引用变化的成果（必须重签）

`invariant_check.json` 与 `impact_matrix.json` 的 `v3_numeric_invariant` 证明，实访
**v3 这条 plot-cover 事实**在 E0 与 E3-DF 之间四个数值字段**完全相同**：

| 字段 | E0-baseline | E3-DF | 数值相同 |
| --- | --- | --- | --- |
| 引用 | (p2, **pine**) | (p2, **mixed**) | 否（引用变化） |
| represented_ha | 2 | 2 | 是 |
| expanded_trees | 0 | 0 | 是 |
| expanded_basal_pi_m2 | 0 | 0 | 是 |
| expanded_by_10cm_class | `{}` | `{}` | 是 |

数值一个不变，但该成果所用实访的 (plot, cover) 引用已经改变。本轮签署同时确认**数值与
所用实访**，因此 v3 事实（以及汇总它的 North/mixed、North/pine 报告行）**必须重签**，
不能因数值相同而沿用旧签。

说明（如实记录约束）：在「所有份额严格正、不发明访问」的前提下，**报告行级**的全字段
不变量不存在——把第二条实访 v3 并入 North/mixed 必然把该报告密度按面积稀释（100→100/3），
这是归约器 `summarize()` 的确定性行为。可严格保持的不变量在实访事实级（上表），报告级
能保持不变的只有 E1-D 阶段的密度字段（v3 尚未迁入时）。

## 5. 哪些回放足以支撑结论，哪些贡献仍无法单独识别

足以支撑结论的回放：

1. 七个组合的 CLI 预览矩阵 → 直接给出接受/拒绝与拒绝原文，证明 F 对 D 的前置依赖；
2. 六个不可变版的 `publish` + `replay` 与 SHA-256（`hashes.json`）→ 证明成果可重放、
   封存输入与成果完整；E0 与基线发布哈希逐位相同；
3. E0/E1/E3 三点 → 分离 D 的面积效应与 F 的迁移效应（D 可独立测，F 仅在 D 后可测）；
4. E2/E4/E5 与 E1/E3 的交叉对照 → 证明 S 完全独立、边际与上下文无关；
5. v3 事实跨版路径与不变量断言 → 支撑第 4 节重签结论；
6. South 两版 null 密度断言（`analyze.py` 内置 assert）→ 支撑第 6 节不补零结论。

仍无法单独识别的贡献：

- **F 相对基线的独立差额**：F-only 是被校验拒绝的输入，系统内不存在可发布对照，
  任何回放都无法给出「F 单独造成多少变化」。可观测的只有 D 就位后的条件边际
  （3.2 节），以及 D+F 的联合落地（3.3 节）。按复盘纪律，不为补齐对照而虚构 p2/mixed
  的实访，也不把 v4/v5 等非实访状态改写成实访。

## 6. 报表负责人核对项：「只重签 North/pine 就够」不成立

- **North/pine**：visits `[v1,v3] → [v1]`，密度、径阶、面积全部变化 → 必须重签；
- **North/mixed**：visits `[v2] → [v2,v3]`，密度被稀释、面积变化 → 必须重签；
- **v3 实访事实**：数值不变但 (plot, cover) 引用变化（第 4 节）→ 必须重签；
- **South/pine、South/mixed（若采纳 S）**：密度仍为 `null`，但 design_ha/unobserved_ha
  变化，成果哈希随之变化 → 随发布范围重签；不采纳 S 则 South 完全不动。

**未获实访支持的成果不能补零发布**：South 两个报告在全部六个版中
`trees_per_visited_ha`、`basal_pi_m2_per_visited_ha` 恒为 `null`、visits 恒为 `[]`。
零值只允许来自「已访问且零样木」的真实支撑（v3 即此类，其扩展量合法为 0）；
`inaccessible`（v4）、`not_scheduled`（v5）、`not_recorded`（归约器为缺失组合合成的
状态）与「已访零树」不是同一状态，归约器对它们输出 `null`，复盘全程未改写。
