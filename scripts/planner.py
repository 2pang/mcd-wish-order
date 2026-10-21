"""候选组合规划器。

第三层之前的那一步：拿到翻译好的 Wish，从菜单里生成候选组合，
用官方口径核价，筛掉超预算的，再按「像不像你许的那个愿」排序。

设计上有两个刻意的选择：

1. 排序用官方应付价，不用口算价。
   如果一个方案只有靠算错钱才显得便宜，它不该被推荐。
2. 每个推荐都带推荐理由，理由必须能对回 Wish 里的某个字段。
   说不清为什么推它的，就不推。
"""

from __future__ import annotations

import itertools
import json
import os
from dataclasses import dataclass, field

from pricing import compare

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


@dataclass
class Candidate:
    name: str
    items: list[dict]
    unit_count: int
    score: float
    reasons: list[str] = field(default_factory=list)
    price: dict = field(default_factory=dict)
    payable: float = 0.0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "items": [i["name"] for i in self.items],
            "codes": [i["code"] for i in self.items],
            "unit_count": self.unit_count,
            "score": round(self.score, 2),
            "reasons": self.reasons,
            "price": self.price,
            "payable": round(self.payable, 2),
        }


def load_snapshot(name: str = "snapshot_menu.json") -> dict:
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def _meal_index(snap: dict) -> dict:
    return {m["code"]: m for m in snap["meals"]}


def _expand(combo: dict, idx: dict) -> list[dict]:
    return [idx[c] for c in combo["items"] if c in idx]


# ------------------------------------------------------------------ 打分


def _score(items: list[dict], wish, budget: float, payable: float) -> tuple[float, list[str]]:
    """返回 (分数, 理由列表)。理由必须能对回 Wish 的字段。

    payable 是官方口径下的实付总额（已含人数与优惠），预算利用率按它算，
    不能按商品原价算——原价 27 元的组合用了 1+1 券后只花 13.9，
    按原价算会误判成「已经花掉九成预算」。
    """
    tags = [t for i in items for t in i.get("tags", [])]
    score = 0.0
    reasons: list[str] = []

    # 命中偏好
    hits = [t for t in wish.taste if t in tags]
    if hits:
        score += 3.0 * len(hits)
        reasons.append(f"命中偏好：{'、'.join(sorted(set(hits)))}")

    # 命中排除项，直接重罚
    bad = [t for t in wish.avoid if t in tags]
    if bad:
        score -= 12.0 * len(bad)
        reasons.append(f"含要排除的：{'、'.join(sorted(set(bad)))}")

    # 预算贴合度：非「省钱」类愿望要的是用足预算，不是越便宜越好
    if payable > budget:
        score -= 8.0 + (payable - budget) * 0.5
        reasons.append(f"超预算 {payable - budget:.0f} 元")
    else:
        ratio = payable / budget if budget else 1
        if "value" in wish.taste:
            # 明确说了要省钱，越省越好
            score += max(0.0, (1 - ratio) * 6.0)
            reasons.append(f"实付 {payable:.0f} 元，留了 {budget - payable:.0f} 元余量")
        else:
            # 其余场景（治愈/体面/吃饱/奖励）用足预算才成立：
            # 只花掉一半预算的方案，通常不是用户想要的答案
            target = 0.88
            score += max(0.0, 5.0 - abs(ratio - target) * 12.0)
            if ratio < 0.55:
                score -= (0.55 - ratio) * 10.0
                reasons.append(f"只用到预算的 {ratio*100:.0f}%，可能不够")

    # 品类多样性（人多、要体面时更重要）
    if any(t in wish.taste for t in ("variety", "share")):
        cats = {i.get("category") for i in items}
        score += 2.0 * len(cats)
        if len(cats) >= 3:
            reasons.append(f"品类齐（{'、'.join(sorted(cats))}），桌上不单调")

    # 单独一个人吃，不需要过多件数
    if wish.people == 1 and len(items) > 3:
        score -= 2.0

    # 甜品类给「治愈」「奖励」加权
    if any(m in ("治愈", "安慰", "奖励", "犒劳", "开心") for m in wish.moods):
        if any(i.get("category") == "甜品" for i in items):
            score += 3.0
            reasons.append("带了甜品，对应你说的那口气")

    # 低卡诉求：热量越低越好
    if "lowcal" in wish.taste:
        kcal = sum(i.get("kcal", 0) for i in items)
        score += max(0.0, (900 - kcal) / 150)
        reasons.append(f"总热量约 {kcal} 大卡")

    return score, reasons


# ------------------------------------------------------------------ 生成


def build_candidates(wish, snap: dict | None = None, limit: int = 6) -> list[Candidate]:
    snap = snap or load_snapshot()
    idx = _meal_index(snap)
    budget = wish.budget
    coupons = snap["coupons"]
    channel = snap.get("channel_default", "pickup")
    fee = float(snap.get("delivery_fee", 9.0))

    raw: list[tuple[str, list[dict], int]] = []

    # 1) 套餐候选（通常最划算）
    for combo in snap["combos"]:
        items = _expand(combo, idx)
        if not items:
            continue
        raw.append((combo["name"], items, 1))

    # 2) 随心配之外的单品自由组合：汉堡 + 小食 + 饮料
    burgers = [m for m in snap["meals"] if m["category"] == "汉堡"]
    sides = [m for m in snap["meals"] if m["category"] == "小食"]
    drinks = [m for m in snap["meals"] if m["category"] == "饮料"]
    desserts = [m for m in snap["meals"] if m["category"] == "甜品"]

    for b, s, d in itertools.product(burgers, sides, drinks):
        picked = [b, s, d]
        total = sum(float(i["price"]) for i in picked) * max(wish.people, 1)
        if total > budget * 1.05:  # 明显超预算的先不生成，减少无谓计算
            continue
        raw.append((f"{b['name']} + {s['name']} + {d['name']}", picked, 1))

    # 3) 小份组合：单品 + 甜品（治愈/夜宵/单人场景）
    if wish.people == 1:
        for b, t in itertools.product(burgers + sides, desserts):
            picked = [b, t]
            total = sum(float(i["price"]) for i in picked)
            if total > budget * 1.05:
                continue
            raw.append((f"{b['name']} + {t['name']}", picked, 1))

    # 去重（按商品集合）
    seen, uniq = set(), []
    for name, items, unit in raw:
        key = tuple(sorted(i["code"] for i in items))
        if key in seen:
            continue
        seen.add(key)
        uniq.append((name, items, unit))

    # 核价 + 打分
    out: list[Candidate] = []
    for name, items, unit in uniq:
        units = max(wish.people, 1)
        priced = compare(
            items, coupons, channel=channel, slot=wish.meal_slot, delivery_fee=fee, units=units
        )
        payable_total = priced["official"]["payable"]
        if payable_total > budget + 0.01:
            continue
        score, reasons = _score(items, wish, budget, payable_total)
        # 同等条件下更便宜的排前面，但价格只作为次级项，不压过偏好
        score += max(0.0, (budget - payable_total) / max(budget, 1)) * 2.0
        out.append(
            Candidate(
                name=name if wish.people == 1 else f"{name} × {wish.people} 人",
                items=items,
                unit_count=max(wish.people, 1),
                score=score,
                reasons=reasons,
                price=priced,
                payable=payable_total,
            )
        )

    out.sort(key=lambda c: (-c.score, c.payable))
    return out[:limit]


def pick_best(wish, snap: dict | None = None) -> Candidate | None:
    cands = build_candidates(wish, snap, limit=1)
    return cands[0] if cands else None


if __name__ == "__main__":
    from wish_engine import parse_wish

    for text in [
        "今天被领导骂了一顿，想吃点能治愈的，别超过30块",
        "明天要带三个同事吃饭，我不想显得抠，也不想超预算",
        "减肥第三周，馋得不行，给我找个能骗过自己的",
    ]:
        print("=" * 70)
        w = parse_wish(text)
        for line in w.explain():
            print(line)
        print("-" * 70)
        best = pick_best(w)
        if not best:
            print("没有在预算内找到方案")
            continue
        print(f"推荐：{best.name}  应付 {best.payable:.2f} 元  （口算 {best.price['naive']['payable']:.2f}）")
        for r in best.reasons:
            print(f"  - {r}")
