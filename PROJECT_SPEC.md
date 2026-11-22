# 当前数据契约

扩展乘子等于代表公顷数 ÷ 整样地面积（公顷）；分地类代表面积再乘地类份额。断面积以 π 平方米为单位报告有理数系数。只对已访问支撑发布密度；已访零样木与不可达或未记录不是同一状态。样木 ID 在访问内唯一。

结构与字段示例以 `examples/baseline.json` 为准。`changed.json` 是可合法发布的另一个输入；`invalid.json` 是必须拒绝的反例。

独立核对值：North/pine：设计 11 ha，已访 5 ha，断面积密度 1/4（π m²/ha），株数密度 40；South 无可发布密度。

入口为 `plotledger/cli.py`，归约编排在 `plotledger/workflow.py`。有理数输出为整数或 `分子/分母` 字符串；输入的精确非整数应使用 JSON 字符串。不将 `null` 当作零。SQLite 每版保留规范化输入、成果及两者的 SHA-256；这是本地完整性核对，不是数字签名。

# 校准估计包

含 `controls` 的输入产出校准估计包（`product: calibration-package`），仍走 preview/publish/replay 同一归档；实现位于 `plotledger/calibrate.py`，精确有理数线性规划位于 `plotledger/exactlp.py`。

## 输入附加字段

- `plots[].zone_shares`：样地在新版底图分区上的交叉份额（精确数，和为 1）。
- `plots[].factor_bounds`：`[下界, 上界]`，扩展倍率 g 的可接受区间；下界必须为正、上界有限——不许为凑控制数把单个样地无限放大。
- `controls.zone_cover`：新分区×地类控制面积（公顷，非负精确数）。
- `controls.district_cover`：旧经营区×地类可比边际。
- `density_thresholds`：业务界线，形如 `{margin, zone|district, cover, statistic, threshold}`；`statistic` 取 `trees_per_ha` 或 `basal_pi_m2_per_ha`，界线必须为正且指向已存在的控制格。

只有已访问（visited）的样地×地类构成控制格支撑；有支撑的控制格必须出现在控制表中，否则输入被拒绝。控制数为零而有支撑的格子会使系统不相容（倍率下界为正，代表面积不可能压到零）。

## 可行性与分类

变量为每样地一个倍率 g_i ∈ [下界, 上界]，约束为控制等式；分别检验新分区边际、旧经营区边际与联立系统：

- `no_visit_support`：控制数为正但没有任何已访问支撑（不可达或未记录不是已访；设计面积照常统计）。同时给出 `supported_part_feasible`，说明剔除无支撑格后其余部分是否可行。
- `incompatible_controls`：有支撑但控制数在倍率区间内不可达。`feasible_without_upper_bounds` 区分"倍率区间太紧"（放开上界可行）与"控制本身互相矛盾"（放开仍不可行）。
- 两套边际各自可行而联立不可行时，`separately_feasible_jointly_infeasible` 为真。

不可行是审查结论而非输入错误：包正常产出，`outcome` 为 `infeasible`，`weights` 为 `null`，不发布密度。

## 发布内容

- `cells`：每个控制格的控制面积、设计面积、已访基准面积、倍率区间内可代表面积 `[min_representable_ha, max_representable_ha]` 及支撑明细（样地、访问、份额、倍率区间）。控制总量与访问统计一起发布。
- `weights`：联立可行时的一组见证倍率——在可行域内使 Σ|g_i − 1| 最小（离基准权重最近），由单纯形法确定性选取，可复算。
- `densities`：每个有支撑控制格 × 两种统计量的发布密度。`interval` 是全部合法权重上的精确值域（Charnes-Cooper 变换后的有理数 LP 求得）；`at_certificate` 只是见证权重下的取值。界线关系 `relation` 为 `above`/`below`/`on_line`/`straddles`/`touches`；区间跨过或触及界线时 `determinate` 为假——多组合法权重会把密度放到界线两侧，不允许用一套权重掩盖这种不确定性，只能连同区间一起发布。

独立核对值（`examples/calibrated.json`）：见证倍率 p1=1、p2=3/2、p3=1、p4=5/4、p5=1；Z2/pine 株数密度区间 [625/17, 800/17]；South/pine 株数密度区间 [425/14, 300/7]；North/pine 断面积密度 5/24（π m²/ha），在界线 1/4 之下。
