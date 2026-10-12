"""端到端测试：整条流水线可跑通、结果可复现、产物齐全。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from _fixtures import CONFIG_PATH  # type: ignore

from cityload.dataio import DataFormatError, export_instance, load_instance
from cityload.pipeline import ConfigError, load_config, run_benchmark
from cityload.report import write_outputs


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(CONFIG_PATH)
        cls.result = run_benchmark(cls.config, verbose=False)

    def test_all_four_variants_present(self) -> None:
        keys = [v["key"] for v in self.result["variants"]]
        self.assertEqual(keys, ["A", "B", "C", "D"])

    def test_no_goods_left_behind(self) -> None:
        for v in self.result["variants"]:
            self.assertEqual(v["metrics"]["unplaced_blocks"], 0, v["key"])
            self.assertTrue(v["metrics"]["feasible"], v["key"])

    def test_every_store_is_served(self) -> None:
        expected = len(self.result["instance"].stores)
        for v in self.result["variants"]:
            self.assertEqual(v["metrics"]["stores_served"], expected, v["key"])

    def test_results_are_reproducible(self) -> None:
        again = run_benchmark(self.config, verbose=False)
        for a, b in zip(self.result["variants"], again["variants"]):
            self.assertEqual(a["metrics"]["total_cost"], b["metrics"]["total_cost"])
            self.assertEqual(a["metrics"]["reload_ops"], b["metrics"]["reload_ops"])

    def test_baseline_is_reference_for_improvements(self) -> None:
        for v in self.result["variants"]:
            if v["key"] == "A":
                self.assertEqual(v["improvement_vs_baseline"], {})
            else:
                self.assertIn("total_cost_%", v["improvement_vs_baseline"])

    def test_path_optimisation_cuts_distance(self) -> None:
        by = {v["key"]: v["metrics"] for v in self.result["variants"]}
        self.assertLess(by["C"]["total_distance_km"], by["A"]["total_distance_km"])

    def test_alignment_cuts_rehandling(self) -> None:
        by = {v["key"]: v["metrics"] for v in self.result["variants"]}
        self.assertLess(by["D"]["reload_ops"], by["A"]["reload_ops"])


class TestIOAndReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(CONFIG_PATH)
        cls.result = run_benchmark(cls.config, verbose=False)

    def test_csv_roundtrip_preserves_demand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            export_instance(self.result["instance"], tmp)
            back = load_instance(tmp)
            self.assertEqual(len(back.stores), len(self.result["instance"].stores))
            self.assertEqual(len(back.skus), len(self.result["instance"].skus))
            self.assertEqual(back.total_demand_units(),
                             self.result["instance"].total_demand_units())
            self.assertEqual(round(back.total_demand_volume_m3(), 6),
                             round(self.result["instance"].total_demand_volume_m3(), 6))

    def test_benchmark_runs_on_csv_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            export_instance(self.result["instance"], tmp)
            res = run_benchmark(self.config, data_dir=tmp, verbose=False)
            self.assertEqual(len(res["variants"]), 4)

    def test_outputs_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outs = write_outputs(self.result, tmp, viz_plan_key="D")
            for path in outs.values():
                self.assertTrue(os.path.exists(path), path)
            with open(outs["json"], encoding="utf-8") as fh:
                payload = json.load(fh)
            self.assertEqual(len(payload["variants"]), 4)
            with open(outs["viz"], encoding="utf-8") as fh:
                html = fh.read()
            self.assertIn("<svg", html)
            self.assertNotIn("http://", html.split("<body>")[1].split("</body>")[0])


class TestErrorHandling(unittest.TestCase):
    """黑盒测试发现的问题的回归用例。

    对应「评审在 Windows 上手改配置 / 指错数据目录」的真实路径：
    工具必须给出人话提示与稳定退出码，而不是甩 Python 堆栈。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(CONFIG_PATH)
        cls.result = run_benchmark(cls.config, verbose=False)

    def test_missing_data_dir_raises_friendly_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DataFormatError) as ctx:
                load_instance(tmp)
            msg = str(ctx.exception)
            self.assertIn("缺少必需文件", msg)
            self.assertIn("stores.csv", msg)
            self.assertIn("data/README.md", msg)

    def test_nonexistent_data_dir_raises_friendly_error(self) -> None:
        with self.assertRaises(DataFormatError) as ctx:
            load_instance(os.path.join(tempfile.gettempdir(), "definitely-not-here-xyz"))
        self.assertIn("数据目录不存在", str(ctx.exception))

    def test_partial_data_dir_reports_exactly_which_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            export_instance(self.result["instance"], tmp)
            os.remove(os.path.join(tmp, "vehicles.csv"))
            with self.assertRaises(DataFormatError) as ctx:
                load_instance(tmp)
            self.assertIn("vehicles.csv", str(ctx.exception))

    def test_config_with_utf8_bom_is_accepted(self) -> None:
        """记事本 / PowerShell -Encoding UTF8 / Excel 导出的 JSON 都带 BOM。"""

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bom.json")
            with open(path, "w", encoding="utf-8-sig") as fh:
                json.dump(self.config, fh, ensure_ascii=False)
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(3), b"\xef\xbb\xbf", "前置条件：文件确实带 BOM")
            self.assertEqual(load_config(path)["seed"], self.config["seed"])

    def test_invalid_json_config_raises_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "broken.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{ "seed": 1, }')
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertIn("不是合法 JSON", str(ctx.exception))

    def test_cli_exit_codes_are_stable(self) -> None:
        from cityload.cli import EXIT_CONFIG, EXIT_DATA, main

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                main(["all", "--config", os.path.join(tmp, "nope.json")]), EXIT_CONFIG
            )
            self.assertEqual(
                main(["benchmark", "--data", tmp, "--out", os.path.join(tmp, "o"), "--quiet"]),
                EXIT_DATA,
            )


if __name__ == "__main__":
    unittest.main()
