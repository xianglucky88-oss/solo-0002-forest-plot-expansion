# 当前数据契约

扩展乘子等于代表公顷数 ÷ 整样地面积（公顷）；分地类代表面积再乘地类份额。断面积以 π 平方米为单位报告有理数系数。只对已访问支撑发布密度；已访零样木与不可达或未记录不是同一状态。样木 ID 在访问内唯一。

结构与字段示例以 `examples/baseline.json` 为准。`changed.json` 是可合法发布的另一个输入；`invalid.json` 是必须拒绝的反例。`nested.json` 是同心小圆/大圆的嵌套设计输入。

独立核对值：North/pine：设计 11 ha，已访 5 ha，断面积密度 1/4（π m²/ha），株数密度 40；South 无可发布密度。

入口为 `plotledger/cli.py`，归约编排在 `plotledger/workflow.py`。有理数输出为整数或 `分子/分母` 字符串；输入的精确非整数应使用 JSON 字符串。不将 `null` 当作零。SQLite 每版保留规范化输入、成果及两者的 SHA-256；这是本地完整性核对，不是数字签名。

## 嵌套小圆/大圆

嵌套访问把 `nested.design` 与 `nested.readings` 放在访问记录内，不与 `trees` 混用。`design.threshold_cm` 是径级分界；`small`/`large` 各给 `plot_m2` 与 `radius_cm`，面积须与半径平方成正比。记录 ID 在访问内唯一，`tree` 是树的身份，可在多条记录中重复。

- `circle`：记录所在圆（`small` 或 `large`）。
- `distance_cm`：距中心的范围 `[最小, 最大]`，最小不得超出该圆半径。
- `remeasure`：`none`（初测）、`accepted`（采信的复测）、`rejected`（不采信）、`unresolved`（未决）。

每棵物理树只按一个合法设计扩展一次：

1. 有采信复测时只用采信复测；否则只用初测。不采信和未决复测不单独成为观察。
2. 最终径级决定设计：直径 ≥ `threshold_cm` 入大圆设计，否则小圆设计。小圆内的树必然也在大圆内，因此最终径级可把小圆初测的树移到大圆；反向移动可能落到小圆之外而被排除。
3. 距离范围完全落在所选圆内才入样；完全落在圆外则排除；跨越边界则证明存在但无法确认入样，记为 `unconfirmed`，不计入确定观察或密度。
4. 只有记录而没有可用测量的树同样记为 `unconfirmed`。

嵌套访问的扩展乘子按所选圆的面积计算（同一代表公顷数 ÷ 该圆面积），因此小圆中的小径木乘子大于大圆中的大径木；访问级代表公顷数仍按分地类份额计。成果按树给出 `design`、`dbh_cm`、`distance_cm`、`expansion_unit`、`adopted_records`；访问级 `expanded_trees`、`expanded_basal_pi_m2`、`expanded_by_10cm_class` 只汇总入样的树。区级报告新增 `unconfirmed_trees` 清单。旧单圈（无 `nested`）输入的成果与既有版本保持逐字节一致。
