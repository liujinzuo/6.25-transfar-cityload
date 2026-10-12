"""3D 配载器测试：几何可行性、装载约束、以及与交付顺序对齐的收益。"""

from __future__ import annotations

import unittest

from _fixtures import CONFIG_PATH  # type: ignore

from cityload.categories import RULES, rule_of, zones_in_packing_order
from cityload.data_synth import build_instance
from cityload.domain import overlaps
from cityload.metrics import reload_ops
from cityload.packing import (
    PackOptions,
    allocate_space,
    build_blocks,
    normalize_blocks_for_height,
    pack_plan,
    pack_route,
)
from cityload.pipeline import load_config
from cityload.routing import store_load


class TestPacking(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(CONFIG_PATH)
        cls.inst = build_instance(cls.config)
        cls.opts = PackOptions.from_config(cls.config, align_to_delivery_order=True)
        cls.blocks = build_blocks(cls.inst, cls.opts)
        cls.v42 = [v for v in cls.inst.vehicles if v.vtype.type_id == "V42"][0]

    def _pack(self, route, align: bool):
        opts = PackOptions.from_config(self.config, align_to_delivery_order=align)
        return pack_route(self.inst, route, self.v42, self.blocks, opts)

    def test_no_geometric_violations(self) -> None:
        route = [s.store_id for s in self.inst.stores[:6]]
        load = self._pack(route, align=True)
        self.assertTrue(load.placements)
        for p in load.placements:
            self.assertLessEqual(p.x1_mm, self.v42.inner_length_mm)
            self.assertLessEqual(p.y1_mm, self.v42.inner_width_mm)
            self.assertLessEqual(p.z1_mm, self.v42.inner_height_mm)
        for i, a in enumerate(load.placements):
            for b in load.placements[i + 1 :]:
                self.assertFalse(overlaps(a, b), f"货位重叠：{a.block.block_id} / {b.block.block_id}")

    def test_all_boxes_land_somewhere(self) -> None:
        route = [s.store_id for s in self.inst.stores[:6]]
        load = self._pack(route, align=True)
        planned = sum(b.units for sid in route for b in self.blocks[sid])
        placed = sum(p.block.units for p in load.placements)
        self.assertEqual(placed + sum(b.units for b in load.unplaced), planned)

    def test_fragile_block_is_never_loaded_on(self) -> None:
        route = [s.store_id for s in self.inst.stores]
        load = self._pack(route, align=True)
        fragile = {p.block.block_id for p in load.placements if p.block.fragile}
        for p in load.placements:
            if p.z_mm == 0:
                continue
            below = [
                q
                for q in load.placements
                if abs(q.z1_mm - p.z_mm) <= 1
                and min(q.x1_mm, p.x1_mm) > max(q.x_mm, p.x_mm)
                and min(q.y1_mm, p.y1_mm) > max(q.y_mm, p.y_mm)
            ]
            for q in below:
                self.assertNotIn(q.block.block_id, fragile, "易碎件上方不得压货")

    def test_heavy_side_is_below_light_side(self) -> None:
        route = [s.store_id for s in self.inst.stores[:8]]
        load = self._pack(route, align=True)
        weighted_height = sum(
            p.z_mm * p.block.weight_kg for p in load.placements
        )
        total_weight = sum(p.block.weight_kg for p in load.placements)
        max_height = max((p.z1_mm for p in load.placements), default=1)
        self.assertLess(weighted_height / total_weight, max_height * 0.5)

    def test_height_normalization_splits_tall_columns(self) -> None:
        long_route = [s.store_id for s in self.inst.stores[:6]]
        tall = self.blocks[long_route[0]]
        trimmed = normalize_blocks_for_height(tall, 900)
        self.assertTrue(all(b.height_mm <= 900 for b in trimmed))
        self.assertEqual(sum(b.units for b in trimmed), sum(b.units for b in tall))

    def test_zone_segments_cover_container_and_reverse_delivery(self) -> None:
        route = [s.store_id for s in self.inst.stores[:5]]
        usable_h = int(self.v42.inner_height_mm * 0.95)
        trimmed = {
            sid: normalize_blocks_for_height(self.blocks[sid], usable_h) for sid in route
        }
        segs = allocate_space(route, trimmed, self.v42.inner_length_mm)
        self.assertTrue(segs)
        # 功能区必须首尾相接、铺满整条车长，且相邻不重叠
        ordered = sorted(segs.items(), key=lambda kv: kv[1][0])
        self.assertEqual(ordered[0][1][0], 0)
        self.assertEqual(ordered[-1][1][1], self.v42.inner_length_mm)
        for (_z1, (a0, a1)), (_z2, (b0, b1)) in zip(ordered, ordered[1:]):
            self.assertEqual(a1, b0)
            self.assertLess(a0, a1)
        # 冷链区必须贴近尾门（x 最大）
        if "COLD" in segs:
            self.assertEqual(max(s[1] for s in segs.values()), segs["COLD"][1])

    def test_every_block_lands_in_its_own_zone_when_strict(self) -> None:
        """空间分配生效时，货物不得越出所属功能区（品类隔离是可验证的硬性质）。"""

        route = [s.store_id for s in self.inst.stores[:3]]
        usable_h = int(self.v42.inner_height_mm * 0.95)
        trimmed = {
            sid: normalize_blocks_for_height(self.blocks[sid], usable_h) for sid in route
        }
        segs = allocate_space(route, trimmed, self.v42.inner_length_mm)
        opts = PackOptions(align_to_delivery_order=True, strict_zones=True)
        load = pack_route(self.inst, route, self.v42, self.blocks, opts)
        if load.allocation_tier != 1:
            self.skipTest("该车次装载过紧，算法已按设计降级，本用例不适用")
        for p in load.placements:
            seg = segs.get(p.block.functional_zone)
            self.assertIsNotNone(seg, p.block.functional_zone)
            self.assertGreaterEqual(p.x_mm, seg[0] - 1)
            self.assertLessEqual(p.x1_mm, seg[1] + 1)

    def test_delivery_alignment_reduces_rehandling(self) -> None:
        """装载宽松时，空间分配必须真正生效，并显著降低重复搬运段数。"""

        route = [s.store_id for s in self.inst.stores[:3]]
        v68 = [v for v in self.inst.vehicles if v.vtype.type_id == "V68"][0]
        opts_a = PackOptions.from_config(self.config, align_to_delivery_order=True)
        opts_b = PackOptions.from_config(self.config, align_to_delivery_order=False)
        aligned = pack_route(self.inst, route, v68, self.blocks, opts_a)
        loose = pack_route(self.inst, route, v68, self.blocks, opts_b)
        self.assertEqual(
            aligned.allocation_tier,
            1,
            "3 家门店装进 6.8m 车厢属于宽松装载，本应保持最高等级的空间分配",
        )
        self.assertLess(
            reload_ops(aligned),
            reload_ops(loose),
            "按交付顺序分区装载应显著减少重复搬运段数",
        )

    def test_pack_plan_batch_api(self) -> None:
        route = [s.store_id for s in self.inst.stores[:4]]
        vol, _ = store_load(self.inst, route)
        self.assertLess(vol, self.v42.volume_m3)
        loads = pack_plan(self.inst, [(route, self.v42)], self.opts, self.blocks)
        self.assertEqual(len(loads), 1)
        self.assertEqual(loads[0].route, route)


class TestCategoryRules(unittest.TestCase):
    """品类装载规则必须真的约束住配载结果，而不只是写在表里。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(CONFIG_PATH)
        cls.inst = build_instance(cls.config)
        cls.opts = PackOptions.from_config(cls.config, align_to_delivery_order=True)
        cls.blocks = build_blocks(cls.inst, cls.opts)
        cls.v68 = [v for v in cls.inst.vehicles if v.vtype.type_id == "V68"][0]

    def test_cold_chain_and_precise_items_are_never_loaded_on(self) -> None:
        route = [s.store_id for s in self.inst.stores]
        load = pack_route(self.inst, route, self.v68, self.blocks, self.opts)
        for p in load.placements:
            if p.z_mm == 0:
                continue
            below = [
                q
                for q in load.placements
                if abs(q.z1_mm - p.z_mm) <= 1
                and min(q.x1_mm, p.x1_mm) > max(q.x_mm, p.x_mm)
                and min(q.y1_mm, p.y1_mm) > max(q.y_mm, p.y_mm)
            ]
            for q in below:
                self.assertGreater(
                    q.block.max_load_above_kg, 0.0, "禁压品类上方不得放货"
                )

    def test_incompatible_categories_never_stack_directly(self) -> None:
        route = [s.store_id for s in self.inst.stores]
        load = pack_route(self.inst, route, self.v68, self.blocks, self.opts)
        for p in load.placements:
            for q in load.placements:
                if abs(q.z1_mm - p.z_mm) > 1:
                    continue
                if not (
                    min(q.x1_mm, p.x1_mm) > max(q.x_mm, p.x_mm)
                    and min(q.y1_mm, p.y1_mm) > max(q.y_mm, p.y_mm)
                ):
                    continue
                self.assertNotIn(q.block.category, rule_of(p.block.category).incompatible)
                self.assertNotIn(p.block.category, rule_of(q.block.category).incompatible)

    def test_heavy_bottom_categories_stay_low(self) -> None:
        route = [s.store_id for s in self.inst.stores]
        load = pack_route(self.inst, route, self.v68, self.blocks, self.opts)
        limit = self.opts.heavy_bottom_max_z_mm
        for p in load.placements:
            if p.block.heavy_bottom:
                self.assertLessEqual(p.z_mm, limit)

    def test_zone_order_puts_cold_chain_nearest_the_door(self) -> None:
        self.assertEqual(zones_in_packing_order()[-1], "COLD")

    def test_rule_table_covers_every_generated_category(self) -> None:
        for sku in self.inst.skus.values():
            self.assertIn(sku.category, RULES, sku.category)


if __name__ == "__main__":
    unittest.main()
