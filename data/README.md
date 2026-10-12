# 数据目录

## 全部为模拟数据（重要）

本目录下的所有算例（门店、SKU、订单、车辆）均由 `cityload/data_synth.py`
按配置中的随机种子**程序化合成**，**不含传化集团或任何客户的真实门店、商品、
订单、价格与运力信息**。

合成数据的**字段结构**与赛题《作品提交与评审说明》中的“数据示例参考”一致，
可被真实业务数据直接替换。

## 文件说明

`data/synthetic/` 由 `python -m cityload.cli data` 生成：

| 文件 | 对应赛题数据类别 | 字段 |
|------|-----------------|------|
| `stores.csv` | 配送网络数据 | `store_id, name, x_km, y_km, tw_start_min, tw_end_min, service_min` |
| `skus.csv` | 订单与货物数据 | `sku_id, name, category, length_mm, width_mm, height_mm, weight_kg, fragile, max_stack_units` |
| `order_lines.csv` | 订单与货物数据 | `store_id, sku_id, qty` |
| `vehicles.csv` | 车辆与运力数据 | `type_id, name, inner_length_mm, inner_width_mm, inner_height_mm, max_payload_kg, fixed_cost, cost_per_km, count` |

装载与调度规则不在 CSV 中，而在：

- `configs/default.json` 的 `packing` 段（支撑率、单柱高度比、重货置底高度、排线容积折减等）
- `cityload/categories.py` 的品类规则表（功能区归属、可承压上限、品类不相容、重货置底）

## 用真实数据替换

1. 按上表准备四个 CSV，表头保持一致，编码 UTF-8；
2. 运行：

```bash
python -m cityload.cli all --data path/to/csv_dir
```

算法代码**一行都不用改**。载入实现见 `cityload/dataio.py`。

载入时会校验订单行引用的门店与 SKU 是否存在（缺失会直接报错，不会静默丢数据）。

## 关于人工设定的参数

车辆成本、车速、绕行系数、承压上限等**不是从数据中学出来的**，而是人工假设值。
完整清单与取值范围见 [`docs/04-数据说明.md`](../docs/04-数据说明.md) 第 4 节。
替换真实数据时，这些参数也应一并替换为业务口径。

## 为什么不把 `data/synthetic/` 提交进仓库

`.gitignore` 已忽略该目录 —— 它是**可复现的派生产物**，
一条命令即可重新生成，提交它只会让仓库变脏。
