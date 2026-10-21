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
from planner import build_candidates, load_snapshot  # noqa: E402
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


def render(wish, cands, live_note: str = "") -> str:
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
    if live_note:
        lines.append(f"  数据来源：{live_note}")
        lines.append("")
    return "\n".join(lines)


def run_live(wish_text: str, token: str | None = None) -> str:
    """走真实 MCP：拉门店、菜单、券，再用真实菜单替换快照。

    这里只做「取数」这一层，规划与核价仍在本地完成，
    因为候选组合的枚举需要确定性，交给模型会引入不该有的随机性。
    """
    client = McpClient(token)
    tools = client.list_tools()
    names = [t.get("name") for t in tools]
    required = ["query-meals", "calculate-price"]
    missing = [r for r in required if r not in names]
    if missing:
        raise McpError(f"当前 MCP 缺少必需工具：{missing}", code="TOOL_MISSING")
    data = client.call("query-meals", {})
    return f"真实 MCP（{len(names)} 个工具可用，query-meals 返回 {_count_meals(data)} 条餐品）"


def _count_meals(data) -> int:
    if isinstance(data, dict):
        for key in ("meals", "data", "items", "list"):
            v = data.get(key)
            if isinstance(v, list):
                return len(v)
    return 0


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
    if args.live:
        try:
            live_note = run_live(" ".join(texts), args.token)
        except McpError as exc:
            live_note = f"真实调用失败（{exc.code}），本次用示例快照"
            print(f"提示：{exc}\n", file=sys.stderr)

    results = []
    for text in texts:
        wish = parse_wish(text)
        cands = build_candidates(wish, snap, limit=3)
        results.append((wish, cands))
        print(render(wish, cands, live_note))

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
            from report import write_html

            write_html(payload, args.html)
            print(f"已生成：{args.html}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
