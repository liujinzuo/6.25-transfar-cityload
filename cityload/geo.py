"""距离与时间：城市路网里程与行驶时间的可复现近似。"""

from __future__ import annotations

import math
from typing import Dict, Tuple

from .domain import Depot, Store


class Network:
    """路网近似：直线距离 × 绕行系数 = 里程；里程 / 平均车速 = 行驶时间。

    这是本仓库明确的**简化假设**：真实业务应接入路网距离矩阵与分时段通行速度。
    详见 docs/05-当前限制与落地路径.md。
    """

    def __init__(self, detour_factor: float = 1.25, speed_kmh: float = 30.0) -> None:
        self.detour_factor = detour_factor
        self.speed_kmh = speed_kmh

    def distance_km(self, a: Tuple[float, float], b: Tuple[float, float]) -> float:
        straight = math.hypot(a[0] - b[0], a[1] - b[1])
        return straight * self.detour_factor

    def travel_min(self, a: Tuple[float, float], b: Tuple[float, float]) -> float:
        return self.distance_km(a, b) / self.speed_kmh * 60.0

    def matrix(self, depot: Depot, stores: Dict[str, Store]) -> Dict[Tuple[str, str], float]:
        """返回 (from_id, to_id) -> 行驶分钟 的完整矩阵，节点 id 用 "DEPOT" 与门店 id。"""

        nodes: Dict[str, Tuple[float, float]] = {"DEPOT": (depot.x_km, depot.y_km)}
        for sid, s in stores.items():
            nodes[sid] = (s.x_km, s.y_km)
        out: Dict[Tuple[str, str], float] = {}
        for i, (aid, ap) in enumerate(nodes.items()):
            for bid, bp in nodes.items():
                if aid == bid:
                    continue
                out[(aid, bid)] = self.travel_min(ap, bp)
        return out

    def distance_matrix_km(
        self, depot: Depot, stores: Dict[str, Store]
    ) -> Dict[Tuple[str, str], float]:
        """返回 (from_id, to_id) -> 里程 km 的完整矩阵。"""

        nodes: Dict[str, Tuple[float, float]] = {"DEPOT": (depot.x_km, depot.y_km)}
        for sid, s in stores.items():
            nodes[sid] = (s.x_km, s.y_km)
        out: Dict[Tuple[str, str], float] = {}
        for aid, ap in nodes.items():
            for bid, bp in nodes.items():
                if aid == bid:
                    continue
                out[(aid, bid)] = self.distance_km(ap, bp)
        return out
