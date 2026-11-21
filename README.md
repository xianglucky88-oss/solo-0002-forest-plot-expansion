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

样本字段及成果口径见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。这里的规则仅用于合成数据，并非真实业务规范。
