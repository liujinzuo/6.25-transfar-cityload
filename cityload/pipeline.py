"""求解流水线：合成/载入算例 → 排线 → 配载 → 回检 → 指标 → 对照实验。

对照实验采用 2×2 消融设计（路径方法 × 装载方法），使“节省究竟来自路径优化
还是装载优化”可以被单独归因，而不是把所有收益混在一个总数里。

额外的 **配载回检（load-check loop）** 是本方案的工程化要点：
路径构造阶段用的是折减后的计划能力，最终由 3D 配载器验证是否真的装得下；
装不下就自动升级车型重新配载 —— 排线与装车不是两个割裂的步骤。
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from typing import Dict, List, Sequence, Tuple

from .baseline import nearest_neighbor_routes
from .data_synth import build_instance, describe
from .domain import Instance, Plan, Vehicle, VehicleLoad
from .geo import Network
from .metrics import PlanMetrics, compare, evaluate
from .packing import PackOptions, build_blocks, pack_route
from .routing import (
    PlanningLimits,
    route_distance_km,
    savings_routes,
    store_load,
    two_opt_route,
)

VARIANT_DEFS: Sequence[dict] = (
    {
        "key": "A",
        "name": "人工经验基线（最近邻排线 + 按品类集中装车）",
        "route": "nn",
        "pack": "ff",
        "is_baseline": True,
    },
    {
        "key": "B",
        "name": "仅优化装载（最近邻排线 + 分区装载对齐交付顺序）",
        "route": "nn",
        "pack": "zone",
        "is_baseline": False,
    },
    {
        "key": "C",
        "name": "仅优化路径（节约算法+2-opt + 按品类集中装车）",
        "route": "savings",
        "pack": "ff",
        "is_baseline": False,
    },
    {
        "key": "D",
        "name": "本文方案（节约算法+2-opt + 分区装载对齐交付顺序）",
        "route": "savings",
        "pack": "zone",
        "is_baseline": False,
    },
)


class ConfigError(Exception):
    """配置文件缺失/不合法时抛出，供命令行给出人话提示而不是堆栈。"""


def load_config(path: str) -> dict:
    """读取 JSON 配置。

    使用 ``utf-8-sig`` 打开：Windows 记事本、PowerShell ``-Encoding UTF8``、
    Excel 导出的 JSON 都带 UTF-8 BOM，若按普通 ``utf-8`` 解析会直接抛
    ``Unexpected UTF-8 BOM`` —— 而这恰恰是评审在 Windows 上手改配置最常走的路径。
    """

    with open(path, encoding="utf-8-sig") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"配置文件 {path} 不是合法 JSON：{exc}\n"
                f"    常见原因：多余逗号、中文引号、注释。可对照 configs/default.json 检查。"
            ) from exc


def build_context(
    config: dict, data_dir: str | None = None
) -> Tuple[Instance, Network, dict, dict]:
    """构建算例、路网与两张矩阵（行驶时间 / 里程）。"""

    if data_dir:
        from .dataio import load_instance

        inst = load_instance(data_dir)
    else:
        inst = build_instance(config)
    net_cfg = config.get("network", {})
    net = Network(
        detour_factor=float(net_cfg.get("detour_factor", 1.25)),
        speed_kmh=float(net_cfg.get("speed_kmh", 30.0)),
    )
    stores_map = {s.store_id: s for s in inst.stores}
    matrix = net.matrix(inst.depot, stores_map)
    dist = net.distance_matrix_km(inst.depot, stores_map)
    return inst, net, matrix, dist


def route_sets(
    inst: Instance, matrix: dict, dist: dict, limits: PlanningLimits
) -> Dict[str, List[List[str]]]:
    """基线排线方案（人工经验：最近邻贪心串点）。"""

    return {"nn": nearest_neighbor_routes(inst, matrix, dist, limits=limits)}


def plan_cost(inst: Instance, dist: dict, loads: Sequence[VehicleLoad]) -> float:
    total = 0.0
    for load in loads:
        d = route_distance_km(load.route, inst, dist)
        total += load.vehicle.vtype.fixed_cost + load.vehicle.vtype.cost_per_km * d
    return round(total, 2)


#: 路径批量搜索的候选容积占比：调小会得到更多更小的路径，配合小车型可能总成本更低
ROUTE_CAP_RATIOS = (0.90, 0.65, 0.45)


def plan_with_savings(
    inst: Instance,
    matrix: dict,
    dist: dict,
    options: PackOptions,
    blocks: Dict[str, list],
    limits: PlanningLimits,
    verbose: bool = False,
) -> dict:
    """排线 + 配载 + 车型的**联合择优**：搜索路径批量，取总成本最低的一组。

    单条路径能装多大，取决于车型成本结构与装载器实际能装到多满。
    把批量当成一个可搜索的决策变量，比固定“装满最大车”更接近真实最优。
    """

    best: dict | None = None
    seen: set = set()
    for ratio in ROUTE_CAP_RATIOS:
        routes = [
            two_opt_route(r, inst, matrix, limits=limits)
            for r in savings_routes(
                inst, matrix, volume_cap_ratio=ratio, limits=limits
            )
        ]
        signature = tuple(tuple(r) for r in routes)
        if signature in seen:
            # 该批量下路径集合与已评估过的完全相同（通常是时间窗而非容积在起作用），跳过重复装车
            continue
        seen.add(signature)
        loads, unassigned = assign_and_pack(inst, routes, options, blocks)
        cost = plan_cost(inst, dist, loads)
        if verbose:
            print(
                f"    [批量搜索] 容积占比 {ratio:.2f} → {len(routes)} 条路径 / "
                f"{len(loads)} 车 / 成本 {cost}"
            )
        if best is None or cost < best["cost"]:
            best = {
                "ratio": ratio,
                "routes": routes,
                "loads": loads,
                "unassigned": unassigned,
                "cost": cost,
            }
    assert best is not None
    return best


def assign_and_pack(
    inst: Instance,
    routes: Sequence[Sequence[str]],
    options: PackOptions,
    blocks: Dict[str, list],
    physical_volume_ratio: float = 0.98,
) -> Tuple[List[VehicleLoad], int]:
    """配载感知的车辆指派：**先试装，再定车**。

    按“单位成本从低到高”依次尝试可用车型，用真正的 3D 配载器验证是否装得下；
    第一个装得下的车型即为该路径的指派结果。这样排线与装车不再割裂：
    装车效率高，就能用更小、更便宜的车，直接反映到成本指标上。
    """

    pool: Dict[str, List[Vehicle]] = {}
    for v in inst.vehicles:
        pool.setdefault(v.vtype.type_id, []).append(v)
    types = sorted(
        {v.vtype for v in inst.vehicles},
        key=lambda t: (t.fixed_cost + t.cost_per_km, t.volume_m3),
    )

    loads: List[VehicleLoad] = []
    unassigned: List[List[str]] = []
    for route in sorted(routes, key=lambda r: -store_load(inst, r)[0]):
        vol, kg = store_load(inst, route)
        trial: VehicleLoad | None = None
        for t in types:
            if not pool.get(t.type_id):
                continue
            # 物理上不可能装下，直接跳过，避免无谓的装车尝试
            if (
                vol > t.volume_m3 * physical_volume_ratio + 1e-9
                or kg > t.max_payload_kg + 1e-9
            ):
                continue
            candidate = pack_route(inst, route, pool[t.type_id][0], blocks, options)
            if not candidate.unplaced:
                trial = candidate
                break
        if trial is None:
            avail = [v for lst in pool.values() for v in lst]
            if not avail:
                unassigned.append(list(route))
                continue
            v = max(avail, key=lambda x: (x.volume_m3, x.max_payload_kg))
            trial = pack_route(inst, route, v, blocks, options)
        pool[trial.vehicle.vtype.type_id].remove(trial.vehicle)
        loads.append(trial)

    return loads, len(unassigned)


def run_benchmark(
    config: dict,
    data_dir: str | None = None,
    verbose: bool = True,
) -> dict:
    """跑完整个对照实验，返回可直接序列化的结果。"""

    t0 = time.perf_counter()
    inst, net, matrix, dist = build_context(config, data_dir=data_dir)
    limits = PlanningLimits.from_config(config)
    summary = describe(inst)

    if verbose:
        print(
            f"[算例] {summary['stores']} 门店 / {summary['order_lines']} 订单行 / "
            f"{summary['demand_units']} 箱 / {summary['demand_volume_m3']} m³ / "
            f"{summary['demand_weight_kg']} kg / 密度 "
            f"{summary['cargo_density_kg_per_m3']} kg/m³ / 车辆数下界 "
            f"{summary['vehicles_lower_bound']}"
        )

    routes = route_sets(inst, matrix, dist, limits)
    if verbose:
        print(f"[排线] 人工经验基线（最近邻）{len(routes['nn'])} 条路径")

    pack_opts = PackOptions.from_config(config, align_to_delivery_order=True)
    blocks = build_blocks(inst, pack_opts)

    variants: List[dict] = []
    plans: Dict[str, Plan] = {}
    metrics_by_key: Dict[str, PlanMetrics] = {}

    for defn in VARIANT_DEFS:
        t_start = time.perf_counter()
        opts = PackOptions.from_config(
            config, align_to_delivery_order=(defn["pack"] == "zone")
        )
        if defn["route"] == "savings":
            picked = plan_with_savings(
                inst, matrix, dist, opts, blocks, limits, verbose=verbose
            )
            loads, unassigned = picked["loads"], picked["unassigned"]
        else:
            loads, unassigned = assign_and_pack(inst, routes["nn"], opts, blocks)
        elapsed = time.perf_counter() - t_start

        plan = Plan(
            name=defn["name"],
            loads=loads,
            route_method=defn["route"],
            pack_method=defn["pack"],
        )
        m = evaluate(plan, inst, matrix, dist, limits)
        m.solve_seconds = round(elapsed, 3)
        m.unassigned_routes = unassigned
        if unassigned:
            m.feasible = False
        plans[defn["key"]] = plan
        metrics_by_key[defn["key"]] = m
        variants.append(
            {
                "key": defn["key"],
                "name": defn["name"],
                "is_baseline": defn["is_baseline"],
                "metrics": m.as_dict(),
            }
        )
        if verbose:
            print(
                f"[{defn['key']}] 车 {m.vehicles_used} / 里程 {m.total_distance_km} km / "
                f"成本 {m.total_cost} / 容积率 {m.volume_utilization:.1%} / "
                f"载重率 {m.payload_utilization:.1%} / 准时率 {m.on_time_rate:.1%} / "
                f"倒腾 {m.reload_ops} / 未装载 {m.unplaced_blocks} / "
                f"可执行 {'是' if m.executable else '否'} / 耗时 {m.solve_seconds}s"
            )

    baseline = metrics_by_key["A"]
    for v in variants:
        v["improvement_vs_baseline"] = (
            {} if v["key"] == "A" else compare(baseline, metrics_by_key[v["key"]])
        )

    total = time.perf_counter() - t0
    if verbose:
        print(f"[完成] 全流程 {total:.2f}s")

    return {
        "config": config,
        "instance_summary": summary,
        "variants": variants,
        "total_seconds": round(total, 3),
        "plans": plans,
        "metrics": metrics_by_key,
        "instance": inst,
    }
