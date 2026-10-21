"""愿望 → 约束翻译器。

「许愿式点餐」的第一层：用户说的是愿望，不是参数。
本模块负责把一句带情绪的模糊输入，翻译成可执行的硬约束，并且把每个字段的
来源标出来（显式给出的 / 我替你猜的），让用户能看见 AI 在做什么判断。

设计原则：
1. 不追问。缺参数就按常识给默认值，并在 notes 里说明「这是猜的，可以改」。
2. 每个推断都要可解释。confidence 记录字段来源：explicit / inferred / default。
3. 只用规则，不依赖模型。在 WorkBuddy 里由 Agent 承担更自然的翻译，本模块是离线兜底，
   保证没有模型也能跑出确定结果。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

# ---------------------------------------------------------------- 词表

MEAL_SLOTS = ("breakfast", "lunch", "dinner", "latenight")

# 情绪/处境 → 品类倾向。右侧是给 planner 的软信号，不是硬过滤。
MOOD_TO_TASTE = {
    "治愈": ["sweet", "warm", "comfort"],
    "安慰": ["sweet", "warm", "comfort"],
    "难过": ["sweet", "comfort"],
    "emo": ["sweet", "comfort"],
    "被骂": ["sweet", "comfort"],
    "委屈": ["sweet", "comfort"],
    "累": ["comfort", "caffeine"],
    "加班": ["filling"],
    "熬夜": ["caffeine", "filling"],
    "奖励": ["beef", "large", "dessert"],
    "犒劳": ["beef", "large", "dessert"],
    "发工资": ["beef", "large", "dessert"],
    "开心": ["dessert", "sweet"],
    "减肥": ["light", "lowcal"],
    "减脂": ["light", "lowcal"],
    "低卡": ["light", "lowcal"],
    "控卡": ["light", "lowcal"],
    "省钱": ["value"],
    "划算": ["value"],
    "便宜": ["value"],
    "饿": ["filling", "staple"],
    "很饿": ["filling", "staple", "large"],
    "饱": ["filling", "staple"],
    "请客": ["share", "variety"],
    "面子": ["share", "variety", "large"],
    "抠": ["share", "variety"],
    "同事": ["share", "variety"],
    "赶时间": ["quick"],
    "清淡": ["light"],
}

# 口味词 → 食材/品类偏好
TASTE_WORDS = {
    "辣": "spicy",
    "甜": "sweet",
    "咖啡": "caffeine",
    "可乐": "soda",
    "鸡": "chicken",
    "牛": "beef",
    "鱼": "fish",
    "薯条": "fries",
    "冰淇淋": "dessert",
    "甜品": "dessert",
}

AVOID_PATTERNS = {
    "不辣": "spicy",
    "不吃辣": "spicy",
    "不要辣": "spicy",
    "不要甜": "sweet",
    "不吃甜": "sweet",
    "不要饮料": "drink",
    "不喝": "drink",
}

# 「随便」不是没有偏好，而是不愿承担决策责任。
# 这类输入不能反问「你想吃什么」（他答不出），只能给差异化的选项让他挑。
UNDECIDED_PATTERNS = [
    r"随便",
    r"都行",
    r"都可以",
    r"无所谓",
    r"不知道(吃什么|吃啥|点啥)?",
    r"你(来)?定",
    r"你(帮我)?选",
    r"看你们",
    r"吃啥都行",
    r"没啥想法",
    r"没想法",
]

CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 每人预算的兜底默认值（元）。用于用户没说钱、只说了人数或情绪的场合。
DEFAULT_BUDGET_PER_HEAD = {"breakfast": 25, "lunch": 55, "dinner": 60, "latenight": 45}


SLOT_CN = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "latenight": "夜宵"}


@dataclass
class Wish:
    """一条被翻译过的愿望。字段全部可解释。"""

    raw: str
    budget: float
    people: int
    meal_slot: str
    moods: list[str] = field(default_factory=list)
    taste: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    undecided: bool = False
    source: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def per_head(self) -> float:
        return round(self.budget / max(self.people, 1), 2)

    def explain(self) -> list[str]:
        """把翻译过程讲成人话，供终端与 HTML 直接展示。"""
        lines = [f"你说的是：{self.raw}"]
        for key, label in (
            ("budget", "总预算"),
            ("people", "人数"),
            ("meal_slot", "餐段"),
        ):
            val = {
                "budget": f"{self.budget:.0f} 元",
                "people": f"{self.people} 人",
                "meal_slot": SLOT_CN.get(self.meal_slot, self.meal_slot),
            }[key]
            tag = {"explicit": "你说的", "inferred": "我推断的", "default": "我替你定的"}[self.source.get(key, "default")]
            lines.append(f"  {label}：{val}（{tag}）")
        if self.moods:
            lines.append(f"  情绪：{'、'.join(self.moods)} → 倾向 {'、'.join(self.taste) or '无特定倾向'}")
        if self.undecided:
            lines.append("  态度：随便（不是没要求，是不想担决策责任）")
        if self.avoid:
            lines.append(f"  排除：{'、'.join(self.avoid)}")
        if self.notes:
            for n in self.notes:
                lines.append(f"  说明：{n}")
        lines.append(f"  折合每人 {self.per_head:.0f} 元")
        return lines


# ---------------------------------------------------------------- 解析

_BUDGET_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB|人民币)")
_BUDGET_ALT_RE = re.compile(r"(?:预算|不超过|别超|最多|控制在)\s*(\d+(?:\.\d+)?)")
_BUDGET_SUFFIX_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元)?\s*(?:以内|以下|之内|左右)")
_PERHEAD_RE = re.compile(r"人均\s*(\d+(?:\.\d+)?)")
_PEOPLE_RE = re.compile(r"(\d+|[一二两三四五六七八九十])\s*(?:个)?\s*(?:人|位|同事|朋友|室友)")
_ALONE_RE = re.compile(r"(一个人|自己吃|单人|就我)")


def _cn_to_int(s: str) -> int:
    if s.isdigit():
        return int(s)
    return CN_NUM.get(s, 1)


def parse_wish(text: str, now_hour: int | None = None) -> Wish:
    """把一句愿望翻译成 Wish。这是整个产品的入口。"""
    raw = (text or "").strip()
    low = raw.lower()
    notes: list[str] = []
    source: dict = {}

    # ---- 人数（先算人数，人均预算要用）
    people = 1
    m = _PEOPLE_RE.search(raw)
    if m:
        people = _cn_to_int(m.group(1))
        source["people"] = "explicit"
        # 「带三个同事」= 我 + 三个 = 4 人
        if "带" in raw or "请" in raw:
            people += 1
            notes.append(f"你说带 {m.group(1)} 个，按「你 + 他们」共 {people} 人算")
    elif _ALONE_RE.search(raw):
        people = 1
        source["people"] = "explicit"
    else:
        source["people"] = "default"

    # ---- 预算
    budget = None
    # 「人均 50」这类说法在多人场景里最常见，必须按人数换算成总预算
    m = _PERHEAD_RE.search(raw)
    if m:
        per_head = float(m.group(1))
        budget = per_head * people
        source["budget"] = "explicit"
        notes.append(f"按人均 {per_head:.0f} 元 × {people} 人 = {budget:.0f} 元")
    else:
        m = _BUDGET_RE.search(raw) or _BUDGET_ALT_RE.search(raw) or _BUDGET_SUFFIX_RE.search(raw)
        if m:
            budget = float(m.group(1))
            source["budget"] = "explicit"

    # ---- 餐段
    slot = None
    if re.search(r"早|早餐|早饭", raw):
        slot = "breakfast"
        source["meal_slot"] = "explicit"
    elif re.search(r"中午|午餐|午饭|午饭|中饭", raw):
        slot = "lunch"
        source["meal_slot"] = "explicit"
    elif re.search(r"晚|晚餐|晚饭|夜宵|宵夜", raw):
        slot = "latenight" if re.search(r"夜宵|宵夜", raw) else "dinner"
        source["meal_slot"] = "explicit"

    # ---- 情绪与口味
    # 长词优先：命中「很饿」就不再单独记「饿」，避免同一情绪记两遍
    moods = []
    for w in MOOD_TO_TASTE:
        if w not in low:
            continue
        if any(o != w and w in o and o in low for o in MOOD_TO_TASTE):
            continue
        moods.append(w)

    taste: list[str] = []
    for w in moods:
        for t in MOOD_TO_TASTE[w]:
            if t not in taste:
                taste.append(t)

    for w, tag in TASTE_WORDS.items():
        if w in raw and tag not in taste:
            taste.append(tag)

    avoid = [tag for phrase, tag in AVOID_PATTERNS.items() if phrase in raw]

    # 否定优先级高于肯定：说了「不吃辣」就不该再把辣算进偏好
    if avoid:
        taste = [t for t in taste if t not in avoid]
        moods = [w for w in moods if not any(t in avoid for t in MOOD_TO_TASTE[w])]

    # 「不想显得抠 / 别丢面子」这类否定式表达，语义是「要体面」，不是「抠」
    if re.search(r"(不想|别|不要)\s*(显得|让人|被)?\s*(抠|小气|寒酸)", raw):
        for t in ("share", "variety", "large"):
            if t not in taste:
                taste.append(t)
        notes.append("「不想显得抠」按「要体面、份量要够」理解")


    # 「随便 / 都行 / 你定」：不是没有偏好，是不想担这个决策责任
    undecided = any(re.search(p, raw) for p in UNDECIDED_PATTERNS)
    if undecided:
        notes.append("听到「随便」了。不反问你，直接按大众口味给方案；哪一份不想要，说一声换掉就行")

    # ---- 预算兜底
    if budget is None:
        if slot is None:
            h = now_hour if now_hour is not None else 12
            slot = "breakfast" if h < 10 else ("lunch" if h < 15 else ("dinner" if h < 21 else "latenight"))
            source["meal_slot"] = "inferred"
            notes.append("你没说什么时候吃，按当前时间推的")
        per = DEFAULT_BUDGET_PER_HEAD[slot]
        budget = float(per * people)
        source["budget"] = "default"
        notes.append(f"你没给金额，按每人 {per} 元 × {people} 人 = {budget:.0f} 元先定，可以改")
    elif slot is None:
        h = now_hour if now_hour is not None else 12
        slot = "breakfast" if h < 10 else ("lunch" if h < 15 else ("dinner" if h < 21 else "latenight"))
        source["meal_slot"] = "inferred"

    if not moods and not taste and not undecided:
        notes.append("没听出明显偏好，按「不出错的大众口味」配")

    return Wish(
        raw=raw,
        budget=budget,
        people=people,
        meal_slot=slot,
        moods=moods,
        taste=taste,
        avoid=avoid,
        undecided=undecided,
        source=source,
        notes=notes,
    )


if __name__ == "__main__":
    import sys

    samples = sys.argv[1:] or [
        "今天被领导骂了一顿，想吃点能治愈的，别超过30块",
        "明天要带三个同事吃饭，我不想显得抠，也不想超预算",
        "减肥第三周，馋得不行，给我找个能骗过自己的",
        "加班到现在，很饿，想吃饱一点，不吃辣",
    ]
    for s in samples:
        print("=" * 60)
        for line in parse_wish(s).explain():
            print(line)
