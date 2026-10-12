"""数据读写：合成算例导出为 CSV，以及从 CSV 载入真实业务数据。

字段结构与赛题“数据示例参考”对应：

- ``stores.csv``        配送网络数据（门店坐标、时间窗、服务时长）
- ``skus.csv``          订单与货物数据（长宽高、重量、易碎、可堆叠层数）
- ``order_lines.csv``   订单与货物数据（门店 × SKU × 数量）
- ``vehicles.csv``      车辆与运力数据（车厢内尺寸、额定载重、成本）

替换为真实数据时，只需保证表头一致，``cityload.cli benchmark --data <dir>``
即可直接跑同一套算法。
"""

from __future__ import annotations

import csv
import os
from typing import Dict, List

from .domain import Depot, Instance, OrderLine, SKU, Store, Vehicle, VehicleType

#: ``--data`` 目录下必须存在的四个文件（表头见 data/README.md）
REQUIRED_FILES = ("stores.csv", "skus.csv", "order_lines.csv", "vehicles.csv")


class DataFormatError(Exception):
    """数据目录缺文件或格式不对时抛出，供命令行给出人话提示而不是堆栈。"""


def _w(path: str, header: List[str], rows: List[List[object]]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def export_instance(inst: Instance, out_dir: str) -> Dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    files = {
        "stores": os.path.join(out_dir, "stores.csv"),
        "skus": os.path.join(out_dir, "skus.csv"),
        "order_lines": os.path.join(out_dir, "order_lines.csv"),
        "vehicles": os.path.join(out_dir, "vehicles.csv"),
    }
    _w(
        files["stores"],
        ["store_id", "name", "x_km", "y_km", "tw_start_min", "tw_end_min", "service_min"],
        [
            [s.store_id, s.name, s.x_km, s.y_km, s.tw_start_min, s.tw_end_min, s.service_min]
            for s in inst.stores
        ],
    )
    _w(
        files["skus"],
        [
            "sku_id", "name", "category", "length_mm", "width_mm", "height_mm",
            "weight_kg", "fragile", "max_stack_units",
        ],
        [
            [
                k.sku_id, k.name, k.category, k.length_mm, k.width_mm, k.height_mm,
                k.weight_kg, int(k.fragile), k.max_stack_units,
            ]
            for k in inst.skus.values()
        ],
    )
    _w(
        files["order_lines"],
        ["store_id", "sku_id", "qty"],
        [[l.store_id, l.sku_id, l.qty] for l in inst.order_lines],
    )
    seen: Dict[str, VehicleType] = {}
    for v in inst.vehicles:
        seen.setdefault(v.vtype.type_id, v.vtype)
    _w(
        files["vehicles"],
        [
            "type_id", "name", "inner_length_mm", "inner_width_mm", "inner_height_mm",
            "max_payload_kg", "fixed_cost", "cost_per_km", "count",
        ],
        [
            [
                t.type_id, t.name, t.inner_length_mm, t.inner_width_mm, t.inner_height_mm,
                t.max_payload_kg, t.fixed_cost, t.cost_per_km, t.count,
            ]
            for t in seen.values()
        ],
    )
    return files


def _rows(path: str) -> List[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def load_instance(data_dir: str, depot: Depot | None = None) -> Instance:
    """从 CSV 目录载入算例。

    缺文件时抛出 :class:`DataFormatError` 并给出可直接照做的提示，
    而不是把 ``FileNotFoundError`` 的堆栈甩给使用者。
    """

    if not os.path.isdir(data_dir):
        raise DataFormatError(
            f"数据目录不存在：{data_dir}\n"
            f"    请确认路径，或去掉 --data 参数改用内置合成算例。"
        )
    missing = [
        f for f in REQUIRED_FILES if not os.path.exists(os.path.join(data_dir, f))
    ]
    if missing:
        raise DataFormatError(
            f"数据目录 {data_dir} 缺少必需文件：{', '.join(missing)}\n"
            f"    该目录需要这四个 CSV：{', '.join(REQUIRED_FILES)}\n"
            f"    表头格式见 data/README.md；也可先跑 "
            f"`python -m cityload.cli data` 生成一份样例照着改。"
        )

    stores = [
        Store(
            store_id=r["store_id"],
            name=r["name"],
            x_km=float(r["x_km"]),
            y_km=float(r["y_km"]),
            tw_start_min=int(r["tw_start_min"]),
            tw_end_min=int(r["tw_end_min"]),
            service_min=int(r["service_min"]),
        )
        for r in _rows(os.path.join(data_dir, "stores.csv"))
    ]
    skus = {
        r["sku_id"]: SKU(
            sku_id=r["sku_id"],
            name=r["name"],
            category=r["category"],
            length_mm=int(r["length_mm"]),
            width_mm=int(r["width_mm"]),
            height_mm=int(r["height_mm"]),
            weight_kg=float(r["weight_kg"]),
            fragile=bool(int(r["fragile"])),
            max_stack_units=int(r["max_stack_units"]),
        )
        for r in _rows(os.path.join(data_dir, "skus.csv"))
    }
    lines = [
        OrderLine(store_id=r["store_id"], sku_id=r["sku_id"], qty=int(r["qty"]))
        for r in _rows(os.path.join(data_dir, "order_lines.csv"))
    ]
    vehicles: List[Vehicle] = []
    for r in _rows(os.path.join(data_dir, "vehicles.csv")):
        vt = VehicleType(
            type_id=r["type_id"],
            name=r["name"],
            inner_length_mm=int(r["inner_length_mm"]),
            inner_width_mm=int(r["inner_width_mm"]),
            inner_height_mm=int(r["inner_height_mm"]),
            max_payload_kg=float(r["max_payload_kg"]),
            fixed_cost=float(r["fixed_cost"]),
            cost_per_km=float(r["cost_per_km"]),
            count=int(r["count"]),
        )
        for k in range(vt.count):
            vehicles.append(Vehicle(vehicle_id=f"{vt.type_id}-{k + 1:02d}", vtype=vt))
    return Instance(
        depot=depot or Depot("DEPOT", "中心仓", 0.0, 0.0, 360),
        stores=stores,
        skus=skus,
        vehicles=vehicles,
        order_lines=lines,
    )
