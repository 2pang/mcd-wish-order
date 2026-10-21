"""抓一份真实菜单快照，转成本作品的快照格式。

用法：
    export MCD_MCP_TOKEN=...
    python scripts/capture.py --city 北京 --keyword 北京

做三件事：
1. 查门店 → 取第一个可用门店的 storeCode
2. query-meals 拿真实菜单（categories 是骨架，meals 是 code → 详情）
3. 转成 snapshot 格式并写盘，同时保留原始响应供核对

金额单位提醒：query-meals 的 currentPrice 是「元」的字符串，
而 calculate-price 返回的是「分」的整数。两者差 100 倍，这是实测出来的坑。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mcd_client import McpClient, McpError  # noqa: E402

ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")

# 名称 → 标签。真实菜单不返回 tags，只能按名称推断。
# 规则写死在这里，是为了让推断过程可核对、可复现，而不是交给模型自由发挥。
TAG_RULES = [
    (r"美式|红茶|纯牛奶|燕麦奶|无糖|零度", ["light", "lowcal"]),
    (r"麦辣|辣", ["spicy"]),
    (r"巨无霸|安格斯|厚牛|牛堡|双层牛|吉士汉堡|培根|不素之霸", ["beef"]),
    (r"板烧|鸡腿|麦香鸡|麦乐鸡|炸鸡|鸡排|鸡翅|那么大鸡|脆汁鸡|V翅", ["chicken"]),
    (r"鳕鱼|麦香鱼|鱼堡", ["fish"]),
    (r"麦咖啡|咖啡|拿铁|美式|卡布奇诺", ["caffeine", "drink"]),
    (r"可乐|雪碧|芬达|橙|柠|饮|茶|果汁|美汁源|牛奶|燕麦奶", ["drink"]),
    (r"薯条|薯饼", ["fries"]),
    (r"冰淇淋|麦旋风|圆筒|新地|派|甜品|蛋糕|圣代", ["dessert", "sweet"]),
    (r"玉米杯|苹果|沙拉|蔬菜|红茶", ["light"]),
    (r"套餐|三件套|大套餐|组合|随心配|四宫格|拼盘|双拼", ["staple", "variety"]),
    (r"堡|汉堡|卷", ["staple"]),
]

# 真实菜单会混进非食品类商品（周边、玩具等），必须排除，否则会被当成餐品推荐
EXCLUDE_PAT = r"按摩捶|痒痒挠|玩具|周边|卡片|盲盒|徽章|公仔|礼盒|券$"

CATEGORY_RULES = [
    ("人气", "人气"),
    ("精选单人餐", "套餐"),
    ("鸡肉汉堡", "汉堡"),
    ("小食拼盘", "小食"),
    ("巨无霸", "汉堡"),
    ("安格斯", "汉堡"),
    ("炸鸡", "小食"),
    ("小食甜品", "小食"),
    ("麦咖啡", "饮料"),
    ("饮品", "饮料"),
]

# 名称含这些词的，视为「一份完整餐」，单独作为候选，不再拆成汉堡+小食+饮料
FULL_MEAL_PAT = r"套餐|三件套|拼盘|四宫格|双拼|组合|随心配"


def map_category(raw: str) -> str:
    for prefix, mapped in CATEGORY_RULES:
        if raw.startswith(prefix):
            return mapped
    return raw or "其他"


def clean_name(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").replace("\n", ""))


def guess_tags(name: str) -> list[str]:
    tags: list[str] = []
    for pattern, add in TAG_RULES:
        if re.search(pattern, name):
            for t in add:
                if t not in tags:
                    tags.append(t)
    if not tags:
        tags = ["staple"]
    return tags


def pick_store(client: McpClient, city: str, keyword: str) -> dict:
    r = client.call(
        "query-nearby-stores", {"beType": 1, "searchType": 2, "city": city, "keyword": keyword}
    )
    if not r.get("success"):
        raise McpError(f'查门店失败：{r.get("message")}（code={r.get("code")}）', code="STORE_FAIL")
    stores = r.get("data") or []
    open_stores = [s for s in stores if s.get("businessStatus")] or stores
    if not open_stores:
        raise McpError("没查到可用门店", code="NO_STORE")
    return open_stores[0]


def capture(city: str, keyword: str) -> dict:
    client = McpClient()
    store = pick_store(client, city, keyword)
    store_code = store["storeCode"]

    meals_resp = client.call("query-meals", {"storeCode": store_code, "orderType": 1, "beType": 1})
    if not meals_resp.get("success"):
        raise McpError(f'取菜单失败：{meals_resp.get("message")}', code="MEALS_FAIL")
    data = meals_resp["data"]

    # categories 只是骨架（code + tags），详情在 meals 字典里
    code_to_cat: dict[str, str] = {}
    for cat in data.get("categories", []):
        raw = clean_name(cat.get("name", ""))
        mapped = map_category(raw)
        for m in cat.get("meals", []):
            code_to_cat[m["code"]] = mapped

    meals = []
    for code, info in (data.get("meals") or {}).items():
        name = clean_name(info.get("name", ""))
        if not name or re.search(EXCLUDE_PAT, name):
            continue
        current = info.get("currentPrice")
        original = info.get("originalPrice")
        try:
            price = float(current) if current is not None else 0.0
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            continue
        meals.append(
            {
                "code": code,
                "name": name,
                "category": code_to_cat.get(code, "其他"),
                "price": price,
                "original_price": float(original) if original else price,
                "full_meal": bool(re.search(FULL_MEAL_PAT, name)),
                "tags": guess_tags(name),
            }
        )

    meals.sort(key=lambda m: (m["category"], -m["price"]))

    # 券池：测试账号券包为空（query-store-coupons 返回 []，available-coupons 返回 null），
    # 因此券数据使用结构对齐的示例，并在 meta 里标注来源，不冒充真实券。
    coupons = []
    coupon_path = os.path.join(DATA, "coupons_demo.json")
    if os.path.exists(coupon_path):
        with open(coupon_path, encoding="utf-8") as f:
            coupons = json.load(f).get("coupons", [])

    return {
        "meta": {
            "source": "captured",
            "captured_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "note": "菜单与价格来自麦当劳 MCP 的 query-meals 真实返回（到店自取场景）。"
                    "券池部分为结构对齐的示例数据：测试账号券包为空，无法取得真实券。"
                    "price 单位为元，由 query-meals 的 currentPrice 直接解析；"
                    "注意 calculate-price 返回的金额单位是分，两者相差 100 倍。",
            "store_code": store_code,
            "store_name": store.get("storeName"),
            "business_hours": f'{store.get("businessStartTime")}-{store.get("businessEndTime")}',
            "meal_count": len(meals),
            "category_count": len({m["category"] for m in meals}),
        },
        "store": {
            "code": store_code,
            "name": store.get("storeName"),
            "available_slots": ["breakfast", "lunch", "dinner", "latenight"],
        },
        "meals": meals,
        "combos": [],
        "coupons": coupons,
        "coupons_source": "demo" if coupons else "none",
        "delivery_fee": 9.0,
        "channel_default": "pickup",
        "_raw_categories": [
            {"name": clean_name(c.get("name", "")), "count": len(c.get("meals", []))}
            for c in data.get("categories", [])
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="抓真实菜单快照")
    ap.add_argument("--city", default="北京")
    ap.add_argument("--keyword", default="北京")
    ap.add_argument("--out", default=os.path.join(DATA, "snapshot_real.json"))
    ap.add_argument("--raw", default=os.path.join(DATA, "raw_query_meals.json"))
    args = ap.parse_args()

    try:
        snap = capture(args.city, args.keyword)
    except McpError as exc:
        print(f"[{exc.code}] {exc}")
        if exc.hint:
            print("提示：", exc.hint)
        return 1

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    os.makedirs(os.path.dirname(args.raw), exist_ok=True)
    with open(args.raw, "w", encoding="utf-8") as f:
        json.dump({"store": snap["store"], "meals_sample": snap["meals"][:5]}, f, ensure_ascii=False, indent=2)

    m = snap["meta"]
    print(f'门店：{m["store_name"]}（{m["store_code"]}）')
    print(f'餐品：{m["meal_count"]} 款，分类 {m["category_count"]} 个')
    print(f'分类：{[c["name"] for c in snap["_raw_categories"]]}')
    print(f"已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
