"""基线方法：人工经验排线与装车。

基线的作用不是“凑一个更差的方案”，而是把**当前人工做法**形式化，
使指标体系上的改善幅度可核查、可复现。

- ``nearest_neighbor_routes``：按最近距离贪心串点，装不下就换车（人工排线常态）
- ``one_store_per_vehicle``：一店一车，作为成本上界的极端参照
- 装车基线见 ``cityload.packing.PackOptions(align_to_delivery_order=False)``
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from .domain import Instance
from .routing import (
    PlanningLimits,
    planning_capacity,
    route_distance_km,
    route_is_executable,
    simulate_route,
    store_load,
)


def nearest_neighbor_routes(
    inst: Instance,
    matrix: Dict[Tuple[str, str], float],
    dist: Dict[Tuple[str, str], float],
    respect_time_windows: bool = True,
    limits: PlanningLimits | None = None,
) -> List[List[str]]:
    """最近邻贪心排线：从中心仓出发，每次去最近的、装得下且不误时的门店，装满换车。

    这正是人工排线的典型做法：局部就近串点 + 装不下就换车，
    不做全局节约值合并，也不做路径内交换改进。
    """

    limits = limits or PlanningLimits()
    cap_v, cap_p = planning_capacity(inst)
    remaining = {s.store_id for s in inst.stores}
    routes: List[List[str]] = []

    while remaining:
        route: List[str] = []
        vol = 0.0
        kg = 0.0
        pos = "DEPOT"
        while True:
            chosen = None
            for sid in sorted(remaining, key=lambda s: dist[(pos, s)]):
                dv, dk = store_load(inst, [sid])
                if vol + dv > cap_v + 1e-9 or kg + dk > cap_p + 1e-9:
                    continue  # 装不下，试下一近的门店
                if respect_time_windows and not route_is_executable(
                    route + [sid], inst, matrix, limits
                ):
                    continue  # 会误时或超工时，试下一近的门店
                chosen = (sid, dv, dk)
                break
            if chosen is None:
                break
            sid, dv, dk = chosen
            route.append(sid)
            vol += dv
            kg += dk
            remaining.discard(sid)
            pos = sid
        if not route:
            # 单车装不下单店（极端情况），单独成线避免死循环
            sid = next(iter(remaining))
            route.append(sid)
            remaining.discard(sid)
        routes.append(route)
    return routes


def baseline_distance(
    inst: Instance,
    routes: Sequence[Sequence[str]],
    dist: Dict[Tuple[str, str], float],
) -> float:
    """一组路径的总里程，用于报告基线里程。"""

    return round(sum(route_distance_km(r, inst, dist) for r in routes), 3)
