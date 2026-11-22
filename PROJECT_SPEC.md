# 当前数据契约

扩展乘子等于代表公顷数 ÷ 整样地面积（公顷）；分地类代表面积再乘地类份额。断面积以 π 平方米为单位报告有理数系数。只对已访问支撑发布密度；已访零样木与不可达或未记录不是同一状态。样木 ID 在访问内唯一。

结构与字段示例以 `examples/baseline.json` 为准。`changed.json` 是可合法发布的另一个输入；`invalid.json` 是必须拒绝的反例。`replacement.json` 演示替访名录与采用判断。

独立核对值：North/pine：设计 11 ha，已访 5 ha，断面积密度 1/4（π m²/ha），株数密度 40；South 无可发布密度。

入口为 `plotledger/cli.py`，归约编排在 `plotledger/workflow.py`。有理数输出为整数或 `分子/分母` 字符串；输入的精确非整数应使用 JSON 字符串。不将 `null` 当作零。SQLite 每版保留规范化输入、成果及两者的 SHA-256；这是本地完整性核对，不是数字签名。

## 替访名录（可选）

输入可带 `replacement_roster`：调查员为不可达样地列出的候选点。键缺失时走旧口径，旧输入成果逐字节不变；键存在但为空列表必须拒绝。

- 每个候选点带 `id`、`district`（必须是已知设计域）、整数 `location` 坐标 `[x, y]`、`plot_m2`、`expansion_ha`、布尔 `public_direct`，以及按访问列出的 `visits`；每条访问注明 `id`、`cover`、可选 `fraction`（缺省 1，各 fraction 之和必须为 1）、`status` 与样木。候选访问 ID 在整个名录内唯一。
- 只有 `public_direct: true` 且状态为 `visited` 的候选访问可作为直接替访；非公开候选保留在名录中并在成果里标明 `not_public`，不参与分配。
- 一个候选点只能指派给一个原样地；同一原样地同一地类上累计替访代表面积不得超过其原代表面积（候选面积超限即对该目标不可行）。候选点与目标必须同设计域、同地类。
- 原样地中直接 `visited` 的结果（含已访零）一律不动；替访只补 `inaccessible`、`not_scheduled`、`not_recorded` 的缺口。已访零是真实的零证据，不视为无匹配。
- 成果的 `replacement_allocation` 给出：`eligible_targets`（各缺口需求面积）、按有效替访代表面积最大化选出的 `canonical`（含 `optimal_allocation_count` 与具体指派）、`nearest_greedy`（各缺口各自选最近点的对照，会损失部分可替访面积）、`lost_ha_vs_demand` 与 `unassigned_candidates`。
- 输入可选 `adoption_basal_pi_m2_per_ha`（断面积采用界线，有理数）。区域报告在各覆盖组的 `adoption` 中给出结论：所有等量最优分配都在界线同侧为 `stable_above`/`stable_below`；等量分配使结论跨线为 `pending_choice`，并附最小/最大密度与替访面积。无直接访问的组只按替访证据自身判断；没有任何证据的组不给结论。未提供界线时状态为 `no_adoption_line`。
