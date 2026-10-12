"""品类装载规则表 —— “不同种类货物如何合理摆放”的显式知识。

真实城市配送（尤其是医药连锁）里，装车远不只是“塞得下”：
不同品类有各自的温层、耐压、污染隔离与卸货优先级要求。把这些要求写成
**显式规则**而不是埋在代码里的 if，既能被业务方核对，也能直接约束配载器。

规则字段说明
------------
``zone`` / ``zone_label``
    该品类所属的**功能区**。功能区是车厢空间的“一级分配单元”，
    同一功能区的货物在车厢里占据连续的一段长度。
``max_load_above_kg``
    该品类单箱上方**最多能承受多少公斤**压载。0 表示不可被压
    （冷链生物制品、精密医疗器械）。
``heavy_bottom``
    是否必须放在车厢底层（离地高度受限）。
``priority_unload``
    是否属于需要优先取出的品类（冷链需要尽快入冷链柜，因此放在最靠尾门处）。
``incompatible``
    不允许与之**直接上下相邻**的品类（防止泄漏、交叉污染）。

``ZONE_ORDER_FROM_DOOR`` 给出功能区沿车长的排列顺序：
列表越靠前的功能区越靠近尾门（先卸、易取）。配载器会从**列表末尾**（车头侧）
开始向尾门方向码放。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class CategoryRule:
    zone: str
    zone_label: str
    max_load_above_kg: float
    heavy_bottom: bool
    priority_unload: bool
    incompatible: Tuple[str, ...] = ()


RULES: Dict[str, CategoryRule] = {
    "冷链药品": CategoryRule(
        zone="COLD",
        zone_label="冷链温控区",
        max_load_above_kg=0.0,
        heavy_bottom=False,
        priority_unload=True,
        incompatible=(),
    ),
    "医疗器械": CategoryRule(
        zone="PRECISE",
        zone_label="精密器械区",
        max_load_above_kg=0.0,
        heavy_bottom=False,
        priority_unload=False,
        incompatible=("日化耗材",),
    ),
    "口服药品": CategoryRule(
        zone="AMBIENT",
        zone_label="常规药品区",
        max_load_above_kg=45.0,
        heavy_bottom=False,
        priority_unload=False,
        incompatible=("日化耗材",),
    ),
    "外用药品": CategoryRule(
        zone="AMBIENT",
        zone_label="常规药品区",
        max_load_above_kg=30.0,
        heavy_bottom=False,
        priority_unload=False,
        incompatible=("日化耗材",),
    ),
    "保健品": CategoryRule(
        zone="AMBIENT",
        zone_label="常规药品区",
        max_load_above_kg=50.0,
        heavy_bottom=False,
        priority_unload=False,
        incompatible=("日化耗材",),
    ),
    "日化耗材": CategoryRule(
        zone="ISOLATED",
        zone_label="日化隔离区",
        max_load_above_kg=60.0,
        heavy_bottom=True,
        priority_unload=False,
        incompatible=("口服药品", "外用药品", "保健品", "医疗器械"),
    ),
}

#: 功能区沿车长的排列：越靠前越靠近尾门（先卸、易取）
ZONE_ORDER_FROM_DOOR: Tuple[str, ...] = (
    "COLD",       # 冷链：温控，需最快取出
    "ISOLATED",   # 日化耗材：独立成段，避免与药品交叉污染
    "AMBIENT",    # 常规药品：主力货量
    "PRECISE",    # 精密器械：不怕压但要稳固，放最里侧
)

DEFAULT_RULE = CategoryRule(
    zone="AMBIENT",
    zone_label="常规药品区",
    max_load_above_kg=45.0,
    heavy_bottom=False,
    priority_unload=False,
    incompatible=(),
)


def rule_of(category: str) -> CategoryRule:
    return RULES.get(category, DEFAULT_RULE)


def zone_label(zone: str) -> str:
    for rule in RULES.values():
        if rule.zone == zone:
            return rule.zone_label
    return zone


def zones_in_packing_order() -> Tuple[str, ...]:
    """从车头侧（最里）到尾门侧的码放顺序。"""

    return tuple(reversed(ZONE_ORDER_FROM_DOOR))
