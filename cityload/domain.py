"""领域模型。

术语与真实城市配送业务一一对应：

- ``SKU``      最小存货单元，一个整箱货物（重量、长宽高、是否易碎、可堆叠层数）
- ``Store``    终端门店（连锁药店门店），带去货时间窗
- ``Depot``    中心仓
- ``Vehicle``  车辆（车厢内尺寸、额定载重、固定成本与里程成本）
- ``Block``    装载单元：同一门店同一 SKU 的整箱码垛（若干箱竖直叠成一柱）
- ``Placement`` 一个 Block 在车厢中的具体落位（车厢坐标系，单位 mm）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

MM3_PER_M3 = 1_000_000_000


@dataclass(frozen=True)
class SKU:
    """最小存货单元（整箱）。"""

    sku_id: str
    name: str
    category: str
    length_mm: int
    width_mm: int
    height_mm: int
    weight_kg: float
    fragile: bool
    max_stack_units: int  # 同一 SKU 单柱最多码放箱数（按货物耐压能力给定）

    @property
    def volume_m3(self) -> float:
        return self.length_mm * self.width_mm * self.height_mm / MM3_PER_M3

    @property
    def base_area_mm2(self) -> int:
        return self.length_mm * self.width_mm


@dataclass(frozen=True)
class Store:
    """终端门店。时间窗与行驶时间同单位：分钟（自 00:00 起算）。"""

    store_id: str
    name: str
    x_km: float
    y_km: float
    tw_start_min: int
    tw_end_min: int
    service_min: int

    @property
    def tw_width_min(self) -> int:
        return self.tw_end_min - self.tw_start_min


@dataclass(frozen=True)
class Depot:
    """中心仓。"""

    depot_id: str
    name: str
    x_km: float
    y_km: float
    open_min: int


@dataclass(frozen=True)
class VehicleType:
    """车辆类型（同一类型可有多台）。"""

    type_id: str
    name: str
    inner_length_mm: int
    inner_width_mm: int
    inner_height_mm: int
    max_payload_kg: float
    fixed_cost: float
    cost_per_km: float
    count: int

    @property
    def volume_m3(self) -> float:
        return (
            self.inner_length_mm * self.inner_width_mm * self.inner_height_mm / MM3_PER_M3
        )


@dataclass(frozen=True)
class Vehicle:
    """一台具体车辆。"""

    vehicle_id: str
    vtype: VehicleType

    @property
    def inner_length_mm(self) -> int:
        return self.vtype.inner_length_mm

    @property
    def inner_width_mm(self) -> int:
        return self.vtype.inner_width_mm

    @property
    def inner_height_mm(self) -> int:
        return self.vtype.inner_height_mm

    @property
    def max_payload_kg(self) -> float:
        return self.vtype.max_payload_kg

    @property
    def volume_m3(self) -> float:
        return self.vtype.volume_m3


@dataclass(frozen=True)
class OrderLine:
    """门店订单行。"""

    store_id: str
    sku_id: str
    qty: int


@dataclass
class Instance:
    """一次配送任务的全部输入。"""

    depot: Depot
    stores: List[Store]
    skus: Dict[str, SKU]
    vehicles: List[Vehicle]
    order_lines: List[OrderLine]

    def store(self, store_id: str) -> Store:
        for s in self.stores:
            if s.store_id == store_id:
                return s
        raise KeyError(store_id)

    def demand_by_store(self) -> Dict[str, List[OrderLine]]:
        out: Dict[str, List[OrderLine]] = {s.store_id: [] for s in self.stores}
        for line in self.order_lines:
            out[line.store_id].append(line)
        return out

    def total_demand_units(self) -> int:
        return sum(line.qty for line in self.order_lines)

    def total_demand_volume_m3(self) -> float:
        return sum(self.skus[l.sku_id].volume_m3 * l.qty for l in self.order_lines)

    def total_demand_weight_kg(self) -> float:
        return sum(self.skus[l.sku_id].weight_kg * l.qty for l in self.order_lines)

    def fleet_volume_m3(self) -> float:
        return sum(v.volume_m3 for v in self.vehicles)

    def fleet_payload_kg(self) -> float:
        return sum(v.max_payload_kg for v in self.vehicles)


@dataclass(frozen=True)
class Block:
    """装载单元：同一门店同一 SKU 的整箱码垛成一柱。

    除了几何与重量，Block 还携带**品类装载规则**（功能区、可承压上限、是否重货置底），
    这些是配载器做“不同种类合理摆放”的依据。
    """

    block_id: str
    store_id: str
    sku_id: str
    category: str
    units: int
    length_mm: int
    width_mm: int
    height_mm: int
    weight_kg: float
    fragile: bool
    max_stack_units: int
    functional_zone: str = "AMBIENT"
    max_load_above_kg: float = 45.0
    heavy_bottom: bool = False

    @property
    def volume_m3(self) -> float:
        return self.length_mm * self.width_mm * self.height_mm / MM3_PER_M3

    @property
    def base_area_mm2(self) -> int:
        return self.length_mm * self.width_mm

    @property
    def density_kg_per_m3(self) -> float:
        vol = self.volume_m3
        return self.weight_kg / vol if vol > 0 else 0.0


@dataclass
class Placement:
    """一个 Block 在车厢中的落位（车厢左后下角为原点；x 沿车长指向尾门）。"""

    block: Block
    x_mm: int
    y_mm: int
    z_mm: int
    length_mm: int
    width_mm: int
    height_mm: int

    @property
    def x1_mm(self) -> int:
        return self.x_mm + self.length_mm

    @property
    def y1_mm(self) -> int:
        return self.y_mm + self.width_mm

    @property
    def z1_mm(self) -> int:
        return self.z_mm + self.height_mm

    @property
    def store_id(self) -> str:
        return self.block.store_id

    @property
    def sku_id(self) -> str:
        return self.block.sku_id

    def as_dict(self) -> Dict[str, object]:
        return {
            "block_id": self.block.block_id,
            "store_id": self.store_id,
            "sku_id": self.sku_id,
            "units": self.block.units,
            "x_mm": self.x_mm,
            "y_mm": self.y_mm,
            "z_mm": self.z_mm,
            "length_mm": self.length_mm,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "weight_kg": round(self.block.weight_kg, 3),
        }


@dataclass
class VehicleLoad:
    """一台车的完整配载结果。"""

    vehicle: Vehicle
    route: List[str]  # 门店交付顺序（不含中心仓）
    placements: List[Placement] = field(default_factory=list)
    unplaced: List[Block] = field(default_factory=list)
    #: 空间分配等级：0=该方案不做空间分配（不适用），1=严格功能区，
    #: 2=功能区仅作偏好，3=降级为未做空间分配
    allocation_tier: int = 1

    @property
    def used_volume_m3(self) -> float:
        return sum(p.block.volume_m3 for p in self.placements)

    @property
    def used_weight_kg(self) -> float:
        return sum(p.block.weight_kg for p in self.placements)

    @property
    def volume_utilization(self) -> float:
        cap = self.vehicle.volume_m3
        return self.used_volume_m3 / cap if cap > 0 else 0.0

    @property
    def payload_utilization(self) -> float:
        cap = self.vehicle.max_payload_kg
        return self.used_weight_kg / cap if cap > 0 else 0.0

    @property
    def max_stack_height_mm(self) -> int:
        return max((p.z1_mm for p in self.placements), default=0)

    @property
    def is_feasible(self) -> bool:
        """体积、载重、车厢尺寸、货位不重叠的基本可行性。"""
        if self.unplaced:
            return False
        if self.used_volume_m3 > self.vehicle.volume_m3 + 1e-9:
            return False
        if self.used_weight_kg > self.vehicle.max_payload_kg + 1e-9:
            return False
        for p in self.placements:
            if (
                p.x1_mm > self.vehicle.inner_length_mm
                or p.y1_mm > self.vehicle.inner_width_mm
                or p.z1_mm > self.vehicle.inner_height_mm
            ):
                return False
        for i, a in enumerate(self.placements):
            for b in self.placements[i + 1 :]:
                if overlaps(a, b):
                    return False
        return True


@dataclass
class Plan:
    """一次求解给出的完整配送方案。"""

    name: str
    loads: List[VehicleLoad]
    route_method: str
    pack_method: str

    @property
    def stores_served(self) -> int:
        seen: List[str] = []
        for load in self.loads:
            for sid in load.route:
                if sid not in seen:
                    seen.append(sid)
        return len(seen)

    @property
    def unplaced_blocks(self) -> int:
        return sum(len(load.unplaced) for load in self.loads)


def overlaps(a: Placement, b: Placement) -> bool:
    """两个货位在三维空间中是否相交（共享面不算相交）。"""

    if a.x1_mm <= b.x_mm or b.x1_mm <= a.x_mm:
        return False
    if a.y1_mm <= b.y_mm or b.y1_mm <= a.y_mm:
        return False
    if a.z1_mm <= b.z_mm or b.z1_mm <= a.z_mm:
        return False
    return True


def base_overlap_area_mm2(a: Placement, b: Placement) -> int:
    """两个货位底面投影的重叠面积。"""

    ox = min(a.x1_mm, b.x1_mm) - max(a.x_mm, b.x_mm)
    oy = min(a.y1_mm, b.y1_mm) - max(a.y_mm, b.y_mm)
    if ox <= 0 or oy <= 0:
        return 0
    return ox * oy


def blocks_from_orders(
    order_lines: Sequence[OrderLine],
    skus: Dict[str, SKU],
    container_height_mm: int,
    max_height_ratio: float = 0.95,
) -> List[Block]:
    """把订单行拆成可装载的整箱码垛 Block，并附上品类装载规则。

    同一订单行内的同一 SKU 竖直码垛；若码垛高度超过车厢可用高度，
    或超过该 SKU 自身的单柱码放上限，则拆成多柱。
    """

    from .categories import rule_of

    usable_h = int(container_height_mm * max_height_ratio)
    blocks: List[Block] = []
    for line in order_lines:
        sku = skus[line.sku_id]
        rule = rule_of(sku.category)
        per_col = max(1, usable_h // max(1, sku.height_mm))
        per_col = max(1, min(per_col, sku.max_stack_units, line.qty))
        remaining = line.qty
        idx = 0
        while remaining > 0:
            units = min(per_col, remaining)
            remaining -= units
            idx += 1
            blocks.append(
                Block(
                    block_id=f"{line.store_id}:{line.sku_id}#{idx}",
                    store_id=line.store_id,
                    sku_id=line.sku_id,
                    category=sku.category,
                    units=units,
                    length_mm=sku.length_mm,
                    width_mm=sku.width_mm,
                    height_mm=sku.height_mm * units,
                    weight_kg=round(sku.weight_kg * units, 4),
                    fragile=sku.fragile,
                    max_stack_units=sku.max_stack_units,
                    functional_zone=rule.zone,
                    max_load_above_kg=rule.max_load_above_kg,
                    heavy_bottom=rule.heavy_bottom,
                )
            )
    return blocks
