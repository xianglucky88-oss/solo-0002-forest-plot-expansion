# 当前数据契约

扩展乘子等于代表公顷数 ÷ 整样地面积（公顷）；分地类代表面积再乘地类份额。断面积以 π 平方米为单位报告有理数系数。只对已访问支撑发布密度；已访零样木与不可达或未记录不是同一状态。样木 ID 在访问内唯一。

结构与字段示例以 `examples/baseline.json` 为准。`changed.json` 是可合法发布的另一个输入；`invalid.json` 是必须拒绝的反例。

独立核对值：North/pine：设计 11 ha，已访 5 ha，断面积密度 1/4（π m²/ha），株数密度 40；South 无可发布密度。

入口为 `plotledger/cli.py`，归约编排在 `plotledger/workflow.py`。有理数输出为整数或 `分子/分母` 字符串；输入的精确非整数应使用 JSON 字符串。不将 `null` 当作零。SQLite 每版保留规范化输入、成果及两者的 SHA-256；这是本地完整性核对，不是数字签名。

## 删除组重复权重

输入可选带 `replications`（或 `replicate_designs`，两者不可同用），列出若干套重复设计。每套设计有唯一 `id`、若干 `replicates`（每组唯一 `id`、正的 `coefficient`、覆盖全部样地的 `weights`）以及若干 `contrasts`（`district` 与两个不同的 `covers`）。

- 每个重复组是一份完整的采样设计编号：`weights` 逐样地给出扩展公顷数。
- 分子分母都在该组自己的权重下重算：代表已访面积、扩展株数与断面积系数各自按访问、入样与面积事实重建，再相除。不使用全样本比值的缩放近似。
- 对比只在同一个重复组内求差：`差异 = 地类甲的组内密度 − 地类乙的组内密度`。只支持其中一地类的组记为未配对（`only_first_cover` / `only_second_cover` / `neither_cover`），绝不与只支持另一地类的组拼配。
- 组内变异按声明的 `coefficient` 原样加权：`variance = Σ coefficient × (组内值 − 全样本值)²`；标准误以精确平方根报告（完全平方给有理数，否则给小数文本）。
- 分区估计（`domains`）与该区的地类对比（`contrasts`）分别判定：
  - `publishable`：全样本分子分母有支撑，且该区每个声明的重复组都有支撑。分区估计另需至少两个可用组；对比另需全部声明组都同时支持两地类。
  - `publishable_partial_replicates`：全样本可估，但只有部分组有支撑；报告可用/不可用组及可用系数和，不静默重标化。
  - `insufficient_replicates` / `insufficient_common_replicates`：可用组不足两个，或共同支持两地类的组不足两个。边际估计仍可发布，对比不可发布。
  - `incomplete_common_replicates`：有至少两个共同组，但有声明组未同时支持两地类；对比不可发布。
  - `unsupported` / `unsupported_full_sample`：全样本缺少已访支撑。
- 每套设计的 `release` 字段把可发布的分区估计、仅部分重复支撑的分区、无不确定性的分区，以及可发布与扣留的对比分开列出。

验收示例 `examples/replication.json` 含三套设计：`concordant` 与 `discordant` 的逐样地边际重复权重完全相同，仅组内联动相反，故各区边际方差相同而配对对比方差不同；`split-support` 让每个地类都有边际统计但两地类没有共同重复组，分区估计可发布、对比扣留。
