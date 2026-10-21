"""核价：两种算法的并排对照。

这个文件是整个作品的核心。它实现两件事：

1. official_price()  —— 按官方 calculate-price 的口径核价：
   商品金额 + 配送费 - 优惠 = 应付，券要过门槛、要过渠道/时段、互斥生效。

2. naive_price()     —— 复刻一种常见的「AI 式口算」：
   把群里/卡包里看到的券面额直接相加扣掉，不看门槛、不看互斥、不看渠道。

第二件事不是虚构的稻草人。它是真实存在且高频的错误模式：
券面额不等于实际节省，门槛没到就不能减，两张券往往互斥，
外送券到店不能用。这些规则都在券面上，但口算时最容易被跳过。

本作品要证明的命题只有一句：
    AI 可以替你许愿，但不能替你算钱。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PriceResult:
    """一次核价的结果。两种算法返回同一个结构，便于并排展示。"""

    label: str
    subtotal: float
    delivery_fee: float
    discount: float
    payable: float
    applied: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "subtotal": round(self.subtotal, 2),
            "delivery_fee": round(self.delivery_fee, 2),
            "discount": round(self.discount, 2),
            "payable": round(self.payable, 2),
            "applied": self.applied,
            "rejected": self.rejected,
            "notes": self.notes,
        }


# ------------------------------------------------------------------ 官方口径


def official_price(
    items: list[dict],
    coupons: list[dict],
    channel: str = "pickup",
    slot: str = "lunch",
    delivery_fee: float = 9.0,
    coupon_id: str | None = None,
) -> PriceResult:
    """按 calculate-price 的口径算应付金额。

    items   每个元素需含 price / tags / name
    coupons 券池，结构见 data/snapshot_menu.json
    channel pickup / delivery
    coupon_id 指定用哪张券；不给则自动挑「实际省最多」的一张
    """
    subtotal = sum(float(i.get("price", 0)) for i in items)
    fee = delivery_fee if channel == "delivery" else 0.0

    # 筛出「当前场景下真的能生效」的券
    usable: list[tuple[float, dict, str]] = []
    rejected: list[str] = []

    for c in coupons:
        ok, reason = _check_coupon(c, items, subtotal, channel, slot)
        if ok:
            save = _coupon_saving(c, items, subtotal)
            usable.append((save, c, reason))
        else:
            rejected.append(f"{c['name']}：{reason}")

    # 选一张（互斥：stackable=false 的券只能用一张；本作品保守起见一律只叠一张）
    chosen = None
    if coupon_id:
        for save, c, _ in usable:
            if c["id"] == coupon_id:
                chosen = (save, c)
                break
        if chosen is None:
            rejected.insert(0, f"指定的券 {coupon_id} 在当前场景不可用")
    elif usable:
        chosen = max(usable, key=lambda x: x[0])[:2]

    discount = chosen[0] if chosen else 0.0
    applied = [chosen[1]["name"]] if chosen else []
    notes = []
    if chosen:
        notes.append(f"按互斥规则只用一张：{chosen[1]['name']}（{chosen[1].get('note','')}）")

    payable = max(subtotal + fee - discount, 0.0)
    return PriceResult(
        label="官方口径",
        subtotal=subtotal,
        delivery_fee=fee,
        discount=discount,
        payable=payable,
        applied=applied,
        rejected=rejected,
        notes=notes,
    )


def _check_coupon(c: dict, items: list[dict], subtotal: float, channel: str, slot: str) -> tuple[bool, str]:
    """返回 (可用?, 原因)。原因在不可用时就是拒绝理由，可用时是生效条件。"""
    if c.get("channel", "any") not in ("any", channel):
        return False, f"仅{'外送' if c['channel']=='delivery' else '到店'}可用，当前是{'外送' if channel=='delivery' else '到店'}"
    if c.get("slot", "any") not in ("any", slot):
        return False, f"仅{c['slot']}时段可用"

    ctype = c.get("type")
    if ctype == "threshold":
        if subtotal < float(c.get("threshold", 0)):
            return False, f"未达门槛 {c['threshold']:.0f} 元（当前 {subtotal:.2f} 元）"
        return True, f"满 {c['threshold']:.0f} 可用"

    if ctype == "bundle":
        need = c.get("requires_tags") or []
        if need and not all(any(t in i.get("tags", []) for i in items) for t in need):
            return False, "组合条件不满足"
        return True, "组合价可用"

    if ctype in ("second_half", "second_fixed"):
        cat = c.get("category", "小食")
        same = [i for i in items if i.get("category") == cat]
        if len(same) < 2:
            return False, f"需同类别两件（{cat}），当前只有 {len(same)} 件"
        return True, "第二件优惠可用"

    return False, "未识别的券类型"


def _coupon_saving(c: dict, items: list[dict], subtotal: float) -> float:
    ctype = c.get("type")
    if ctype == "threshold":
        return float(c.get("off", 0))
    if ctype == "bundle":
        base = sum(float(i.get("price", 0)) for i in items)
        return max(base - float(c.get("fixed_price", base)), 0.0)
    if ctype in ("second_half", "second_fixed"):
        cat = c.get("category", "小食")
        same = sorted((i for i in items if i.get("category") == cat), key=lambda x: float(x["price"]), reverse=True)
        if len(same) < 2:
            return 0.0
        second = float(same[1]["price"])
        if ctype == "second_half":
            return second * 0.5
        return max(second - float(c.get("fixed_price", 0)), 0.0)
    return 0.0


# ------------------------------------------------------------------ AI 式口算


def naive_price(
    items: list[dict],
    coupons: list[dict],
    channel: str = "pickup",
    slot: str = "lunch",
    delivery_fee: float = 9.0,
) -> PriceResult:
    """复刻一种常见的口算方式：挑面额最大的那张券直接减。

    它的错处不在算术，在选择：
    1. 选券只看面额，不看门槛 —— 「满 50 减 12」最诱人，可这单只有 36 元。
    2. 不看互斥与渠道 —— 外送券用在到店，或者以为几张能叠加。
    3. 忘了配送费 —— 外送场景只在券面额上做减法。

    券面额越大，越容易诱发这种选择。所以额度最高的那张，往往正是最不该用的那张。
    """
    subtotal = sum(float(i.get("price", 0)) for i in items)
    with_off = [c for c in coupons if float(c.get("off", 0)) > 0]
    pick = max(with_off, key=lambda c: float(c["off"])) if with_off else None

    reasons: list[str] = []
    discount = 0.0
    applied: list[str] = []
    if pick:
        discount = float(pick["off"])
        applied = [pick["name"]]
        need = float(pick.get("threshold", 0))
        if pick.get("type") == "threshold" and subtotal < need:
            reasons.append(
                f"选了面额最大的「{pick['name']}」，但这单只有 {subtotal:.2f} 元，差 {need - subtotal:.2f} 元没到门槛"
            )
        if pick.get("channel", "any") not in ("any", channel):
            reasons.append(f"「{pick['name']}」是{'外送' if pick['channel']=='delivery' else '到店'}专享，被跨渠道用了")
        others = [c for c in with_off if c["id"] != pick["id"]]
        if others:
            reasons.append(f"另外 {len(others)} 张券没有参与比较（互斥规则被跳过）")
    if channel == "delivery":
        reasons.append(f"未计入配送费 {delivery_fee:.0f} 元")

    return PriceResult(
        label="AI 式口算",
        subtotal=subtotal,
        delivery_fee=0.0,
        discount=discount,
        payable=max(subtotal - discount, 0.0),
        applied=applied,
        rejected=[],
        notes=reasons,
    )


def compare(items, coupons, channel="pickup", slot="lunch", delivery_fee=9.0, units: int = 1) -> dict:
    """把两种算法摆在一起，算出差额。差额为正 = 口算低估了实付金额。

    units 为份数（人数）。多人场景按「每人一份、各自核价」处理，
    这是一个明确写进文档的简化口径，不假装它等于一张合并订单的算法。
    """
    units = max(int(units), 1)
    # 多人场景按「一单合并结算」处理：把商品按份数摊平后再核价，
    # 这样券的门槛（满 50 减 12）才是按整单金额判断的，符合真实下单逻辑。
    basket = [dict(i) for i in items for _ in range(units)]

    naive = naive_price(basket, coupons, channel, slot, delivery_fee)
    official = official_price(basket, coupons, channel, slot, delivery_fee)
    gap = official.payable - naive.payable
    return {
        "naive": naive.as_dict(),
        "official": official.as_dict(),
        "unit_price": {
            "naive": round(naive.payable / units, 2),
            "official": round(official.payable / units, 2),
        },
        "units": units,
        "gap": round(gap, 2),
        "gap_pct": round(gap / official.payable * 100, 1) if official.payable else 0.0,
        "verdict": "口算低估了实付金额" if gap > 0.005 else ("口算高估了实付金额" if gap < -0.005 else "两种算法一致"),
    }
