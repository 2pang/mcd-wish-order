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


SNAPSHOT_CANDIDATES = ("snapshot_real.json", "snapshot_menu.json")


def load_snapshot(name: str | None = None) -> dict:
    """优先读真实抓取的快照，没有则退回示例快照。"""
    if name:
        with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    for cand in SNAPSHOT_CANDIDATES:
        path = os.path.join(DATA_DIR, cand)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError("data/ 下找不到任何快照文件")


def _meal_index(snap: dict) -> dict:
    return {m["code"]: m for m in snap["meals"]}


def _expand(combo: dict, idx: dict) -> list[dict]:
    return [idx[c] for c in combo["items"] if c in idx]


# ------------------------------------------------------------------ 打分


HEAVY_TAGS = {"dessert", "sweet", "fries"}


def is_heavy(item: dict) -> bool:
    """判断一项是不是「减肥场景里的硬伤」。

    不只是甜品和薯条：小食里的炸鸡、鸡翅、鸡排同样算。
    区分烤与炸靠品类（小食 + chicken）而不是靠食材本身，
    因为板烧鸡腿堡这类主食是烤的，不能一刀切。
    """
    tags = set(item.get("tags", []))
    if tags & HEAVY_TAGS:
        return True
    if item.get("category") == "小食" and ("chicken" in tags or "fries" in tags):
        return True
    return False


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

    # 低卡诉求：有营养数据按热量算，没有就按标签倾向算
    if "lowcal" in wish.taste:
        # 负向约束优先：炸物与甜品是「减肥」场景的硬伤，不能靠一杯低卡饮料抵消
        heavy_hits = sum(1 for i in items if is_heavy(i))
        if heavy_hits:
            score -= heavy_hits * 5.0
            reasons.append(f"含 {heavy_hits} 项高热量（炸物/甜品）")
        if any("lowcal" in i.get("tags", []) for i in items):
            score += 3.0
            reasons.append("带了低卡项")
        else:
            score -= 4.0
        kcals = [i["kcal"] for i in items if i.get("kcal")]
        if kcals:
            kcal = sum(kcals)
            score += max(0.0, (900 - kcal) / 150)
            reasons.append(f"总热量约 {kcal} 大卡")

    # 明确说「饿 / 要吃饱」时，必须含主食，纯饮料甜品不解决问题
    if {"filling", "staple"} & set(wish.taste):
        if not any("staple" in i.get("tags", []) for i in items):
            score -= 6.0
            reasons.append("没有主食，吃不饱")

    return score, reasons


# ------------------------------------------------------------------ 生成


def build_candidates(wish, snap: dict | None = None, limit: int = 6) -> list[Candidate]:
    snap = snap or load_snapshot()
    idx = _meal_index(snap)
    budget = wish.budget
    coupons = snap.get("coupons", [])
    channel = snap.get("channel_default", "pickup")
    fee = float(snap.get("delivery_fee", 9.0))
    units = max(wish.people, 1)

    raw: list[tuple[str, list[dict], int]] = []

    # 1) 菜单里本身就是「一份完整餐」的（三件套 / 拼盘 / 套餐），单独成候选
    for m in snap["meals"]:
        if m.get("full_meal"):
            raw.append((m["name"], [m], 1))

    # 2) 旧快照里的 combos 结构（若存在则展开），保持向后兼容
    for combo in snap.get("combos", []):
        items = _expand(combo, idx)
        if items:
            raw.append((combo["name"], items, 1))

    # 3) 单品自由组合：汉堡 + 小食 + 饮料
    burgers = [m for m in snap["meals"] if m["category"] == "汉堡" and not m.get("full_meal")]
    sides = [m for m in snap["meals"] if m["category"] == "小食" and not m.get("full_meal")]
    drinks = [m for m in snap["meals"] if m["category"] == "饮料"]
    desserts = [m for m in snap["meals"] if m["category"] == "甜品"]

    # 先按单价粗筛，避免组合爆炸后再逐个核价
    cheap_burgers = [b for b in burgers if float(b["price"]) * units <= budget]
    cheap_sides = [s for s in sides if float(s["price"]) * units <= budget]
    cheap_drinks = [d for d in drinks if float(d["price"]) * units <= budget]

    for b, s, d in itertools.product(cheap_burgers, cheap_sides, cheap_drinks):
        picked = [b, s, d]
        if sum(float(i["price"]) for i in picked) * units > budget * 1.1:
            continue
        raw.append((f"{b['name']} + {s['name']} + {d['name']}", picked, 1))

    # 3b) 两件组合：预算紧的时候，汉堡 + 饮料或汉堡 + 小食才是真答案
    for b, d in itertools.product(cheap_burgers, cheap_drinks):
        picked = [b, d]
        if sum(float(i["price"]) for i in picked) * units > budget * 1.05:
            continue
        raw.append((f"{b['name']} + {d['name']}", picked, 1))
    for b, s in itertools.product(cheap_burgers, cheap_sides):
        picked = [b, s]
        if sum(float(i["price"]) for i in picked) * units > budget * 1.05:
            continue
        raw.append((f"{b['name']} + {s['name']}", picked, 1))

    # 4) 小份组合：单品 + 甜品（治愈 / 单人 / 夜宵场景）
    if wish.people == 1:
        for b, t in itertools.product(cheap_burgers + cheap_sides, desserts):
            picked = [b, t]
            if sum(float(i["price"]) for i in picked) > budget * 1.1:
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


# ------------------------------------------------------------------ 多人分工

def build_party_plan(wish, snap: dict | None = None, limit: int = 2) -> list[dict]:
    """多人各点不同的分工方案。

    与 build_candidates 的差别只有一件事：那边是「同一份 × N 人」，
    这里每个人拿到的是不同的一份。

    为什么需要这个：聚餐的真实难点从来不是算总价，而是「谁吃什么」。
    四份一模一样的餐摆在桌上，就是那句「随便」的后果。
    所以这里的目标不是「最省」，是「每个人都能接受，且桌上不重复」。
    """
    snap = snap or load_snapshot()
    units = max(wish.people, 1)
    budget = wish.budget
    coupons = snap.get("coupons", [])
    channel = snap.get("channel_default", "pickup")
    fee = float(snap.get("delivery_fee", 9.0))
    per_head = budget / units

    scored = []
    for m in snap["meals"]:
        price = float(m["price"])
        if price > per_head * 1.35:
            continue
        sc, reasons = _score([m], wish, per_head * 1.35, price)
        # 多人场合，整份餐（三件套/套餐）更像聚餐，不像凑数
        if m.get("full_meal"):
            sc += 2.0
        scored.append((sc, m, reasons))
    scored.sort(key=lambda x: (-x[0], float(x[1]["price"])))

    # 每人至少一份主食：纯饮料不能占掉一个人的名额。
    # 只有当非饮料候选不够分时，才允许饮料补位。
    mains = [x for x in scored if x[1]["category"] != "饮料"]
    pool = mains if len(mains) >= units else scored
    if len(pool) < units:
        return []

    plans: list[dict] = []
    for strategy in ("varied", "loose"):
        picks: list[tuple[dict, list[str]]] = []
        used: set[str] = set()
        cats: set[str] = set()

        # 每人固定额度，不动态挪用后面人的预算。
        # 动态挪用的后果是「前三个吃套餐，第四个只剩一杯饮料」。
        cap = per_head * 1.02

        for i in range(units):
            chosen = None
            for _sc, m, reasons in pool:
                if m["code"] in used:
                    continue
                if float(m["price"]) > cap:
                    continue
                if strategy == "varied" and m["category"] in cats:
                    continue
                chosen = (m, reasons)
                break
            if chosen is None and strategy == "varied":
                # 品类不重复已经凑不齐，退一步允许同类
                for _sc, m, reasons in pool:
                    if m["code"] in used:
                        continue
                    if float(m["price"]) > cap:
                        continue
                    chosen = (m, reasons)
                    break
            if chosen is None:
                # 额度内没有可选的了，取剩余里最便宜的一份补齐
                rest = [x for x in pool if x[1]["code"] not in used]
                if not rest:
                    break
                cheapest = min(rest, key=lambda x: float(x[1]["price"]))
                chosen = (cheapest[1], cheapest[2])
            m, reasons = chosen
            used.add(m["code"])
            cats.add(m["category"])
            picks.append((m, reasons))

        if len(picks) < units:
            continue

        items = [m for m, _ in picks]
        priced = compare(items, coupons, channel=channel, slot=wish.meal_slot, delivery_fee=fee, units=1)
        payable = priced["official"]["payable"]
        if payable > budget + 0.01:
            continue

        plans.append(
            {
                "strategy": strategy,
                "assignments": [
                    {"no": i + 1, "meal": m, "reasons": r} for i, (m, r) in enumerate(picks)
                ],
                "items": items,
                "price": priced,
                "payable": payable,
                "category_count": len(cats),
                "distinct": len({m["code"] for m in items}),
            }
        )

    plans.sort(key=lambda p: (-p["distinct"], -p["category_count"], p["payable"]))
    return plans[:limit]


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
