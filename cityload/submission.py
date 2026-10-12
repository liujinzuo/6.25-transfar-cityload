"""生成**单文件提交说明页**（SUBMISSION.html）。

用途：赛事渠道往往只需要「一个可查看的入口 + 一个仓库链接」。
本模块把实验结果、3D 示意图、运行方式、合规声明与提交清单
全部内联进**一个 HTML 文件**（图片以 base64 内嵌，无任何外部依赖），
双击即可查看，也可直接作为附件上传或发给评委。

用法::

    python -m cityload.cli submission
"""

from __future__ import annotations

import base64
import html
import json
import os
import time
from typing import Dict, List, Optional

#: 团队信息。联系方式（手机号等）**一律不写入仓库** —— 它属于赛事登记表的内容，
#: 写进代码或文档，只会在仓库被转发 / 转公开时造成不必要的隐私扩散。
#: 如需在提交说明页展示联系方式，请在这里显式补充 contact 字段。
TEAM = {
    "name": "6.25",
    "mode": "个人参赛",
    "leader": "刘劲作",
    "contact": None,
}

STYLE = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;padding:0;background:#07090e;color:#e8edf4;
     font:15px/1.75 -apple-system,"Segoe UI","Noto Sans SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:48px 28px 80px}
header.top{border-bottom:1px solid #1c2632;padding-bottom:22px;margin-bottom:34px}
.kicker{color:#ff5c18;font-size:12px;letter-spacing:.16em;text-transform:uppercase;margin:0 0 10px}
h1{font-size:29px;line-height:1.35;margin:0 0 12px}
h2{font-size:19px;margin:44px 0 14px;padding-left:11px;border-left:3px solid #ff5c18}
h3{font-size:15px;margin:24px 0 8px;color:#c8d4e2}
p{margin:10px 0}
.sub{color:#8b98a9}
.lead{font-size:16px;color:#c8d4e2;background:#0d131b;border:1px solid #1c2632;
      border-left:3px solid #ff5c18;border-radius:0 10px 10px 0;padding:14px 18px;margin:18px 0}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:13.5px}
th,td{border:1px solid #1e2a38;padding:7px 11px;text-align:left}
th{background:#131b25;color:#9fb0c4;font-weight:600}
td.num{text-align:right;font-variant-numeric:tabular-nums}
tr.hl td{background:#121a24}
code{background:#111925;border:1px solid #1e2a38;border-radius:5px;padding:1px 6px;font-size:13px}
pre{background:#0b1119;border:1px solid #1e2a38;border-radius:10px;padding:15px 18px;
    overflow-x:auto;font-size:13px;line-height:1.7}
ul,ol{margin:10px 0;padding-left:22px}
li{margin:5px 0}
.ok{color:#51cf66}.bad{color:#ff6b6b}.warn{color:#fcc419}
.box{background:#0d131b;border:1px solid #1c2632;border-radius:12px;padding:16px 20px;margin:16px 0}
.todo{border-color:#3a2f1f;background:#14100a}
.todo h3{color:#fcc419;margin-top:0}
img.shot{width:100%;border:1px solid #1c2632;border-radius:12px;display:block;margin:16px 0}
footer{margin-top:56px;padding-top:20px;border-top:1px solid #1c2632;color:#5f6b7a;font-size:12.5px}
a{color:#4dabf7}
"""


def _fmt(v: float, spec: str) -> str:
    try:
        return spec.format(v)
    except Exception:
        return str(v)
    return str(v)


def _metrics_table(title: str, variants: List[dict], rows: List[tuple], note: str = "") -> str:
    head = "".join(f"<th>{html.escape(v['key'])}</th>" for v in variants)
    body = []
    for key, label, spec, highlight in rows:
        cells = []
        for v in variants:
            val = v["metrics"].get(key, 0)
            txt = _fmt(val, spec)
            cells.append(f'<td class="num">{html.escape(txt)}</td>')
        cls = ' class="hl"' if highlight else ""
        body.append(f"<tr{cls}><td>{html.escape(label)}</td>{''.join(cells)}</tr>")
    return (
        f"<h3>{html.escape(title)}</h3>"
        f"<table><thead><tr><th>指标</th>{head}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
        + (f'<p class="sub">{note}</p>' if note else "")
    )


RESULT_ROWS = [
    ("vehicles_used", "车辆数（台）", "{:.0f}", False),
    ("total_distance_km", "总里程（km）", "{:.1f}", False),
    ("total_cost", "总成本（元）", "{:.0f}", False),
    ("volume_utilization", "容积利用率", "{:.1%}", False),
    ("payload_utilization", "载重利用率", "{:.1%}", False),
    ("on_time_rate", "时间窗达成率", "{:.1%}", False),
    ("reload_ops", "重复搬运段数", "{:.0f}", True),
    ("relaxed_loads", "空间分配降级车次", "{:.0f}", True),
    ("zone_violations", "越区箱垛数", "{:.0f}", False),
    ("unplaced_blocks", "未装载整箱垛", "{:.0f}", False),
    ("solve_seconds", "求解耗时（s）", "{:.2f}", False),
]


def _load(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _embed_png(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    return f'<img class="shot" alt="3D 装卸示意图" src="data:image/png;base64,{b64}">'


def render_submission_html(
    reports_dir: str = "reports",
    compact_dir: str = os.path.join("reports", "compact"),
    version: str = "0.1.0",
) -> str:
    main = _load(os.path.join(reports_dir, "benchmark.json"))
    compact = _load(os.path.join(compact_dir, "benchmark.json"))
    shot = _embed_png(os.path.join(compact_dir, "preview.png")) or _embed_png(
        os.path.join(reports_dir, "preview.png")
    )

    parts: List[str] = []
    a = parts.append
    a("<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>")
    a("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    a("<title>提交说明 · 城市配送智能配载与调度</title>")
    a(f"<style>{STYLE}</style></head><body><div class='wrap'>")

    a("<header class='top'>")
    a("<p class='kicker'>传化 × 动势 · AI重构产业·架构师大赛</p>")
    a("<h1>城市配送智能配载与调度<br><span class='sub'>AI＋物流赛道 ｜ 重点推荐场景：城市配送智能配载</span></h1>")
    contact = TEAM.get("contact")
    team_line = (
        f"<p class='sub'><b>团队 {html.escape(TEAM['name'])}</b>"
        f"（{html.escape(TEAM['mode'])}）｜ 队长 {html.escape(TEAM['leader'])}"
        + (f" ｜ 联系方式 {html.escape(contact)}" if contact else "")
        + "</p>"
    )
    a(team_line)
    a(f"<p class='sub'>提交版本 v{html.escape(version)} ｜ 生成时间 {time.strftime('%Y-%m-%d %H:%M')} "
      "｜ 核心代码仅依赖 Python 标准库（零第三方依赖）</p>")
    a("</header>")

    a("<div class='lead'><b>一句话：</b>把「一车货怎么摆」拆成两个可解释、可核查的问题 —— "
      "<b>①空间怎么分</b>（按品类合规把车厢切成连续功能区，分配长度由试装结果闭环回检确定）；"
      "<b>②不同种类的货怎么摆</b>（由显式品类装载规则表驱动落位：禁压上限、品类不相容、"
      "重货置底、易碎置顶、底面支撑率），并在每个功能区内按交付顺序逆序逐店码放，做到卸货不倒腾。</div>")

    a("<h2>一、核心设计</h2>")
    a("<h3>一级空间分配：品类功能区（硬隔离）</h3>")
    a("<table><thead><tr><th>位置</th><th>功能区</th><th>承接品类</th><th>分配依据</th></tr></thead><tbody>"
      "<tr><td>车头侧</td><td>精密器械区</td><td>家用医疗器械</td><td>禁压、需稳固，放最里侧</td></tr>"
      "<tr><td>中段</td><td>常规药品区</td><td>口服药品 / 外用药品 / 保健品</td><td>主力货量，居中</td></tr>"
      "<tr><td>中段偏门</td><td>日化隔离区</td><td>医用日化耗材</td><td>独立成段，避免与药品交叉污染</td></tr>"
      "<tr><td>贴尾门</td><td>冷链温控区</td><td>冷链生物制品</td><td>温控优先，需最快取出</td></tr>"
      "</tbody></table>")
    a("<p>功能区长度<b>按底面面积分配、并由试装结果闭环回检</b>："
      "先按占比切分 → 试装 → 把装不下的功能区加长、其余相应缩短 → 再试装。"
      "<b>空间分配本身是被求解出来的，不是一次性估算。</b></p>")

    a("<h3>品类装载规则表</h3>")
    a("<table><thead><tr><th>品类</th><th>功能区</th><th>单箱可承压</th><th>重货置底</th>"
      "<th>优先卸货</th><th>不相容品类</th></tr></thead><tbody>"
      "<tr><td>冷链药品</td><td>COLD</td><td class='bad'>0 kg（禁压）</td><td>—</td><td>✔</td><td>—</td></tr>"
      "<tr><td>医疗器械</td><td>PRECISE</td><td class='bad'>0 kg（禁压）</td><td>—</td><td>—</td><td>日化耗材</td></tr>"
      "<tr><td>口服药品</td><td>AMBIENT</td><td>45 kg</td><td>—</td><td>—</td><td>日化耗材</td></tr>"
      "<tr><td>外用药品</td><td>AMBIENT</td><td>30 kg</td><td>—</td><td>—</td><td>日化耗材</td></tr>"
      "<tr><td>保健品</td><td>AMBIENT</td><td>50 kg</td><td>—</td><td>—</td><td>日化耗材</td></tr>"
      "<tr><td>日化耗材</td><td>ISOLATED</td><td>60 kg</td><td>✔（底面 ≤ 500mm）</td><td>—</td>"
      "<td>药品与器械全类</td></tr>"
      "</tbody></table>")
    a("<p>规则以<b>显式规则表</b>落地（<code>cityload/categories.py</code>），"
      "业务方可以直接核对与增删，不需要读算法代码。</p>")

    a("<h2>二、实测结果</h2>")
    a("<p class='sub'>对照实验为 2×2 消融：A=人工经验（最近邻排线+集中码放）、"
      "B=仅启用空间分配、C=仅优化路径、D=本方案（两者叠加）。"
      "A/B 与 C/D 各自共用同一组路径，<b>唯一变量是「是否启用空间分配」</b>，收益可干净归因。</p>")

    if compact:
        a(_metrics_table(
            "算例一：8 门店（装载宽松）—— 空间分配完整生效",
            compact["variants"], RESULT_ROWS,
            "D 的 3 台车全部按最高等级执行空间分配（降级 0 车次、越区 0 箱垛），"
            "重复搬运段数 37 → 6（−83.8%），车辆数/里程/成本零代价。",
        ))
    if main:
        a(_metrics_table(
            "算例二：30 门店（紧装载，对齐赛题“30 家连锁药店”规模）",
            main["variants"], RESULT_ROWS,
            "该规模下空间分配按设计降级（D 的 6 台车中仅 1 台维持严格品类分区），"
            "因此 B≈A、D≈C。成因与改进路线已在仓库 docs/05、docs/06 中量化说明。",
        ))

    if shot:
        a("<h3>3D 装卸示意图（8 门店算例，D 方案）</h3>")
        a("<p class='sub'>图中虚线为功能区边界，颜色区分交付门店；"
          "随仓库提供的 <code>reports/loadplan.html</code> 为可交互版本，含分站装卸指引。</p>")
        a(shot)

    a("<h2>三、运行方式</h2>")
    a("<pre>环境：Python 3.10+，无需安装任何第三方包\n\n"
      "# 全流程：生成算例 → 对照实验 → 报告 → 3D 示意图\n"
      "python -m cityload.cli all\n\n"
      "# 8 门店快速算例（约 2 秒）\n"
      "python -m cityload.cli all --config configs/test.json --out reports/compact\n\n"
      "# 用真实数据替换合成数据\n"
      "python -m cityload.cli all --data path/to/csv_dir\n\n"
      "# 测试（45 个用例）\n"
      "python -m unittest discover -s tests -v</pre>")

    a("<h2>四、合规声明</h2>")
    a("<div class='box'><ul>"
      "<li><b>原创性</b>：全部算法为本团队自行实现，<b>未使用任何第三方代码、模型权重或闭源组件</b>；"
      "核心代码仅依赖 Python 标准库（<code>dependencies = []</code>）；"
      "可视化 HTML 不引用任何 CDN、图片或字体。</li>"
      "<li><b>数据</b>：仓库内全部算例由 <code>cityload/data_synth.py</code> 按固定随机种子"
      "<b>程序化合成</b>，不含传化集团或任何客户的真实门店、商品、订单与运力信息；"
      "字段结构对齐赛题“数据示例参考”，可被真实数据直接替换。</li>"
      "<li><b>人工设定参数</b>：车辆成本、车速、绕行系数、承压上限等为人工假设值，"
      "已在 <code>docs/04-数据说明.md</code> 逐条标注来源与范围。</li>"
      "<li><b>知识产权</b>：按赛事要求，参赛后提交作品的知识产权归主办方所有。</li>"
      "</ul></div>")

    a("<h2>五、已知限制（如实说明）</h2>")
    a("<p>在装载偏紧、单车站点数多的算例上，<b>一维品类功能区与高装载率存在结构性冲突</b>："
      "功能区长度按体积/面积分配，但车厢长度本质是「地板资源」，"
      "而装载器对异构箱垛平均只能叠到约 1.4 层，于是各功能区集体缺地，"
      "算法按设计降级（降级比例作为指标 <code>relaxed_loads</code> / "
      "<code>unallocated_loads</code> / <code>zone_violations</code> 公开暴露，不做隐藏）。</p>")
    a("<p>改进路线（按预期收益排序，详见 <code>docs/06</code>）："
      "① <b>二维功能区布局</b>（同时沿车宽切分，使每个功能区都能用满车长）最直接；"
      "② 按楼层而非仅按长度分配；③ 候选落位点集补全；"
      "④ 引入 CP-SAT 对照量化与最优解的差距。</p>")

    a("<h2>六、提交清单</h2>")
    a("<div class='box todo'><h3>提交前需人工完成</h3><ol>"
      "<li>填写团队信息：<code>docs/08-团队信息.md</code></li>"
      "<li>创建 <b>GitHub 私有仓库</b>并推送本仓库</li>"
      "<li>邀请官方账号 <b>DsChHack</b> 加入 Collaborators，确认评审期可访问</li>"
      "<li>通过<b>赛事指定渠道</b>提交该私有仓库链接</li>"
      "</ol></div>")
    a("<p class='sub'>其余 18 项（项目代码、README、运行说明、关键算法与模型、技术架构、数据来源、"
      "外部依赖、运行方式、测试结果、当前限制、Demo 展示、合规四项）均已在仓库内就位，"
      "逐条自查见 <code>docs/07-提交检查清单.md</code>。</p>")

    if compact and main:
        a(f"<footer>赛题数据（30 门店算例）规模：{compact['instance_summary']['stores']} / "
          f"{main['instance_summary']['stores']} 门店；"
          f"需求体积 {main['instance_summary']['demand_volume_m3']} m³；"
          f"车队 {main['instance_summary']['fleet_vehicles']} 台。<br>"
          "本页为单文件提交说明，图片以 base64 内嵌，无任何外部依赖，可离线打开或直接上传。</footer>")
    a("</div></body></html>")
    return "".join(parts)


def write_submission(
    out_path: str = "SUBMISSION.html",
    reports_dir: str = "reports",
    compact_dir: str = os.path.join("reports", "compact"),
    version: str = "0.1.0",
) -> str:
    page = render_submission_html(reports_dir, compact_dir, version)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return out_path
