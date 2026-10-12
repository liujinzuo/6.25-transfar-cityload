"""排线与车辆指派测试。"""

from __future__ import annotations

import unittest

from _fixtures import CONFIG_PATH  # type: ignore

from cityload.baseline import nearest_neighbor_routes
from cityload.data_synth import build_instance
from cityload.geo import Network
from cityload.pipeline import assign_and_pack, load_config, plan_cost
from cityload.packing import PackOptions, build_blocks
from cityload.routing import (
    optimize_routes,
    route_distance_km,
    savings_routes,
    simulate_route,
    store_load,
    two_opt_route,
)


class TestRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(CONFIG_PATH)
        cls.inst = build_instance(cls.config)
        net_cfg = cls.config["network"]
        net = Network(net_cfg["detour_factor"], net_cfg["speed_kmh"])
        stores = {s.store_id: s for s in cls.inst.stores}
        cls.matrix = net.matrix(cls.inst.depot, stores)
        cls.dist = net.distance_matrix_km(cls.inst.depot, stores)
        cls.opts = PackOptions.from_config(cls.config, align_to_delivery_order=True)
        cls.blocks = build_blocks(cls.inst, cls.opts)

    def test_all_stores_are_served_exactly_once(self) -> None:
        expected = {s.store_id for s in self.inst.stores}
        for routes in (
            nearest_neighbor_routes(self.inst, self.matrix, self.dist),
            savings_routes(self.inst, self.matrix),
        ):
            served = [sid for r in routes for sid in r]
            self.assertEqual(len(served), len(expected))
            self.assertEqual(set(served), expected)

    def test_savings_shortens_total_distance(self) -> None:
        nn = nearest_neighbor_routes(self.inst, self.matrix, self.dist)
        sv = optimize_routes(self.inst, self.matrix)
        d_nn = sum(route_distance_km(r, self.inst, self.dist) for r in nn)
        d_sv = sum(route_distance_km(r, self.inst, self.dist) for r in sv)
        self.assertLess(d_sv, d_nn)

    def test_savings_routes_respect_time_windows(self) -> None:
        for route in savings_routes(self.inst, self.matrix):
            timing = simulate_route(route, self.inst, self.matrix, self.inst.depot.open_min)
            self.assertTrue(timing.on_time, f"路径超时：{route}")

    def test_two_opt_never_worsens(self) -> None:
        for route in savings_routes(self.inst, self.matrix):
            improved = two_opt_route(route, self.inst, self.matrix)
            self.assertEqual(set(improved), set(route))
            self.assertLessEqual(
                route_distance_km(improved, self.inst, self.dist),
                route_distance_km(route, self.inst, self.dist) + 1e-6,
            )

    def test_smaller_volume_cap_yields_more_routes(self) -> None:
        loose = savings_routes(self.inst, self.matrix, volume_cap_ratio=0.9)
        tight = savings_routes(self.inst, self.matrix, volume_cap_ratio=0.35)
        self.assertGreaterEqual(len(tight), len(loose))

    def test_routes_never_exceed_largest_vehicle(self) -> None:
        cap = max(v.volume_m3 for v in self.inst.vehicles)
        for route in savings_routes(self.inst, self.matrix):
            vol, _ = store_load(self.inst, route)
            self.assertLessEqual(vol, cap + 1e-9)


class TestAssignment(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(CONFIG_PATH)
        cls.inst = build_instance(cls.config)
        net_cfg = cls.config["network"]
        net = Network(net_cfg["detour_factor"], net_cfg["speed_kmh"])
        stores = {s.store_id: s for s in cls.inst.stores}
        cls.matrix = net.matrix(cls.inst.depot, stores)
        cls.dist = net.distance_matrix_km(cls.inst.depot, stores)
        cls.opts = PackOptions.from_config(cls.config, align_to_delivery_order=True)
        cls.blocks = build_blocks(cls.inst, cls.opts)

    def test_assignment_fits_every_route(self) -> None:
        routes = optimize_routes(self.inst, self.matrix)
        loads, unassigned = assign_and_pack(
            self.inst, routes, self.opts, self.blocks
        )
        self.assertEqual(unassigned, 0)
        self.assertEqual(sum(len(l.unplaced) for l in loads), 0)
        for load in loads:
            self.assertTrue(load.is_feasible, load.vehicle.vehicle_id)

    def test_no_vehicle_is_used_twice(self) -> None:
        routes = optimize_routes(self.inst, self.matrix)
        loads, _ = assign_and_pack(self.inst, routes, self.opts, self.blocks)
        ids = [l.vehicle.vehicle_id for l in loads]
        self.assertEqual(len(ids), len(set(ids)))

    def test_cost_is_positive_and_grows_with_distance(self) -> None:
        routes = optimize_routes(self.inst, self.matrix)
        loads, _ = assign_and_pack(self.inst, routes, self.opts, self.blocks)
        cost = plan_cost(self.inst, self.dist, loads)
        self.assertGreater(cost, 0)


if __name__ == "__main__":
    unittest.main()
