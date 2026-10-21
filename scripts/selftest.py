"""自检脚本：不需要 Token，验证解析、核价、边界三组行为。

用法：
    python scripts/selftest.py

每条断言都对应一条明确的规则。改逻辑后必须重跑。
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from planner import build_candidates, load_snapshot  # noqa: E402
from pricing import compare, official_price  # noqa: E402
from wish_engine import parse_wish  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name if not detail else f"{name} → {detail}")


# ---------------------------------------------------------------- 解析

w = parse_wish("今天被领导骂了一顿，想吃点能治愈的，别超过30块")
check("识别显式预算", w.budget == 30, f"got {w.budget}")
check("识别情绪词", "治愈" in w.moods, str(w.moods))
check("情绪映射到口味", "sweet" in w.taste, str(w.taste))

w2 = parse_wish("明天要带三个同事吃饭，我不想显得抠，也不想超预算")
check("带三人 → 共 4 人", w2.people == 4, f"got {w2.people}")
check("否定式「不想显得抠」判为要体面", "share" in w2.taste, str(w2.taste))
check("未给金额时标记为默认值", w2.source.get("budget") == "default", str(w2.source))

w3 = parse_wish("加班到现在，很饿，想吃饱一点，不吃辣")
check("「不吃辣」不进偏好", "spicy" not in w3.taste, str(w3.taste))
check("「不吃辣」进排除项", "spicy" in w3.avoid, str(w3.avoid))
check("「很饿」不与「饿」重复计", w3.moods.count("饿") == 0, str(w3.moods))

# ---------------------------------------------------------------- 核价

# 用固定的示例快照跑断言，避免真实抓取的数据变化导致结果漂移
snap = load_snapshot("snapshot_menu.json")
idx = {m["code"]: m for m in snap["meals"]}
items = [idx["M003"], idx["T001"]]  # 板烧 23 + 圆筒 4 = 27

r = compare(items, snap["coupons"], channel="pickup", slot="lunch")
check("口算忽略门槛，给出更低价", r["naive"]["payable"] < r["official"]["payable"],
      f'{r["naive"]["payable"]} vs {r["official"]["payable"]}')
check("官方口径不应用未达门槛的券", r["official"]["discount"] == 0, str(r["official"]["discount"]))
check("差额为正（口算低估）", r["gap"] > 0, str(r["gap"]))

# 达到门槛时应当用券
items2 = [idx["M001"], idx["S001"], idx["D001"]]  # 24.5 + 12 + 9.5 = 46
o2 = official_price(items2, snap["coupons"], channel="pickup", slot="lunch")
check("达门槛时应用满减券", o2.discount > 0, str(o2.discount))

# 渠道限制
o3 = official_price(items2, snap["coupons"], channel="pickup", slot="lunch")
o4 = official_price(items2, snap["coupons"], channel="delivery", slot="lunch")
check("外送场景计入配送费", o4.delivery_fee > 0, str(o4.delivery_fee))

# 时段限制：早餐券在午餐不可用
o5 = official_price([idx["M005"], idx["S001"]], snap["coupons"], channel="pickup", slot="lunch")
check("午餐不用早餐券", all("早餐" not in a for a in o5.applied), str(o5.applied))

# 多人合并核价：门槛按整单金额判断
one = compare([idx["M001"], idx["S001"], idx["D001"]], snap["coupons"], slot="lunch", units=1)
four = compare([idx["M001"], idx["S001"], idx["D001"]], snap["coupons"], slot="lunch", units=4)
check("四人总价约为单人四倍（含阶梯优惠）", 3.5 * one["official"]["payable"] < four["official"]["payable"],
      f'{one["official"]["payable"]} → {four["official"]["payable"]}')

# ---------------------------------------------------------------- 规划

cands = build_candidates(w, snap, limit=3)
check("能给出候选方案", len(cands) > 0, str(len(cands)))
check("候选均不超预算", all(c.payable <= w.budget + 0.01 for c in cands),
      str([round(c.payable, 2) for c in cands]))
check("候选按分数降序", all(cands[i].score >= cands[i + 1].score for i in range(len(cands) - 1)))
check("每个候选都有理由", all(c.reasons for c in cands))

# 超低预算应无解而不是硬凑
w_low = parse_wish("想吃麦当劳，别超过5块")
low = build_candidates(w_low, snap, limit=3)
check("超低预算返回空而不是硬凑", len(low) == 0, str(len(low)))

# ---------------------------------------------------------------- 汇总

print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  FAIL:", f)
    sys.exit(1)
print("全部通过")
