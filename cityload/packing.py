"""3D 车厢配载：**两级空间分配** + **品类合规码放**。

这是本方案的核心。城市配送装车要同时回答两个问题：
**空间怎么分？** 和 **不同种类的货怎么摆？**

两级空间分配
------------
1. **一级 —— 功能分区**：按品类合规要求，把车厢长度切成若干连续功能区，
   顺序由 ``categories.ZONE_ORDER_FROM_DOOR`` 给定（越靠尾门越先取用）：

   - **冷链温控区**（贴尾门，冷链生物制品需最快转入冷链柜）
   - **日化隔离区**（独立成段，避免与药品交叉污染）
   - **常规药品区**（主力货量）
   - **精密器械区**（靠车头，不怕压但要稳固）

   每个功能区分到的长度按“该功能区在本车次的体积占比”分配，
   并以该功能区最大箱垛的最小底面边长为保底长度。

2. **二级 —— 门店子区**：每个功能区内部再按**交付顺序的逆序**切分成门店子段，
   最后交付的门店在功能区里侧、最先交付的在功能区门口侧。
   这样卸第 k 站时，只需到各功能区取出属于该站的那一段，
   不必挪动别站货物 —— 空间分配与配送顺序是咬合的。

品类合规码放规则（由 ``categories`` 规则表驱动）
------------------------------------------------
- **禁压上限**：单箱上方压载不得超过 ``max_load_above_kg``
  （冷链、精密器械为 0，即不可被压）；压载按支撑面重叠比例分摊。
- **品类不相容**：``incompatible`` 中的品类不得直接上下相邻（防泄漏交叉污染）。
- **重货置底**：``heavy_bottom`` 的品类（日化耗材）离地高度受限。
- **易碎件置顶**：``fragile`` 货物上方不得再压货。
- **支撑率**：底面至少 ``min_support_ratio`` 被下方箱顶或车厢地板支撑。

坐标系：车厢左后下角为原点，x 沿车长指向尾门，y 沿车宽，z 向上，单位 mm。
"""

from __future__ import annotations

from bisect import insort
from dataclasses import dataclass, field, replace
from typing import Dict, List, Sequence, Tuple

from .categories import zone_label, zones_in_packing_order
from .domain import (
    Block,
    Instance,
    Placement,
    Vehicle,
    VehicleLoad,
    base_overlap_area_mm2,
    blocks_from_orders,
    overlaps,
)

BAND_MM = 100


@dataclass
class PackOptions:
    #: 底面至少被支撑的比例
    min_support_ratio: float = 0.75
    #: 允许占用的车厢高度比例
    max_height_ratio: float = 0.95
    #: 是否启用两级空间分配（关闭即退化为“按品类集中码放”的人工装车做法）
    align_to_delivery_order: bool = True
    #: 为了让货物留在自己的分区内，最多愿意额外付出多少高度（mm）
    zone_height_slack_mm: int = 400
    #: 重货置底规则：标记 heavy_bottom 的箱垛其**底面**不得超过该高度
    heavy_bottom_max_z_mm: int = 500
    #: 是否严格把货物限制在所属功能区内（关闭则允许在实在装不下时越区，会被指标统计）
    strict_zones: bool = True
    #: 单柱码垛高度占车厢可用高度的上限（约 0.55 时可让两柱叠起来）
    max_block_height_ratio: float = 0.55
    #: 空间分配的回检轮数：装不下就按“未装载体积”重新分配车厢长度再装
    zone_realloc_rounds: int = 4
    #: 地板候选点网格间距（mm）。极值点只长在已放箱体的角上，
    #: 空区域若没有候选点，箱垛明明有地方也放不进去 —— 必须显式铺一层地板网格。
    floor_grid_step_mm: int = 200

    @classmethod
    def from_config(
        cls, config: dict, align_to_delivery_order: bool = True
    ) -> "PackOptions":
        cfg = config.get("packing", {})
        return cls(
            min_support_ratio=float(cfg.get("min_support_ratio", 0.75)),
            max_height_ratio=float(cfg.get("max_height_ratio", 0.95)),
            align_to_delivery_order=align_to_delivery_order,
            zone_height_slack_mm=int(cfg.get("zone_height_slack_mm", 400)),
            heavy_bottom_max_z_mm=int(cfg.get("heavy_bottom_max_z_mm", 500)),
            max_block_height_ratio=float(cfg.get("max_block_height_ratio", 0.55)),
            zone_realloc_rounds=int(cfg.get("zone_realloc_rounds", 4)),
            floor_grid_step_mm=int(cfg.get("floor_grid_step_mm", 200)),
        )


@dataclass(frozen=True)
class _Bounds:
    """一个装车区域的约束。

    ``prefer_x_max`` 是软边界：落在它以内不加高度惩罚，超出则记一次惩罚，
    只有在软边界内完全放不下时才会用到软边界之外的空间。
    """

    x_min: int
    x_max: int
    y_max: int
    z_max: int
    prefer_x_max: int


@dataclass
class _PackState:
    """一趟车共享的车厢状态（含空间索引，保证候选点不因性能而被人为砍掉）。"""

    placed: List[Placement] = field(default_factory=list)
    #: 每个已放置箱垛上方已承受的压载（kg），按支撑面重叠比例分摊
    load_above_kg: Dict[str, float] = field(default_factory=dict)
    #: 候选落位点，元素为 (z, x, y)，始终按该键升序（bisect 插入维护）
    points: List[Tuple[int, int, int]] = field(default_factory=list)
    seen_points: set = field(default_factory=set)
    by_band: Dict[int, List[Placement]] = field(default_factory=dict)
    by_top: Dict[int, List[Placement]] = field(default_factory=dict)
    limits: Tuple[int, int, int] = (0, 0, 0)  # (usable_l, inner_width, usable_h)

    def add_point(self, zxy: Tuple[int, int, int]) -> None:
        z, x, y = zxy
        if z > self.limits[2] or x > self.limits[0] or y > self.limits[1]:
            return  # 越界候选点永远不会被用到
        if zxy in self.seen_points:
            return
        self.seen_points.add(zxy)
        insort(self.points, zxy)

    def register(self, p: Placement) -> None:
        self.placed.append(p)
        for b in range(p.z_mm // BAND_MM, (p.z1_mm - 1) // BAND_MM + 1):
            self.by_band.setdefault(b, []).append(p)
        self.by_top.setdefault(p.z1_mm, []).append(p)

    def neighbours(self, cand: Placement) -> Sequence[Placement]:
        """按高度带取可能与候选位相交的箱体。

        不做去重 —— 重复项只是多几次极便宜的相交判断，
        而去重本身（建 set + 建 list）在候选点数量级上反而是主要开销。
        """

        b0 = cand.z_mm // BAND_MM
        b1 = (cand.z1_mm - 1) // BAND_MM
        if b0 == b1:
            return self.by_band.get(b0, ())
        out: List[Placement] = []
        for b in range(b0, b1 + 1):
            out.extend(self.by_band.get(b, ()))
        return out


def normalize_blocks_for_height(
    blocks: Sequence[Block], usable_h_mm: int, max_block_height_ratio: float = 0.55
) -> List[Block]:
    """把码垛拆到目标车厢**可叠放**的高度以内。

    两个原因必须拆柱：

    1. Block 是按最高车厢码垛出来的，装进矮车厢时会“永远放不下”；
    2. 更隐蔽的一点 —— 若一柱就码到接近车厢顶，上面再也叠不了东西，
       车厢上部空间会整片浪费。把单柱高度限制在车厢可用高度的一半左右，
       两柱就能叠起来，容积利用率显著提升。
    """

    col_h = max(1, int(usable_h_mm * max_block_height_ratio))
    out: List[Block] = []
    for b in blocks:
        unit_h = max(1, b.height_mm // max(1, b.units))
        per_col = max(1, col_h // unit_h)
        if b.units <= per_col:
            out.append(b)
            continue
        remaining = b.units
        idx = 0
        while remaining > 0:
            units = min(per_col, remaining)
            remaining -= units
            idx += 1
            out.append(
                replace(
                    b,
                    block_id=f"{b.block_id}~{idx}",
                    units=units,
                    height_mm=unit_h * units,
                    weight_kg=round(b.weight_kg * units / b.units, 4),
                )
            )
    return out


def block_sort_key(b: Block) -> Tuple[int, int, int, float]:
    """大件、重件、非易碎件先落位；易碎件留到最后（自然堆在上层）。"""

    return (1 if b.fragile else 0, -b.base_area_mm2, -b.height_mm, -b.weight_kg)


def build_blocks(inst: Instance, options: PackOptions) -> Dict[str, List[Block]]:
    """按门店构建装载单元（同一门店同一 SKU 的整箱码垛，并附品类规则）。"""

    by_store = inst.demand_by_store()
    inner_h = max(v.inner_height_mm for v in inst.vehicles)
    return {
        sid: blocks_from_orders(
            lines,
            inst.skus,
            container_height_mm=inner_h,
            max_height_ratio=options.max_height_ratio * options.max_block_height_ratio,
        )
        for sid, lines in by_store.items()
    }


# --------------------------------------------------------------------------- #
# 两级空间分配
# --------------------------------------------------------------------------- #
def _allocate(
    demands: Dict[str, float], minimums: Dict[str, float], total_length: int
) -> Dict[str, Tuple[int, int]]:
    """按需求占比分配一段长度，并为每项保留最小可用长度。

    返回 {key: (x_start, x_end)}，顺序由 ``demands`` 的插入顺序决定（从 x=0 起算）。
    """

    keys = list(demands.keys())
    if not keys or total_length <= 0:
        return {}
    total_demand = sum(demands.values())
    if total_demand <= 0:
        total_demand = float(len(keys))
        demands = {k: 1.0 for k in keys}

    floor_total = sum(minimums.get(k, 0.0) for k in keys)
    extra = max(0.0, total_length - floor_total)
    weights = [
        minimums.get(k, 0.0) + extra * demands[k] / total_demand for k in keys
    ]
    total_w = sum(weights)
    scale = total_length / total_w if total_w > 0 else 1.0

    out: Dict[str, Tuple[int, int]] = {}
    cursor = 0.0
    for k, w in zip(keys, weights):
        length = w * scale
        out[k] = (int(round(cursor)), int(round(cursor + length)))
        cursor += length
    out[keys[-1]] = (out[keys[-1]][0], total_length)
    return out


def allocate_space(
    route: Sequence[str],
    trimmed: Dict[str, List[Block]],
    inner_length_mm: int,
) -> Dict[str, Tuple[int, int]]:
    """**一级空间分配**：按品类合规要求，把车厢长度切成若干连续功能区。

    功能区从车头侧（x=0）向尾门侧依次排布，顺序为 ``zones_in_packing_order()``：
    精密器械区 → 常规药品区 → 日化隔离区 → 冷链温控区（贴尾门，最先取用）。

    **按底面面积（footprint）分配，而不是按体积分配** —— 这一点很关键：
    车厢长度是「地板」资源，一个功能区真正占用的是地板面积；
    只有当货物能码到车厢顶时，体积占比才等价于地板占比。
    实测装载器对这类异构箱垛只能叠到约 1.4 层，地板面积才是紧约束，
    按体积分配会让各功能区都缺地、集体装不下。

    返回 {功能区: (x_start_mm, x_end_mm)}；未出现的功能区不返回。
    """

    zone_floor: Dict[str, float] = {}
    for sid in route:
        for b in trimmed.get(sid, []):
            zone_floor[b.functional_zone] = (
                zone_floor.get(b.functional_zone, 0.0) + b.base_area_mm2
            )

    ordered_zones = [z for z in zones_in_packing_order() if zone_floor.get(z)]
    if not ordered_zones:
        return {}

    min_len = {
        z: max(
            (
                min(b.length_mm, b.width_mm)
                for sid in route
                for b in trimmed.get(sid, [])
                if b.functional_zone == z
            ),
            default=0.0,
        )
        for z in ordered_zones
    }
    return _allocate(
        {z: zone_floor[z] for z in ordered_zones}, min_len, inner_length_mm
    )


# --------------------------------------------------------------------------- #
# 落位
# --------------------------------------------------------------------------- #
def _orientations(b: Block) -> List[Tuple[int, int]]:
    opts = [(b.length_mm, b.width_mm)]
    if b.length_mm != b.width_mm:
        opts.append((b.width_mm, b.length_mm))
    return opts


def _support(cand: Placement, state: _PackState) -> Tuple[int, List[Placement]]:
    """返回 (支撑面积, 支撑箱体列表)。z=0 视为地板全支撑。"""

    base_area = cand.length_mm * cand.width_mm
    if cand.z_mm == 0:
        return base_area, []
    area = 0
    sups: List[Placement] = []
    for p in state.by_top.get(cand.z_mm, ()):
        a = base_overlap_area_mm2(cand, p)
        if a > 0:
            area += a
            sups.append(p)
    return area, sups


def _rules_allow(
    cand: Placement,
    sups: Sequence[Placement],
    state: _PackState,
    options: PackOptions,
) -> bool:
    """品类合规校验：禁压上限、品类不相容、重货置底、易碎件置顶。"""

    base_area = cand.length_mm * cand.width_mm
    if cand.block.heavy_bottom and cand.z_mm > options.heavy_bottom_max_z_mm:
        return False  # 重货的**底面**必须位于车厢下层（不是限制它的高度）
    if not sups:
        return True
    if any(p.block.fragile for p in sups):
        return False  # 易碎件上方不得压货
    cat = cand.block.category
    for p in sups:
        below = p.block
        if below.category in _incompatible_of(cat):
            return False
        if cat in _incompatible_of(below.category):
            return False
        share = base_overlap_area_mm2(cand, p) / base_area if base_area else 1.0
        added = cand.block.weight_kg * share
        if state.load_above_kg.get(below.block_id, 0.0) + added > below.max_load_above_kg + 1e-9:
            return False  # 超过该品类单箱的可承压上限
    return True


def _incompatible_of(category: str) -> Tuple[str, ...]:
    from .categories import rule_of

    return rule_of(category).incompatible


def _commit(cand: Placement, sups: Sequence[Placement], state: _PackState) -> None:
    base_area = cand.length_mm * cand.width_mm
    state.register(cand)
    state.load_above_kg[cand.block.block_id] = 0.0
    for p in sups:
        share = base_overlap_area_mm2(cand, p) / base_area if base_area else 1.0
        bid = p.block.block_id
        state.load_above_kg[bid] = (
            state.load_above_kg.get(bid, 0.0) + cand.block.weight_kg * share
        )
    for pt in (
        (cand.z_mm, cand.x1_mm, cand.y_mm),
        (cand.z_mm, cand.x_mm, cand.y1_mm),
        (cand.z1_mm, cand.x_mm, cand.y_mm),
        (cand.z1_mm, cand.x1_mm, cand.y_mm),
        (cand.z1_mm, cand.x_mm, cand.y1_mm),
        (cand.z_mm, cand.x1_mm, cand.y1_mm),
    ):
        state.add_point(pt)


def _place_one(
    b: Block, bounds: _Bounds, options: PackOptions, state: _PackState
) -> Tuple[Placement, List[Placement]] | None:
    """为一个 Block 找最优落位。

    候选点按 (z, x, y) 升序扫描，同时记录“分区内”和“全车厢”的最优可行解：

    - 若分区内能把货放在不超过 ``zone_height_slack_mm`` 的高度代价内，就留在本区；
    - 否则宁可用车厢其他位置的更底层空间 —— 否则会把货堆成本区里的一座座高塔，
      车厢空间被切碎，装载率明显下降。

    扫描到 ``z`` 超过已知最优解 + 松弛量即可提前退出。
    """

    best_in: Tuple[Placement, List[Placement]] | None = None
    best_any: Tuple[Placement, List[Placement]] | None = None
    limit_z: int | None = None
    slack = options.zone_height_slack_mm

    for (pz, px, py) in state.points:
        if limit_z is not None and pz > limit_z:
            break
        if px < bounds.x_min or py < 0:
            continue
        if pz + b.height_mm > bounds.z_max:
            break  # 候选点按 z 升序，后面只会更高
        for (L, W) in _orientations(b):
            if px + L > bounds.x_max or py + W > bounds.y_max:
                continue
            cand = Placement(
                block=b, x_mm=px, y_mm=py, z_mm=pz,
                length_mm=L, width_mm=W, height_mm=b.height_mm,
            )
            if any(overlaps(cand, p) for p in state.neighbours(cand)):
                continue
            area, sups = _support(cand, state)
            base_area = cand.length_mm * cand.width_mm
            if area + 1e-9 < base_area * options.min_support_ratio:
                continue
            if not _rules_allow(cand, sups, state, options):
                continue
            if best_any is None:
                best_any = (cand, sups)
                limit_z = pz + slack
            if px + L <= bounds.prefer_x_max and best_in is None:
                best_in = (cand, sups)
                if limit_z is None or pz + slack < limit_z:
                    limit_z = pz + slack
            if best_in is not None and best_any is not None:
                break

    if best_in is None:
        return best_any
    if best_any is None:
        return best_in
    if best_any[0].z_mm + slack < best_in[0].z_mm:
        return best_any
    return best_in


def _pack_items(
    items: Sequence[Tuple[Block, _Bounds]],
    options: PackOptions,
    state: _PackState,
    pre_sorted: bool = False,
) -> Tuple[List[Placement], List[Block]]:
    """按给定顺序依次落位。

    ``pre_sorted=True`` 时完全尊重传入顺序（两级空间分配用它实现“一站一站地装”），
    否则按“大件在下、易碎件在上”全局排序（基线用它还原人工按品类集中装车）。
    """

    ordered = items if pre_sorted else sorted(items, key=lambda t: block_sort_key(t[0]))
    placements: List[Placement] = []
    unplaced: List[Block] = []
    for b, bounds in ordered:
        found = _place_one(b, bounds, options, state)
        if found is None:
            unplaced.append(b)
            continue
        cand, sups = found
        _commit(cand, sups, state)
        placements.append(cand)
    return placements, unplaced


def _pack_store_major(
    route: Sequence[str],
    trimmed: Dict[str, List[Block]],
    zone_segs: Dict[str, Tuple[int, int]],
    vehicle: Vehicle,
    options: PackOptions,
    usable_h: int,
    usable_l: int,
) -> Tuple[List[Placement], List[Block]]:
    """按“功能区 → 交付逆序 → 本店尺寸降序”落位。

    功能区内不切细条：细条会把一个门店的货逼成一根高塔反而装不下。
    改为功能区内逐店顺序码放 —— 从最后交付的门店开始，每店的货在功能区内
    自然聚成连续一段，前面的店依次向尾门方向接续，卸货不互相压。
    """

    state = _PackState(limits=(usable_l, vehicle.inner_width_mm, usable_h))
    for zone in zones_in_packing_order():
        if zone not in zone_segs:
            continue
        z0, z1 = zone_segs[zone]
        _seed_floor(state, z0, z1, vehicle.inner_width_mm, options.floor_grid_step_mm)
    items: List[Tuple[Block, _Bounds]] = []
    for zone in zones_in_packing_order():
        if zone not in zone_segs:
            continue
        z0, z1 = zone_segs[zone]
        x_max = z1 if options.strict_zones else usable_l
        for sid in reversed(list(route)):
            group = [b for b in trimmed.get(sid, []) if b.functional_zone == zone]
            if not group:
                continue
            for b in sorted(group, key=block_sort_key):
                items.append(
                    (
                        b,
                        _Bounds(
                            x_min=z0,
                            x_max=x_max,
                            y_max=vehicle.inner_width_mm,
                            z_max=usable_h,
                            prefer_x_max=z1,
                        ),
                    )
                )
    return _pack_items(items, options, state, pre_sorted=True)


def _seed_floor(
    state: _PackState, x0: int, x1: int, y_max: int, step: int
) -> None:
    """在 [x0, x1) × [0, y_max) 的地板上铺一层候选点网格。

    极值点启发式的候选点只从已放置箱体的角点生长。如果某个区域一直没有箱体落位，
    那片区域就永远没有候选点 —— 箱垛会“明明有地方却放不进去”，
    车厢高度只用了一半就报未装载。铺一层地板网格直接消除这个病态。
    """

    if step <= 0:
        return
    x = x0
    while x < x1:
        y = 0
        while y < y_max:
            state.add_point((0, x, y))
            y += step
        x += step


def _attempt(
    route: Sequence[str],
    trimmed: Dict[str, List[Block]],
    vehicle: Vehicle,
    options: PackOptions,
    usable_h: int,
    usable_l: int,
    tier: int,
) -> Tuple[List[Placement], List[Block]]:
    """按指定空间分配等级试装一次。

    等级 1：严格功能区 + 空间分配闭环回检（合规性最好）
    等级 2：功能区只作落位偏好，装不下时可越区（合规性让位于可行性）
    等级 3：不做空间分配，全部货物共用整个车厢（兜底，保证一定能装）
    """

    if tier == 3:
        ratio = options.max_block_height_ratio
        ordered = normalize_blocks_for_height(
            [b for sid in route for b in trimmed.get(sid, [])], usable_h, ratio
        )
        full = _Bounds(
            x_min=0,
            x_max=usable_l,
            y_max=vehicle.inner_width_mm,
            z_max=usable_h,
            prefer_x_max=usable_l,
        )
        state = _PackState(limits=(usable_l, vehicle.inner_width_mm, usable_h))
        _seed_floor(state, 0, usable_l, vehicle.inner_width_mm, options.floor_grid_step_mm)
        return _pack_items([(b, full) for b in ordered], options, state)

    strict = tier == 1
    base_vol: Dict[str, float] = {}
    mins: Dict[str, float] = {}
    zone_segs = allocate_space(route, trimmed, usable_l)
    for z in zone_segs:
        base_vol[z] = sum(
            b.base_area_mm2
            for sid in route
            for b in trimmed.get(sid, [])
            if b.functional_zone == z
        )
        mins[z] = max(
            (
                min(b.length_mm, b.width_mm)
                for sid in route
                for b in trimmed.get(sid, [])
                if b.functional_zone == z
            ),
            default=0.0,
        )

    opts = options if strict else replace(options, strict_zones=False)
    best_placed: List[Placement] = []
    best_left: List[Block] | None = None
    for _round in range(max(1, options.zone_realloc_rounds)):
        placed, left = _pack_store_major(
            route, trimmed, zone_segs, vehicle, opts, usable_h, usable_l
        )
        if best_left is None or len(left) < len(best_left):
            best_placed, best_left = placed, left
        if not left:
            break
        if not strict:
            break  # 等级 2 不再调分区长度（分区已非硬约束，调它没有意义）
        need = {z: 0.0 for z in zone_segs}
        for b in left:
            need[b.functional_zone] = need.get(b.functional_zone, 0.0) + b.base_area_mm2
        weights = {z: base_vol[z] + 2.0 * need.get(z, 0.0) for z in zone_segs}
        if sum(weights.values()) <= 0:
            break
        new_segs = _allocate(weights, mins, usable_l)
        if new_segs == zone_segs:
            break
        zone_segs = new_segs
    return best_placed, (best_left or [])


def pack_route(
    inst: Instance,
    route: Sequence[str],
    vehicle: Vehicle,
    blocks_by_store: Dict[str, List[Block]],
    options: PackOptions,
) -> VehicleLoad:
    """为一台车的一条路径生成配载方案。

    启用空间分配时按**三级降级**执行，并记录最终生效的等级：

    1. **严格功能区 + 分配闭环回检** —— 品类完全隔离，空间分配按试装结果迭代；
    2. **功能区仅作偏好** —— 实在装不下时允许越区，保住交付可行性；
    3. **不做空间分配** —— 兜底，保证一定能装下。

    “装不下”和“品类混放”都是真实代价，所以降级不被隐藏，而是作为指标暴露出来
    （``relaxed_loads`` / ``unallocated_loads``）。
    """

    load = VehicleLoad(vehicle=vehicle, route=list(route))
    usable_h = int(vehicle.inner_height_mm * options.max_height_ratio)
    usable_l = vehicle.inner_length_mm

    if not options.align_to_delivery_order:
        placed, left = _attempt(route, blocks_by_store, vehicle, options, usable_h, usable_l, 3)
        load.placements.extend(placed)
        load.unplaced.extend(left)
        load.allocation_tier = 0  # 0 = 该方案本就不做空间分配（不适用，不算降级）
        return load

    ratio = options.max_block_height_ratio
    trimmed = {
        sid: normalize_blocks_for_height(
            blocks_by_store.get(sid, []), usable_h, ratio
        )
        for sid in route
    }

    best: Tuple[int, List[Placement], List[Block]] | None = None
    for tier in (1, 2, 3):
        placed, left = _attempt(route, trimmed, vehicle, options, usable_h, usable_l, tier)
        if best is None or len(left) < len(best[2]):
            best = (tier, placed, left)
        if not left:
            break

    assert best is not None
    tier, placed, left = best
    load.allocation_tier = tier
    load.placements.extend(placed)
    load.unplaced.extend(left)
    return load


def pack_plan(
    inst: Instance,
    assigned: Sequence[Tuple[Sequence[str], Vehicle]],
    options: PackOptions,
    blocks_by_store: Dict[str, List[Block]] | None = None,
) -> List[VehicleLoad]:
    """给一组 (路径, 车辆) 批量生成配载方案。"""

    blocks = blocks_by_store if blocks_by_store is not None else build_blocks(inst, options)
    return [pack_route(inst, route, vehicle, blocks, options) for route, vehicle in assigned]


def space_allocation_report(load: VehicleLoad) -> List[dict]:
    """输出一条车次的**空间分配说明**，可直接放进报告或现场作业单。"""

    rows: List[dict] = []
    for zone in zones_in_packing_order():
        mine = [p for p in load.placements if p.block.functional_zone == zone]
        if not mine:
            continue
        rows.append(
            {
                "zone": zone,
                "zone_label": zone_label(zone),
                "x_from_mm": min(p.x_mm for p in mine),
                "x_to_mm": max(p.x1_mm for p in mine),
                "blocks": len(mine),
                "units": sum(p.block.units for p in mine),
                "volume_m3": round(sum(p.block.volume_m3 for p in mine), 4),
                "stores": sorted({p.store_id for p in mine}),
            }
        )
    return rows
