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

## 跨版复核队列（`revisit`）

两个封存版次加一份**独立的**候选表即可生成下一轮待访队列；候选表不是样地包，候选不等于确认：

```sh
python3 -m plotledger revisit ledger.sqlite original revised \
    examples/identity_candidates.json \
    --field out/field_packet.json --stats out/stats_packet.json
```

- 同版次同访问、同样地地类、同编号的访问/样木为**已证明同一身份**，只在统计包的 `deduped_without_redispatch` 计数，不重复派单。
- 候选连接按双向图做一对一配对枚举：一个新记录同时可解释两株旧树时，任何合法配对至多接受一条，`blocking_disputes` 给出被拒绝的双重复查主张与全部合法配对。
- 输出两份互不混口径的材料：领队包只含样地、访问、样木编号与直径（写入时递归校验，任何权重/面积键泄漏即报错）；统计包含代表面积、优先级理由、阻断争议与可发布性判断，并用共同的 `task_id`/访问编号回查领队包。
- 队列按新版代表面积降序；同一争议格无论采用哪条合法配对都只产生一次访问，故合法配对之间不改变队列优先级（依据写入每个争议的 `priority_effect`）。
- 未访问地类（`inaccessible`/`not_scheduled`/`not_recorded`）作为覆盖缺口入队；已访零样木不视为缺口。候选表只叠加少量假设，不改变 `preview`/`publish` 的任何既有成果。

样本字段及成果口径见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。这里的规则仅用于合成数据，并非真实业务规范。
