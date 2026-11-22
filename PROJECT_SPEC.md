# 当前数据契约

扩展乘子等于代表公顷数 ÷ 整样地面积（公顷）；分地类代表面积再乘地类份额。断面积以 π 平方米为单位报告有理数系数。只对已访问支撑发布密度；已访零样木与不可达或未记录不是同一状态。样木 ID 在访问内唯一。

结构与字段示例以 `examples/baseline.json` 为准。`changed.json` 是可合法发布的另一个输入；`invalid.json` 是必须拒绝的反例。

独立核对值：North/pine：设计 11 ha，已访 5 ha，断面积密度 1/4（π m²/ha），株数密度 40；South 无可发布密度。

入口为 `plotledger/cli.py`，归约编排在 `plotledger/workflow.py`。有理数输出为整数或 `分子/分母` 字符串；输入的精确非整数应使用 JSON 字符串。不将 `null` 当作零。SQLite 每版保留规范化输入、成果及两者的 SHA-256；这是本地完整性核对，不是数字签名。

## 管理边界上卷（`management`，可选）

同一原样地—地类事实可经 `memberships` 的直属份额（`fraction` ∈ (0,1]）归属多个子区，子区不保证互斥。`overlap_edges` 声明允许重叠的管理边界：

- `kind: "proven"` 必须在 `shared` 中按原样地—地类给出真实共用份额；共用份额须等于两侧直属份额之和减一（`a+b-1`），为零的重叠不得列入。大区面积用包含—扣除重算，重叠只按原份额计一次。
- `kind: "unproven"` 不得触碰两侧共同认领的事实；此时大区只发布仍有支撑的面积区间（连通分量上 `max(成员面积) ≤ 并集 ≤ 成员面积之和`，已访与未访分别给区间），密度与断面积一律 `null`，并在 `unproven_edges` 具名。
- 两个子区共同认领同一事实却无声明边界，或份额超出 (0,1]、证据与直属份额不一致，均以状态 2 拒绝。三个子区同时认领同一事实不被支持。

大区与子区的密度都从原样地代表面积与扩展计数重算，绝不对子区密度做平均。已访零样木计入 `visited_ha`（密度中以零参与），不可达/未记录/未安排只进 `unobserved_ha`。验证样例：`examples/rollup_half.json`（真实共用 1/2）与 `examples/rollup_full.json`（完全共用）中子区面积与密度相同而共用份额不同；`rollup_unproven.json` 演示只报区间；`rollup_invalid.json` 必须拒绝；独立核对脚本为 `verification/check_rollup.py`。
