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

## 复核身份归约

含可选 `reviews` 的输入在常规扩展成果之外追加 `review_reconciliations`，把现场复核的干号拆分/合并与已签认换号关系兑现到株数与断面积两个口径：

```sh
python3 -m plotledger preview examples/review_merge.json
python3 -m plotledger preview examples/review_split.json
python3 -m plotledger preview examples/review_ambiguous.json
python3 -m plotledger preview examples/review_invalid.json   # 状态 2：拒绝
python3 -m unittest tests.test_review_reconciliation -v
```

身份只来自显式语句（`duplicate`/`rename`/`split`/`merge`/`new_tree`）与歧义选项，不按行号或直径近似匹配。重复读数不再次扩展；确定同株但第二干未量（`dbh_cm: null`）时株数仍发布、断面积标记不齐全；两种相容身份解释下不变的部分进入稳定贡献，争议部分按选项列明并要求回现场。字段口径见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。

样本字段及成果口径见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。这里的规则仅用于合成数据，并非真实业务规范。
