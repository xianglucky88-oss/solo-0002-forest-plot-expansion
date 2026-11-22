# 林分样地扩展数据集

以抽样设计为口径归约样地内分地类的访问记录。 使用合成资料。无需联网、账户或第三方 Python 依赖；已验证 Python 3.14。

```sh
python3 -m plotledger preview examples/baseline.json
python3 -m plotledger publish examples/baseline.json ledger.sqlite original
python3 -m plotledger publish examples/changed.json ledger.sqlite revised
python3 -m plotledger history ledger.sqlite
python3 -m plotledger show ledger.sqlite original
python3 -m plotledger source ledger.sqlite original
python3 -m plotledger replay ledger.sqlite revised
```

`preview` 不写库；`show` 读已发布成果，`source` 读随版封存的输入，`replay` 重算并核对。`publish` 不覆盖已用版名。命令在输入错误时以状态 2 退出，不发布结果。重复演示请换版名或用新库。

## 校准估计包

带有 `controls` 的输入走校准流程：审查方按新版底图的分区×地类给出控制面积，同时保留旧经营区（district）的可比边际。样地经 `zone_shares`（跨新分区的交叉份额，和为 1）与 `factor_bounds`（调查方给定的扩展倍率区间，下界必须为正、上界有限）参与校准——单个样地不许为凑控制数被无限放大。

```sh
python3 -m plotledger preview examples/calibrated.json
python3 -m plotledger publish examples/calibrated.json ledger.sqlite calibrated
python3 -m plotledger replay ledger.sqlite calibrated
```

诊断示例：`unsupported.json`（正控制数无访问支撑）、`incompatible.json`（倍率上界使控制不可达）、`margins-conflict.json`（两套边际各自可平衡但联立不可行）、`straddle.json`（密度区间跨过业务界线，不得用一套权重掩盖）。`invalid-calibration.json` 是必须拒绝的反例。控制不相容只是审查结论，不是输入错误：这类包正常产出并以状态 0 退出，`outcome` 字段给出判定。

样本字段及成果口径见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。这里的规则仅用于合成数据，并非真实业务规范。

## 测试

```sh
python3 -m unittest discover -s tests
```
