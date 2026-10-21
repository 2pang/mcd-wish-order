#!/usr/bin/env python3
"""许愿式点餐 · 命令行入口

用法：
    python wish.py "今天被领导骂了一顿，想吃点能治愈的，别超过30块"
    python wish.py "明天要带三个同事吃饭，我不想显得抠，也不想超预算"
    python wish.py --demo                 # 跑内置示例
    python wish.py --probe                # MCP 连通自检（需 Token）
    python wish.py "..." --live           # 用真实 MCP 数据核价
    python wish.py "..." --html out.html  # 同时导出单文件 HTML

设计上只做一件事：把人说的一句愿望，变成一份付得起的订单，
并且把「AI 算的钱」和「官方核的钱」摆在一起给人看。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from mcd_client import McpClient, McpError  # noqa: E402
from planner import build_candidates, build_party_plan, is_heavy, load_snapshot  # noqa: E402
from pricing import compare  # noqa: E402
from wish_engine import SLOT_CN, parse_wish  # noqa: E402


def _dw(text: str) -> int:
    """按终端显示宽度算长度：中日韩全角字符占 2 列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _dw(text))


DEFAULT_CASES = [
    "今天被领导骂了一顿，想吃点能治愈的，别超过30块",
    "明天要带三个同事吃饭，我不想显得抠，也不想超预算",
    "减肥第三周，馋得不行，给我找个能骗过自己的",
    "加班到现在，很饿，想吃饱一点，不吃辣",
]

RULE = "─" * 62


def render(wish, cands, live_note: str = "", official: dict | None = None) -> str:
    """把一次许愿的完整过程排成可读、可截图的文本。"""
    lines: list[str] = []
    lines.append(RULE)
    lines.append(f"  你说：{wish.raw}")
    lines.append(RULE)
    lines.append("")
    lines.append("  愿望拆开看")
    for key, label in (("budget", "总预算"), ("people", "人数"), ("meal_slot", "餐段")):
        val = {
            "budget": f"{wish.budget:.0f} 元",
            "people": f"{wish.people} 人",
            "meal_slot": SLOT_CN.get(wish.meal_slot, wish.meal_slot),
        }[key]
        tag = {"explicit": "你说的", "inferred": "我推断的", "default": "我替你定的"}[
            wish.source.get(key, "default")
        ]
        lines.append(f"    {pad(label, 8)}{pad(val, 14)}{tag}")
    if wish.moods:
        lines.append(f"    情绪    {'、'.join(wish.moods)} → {' / '.join(wish.taste) or '无特定倾向'}")
    if wish.avoid:
        lines.append(f"    排除    {'、'.join(wish.avoid)}")
    for n in wish.notes:
        lines.append(f"    说明    {n}")
    lines.append("")

    if not cands:
        lines.append("  没有在预算内找到方案。可以放宽预算，或把「想吃」说得更具体一点。")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"  找到 {len(cands)} 个方案（按官方实付价与贴合度排序）")
    lines.append("")
    for i, c in enumerate(cands, 1):
        p = c.price
        lines.append(f"  {i}. {c.name}")
        lines.append(
            f"     实付 {c.payable:>7.2f} 元"
            f" ｜ 口算 {p['naive']['payable']:>7.2f} 元"
            f" ｜ 差 {p['gap']:>+6.2f} 元"
        )
        if c.reasons:
            lines.append(f"     理由：{'；'.join(c.reasons)}")
        lines.append("")

    best = cands[0]
    p = best.price
    lines.append(RULE)
    lines.append("  第一名的账，算两遍")
    lines.append(RULE)
    lines.append(f"    商品原价合计        {p['official']['subtotal']:>8.2f} 元")
    if p["official"]["delivery_fee"]:
        lines.append(f"    配送费              {p['official']['delivery_fee']:>8.2f} 元")
    lines.append(
        f"    AI 式口算           {p['naive']['payable']:>8.2f} 元"
        f"   （扣 {p['naive']['discount']:.2f}）"
    )
    lines.append(
        f"    官方口径            {p['official']['payable']:>8.2f} 元"
        f"   （扣 {p['official']['discount']:.2f}）"
    )
    lines.append("")
    for r in p["naive"]["notes"]:
        lines.append(f"      口算这一步：{r}")
    if p["official"]["applied"]:
        lines.append(f"      官方真正生效的券：{'、'.join(p['official']['applied'])}")
    for r in p["official"]["rejected"][:3]:
        lines.append(f"      用不了：{r}")
    lines.append("")
    if abs(p["gap"]) > 0.005:
        lines.append(f"      差 {p['gap']:+.2f} 元。{p['verdict']}。")
    else:
        lines.append("      这一次两种算法结果一致。口算不一定错，错的时候你看不出来。")
    lines.append("")

    # 有真实接口就给官方答案，让本地实现自己认账
    if official is not None:
        if official.get("error"):
            lines.append(f"  官方接口核价失败：{official['error']}")
        else:
            local = p["official"]["payable"]
            same = abs(official["payable"] - local) < 0.01
            lines.append(
                f"  官方接口实调      {official['payable']:>8.2f} 元"
                f"   （calculate-price 返回 {official['raw_cent']} 分）"
            )
            lines.append(
                f"  {'与本地口径一致' if same else '与本地口径不一致，错在本地实现'}"
                f"（本地 {local:.2f} 元）"
            )
        lines.append("")

    # 做不到就直说：低卡诉求下若最接近的方案仍含炸物，不假装它是减肥餐
    if "lowcal" in wish.taste:
        bad = [i["name"] for i in best.items if is_heavy(i)]
        if bad:
            lines.append(f"  说明：这个门店没有真正低卡的组合，给出了最接近的。仍含 {('、'.join(bad))}。")
            lines.append("")
    if live_note:
        lines.append(f"  数据来源：{live_note}")
        lines.append("")
    return "\n".join(lines)


def official_check(client: McpClient, snap: dict, items: list[dict], units: int) -> dict | None:
    """拿 calculate-price 的真实返回，与本地的 official_price 对拍。

    这是本作品的自证机制：本地实现对券规则的理解对不对，不由作者声明，
    由官方接口给答案。两者不一致时，错的一定是本地实现。
    """
    store_code = (snap.get("store") or {}).get("code")
    if not store_code:
        return None
    payload_items = [{"productCode": i["code"], "quantity": units} for i in items]
    try:
        resp = client.call(
            "calculate-price",
            {"storeCode": store_code, "orderType": 1, "beType": 1, "items": payload_items},
        )
    except McpError as exc:
        return {"error": f"[{exc.code}] {exc}"}
    if not resp.get("success"):
        return {"error": resp.get("message") or "calculate-price 返回失败"}
    data = resp.get("data") or {}
    cent = data.get("price")
    if cent is None:
        return {"error": "返回里没有 price 字段"}
    # 关键：calculate-price 的金额单位是「分」
    return {
        "payable": round(float(cent) / 100.0, 2),
        "raw_cent": cent,
        "product_list": data.get("productList") or [],
        "take_way": data.get("takeWayList") or [],
        "discount_cent": data.get("discount", 0),
    }


def render_party(wish, plans, live_note: str = "", official: dict | None = None) -> str:
    """多人分工方案的输出。核心不是总价，是「谁吃什么」。"""
    lines: list[str] = []
    lines.append(RULE)
    lines.append(f"  你说：{wish.raw}")
    lines.append(RULE)
    lines.append("")
    lines.append("  愿望拆开看")
    lines.append(f"    {pad('人数', 8)}{pad(f'{wish.people} 人', 14)}"
                 f"{ {'explicit': '你说的', 'inferred': '我推断的', 'default': '我替你定的'}[wish.source.get('people','default')] }")
    lines.append(f"    {pad('总预算', 8)}{pad(f'{wish.budget:.0f} 元', 14)}"
                 f"{ {'explicit': '你说的', 'inferred': '我推断的', 'default': '我替你定的'}[wish.source.get('budget','default')] }")
    lines.append(f"    {pad('餐段', 8)}{pad(SLOT_CN.get(wish.meal_slot, wish.meal_slot), 14)}"
                 f"{ {'explicit': '你说的', 'inferred': '我推断的', 'default': '我替你定的'}[wish.source.get('meal_slot','default')] }")
    if wish.undecided:
        lines.append("    态度    随便（不是没要求，是不想担决策责任）")
    for n in wish.notes:
        lines.append(f"    说明    {n}")
    lines.append("")

    if not plans:
        lines.append("  没凑出每人一份的分工方案。可以放宽人均预算，或减少人数。")
        lines.append("")
        return "\n".join(lines)

    best = plans[0]
    lines.append(f"  {wish.people} 个人，不用 {wish.people} 份一样的")
    lines.append("")
    for a in best["assignments"]:
        m = a["meal"]
        lines.append(f"    {a['no']}. {pad(m['name'], 26)}{m['price']:>7.2f} 元")
    lines.append("")
    lines.append(
        f"    {best['distinct']} 种不同 · 覆盖 {best['category_count']} 个品类"
        f" · 实付 {best['payable']:.2f} 元（人均 {best['payable'] / wish.people:.2f}）"
    )
    lines.append("")

    if len(plans) > 1:
        alt = plans[1]
        names = "、".join(a["meal"]["name"] for a in alt["assignments"][:3])
        lines.append(f"    另一套（品类更集中）：{names}…  实付 {alt['payable']:.2f} 元")
        lines.append("")

    lines.append(RULE)
    lines.append("  这一桌的账，算两遍")
    lines.append(RULE)
    p = best["price"]
    lines.append(f"    商品原价合计        {p['official']['subtotal']:>8.2f} 元")
    if p["official"]["delivery_fee"]:
        lines.append(f"    配送费              {p['official']['delivery_fee']:>8.2f} 元")
    lines.append(f"    AI 式口算           {p['naive']['payable']:>8.2f} 元   （扣 {p['naive']['discount']:.2f}）")
    lines.append(f"    官方口径            {p['official']['payable']:>8.2f} 元   （扣 {p['official']['discount']:.2f}）")
    lines.append("")
    for r in p["naive"]["notes"]:
        lines.append(f"      口算这一步：{r}")
    if p["official"]["applied"]:
        lines.append(f"      官方真正生效的券：{'、'.join(p['official']['applied'])}")
    lines.append("")
    if abs(p["gap"]) > 0.005:
        lines.append(f"      差 {p['gap']:+.2f} 元。{p['verdict']}。")
    else:
        lines.append("      这一次两种算法结果一致。")
    lines.append("")

    if official is not None and not official.get("error"):
        local = p["official"]["payable"]
        same = abs(official["payable"] - local) < 0.01
        lines.append(f"  官方接口实调      {official['payable']:>8.2f} 元   （返回 {official['raw_cent']} 分）")
        lines.append(f"  {'与本地口径一致' if same else '与本地口径不一致，错在本地实现'}（本地 {local:.2f} 元）")
        lines.append("")

    lines.append("  桌上不重样的原因：四份一样的摆上来，就是那句「随便」的后果。")
    lines.append("")
    if live_note:
        lines.append(f"  数据来源：{live_note}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="许愿式点餐：说一句愿望，拿一份付得起的订单")
    ap.add_argument("wish", nargs="*", help="你想要的，一句话就行")
    ap.add_argument("--demo", action="store_true", help="跑内置示例")
    ap.add_argument("--probe", action="store_true", help="MCP 连通自检（需 MCD_MCP_TOKEN）")
    ap.add_argument("--live", action="store_true", help="用真实 MCP 数据（需 MCD_MCP_TOKEN）")
    ap.add_argument("--token", default=None, help="MCP Token，也可用环境变量 MCD_MCP_TOKEN")
    ap.add_argument("--html", default=None, help="导出单文件 HTML 到指定路径")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON")
    args = ap.parse_args(argv)

    if args.probe:
        try:
            report = McpClient(args.token).probe()
        except McpError as exc:
            print(f"[{exc.code}] {exc}")
            if exc.hint:
                print(f"提示：{exc.hint}")
            return 1
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    texts = args.wish or DEFAULT_CASES
    if not args.wish and not args.demo:
        ap.print_help()
        return 0

    snap = load_snapshot()
    live_note = ""
    client = None
    if args.live:
        try:
            client = McpClient(args.token)
            probe = client.probe()
            live_note = f"真实 MCP 已连接（{probe['tool_count']} 个工具可用）"
        except McpError as exc:
            live_note = f"真实调用失败（{exc.code}），本次用快照数据"
            print(f"提示：{exc}\n", file=sys.stderr)
            client = None

    results = []
    for text in texts:
        wish = parse_wish(text)

        # 两人及以上走分工模式：不是「同一份 × N」，而是「每人一份不同的」
        if wish.people >= 2:
            plans = build_party_plan(wish, snap, limit=2)
            official = None
            if client and plans:
                official = official_check(client, snap, plans[0]["items"], 1)
            print(render_party(wish, plans, live_note, official))
            flat = []
            if plans:
                b = plans[0]
                flat = [
                    {
                        "name": f"{wish.people} 人分工：" + " / ".join(a["meal"]["name"] for a in b["assignments"]),
                        "items": [a["meal"]["name"] for a in b["assignments"]],
                        "codes": [a["meal"]["code"] for a in b["assignments"]],
                        "unit_count": wish.people,
                        "score": 0,
                        "reasons": ["每人一份不同的，桌上不重样"],
                        "price": b["price"],
                        "payable": b["payable"],
                    }
                ]
            results.append((wish, flat))
            continue

        cands = build_candidates(wish, snap, limit=3)
        results.append((wish, cands))
        official = None
        if client and cands:
            best = cands[0]
            official = official_check(client, snap, best.items, max(wish.people, 1))
        print(render(wish, cands, live_note, official))

    if args.json or args.html:
        payload = {
            "generated_at": None,
            "cases": [
                {
                    "wish": w.to_dict(),
                    "candidates": [c.as_dict() for c in cands],
                }
                for w, cands in results
            ],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.html:
            from report import source_note, write_html

            payload["source_note"] = source_note(snap)
            write_html(payload, args.html)
            print(f"已生成：{args.html}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
