"""合成数据生成器。

**重要声明**：本模块生成的全部数据均为程序合成的模拟数据，
不含传化或任何客户的真实订单、门店、商品与运力信息。
生成过程完全由 seed 决定，同 seed 必得同一组数据，便于评审复现。

真实业务数据的字段结构（订单与货物、车辆与运力、配送网络、装载与调度规则）
与赛题说明中的“数据示例参考”一一对应，可通过 ``cityload.dataio`` 用 CSV 替换。
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Sequence, Tuple

from .domain import (
    Depot,
    Instance,
    OrderLine,
    SKU,
    Store,
    Vehicle,
    VehicleType,
)

CATEGORIES: Sequence[Tuple[str, str, Tuple[int, int], Tuple[int, int], Tuple[int, int], Tuple[float, float]]] = (
    # (类别, 名称前缀, 长范围, 宽范围, 高范围, 重量范围)
    ("口服药品", "口服固体制剂", (260, 360), (180, 260), (180, 260), (1.4, 3.6)),
    ("外用药品", "外用搽剂", (240, 340), (160, 240), (220, 320), (1.7, 4.0)),
    ("保健品", "保健食品", (280, 400), (200, 280), (200, 300), (1.2, 3.2)),
    ("医疗器械", "家用医疗器械", (320, 460), (240, 340), (160, 280), (1.0, 2.9)),
    ("日化耗材", "医用日化耗材", (300, 420), (220, 300), (240, 360), (0.8, 2.2)),
    ("冷链药品", "冷链生物制品", (240, 320), (180, 240), (200, 300), (1.9, 4.4)),
)

# 禁压品类：冷链生物制品与精密医疗器械（与 categories.py 的 max_load_above_kg=0 一致）
FRAGILE_CATEGORIES = {"冷链药品", "医疗器械"}


def build_sku_catalog(rng: random.Random, size: int = 48) -> Dict[str, SKU]:
    skus: Dict[str, SKU] = {}
    for i in range(1, size + 1):
        cat, prefix, lr, wr, hr, kgr = CATEGORIES[(i - 1) % len(CATEGORIES)]
        fragile = cat in FRAGILE_CATEGORIES
        # 易碎件基本不能叠压，普通件可码 4~8 箱一柱
        max_stack = 2 if fragile else rng.choice([4, 5, 5, 6, 6, 7, 8])
        sku = SKU(
            sku_id=f"S{i:04d}",
            name=f"{prefix}-{i:04d}",
            category=cat,
            length_mm=rng.randrange(lr[0], lr[1] + 1, 10),
            width_mm=rng.randrange(wr[0], wr[1] + 1, 10),
            height_mm=rng.randrange(hr[0], hr[1] + 1, 10),
            weight_kg=round(rng.uniform(*kgr), 2),
            fragile=fragile,
            max_stack_units=max_stack,
        )
        skus[sku.sku_id] = sku
    return skus


def build_stores(rng: random.Random, count: int, area_km: float) -> List[Store]:
    # 时间窗集中在 08:00–17:00，且都在单车单日 10 小时在线时长之内可覆盖
    windows = [(480, 660), (510, 690), (540, 720), (600, 780), (780, 960), (840, 1020)]
    stores: List[Store] = []
    half = area_km / 2.0
    for i in range(1, count + 1):
        # 均匀撒点，并保证与中心仓保持 1.5km 以上距离
        for _ in range(50):
            x = rng.uniform(-half, half)
            y = rng.uniform(-half, half)
            if math.hypot(x, y) >= 1.5:
                break
        start, end = rng.choice(windows)
        stores.append(
            Store(
                store_id=f"ST{i:03d}",
                name=f"连锁门店{i:03d}",
                x_km=round(x, 3),
                y_km=round(y, 3),
                tw_start_min=start,
                tw_end_min=end,
                service_min=10,
            )
        )
    return stores


def build_vehicles(fleet_cfg: Sequence[dict]) -> List[Vehicle]:
    vehicles: List[Vehicle] = []
    for cfg in fleet_cfg:
        vtype = VehicleType(
            type_id=cfg["type_id"],
            name=cfg["name"],
            inner_length_mm=int(cfg["inner_length_mm"]),
            inner_width_mm=int(cfg["inner_width_mm"]),
            inner_height_mm=int(cfg["inner_height_mm"]),
            max_payload_kg=float(cfg["max_payload_kg"]),
            fixed_cost=float(cfg["fixed_cost"]),
            cost_per_km=float(cfg["cost_per_km"]),
            count=int(cfg["count"]),
        )
        for k in range(vtype.count):
            vehicles.append(Vehicle(vehicle_id=f"{vtype.type_id}-{k + 1:02d}", vtype=vtype))
    return vehicles


def build_instance(config: dict) -> Instance:
    """按配置生成一个完整算例。"""

    seed = int(config.get("seed", 20260214))
    rng = random.Random(seed)

    net_cfg = config.get("network", {})
    dem = config.get("demand", {})

    depot = Depot(
        depot_id="DEPOT",
        name="中心仓",
        x_km=0.0,
        y_km=0.0,
        open_min=int(net_cfg.get("depot_open_min", 360)),
    )
    skus = build_sku_catalog(rng)
    stores = build_stores(rng, int(dem.get("stores", 30)), float(dem.get("area_km", 22.0)))
    vehicles = build_vehicles(config.get("fleet", []))

    sku_ids = sorted(skus.keys())
    lines: List[OrderLine] = []
    for store in stores:
        n_lines = rng.randint(
            int(dem.get("lines_per_store_min", 8)), int(dem.get("lines_per_store_max", 14))
        )
        picked = rng.sample(sku_ids, n_lines)
        for sku_id in picked:
            qty = rng.randint(int(dem.get("qty_min", 1)), int(dem.get("qty_max", 6)))
            lines.append(OrderLine(store_id=store.store_id, sku_id=sku_id, qty=qty))

    return Instance(
        depot=depot,
        stores=stores,
        skus=skus,
        vehicles=vehicles,
        order_lines=lines,
    )


def describe(inst: Instance) -> Dict[str, float]:
    """算例规模摘要，用于报告与日志。"""

    max_vol = max(v.volume_m3 for v in inst.vehicles)
    max_pay = max(v.max_payload_kg for v in inst.vehicles)
    vol_lb = math.ceil(inst.total_demand_volume_m3() / max_vol) if max_vol else 0
    pay_lb = math.ceil(inst.total_demand_weight_kg() / max_pay) if max_pay else 0
    return {
        "stores": len(inst.stores),
        "skus": len(inst.skus),
        "order_lines": len(inst.order_lines),
        "demand_units": inst.total_demand_units(),
        "demand_volume_m3": round(inst.total_demand_volume_m3(), 3),
        "demand_weight_kg": round(inst.total_demand_weight_kg(), 2),
        "cargo_density_kg_per_m3": round(
            inst.total_demand_weight_kg() / max(1e-9, inst.total_demand_volume_m3()), 1
        ),
        "fleet_vehicles": len(inst.vehicles),
        "fleet_volume_m3": round(inst.fleet_volume_m3(), 3),
        "fleet_payload_kg": round(inst.fleet_payload_kg(), 2),
        "vehicles_lower_bound": max(vol_lb, pay_lb),
        "vehicles_lb_by_volume": vol_lb,
        "vehicles_lb_by_payload": pay_lb,
    }
