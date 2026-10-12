"""结果输出：Markdown 报告、JSON 结果与 3D 装卸示意图。"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List

from .domain import Instance, Plan
from .visualize import render_plan_html

METRIC_LABELS = (
    ("vehicles_used", "车辆数（台）", "{:.0f}"),
    ("total_distance_km", "总里程（km）", "{:.1f}"),
    ("total_cost", "总成本（元）", "{:.0f}"),
    ("volume_utilization", "容积利用率", "{:.1%}"),
    ("payload_utilization", "载重利用率", "{:.1%}"),
    ("on_time_rate", "时间窗达成率", "{:.1%}"),
    ("reload_ops", "重复搬运段数", "{:.0f}"),
    ("makespan_min", "最长线路用时（min）", "{:.0f}"),
    ("max_route_wait_min", "单车最长等待（min）", "{:.0f}"),
    ("unplaced_blocks", "未装载整箱垛", "{:.0f}"),
    ("relaxed_loads", "空间分配降级车次", "{:.0f}"),
    ("unallocated_loads", "未做空间分配车次", "{:.0f}"),
    ("zone_violations", "越区箱垛数", "{:.0f}"),
    ("solve_seconds", "求解耗时（s）", "{:.2f}"),
)


def _table(header: List[str], rows: List[List[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def build_report(result: dict) -> str:
    summary = result["instance_summary"]
    variants = result["variants"]
    lines: List[str] = []
    a = lines.append

    a("# 城市配送智能配载与调度 —— 实验结果")
    a("")
    a(f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}　|　"
      f"全流程耗时 {result['total_seconds']} s　|　"
      f"随机种子 {result['config'].get('seed')}")
    a(">")
    a("> **本报告全部基于程序合成的模拟数据，不含传化或任何客户的真实业务数据。**")
    a("")

    a("## 一、算例规模")
    a("")
    a(_table(
        ["项目", "数值"],
        [
            ["门店数", str(summary["stores"])],
            ["SKU 数", str(summary["skus"])],
            ["订单行数", str(summary["order_lines"])],
            ["总箱数", str(summary["demand_units"])],
            ["需求体积", f"{summary['demand_volume_m3']} m³"],
            ["需求重量", f"{summary['demand_weight_kg']} kg"],
            ["货物平均密度", f"{summary['cargo_density_kg_per_m3']} kg/m³"],
            ["车队规模", f"{summary['fleet_vehicles']} 台"],
            ["车辆数下界（仅按总货量，未计时间窗）", str(summary["vehicles_lower_bound"])],
        ],
    ))
    a("")

    a("## 二、对照实验结果")
    a("")
    header = ["指标"] + [f"{v['key']}" for v in variants]
    rows: List[List[str]] = []
    for key, label, fmt in METRIC_LABELS:
        row = [label]
        for v in variants:
            row.append(fmt.format(v["metrics"][key]))
        rows.append(row)
    a(_table(header, rows))
    a("")
    a("方案代号：")
    for v in variants:
        a(f"- **{v['key']}** —— {v['name']}")
    a("")

    a("## 三、相对基线的改善幅度")
    a("")
    header = ["指标（正数=更优）"] + [v["key"] for v in variants if v["key"] != "A"]
    rows = []
    for key in ("total_cost_%", "vehicles_%", "distance_%", "volume_utilization_%",
                "payload_utilization_%", "on_time_rate_%", "reload_ops_%"):
        row = [key.replace("_%", " (%)")]
        for v in variants:
            if v["key"] == "A":
                continue
            val = v["improvement_vs_baseline"].get(key, 0.0)
            row.append(f"{val:+.1f}%")
        rows.append(row)
    a(_table(header, rows))
    a("")

    a("## 四、结论")
    a("")
    for text in _conclusions(variants):
        a(f"- {text}")
    a("")
    a("### 逐方案改善明细（相对基线 A，正数=更优）")
    a("")
    for line in _phrase(variants):
        a(line)
    a("")
    a("> 说明：**重复搬运段数**是一个**指数**而非物理搬运次数 —— 它按功能区统计"
      "「卸货序列里同一门店被切成了几段」，分段越少，现场越不需要把别人的货挪开再放回。")
    a(">")
    a("> `relaxed_loads` / `unallocated_loads` 记录空间分配的**降级车次**"
      "（等级 2 = 功能区仅作偏好可越区；等级 3 = 未做空间分配）；"
      "`zone_violations` 记录越出功能区边界的箱垛数。这些指标用来量化"
      "「为了装下而牺牲了多少品类合规性」，不做隐藏。")
    a("")
    a("## 五、可复现方式")
    a("")
    a("```bash")
    a("python -m cityload.cli all        # 生成数据 → 对照实验 → 报告 → 3D 示意图")
    a("python -m unittest discover -s tests -v")
    a("```")
    a("")
    return "\n".join(lines)


IMPROVEMENT_LABELS = (
    ("distance_%", "总里程"),
    ("total_cost_%", "总成本"),
    ("vehicles_%", "车辆数"),
    ("volume_utilization_%", "容积利用率"),
    ("payload_utilization_%", "载重利用率"),
    ("reload_ops_%", "重复搬运段数"),
)


def _phrase(variants: List[dict]) -> List[str]:
    """把改善幅度写成“方向 + 数值 + 好坏”的无歧义句子。"""

    out: List[str] = []
    for v in variants:
        if v["key"] == "A":
            continue
        parts = []
        for key, label in IMPROVEMENT_LABELS:
            val = v["improvement_vs_baseline"].get(key, 0.0)
            verdict = "更优" if val > 0 else ("持平" if abs(val) < 0.05 else "更差")
            parts.append(f"{label} {val:+.1f}%（{verdict}）")
        out.append(f"- **{v['key']} · {v['name']}** —— " + "；".join(parts) + "。")
    return out


def _dir_phrase(key: str, val: float) -> str:
    """把「正数=更优」的改善幅度写成不含歧义的方向描述。"""

    label = dict(IMPROVEMENT_LABELS)[key]
    worse_word = {
        "distance_%": "增加",
        "total_cost_%": "上升",
        "vehicles_%": "增加",
        "volume_utilization_%": "下降",
        "payload_utilization_%": "下降",
        "reload_ops_%": "增加",
    }[key]
    better_word = {
        "distance_%": "缩短",
        "total_cost_%": "下降",
        "vehicles_%": "减少",
        "volume_utilization_%": "提升",
        "payload_utilization_%": "提升",
        "reload_ops_%": "减少",
    }[key]
    if abs(val) < 0.05:
        return f"{label}持平"
    return f"{label}{better_word if val > 0 else worse_word} {abs(val):.1f}%"


def _conclusions(variants: List[dict]) -> List[str]:
    by = {v["key"]: v for v in variants}
    out: List[str] = []
    if "C" in by and "A" in by:
        d = by["C"]["improvement_vs_baseline"]
        out.append(
            "**路径优化（A→C）：** "
            + "、".join(_dir_phrase(k, d[k]) for k, _ in IMPROVEMENT_LABELS)
            + "。节约算法把货量拆到更多条更短的路径上，单车里程下降，代价是用车台数上升。"
        )
    if "B" in by and "A" in by:
        d = by["B"]["improvement_vs_baseline"]
        out.append(
            "**空间分配（A→B）：** "
            + "、".join(_dir_phrase(k, d[k]) for k, _ in IMPROVEMENT_LABELS)
            + "。"
        )
        if d["reload_ops_%"] > 5:
            out.append(
                "空间分配确实降低了重复搬运段数；同时容积利用率的变化反映了"
                "「品类隔离 / 交付顺序分段」与「装载率」之间的取舍。"
            )
        else:
            out.append(
                "**注意：空间分配在本算例上几乎没有产生效果。** 原因是算法按设计降级了："
                "严格品类功能区在装载偏紧时地板面积不足，`allocation_tier` 落到 3"
                "（未做空间分配），于是 B 退化成 A。降级比例见上方"
                "`relaxed_loads` / `unallocated_loads` 指标与 `docs/06` §1 的成因分析。"
            )
    if "D" in by and "A" in by:
        d = by["D"]["improvement_vs_baseline"]
        out.append(
            "**综合方案 D：** "
            + "、".join(_dir_phrase(k, d[k]) for k, _ in IMPROVEMENT_LABELS)
            + "。"
        )
        if d["total_cost_%"] <= 0:
            out.append(
                "**综合方案在总成本上尚未优于人工经验基线。** 已定位到两个原因："
                "① 路径更短更碎导致用车台数上升，固定成本抵消了里程收益；"
                "② 品类功能区在装载偏紧时降级，装卸效率上的收益没有兑现。"
                "改进路线见 `docs/06-当前限制与落地路径.md`。"
            )
        else:
            out.append("综合方案在里程、成本与重复搬运三个维度上同时优于人工经验基线。")
    out.append(
        "A/B 与 C/D 各自共用同一组路径、同一套装车器与同一套排序规则，"
        "差异可以干净地归因到「是否优化路径」与「是否启用空间分配」这两个因素上。"
    )
    return out


def write_outputs(result: dict, out_dir: str, viz_plan_key: str = "D") -> Dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, "benchmark.md")
    json_path = os.path.join(out_dir, "benchmark.json")
    viz_path = os.path.join(out_dir, "loadplan.html")
    data_path = os.path.join(out_dir, "loadplan.json")

    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(build_report(result))

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_seconds": result["total_seconds"],
        "instance_summary": result["instance_summary"],
        "variants": result["variants"],
    }
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    inst: Instance = result["instance"]
    plans: Dict[str, Plan] = result["plans"]
    plan = plans.get(viz_plan_key) or next(iter(plans.values()))
    with open(viz_path, "w", encoding="utf-8") as fh:
        fh.write(render_plan_html(plan, inst, title=f"{plan.name} —— 3D 装卸示意图"))

    load_json = {
        "plan": plan.name,
        "route_method": plan.route_method,
        "pack_method": plan.pack_method,
        "vehicles": [
            {
                "vehicle_id": l.vehicle.vehicle_id,
                "vehicle_type": l.vehicle.vtype.name,
                "route": list(l.route),
                "volume_utilization": round(l.volume_utilization, 4),
                "payload_utilization": round(l.payload_utilization, 4),
                "placements": [p.as_dict() for p in l.placements],
                "unplaced": [b.block_id for b in l.unplaced],
            }
            for l in plan.loads
        ],
    }
    with open(data_path, "w", encoding="utf-8") as fh:
        json.dump(load_json, fh, ensure_ascii=False, indent=2)

    return {
        "report": report_path,
        "json": json_path,
        "viz": viz_path,
        "loadplan_json": data_path,
    }
