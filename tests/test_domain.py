"""领域模型与装载单元拆分的测试。"""

from __future__ import annotations

import unittest

from _fixtures import CONFIG_PATH  # type: ignore

from cityload.data_synth import build_instance
from cityload.domain import Block, Placement, SKU, overlaps
from cityload.domain import blocks_from_orders, OrderLine
from cityload.pipeline import load_config


class TestDomain(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(CONFIG_PATH)
        cls.inst = build_instance(cls.config)

    def test_instance_is_deterministic(self) -> None:
        again = build_instance(self.config)
        self.assertEqual(
            [(s.store_id, s.x_km, s.y_km) for s in self.inst.stores],
            [(s.store_id, s.x_km, s.y_km) for s in again.stores],
        )
        self.assertEqual(
            [(l.store_id, l.sku_id, l.qty) for l in self.inst.order_lines],
            [(l.store_id, l.sku_id, l.qty) for l in again.order_lines],
        )

    def test_every_order_line_references_known_entities(self) -> None:
        store_ids = {s.store_id for s in self.inst.stores}
        for line in self.inst.order_lines:
            self.assertIn(line.store_id, store_ids)
            self.assertIn(line.sku_id, self.inst.skus)
            self.assertGreater(line.qty, 0)

    def test_blocks_preserve_box_count(self) -> None:
        sku = SKU("SX", "测试品", "口服药品", 300, 200, 250, 3.0, False, 4)
        lines = [OrderLine("ST001", "SX", 10)]
        blocks = blocks_from_orders(lines, {"SX": sku}, 2100, 0.95)
        self.assertEqual(sum(b.units for b in blocks), 10)
        # 单柱不超过 SKU 的码放上限（4 箱）
        self.assertTrue(all(b.units <= 4 for b in blocks))
        # 每柱高度 = 单箱高 × 箱数
        for b in blocks:
            self.assertEqual(b.height_mm, sku.height_mm * b.units)

    def test_blocks_respect_container_height(self) -> None:
        sku = SKU("SY", "高箱", "保健品", 300, 200, 400, 2.0, False, 9)
        blocks = blocks_from_orders([OrderLine("ST001", "SY", 9)], {"SY": sku}, 1000, 0.95)
        self.assertTrue(all(b.height_mm <= 950 for b in blocks))
        self.assertEqual(sum(b.units for b in blocks), 9)

    def test_fragile_sku_is_marked(self) -> None:
        sku = SKU("SF", "冷链品", "冷链药品", 300, 200, 250, 3.0, True, 2)
        blocks = blocks_from_orders([OrderLine("ST001", "SF", 5)], {"SF": sku}, 2100, 0.95)
        self.assertTrue(all(b.fragile for b in blocks))


class TestGeometry(unittest.TestCase):
    def _p(self, x: int, y: int, z: int, l: int, w: int, h: int) -> Placement:
        b = Block("B", "ST001", "S1", "口服药品", 1, l, w, h, 1.0, False, 1)
        return Placement(b, x, y, z, l, w, h)

    def test_overlap_detection(self) -> None:
        a = self._p(0, 0, 0, 100, 100, 100)
        b = self._p(50, 50, 50, 100, 100, 100)
        c = self._p(100, 0, 0, 100, 100, 100)  # 仅共享面
        d = self._p(0, 0, 100, 100, 100, 100)  # 上下相邻
        self.assertTrue(overlaps(a, b))
        self.assertFalse(overlaps(a, c))
        self.assertFalse(overlaps(a, d))


if __name__ == "__main__":
    unittest.main()
