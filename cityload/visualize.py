"""3D 装卸可视化：把配载结果渲染成自包含 HTML 的等轴测装卸示意图。

对应赛题重点推荐场景的第三项能力 —— “智能装卸指导”：
现场人员需要看到 3D 货物摆放示意与分步骤装卸指引，而不是一张表格。

生成的 HTML **不依赖任何外部资源**（无 CDN、无图片、无字体文件），
双击即可离线打开，方便评审直接查看与随仓库分发。
"""

from __future__ import annotations

import html
import math
from typing import Dict, List, Sequence, Tuple

from .domain import Instance, Plan, Placement, VehicleLoad
from .packing import allocate_space, normalize_blocks_for_height

_ZONE_SHORT = {
    "COLD": "冷链区",
    "ISOLATED": "日化隔离区",
    "AMBIENT": "常规药品区",
    "PRECISE": "精密器械区",
}


def _zone_short(zone: str) -> str:
    return _ZONE_SHORT.get(zone, zone)

COS30 = math.cos(math.radians(30.0))
SIN30 = math.sin(math.radians(30.0))

#: 门店配色（15 色循环），保证同一门店的箱体颜色一致
PALETTE = (
    "#ff5c18", "#2f9e44", "#1971c2", "#ae3ec9", "#e8590c", "#0ca678",
    "#f08c00", "#4c6ef5", "#d6336c", "#37b24d", "#1098ad", "#7048e8",
    "#f59f00", "#e64980", "#20c997",
)


def _iso(x: float, y: float, z: float, scale: float) -> Tuple[float, float]:
    """车厢坐标 (x 车长, y 车宽, z 高度, mm) → 屏幕坐标。"""

    return ((x - y) * COS30 * scale, ((x + y) * SIN30 - z) * scale)


def _poly(points: Sequence[Tuple[float, float]]) -> str:
    return " ".join(f"{px:.1f},{py:.1f}" for px, py in points)


def _box_svg(p: Placement, scale: float, ox: float, oy: float, color: str, dim: str) -> str:
    """画一个箱体的三个可见面：顶面、右侧面（x=x1）、左侧面（y=y1）。"""

    def pt(x: float, y: float, z: float) -> Tuple[float, float]:
        sx, sy = _iso(x, y, z, scale)
        return ox + sx, oy + sy

    x0, y0, z0 = p.x_mm, p.y_mm, p.z_mm
    x1, y1, z1 = p.x1_mm, p.y1_mm, p.z1_mm

    top = [pt(x0, y0, z1), pt(x1, y0, z1), pt(x1, y1, z1), pt(x0, y1, z1)]
    right = [pt(x1, y0, z0), pt(x1, y1, z0), pt(x1, y1, z1), pt(x1, y0, z1)]
    left = [pt(x0, y1, z0), pt(x1, y1, z0), pt(x1, y1, z1), pt(x0, y1, z1)]

    title = (
        f"{p.store_id} / {p.sku_id} × {p.block.units} 箱 | "
        f"位置 x={x0}-{x1} y={y0}-{y1} z={z0}-{z1}"
    )
    return (
        f'<g class="box" style="--c:{color}" opacity="{dim}">'
        f"<title>{html.escape(title)}</title>"
        f'<polygon points="{_poly(top)}" fill="var(--c)" fill-opacity="0.95" stroke="#0b0e13" stroke-width="0.6"/>'
        f'<polygon points="{_poly(right)}" fill="var(--c)" fill-opacity="0.68" stroke="#0b0e13" stroke-width="0.6"/>'
        f'<polygon points="{_poly(left)}" fill="var(--c)" fill-opacity="0.48" stroke="#0b0e13" stroke-width="0.6"/>'
        f"</g>"
    )


def render_vehicle(load: VehicleLoad, store_order: Dict[str, int]) -> str:
    v = load.vehicle
    scale = min(0.16, 900.0 / max(v.inner_length_mm + v.inner_width_mm, 1))
    # 计算画布范围
    corners = [
        _iso(0, 0, 0, scale),
        _iso(v.inner_length_mm, 0, 0, scale),
        _iso(0, v.inner_width_mm, 0, scale),
        _iso(v.inner_length_mm, v.inner_width_mm, 0, scale),
        _iso(0, 0, v.inner_height_mm, scale),
        _iso(v.inner_length_mm, v.inner_width_mm, v.inner_height_mm, scale),
    ]
    min_x = min(c[0] for c in corners) - 20
    min_y = min(c[1] for c in corners) - 20
    max_x = max(c[0] for c in corners) + 20
    max_y = max(c[1] for c in corners) + 20
    ox, oy = -min_x, -min_y
    width, height = max_x - min_x, max_y - min_y

    # 车厢线框（画在最底层）
    def pt(x: float, y: float, z: float) -> Tuple[float, float]:
        sx, sy = _iso(x, y, z, scale)
        return ox + sx, oy + sy

    L, W, H = v.inner_length_mm, v.inner_width_mm, v.inner_height_mm
    shell = (
        f'<polygon points="{_poly([pt(0, 0, 0), pt(L, 0, 0), pt(L, W, 0), pt(0, W, 0)])}" '
        f'fill="#111820" stroke="#3b4a52" stroke-width="1"/>'
        f'<polyline points="{_poly([pt(0, W, 0), pt(0, W, H), pt(L, W, H), pt(L, 0, H), pt(L, W, H)])}" '
        f'fill="none" stroke="#2a3644" stroke-width="1" stroke-dasharray="5 4"/>'
    )

    boxes = []
    for p in sorted(load.placements, key=lambda q: (q.x_mm + q.y_mm + q.z_mm)):
        idx = store_order.get(p.store_id, 0)
        boxes.append(_box_svg(p, scale, ox, oy, PALETTE[idx % len(PALETTE)], "1"))

    # 功能区边界（用竖向虚线标在 x-y 地板平面上，便于现场识别分区）
    zone_marks = ""
    if load.placements:
        usable_h = int(v.inner_height_mm * 0.95)
        trimmed: Dict[str, List] = {}
        for p in load.placements:
            trimmed.setdefault(p.store_id, []).append(p.block)
        segs = allocate_space(
            load.route,
            {k: normalize_blocks_for_height(b, usable_h) for k, b in trimmed.items()},
            v.inner_length_mm,
        )
        marks = []
        for zone, (x0, x1) in segs.items():
            for x in (x0, x1):
                marks.append(
                    f'<line x1="{pt(x,0,0)[0]:.1f}" y1="{pt(x,0,0)[1]:.1f}" '
                    f'x2="{pt(x,W,0)[0]:.1f}" y2="{pt(x,W,0)[1]:.1f}" '
                    f'stroke="#4d5c6e" stroke-width="1.2" stroke-dasharray="6 5"/>'
                )
            cx, cy = pt((x0 + x1) / 2, W, 0)
            marks.append(
                f'<text x="{cx:.1f}" y="{cy - 6:.1f}" fill="#7f8fa3" font-size="11" '
                f'text-anchor="middle">{html.escape(_zone_short(zone))}</text>'
            )
        zone_marks = "".join(marks)

    # 每站装卸指引：按功能区分别给出区间（不能把各功能区取并集，否则会误导现场）
    from .categories import zone_label

    steps: List[str] = []
    for i, sid in enumerate(load.route, start=1):
        mine = [p for p in load.placements if p.store_id == sid]
        if not mine:
            continue
        by_zone: Dict[str, List] = {}
        for p in mine:
            by_zone.setdefault(p.block.functional_zone, []).append(p)
        parts: List[str] = []
        for zone, ps in by_zone.items():
            x_lo = min(q.x_mm for q in ps)
            x_hi = max(q.x1_mm for q in ps)
            units = sum(q.block.units for q in ps)
            parts.append(
                f"{zone_label(zone)} x={x_lo}–{x_hi} mm（{len(ps)} 垛 / {units} 箱）"
            )
        total_units = sum(p.block.units for p in mine)
        steps.append(
            f"<li><b>第 {i} 站 {html.escape(sid)}</b>：共 {len(mine)} 个整箱垛 / "
            f"{total_units} 箱，分布在 {len(by_zone)} 个功能区 —— "
            + "；".join(parts)
            + "。各功能区内该站货物连续成段，卸载时无需挪动其他门店货物。</li>"
        )

    color_chips = "".join(
        f'<span class="chip"><i style="background:{PALETTE[store_order[sid] % len(PALETTE)]}"></i>'
        f"第 {i} 站 · {html.escape(sid)}</span>"
        for i, sid in enumerate(load.route, start=1)
        if sid in store_order
    )

    return f"""
<section class="card">
  <header>
    <h3>{html.escape(load.vehicle.vehicle_id)} · {html.escape(v.vtype.name)}</h3>
    <div class="kpis">
      <span>门店 {len(load.route)}</span>
      <span>整箱垛 {len(load.placements)}</span>
      <span>容积率 {load.volume_utilization:.1%}</span>
      <span>载重率 {load.payload_utilization:.1%}</span>
      <span>最高码放 {load.max_stack_height_mm} mm</span>
      <span class="{'ok' if load.is_feasible else 'bad'}">
        {'配载可行' if load.is_feasible else '存在未装载'}
      </span>
    </div>
  </header>
  <div class="legend">{color_chips}</div>
  <svg viewBox="0 0 {width:.0f} {height:.0f}" width="100%" role="img"
       aria-label="{html.escape(load.vehicle.vehicle_id)} 装载等轴测图">
    <g>{shell}{zone_marks}{''.join(boxes)}</g>
  </svg>
  <ol class="steps">{''.join(steps)}</ol>
</section>
"""


STYLE = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;padding:32px;background:#07090e;color:#e8edf4;
     font:14px/1.7 -apple-system,"Segoe UI","Noto Sans SC","Microsoft YaHei",sans-serif}
h1{font-size:22px;margin:0 0 6px}
h3{font-size:15px;margin:0}
.sub{color:#8b98a9;margin:0 0 24px}
.card{background:#0d131b;border:1px solid #1c2632;border-radius:14px;padding:18px;margin-bottom:22px}
.card header{display:flex;flex-wrap:wrap;gap:12px;align-items:baseline;justify-content:space-between}
.kpis{display:flex;flex-wrap:wrap;gap:10px;font-size:12px;color:#9fb0c4}
.kpis span{background:#131b25;border:1px solid #1f2b39;border-radius:999px;padding:2px 10px}
.kpis .ok{color:#51cf66;border-color:#1f3a26}
.kpis .bad{color:#ff6b6b;border-color:#3a1f1f}
.legend{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.chip{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#b9c6d6;
      background:#111925;border:1px solid #1e2a38;border-radius:999px;padding:2px 10px}
.chip i{width:9px;height:9px;border-radius:3px;display:inline-block}
svg{display:block;background:#080b11;border:1px solid #16202c;border-radius:10px}
.steps{color:#9fb0c4;font-size:13px;padding-left:20px}
.steps b{color:#e8edf4}
footer{color:#5f6b7a;font-size:12px;margin-top:26px}
"""


def render_plan_html(plan: Plan, inst: Instance, title: str = "3D 装卸示意图") -> str:
    store_order = {s.store_id: i for i, s in enumerate(inst.stores)}
    from .metrics import reload_ops

    total_reload = sum(reload_ops(l) for l in plan.loads)
    cards = "".join(render_vehicle(l, store_order) for l in plan.loads)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{STYLE}</style></head>
<body>
<h1>{html.escape(title)}</h1>
<p class="sub">{html.escape(plan.name)} &nbsp;·&nbsp; 车辆 {len(plan.loads)} 台
&nbsp;·&nbsp; 门店 {plan.stores_served} 家
&nbsp;·&nbsp; 重复搬运段数合计 {total_reload}
&nbsp;·&nbsp; 全部数据为合成模拟数据，不含任何真实客户信息</p>
{cards}
<footer>传化 × 动势 · AI重构产业·架构师大赛 ｜ AI＋物流赛道 ｜ 城市配送智能配载
｜ 本页由 cityload.visualize 自动生成，无外部依赖，可离线打开</footer>
</body></html>
"""
