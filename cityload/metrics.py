"""评价指标。

指标设计直接对齐赛题“运输成本、交付效率、装载率、装卸效率”四类诉求。

核心指标 ``reload_ops``（重复搬运段数）是本方案针对“配载与配送计划缺乏协同、
容易增加重复搬运及装卸时间”这一业务痛点专门定义的量化指标，**按功能区分别计算**：

    车厢被分成若干功能区（冷链区、日化隔离区、常规药品区、精密器械区）后，
    卸某一站本来就要到各功能区各取一次，因此“跨功能区”不算倒腾；
    真正造成倒腾的是同一个功能区内、同一门店的货被别站切成几段。

        某功能区倒腾次数 = 该功能区卸货序列的门店分段数 - 覆盖到的门店数

两级空间分配对齐交付顺序后，每个门店在各功能区内都连续成段，指标理论下界为 0。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Sequence, Tuple

from .domain import Instance, Placement, Plan, VehicleLoad
from .routing import PlanningLimits, route_distance_km, route_is_executable, simulate_route


@dataclass
class PlanMetrics:
    plan_name: str
    route_method: str
    pack_method: str
    vehicles_used: int = 0
    vehicle_types: Dict[str, int] = field(default_factory=dict)
    stores_served: int = 0
    stores_total: int = 0
    total_distance_km: float = 0.0
    fixed_cost: float = 0.0
    variable_cost: float = 0.0
    total_cost: float = 0.0
    volume_utilization: float = 0.0
    payload_utilization: float = 0.0
    on_time_rate: float = 0.0
    late_stores: int = 0
    total_wait_min: float = 0.0
    makespan_min: float = 0.0
    reload_ops: int = 0
    avg_stops_per_vehicle: float = 0.0
    cost_per_store: float = 0.0
    unplaced_blocks: int = 0
    unassigned_routes: int = 0
    solve_seconds: float = 0.0
    feasible: bool = True
    executable: bool = True
    max_route_wait_min: float = 0.0
    #: 空间分配降级的车次：2=功能区仅作偏好，3=未做空间分配
    relaxed_loads: int = 0
    unallocated_loads: int = 0
    zone_violations: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def reload_ops(load: VehicleLoad) -> int:
    """统计一条路径上的**重复搬运段数**（倒腾次数），按功能区分别计算。

    见模块文档：跨功能区卸货不计倒腾，同一功能区内被别站切段才计。
    """

    if len(load.route) < 2 or not load.placements:
        return 0
    zones: Dict[str, List[Placement]] = {}
    for p in load.placements:
        zones.setdefault(p.block.functional_zone, []).append(p)

    total = 0
    for zone_placements in zones.values():
        ordered = sorted(zone_placements, key=lambda p: (-p.x1_mm, p.y_mm, p.z_mm))
        segments = 0
        prev = None
        stores = set()
        for p in ordered:
            stores.add(p.store_id)
            if p.store_id != prev:
                segments += 1
                prev = p.store_id
        total += max(0, segments - len(stores))
    return total


def zone_violations(load: VehicleLoad) -> int:
    """统计越出所属功能区边界的箱垛数（品类隔离合规性）。

    等级 0（该方案本就不做空间分配）与等级 3（已降级为不做分配）不按越区计。
    """

    if load.allocation_tier in (0, 3):
        return 0
    from .packing import allocate_space, normalize_blocks_for_height

    trimmed: Dict[str, List] = {}
    for p in load.placements:
        trimmed.setdefault(p.store_id, []).append(p.block)
    if not trimmed:
        return 0
    usable_l = load.vehicle.inner_length_mm
    usable_h = int(load.vehicle.inner_height_mm * 0.95)
    norm = {
        sid: normalize_blocks_for_height(bs, usable_h) for sid, bs in trimmed.items()
    }
    segs = allocate_space(load.route, norm, usable_l)
    if not segs:
        return 0
    bad = 0
    for p in load.placements:
        seg = segs.get(p.block.functional_zone)
        if seg is None:
            continue
        if p.x_mm < seg[0] - 1 or p.x1_mm > seg[1] + 1:
            bad += 1
    return bad


def evaluate(
    plan: Plan,
    inst: Instance,
    matrix: Dict[Tuple[str, str], float],
    dist: Dict[Tuple[str, str], float],
    limits: PlanningLimits | None = None,
) -> PlanMetrics:
    limits = limits or PlanningLimits()
    m = PlanMetrics(
        plan_name=plan.name, route_method=plan.route_method, pack_method=plan.pack_method
    )
    m.stores_total = len(inst.stores)
    total_wait = 0.0
    late = 0
    served = 0
    for load in plan.loads:
        m.vehicles_used += 1
        m.vehicle_types[load.vehicle.vtype.type_id] = (
            m.vehicle_types.get(load.vehicle.vtype.type_id, 0) + 1
        )
        d = route_distance_km(load.route, inst, dist)
        m.total_distance_km += d
        m.fixed_cost += load.vehicle.vtype.fixed_cost
        m.variable_cost += load.vehicle.vtype.cost_per_km * d
        timing = simulate_route(load.route, inst, matrix)
        total_wait += timing.wait_min
        m.max_route_wait_min = max(m.max_route_wait_min, timing.wait_min)
        late += len(timing.late_stores)
        served += len(load.route)
        m.makespan_min = max(m.makespan_min, timing.finish_min)
        m.reload_ops += reload_ops(load)
        m.unplaced_blocks += len(load.unplaced)
        if load.allocation_tier >= 2:
            m.relaxed_loads += 1
        if load.allocation_tier == 3:
            m.unallocated_loads += 1
        m.zone_violations += zone_violations(load)
        if not load.is_feasible:
            m.feasible = False
        if not route_is_executable(load.route, inst, matrix, limits):
            m.executable = False

    m.total_cost = m.fixed_cost + m.variable_cost
    m.stores_served = served
    m.late_stores = late
    m.on_time_rate = (served - late) / served if served else 0.0
    m.total_wait_min = total_wait
    m.total_distance_km = round(m.total_distance_km, 3)
    m.fixed_cost = round(m.fixed_cost, 2)
    m.variable_cost = round(m.variable_cost, 2)
    m.total_cost = round(m.total_cost, 2)
    m.total_wait_min = round(m.total_wait_min, 1)
    m.makespan_min = round(m.makespan_min, 1)

    used_vol = sum(l.used_volume_m3 for l in plan.loads)
    cap_vol = sum(l.vehicle.volume_m3 for l in plan.loads)
    used_kg = sum(l.used_weight_kg for l in plan.loads)
    cap_kg = sum(l.vehicle.max_payload_kg for l in plan.loads)
    m.volume_utilization = round(used_vol / cap_vol, 4) if cap_vol else 0.0
    m.payload_utilization = round(used_kg / cap_kg, 4) if cap_kg else 0.0
    m.avg_stops_per_vehicle = round(served / m.vehicles_used, 2) if m.vehicles_used else 0.0
    m.cost_per_store = round(m.total_cost / served, 2) if served else 0.0
    return m


def compare(baseline: PlanMetrics, candidate: PlanMetrics) -> Dict[str, float]:
    """相对基线的改善幅度（正数表示 candidate 更优）。"""

    def delta(key: str, lower_is_better: bool = True, base: float | None = None,
              cand: float | None = None) -> float:
        b = getattr(baseline, key) if base is None else base
        c = getattr(candidate, key) if cand is None else cand
        if b == 0:
            return 0.0
        pct = (c - b) / b * 100.0
        return round(-pct if lower_is_better else pct, 2)

    return {
        "total_cost_%": delta("total_cost"),
        "vehicles_%": delta("vehicles_used"),
        "distance_%": delta("total_distance_km"),
        "volume_utilization_%": delta("volume_utilization", lower_is_better=False),
        "payload_utilization_%": delta("payload_utilization", lower_is_better=False),
        "on_time_rate_%": delta("on_time_rate", lower_is_better=False),
        "reload_ops_%": delta("reload_ops"),
    }
