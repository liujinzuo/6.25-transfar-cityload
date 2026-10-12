"""路径优化：Clarke-Wright 节约算法 + 2-opt 改进 + 时间窗校核。

与“人工经验排线”的差别在于：本模块在构造阶段就同时考虑
载重、容积与到货时间窗，并在构造完成后做局部搜索改进。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

from .domain import Instance, Store, Vehicle, VehicleType


@dataclass
class RouteTiming:
    """一条路径的执行时刻表。"""

    arrivals: Dict[str, float] = field(default_factory=dict)
    wait_min: float = 0.0
    finish_min: float = 0.0
    start_min: float = 0.0
    late_stores: List[str] = field(default_factory=list)

    @property
    def on_time(self) -> bool:
        return not self.late_stores


@dataclass
class PlanningLimits:
    """排线阶段的现实约束与能力标定。

    只考虑“能不能赶上时间窗”是不够的：如果为了凑满一车而让司机在现场干等几小时，
    或者一条线路跑成 17 个小时，方案在业务上是不可执行的。

    ``volume_derate`` 尤为关键：排线时**不能**假设车厢 100% 装满。
    货物规格异构、要靠码、要留装卸间隙，装载器实测能稳定装到约 55%，
    所以路径批量必须按这个可达能力来定，否则会排出一堆根本装不下的“大单”。
    最终是否真的装得下，由 ``cityload.packing`` 的 3D 配载器回检确认。
    """

    max_duration_min: float = 600.0   # 单车单日最长在线时长（10 小时）
    max_wait_min: float = 150.0       # 单条线路累计等待上限
    volume_derate: float = 0.55       # 装载器实测可达的车厢容积利用上限
    payload_derate: float = 0.98      # 载重按额定值基本不折减（重量是确定量）

    @classmethod
    def from_config(cls, config: dict) -> "PlanningLimits":
        net = config.get("network", {})
        pack = config.get("packing", {})
        return cls(
            max_duration_min=float(net.get("max_route_duration_min", 600.0)),
            max_wait_min=float(net.get("max_route_wait_min", 150.0)),
            volume_derate=float(pack.get("planning_volume_derate", 0.55)),
            payload_derate=float(pack.get("planning_payload_derate", 0.98)),
        )


def optimal_departure(
    route: Sequence[str],
    inst: Instance,
    matrix: Dict[Tuple[str, str], float],
) -> Tuple[float, float, float]:
    """求一条路径的**最优发车时刻**。

    发车时刻不是固定 06:00 —— 一条只服务 14:00–17:00 时间窗门店的线路，
    正确做法是 13:00 才发车，而不是 06:00 到场干等 8 小时。
    对固定访问顺序，令 ``c_i`` 为自中心仓出发到第 i 站的累计行驶+服务时间，则发车时刻 t 需满足

        t + c_i >= tw_start_i   （否则等待）
        t + c_i <= tw_end_i     （否则误时）

    因此使等待最小的发车时刻为 ``t = max(仓开时间, max_i(tw_start_i - c_i))``，
    可行上界为 ``min_i(tw_end_i - c_i)``。

    返回 (发车时刻, 可行上界, 全程结束时刻)。
    """

    cum = 0.0
    pos = "DEPOT"
    lower = float(inst.depot.open_min)
    upper = float("inf")
    for sid in route:
        cum += matrix[(pos, sid)]
        store = inst.store(sid)
        lower = max(lower, store.tw_start_min - cum)
        upper = min(upper, store.tw_end_min - cum)
        cum += store.service_min
        pos = sid
    cum += matrix[(pos, "DEPOT")]
    return lower, upper, lower + cum


def simulate_route(
    route: Sequence[str],
    inst: Instance,
    matrix: Dict[Tuple[str, str], float],
    start_min: float | None = None,
) -> RouteTiming:
    """按到货时间窗推演一条路径。

    ``start_min`` 省略时自动采用使等待最小的发车时刻（见 ``optimal_departure``）。
    """

    if start_min is None:
        start_min, _upper, _finish = optimal_departure(route, inst, matrix)

    t = start_min
    pos = "DEPOT"
    arrivals: Dict[str, float] = {}
    wait = 0.0
    late: List[str] = []
    for sid in route:
        t += matrix[(pos, sid)]
        store = inst.store(sid)
        if t < store.tw_start_min:
            wait += store.tw_start_min - t
            t = float(store.tw_start_min)
        arrivals[sid] = t
        if t > store.tw_end_min + 1e-9:
            late.append(sid)
        t += store.service_min
        pos = sid
    t += matrix[(pos, "DEPOT")]
    return RouteTiming(
        arrivals=arrivals,
        wait_min=wait,
        finish_min=t,
        start_min=start_min,
        late_stores=late,
    )


def route_is_executable(
    route: Sequence[str],
    inst: Instance,
    matrix: Dict[Tuple[str, str], float],
    limits: "PlanningLimits | None" = None,
) -> bool:
    """路径是否同时满足时间窗、在线时长与等待时长约束。"""

    limits = limits or PlanningLimits()
    timing = simulate_route(route, inst, matrix)
    if not timing.on_time:
        return False
    if timing.wait_min > limits.max_wait_min + 1e-9:
        return False
    if timing.finish_min - timing.start_min > limits.max_duration_min + 1e-9:
        return False
    return True


def route_distance_km(
    route: Sequence[str], inst: Instance, dist: Dict[Tuple[str, str], float]
) -> float:
    total = 0.0
    pos = "DEPOT"
    for sid in route:
        total += dist[(pos, sid)]
        pos = sid
    total += dist[(pos, "DEPOT")]
    return total


def store_load(
    inst: Instance, store_ids: Sequence[str]
) -> Tuple[float, float]:
    """返回 (体积 m3, 重量 kg)。"""

    by_store = inst.demand_by_store()
    vol = 0.0
    kg = 0.0
    for sid in store_ids:
        for line in by_store.get(sid, []):
            sku = inst.skus[line.sku_id]
            vol += sku.volume_m3 * line.qty
            kg += sku.weight_kg * line.qty
    return vol, kg


def largest_capacity(inst: Instance) -> Tuple[float, float]:
    vmax = max(inst.vehicles, key=lambda v: v.volume_m3)
    pmax = max(inst.vehicles, key=lambda v: v.max_payload_kg)
    return vmax.volume_m3, pmax.max_payload_kg


#: 计划阶段的能力折减默认值（可被 configs 覆盖，见 PlanningLimits）
VOLUME_DERATE = 0.55
PAYLOAD_DERATE = 0.98


def planning_capacity(
    inst: Instance, limits: "PlanningLimits | None" = None
) -> Tuple[float, float]:
    """排线时可用的单车能力（已按装载器可达效率折减）。"""

    limits = limits or PlanningLimits()
    cap_v, cap_p = largest_capacity(inst)
    return cap_v * limits.volume_derate, cap_p * limits.payload_derate


# --------------------------------------------------------------------------- #
# Clarke-Wright 节约算法
# --------------------------------------------------------------------------- #
def savings_routes(
    inst: Instance,
    matrix: Dict[Tuple[str, str], float],
    respect_time_windows: bool = True,
    volume_cap_ratio: float | None = None,
    limits: PlanningLimits | None = None,
) -> List[List[str]]:
    """并行节约算法：从“一店一车”出发，按节约值从大到小合并路径。

    ``volume_cap_ratio`` 控制单条路径允许达到的车厢容积占比：
    调小会得到更多、更小的路径，配合小车型可能总成本更低 ——
    具体取值由上层按总成本搜索（见 ``cityload.pipeline.plan_with_savings``）。
    """

    limits = limits or PlanningLimits()
    if volume_cap_ratio is None:
        cap_v, cap_p = planning_capacity(inst)
    else:
        cap_v = max(v.volume_m3 for v in inst.vehicles) * volume_cap_ratio
        cap_p = max(v.max_payload_kg for v in inst.vehicles) * limits.payload_derate
    by_store = inst.demand_by_store()
    demand: Dict[str, Tuple[float, float]] = {}
    for s in inst.stores:
        v = 0.0
        p = 0.0
        for line in by_store[s.store_id]:
            sku = inst.skus[line.sku_id]
            v += sku.volume_m3 * line.qty
            p += sku.weight_kg * line.qty
        demand[s.store_id] = (v, p)

    routes: Dict[int, List[str]] = {}
    where: Dict[str, int] = {}
    for idx, s in enumerate(inst.stores):
        routes[idx] = [s.store_id]
        where[s.store_id] = idx

    saves: List[Tuple[float, str, str]] = []
    ids = [s.store_id for s in inst.stores]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            s = matrix[("DEPOT", a)] + matrix[("DEPOT", b)] - matrix[(a, b)]
            if s > 0:
                saves.append((s, a, b))
    saves.sort(key=lambda x: -x[0])

    def route_metrics(route: Sequence[str]) -> Tuple[float, float]:
        v = sum(demand[sid][0] for sid in route)
        p = sum(demand[sid][1] for sid in route)
        return v, p

    for _, a, b in saves:
        ra, rb = where.get(a), where.get(b)
        if ra is None or rb is None or ra == rb:
            continue
        A, B = routes[ra], routes[rb]
        if a not in (A[0], A[-1]) or b not in (B[0], B[-1]):
            continue

        # 调整方向，使 a 为 A 的末位、b 为 B 的首位
        if A[-1] != a:
            A = list(reversed(A))
        if B[0] != b:
            B = list(reversed(B))
        merged = A + B
        if merged[0] == merged[-1] and len(merged) > 2:
            continue
        mv, mp = route_metrics(merged)
        if mv > cap_v + 1e-9 or mp > cap_p + 1e-9:
            continue
        if respect_time_windows:
            if not route_is_executable(merged, inst, matrix, limits):
                continue
        routes[ra] = merged
        for sid in B:
            where[sid] = ra
        del routes[rb]

    return [r for r in routes.values() if r]


def two_opt_route(
    route: Sequence[str],
    inst: Instance,
    matrix: Dict[Tuple[str, str], float],
    max_rounds: int = 60,
    limits: PlanningLimits | None = None,
) -> List[str]:
    """路径内 2-opt：反转子段以缩短里程，且不破坏可执行性。"""

    limits = limits or PlanningLimits()
    best = list(route)
    if len(best) < 3:
        return best
    best_len = _route_time(best, inst, matrix)
    improved = True
    rounds = 0
    while improved and rounds < max_rounds:
        improved = False
        rounds += 1
        for i in range(len(best) - 1):
            for j in range(i + 1, len(best)):
                cand = best[:i] + list(reversed(best[i : j + 1])) + best[j + 1 :]
                cand_len = _route_time(cand, inst, matrix)
                if cand_len + 1e-9 < best_len:
                    if not route_is_executable(cand, inst, matrix, limits):
                        continue
                    best, best_len = cand, cand_len
                    improved = True
        if rounds >= max_rounds:
            break
    return best


def _route_time(
    route: Sequence[str], inst: Instance, matrix: Dict[Tuple[str, str], float]
) -> float:
    t = 0.0
    pos = "DEPOT"
    for sid in route:
        t += matrix[(pos, sid)]
        pos = sid
    t += matrix[(pos, "DEPOT")]
    return t


def optimize_routes(
    inst: Instance,
    matrix: Dict[Tuple[str, str], float],
    respect_time_windows: bool = True,
    limits: PlanningLimits | None = None,
) -> List[List[str]]:
    """节约算法构造 + 2-opt 改进的完整排线流程。"""

    limits = limits or PlanningLimits()
    routes = savings_routes(
        inst, matrix, respect_time_windows=respect_time_windows, limits=limits
    )
    return [two_opt_route(r, inst, matrix, limits=limits) for r in routes]


# --------------------------------------------------------------------------- #
# 车辆指派
# --------------------------------------------------------------------------- #
def assign_vehicles(
    routes: Sequence[Sequence[str]], inst: Instance
) -> Tuple[List[Tuple[List[str], Vehicle]], List[List[str]]]:
    """按“装得下的最便宜车型”为一类路径指派车辆（**按体积估算，不做试装**）。

    这是排线与装车割裂时的传统做法，保留下来作为对照基准：
    真正的方案用 ``cityload.pipeline.assign_and_pack`` 的“先试装再定车”。
    """

    pool: Dict[str, List[Vehicle]] = {}
    for v in inst.vehicles:
        pool.setdefault(v.vtype.type_id, []).append(v)
    types: List[VehicleType] = sorted(
        {v.vtype for v in inst.vehicles},
        key=lambda t: (t.fixed_cost + t.cost_per_km, t.volume_m3),
    )

    order = sorted(
        routes, key=lambda r: -store_load(inst, r)[0]
    )  # 大单优先，避免大单无车可用
    assigned: List[Tuple[List[str], Vehicle]] = []
    unassigned: List[List[str]] = []
    for route in order:
        vol, kg = store_load(inst, route)
        picked: Vehicle | None = None
        for t in types:
            if not pool.get(t.type_id):
                continue
            if (
                vol <= t.volume_m3 * VOLUME_DERATE + 1e-9
                and kg <= t.max_payload_kg * PAYLOAD_DERATE + 1e-9
            ):
                picked = pool[t.type_id].pop(0)
                break
        if picked is None:
            unassigned.append(list(route))
        else:
            assigned.append((list(route), picked))
    return assigned, unassigned
