"""生成单文件 HTML 演示页。

零依赖、无 CDN、无外链字体，双击就能看，也可以直接挂 GitHub Pages。
所有示例数据在生成时内嵌为 JSON，页面本身不发任何网络请求。
"""

from __future__ import annotations

import json
import os

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>许愿式点餐 · 你说愿望，它报价格</title>
<style>
  :root{
    --ink:#17171a; --ink2:#5b5b63; --ink3:#8e8e96;
    --line:#e6e3dd; --bg:#fbfaf7; --card:#fff;
    --red:#c8102e; --red-soft:#fdeef0; --gold:#f2b705;
    --green:#1f7a4d; --grey-soft:#f3f2ef;
    --radius:14px;
  }
  *{box-sizing:border-box}
  *{min-width:0}
  html,body{margin:0;padding:0;overflow-x:hidden}
  body{
    background:var(--bg); color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
    line-height:1.65; -webkit-font-smoothing:antialiased;
    overflow-wrap:anywhere; word-break:break-word;
  }
  .wrap{max-width:760px;margin:0 auto;padding:40px 22px 72px}
  header{border-bottom:1px solid var(--line);padding-bottom:22px;margin-bottom:26px}
  .kicker{font-size:12px;letter-spacing:.14em;color:var(--red);font-weight:600;text-transform:uppercase}
  h1{font-size:30px;line-height:1.28;margin:10px 0 8px;letter-spacing:-.01em}
  .sub{color:var(--ink2);font-size:15px;margin:0}
  .thesis{
    margin:22px 0 26px;padding:16px 18px;border-left:3px solid var(--red);
    background:var(--card);border-radius:0 var(--radius) var(--radius) 0;
    font-size:16px;
  }
  .thesis b{color:var(--red)}
  .switcher{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 24px}
  .switcher button{
    font:inherit;font-size:13px;padding:8px 14px;border-radius:999px;cursor:pointer;
    border:1px solid var(--line);background:var(--card);color:var(--ink2);
    transition:.16s; text-align:left; max-width:100%;
  }
  .switcher button:hover{border-color:#cfcac2;color:var(--ink)}
  .switcher button[aria-pressed="true"]{background:var(--ink);border-color:var(--ink);color:#fff}
  .say{
    font-size:19px;line-height:1.5;margin:0 0 4px;font-weight:600;
  }
  .say::before{content:"“";color:var(--ink3);margin-right:2px}
  .say::after{content:"”";color:var(--ink3);margin-left:2px}
  .saywrap{margin-bottom:26px}
  .label{font-size:12px;letter-spacing:.1em;color:var(--ink3);font-weight:600;margin:0 0 10px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:14px}
  .cell{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px}
  .cell .k{font-size:12px;color:var(--ink3);margin-bottom:2px}
  .cell .v{font-size:17px;font-weight:600}
  .cell .s{font-size:11px;color:var(--ink3);margin-top:2px}
  .notes{list-style:none;padding:0;margin:0 0 30px}
  .notes li{
    font-size:13px;color:var(--ink2);padding:7px 0 7px 18px;position:relative;
    border-bottom:1px dashed var(--line);
  }
  .notes li:last-child{border-bottom:0}
  .notes li::before{content:"·";position:absolute;left:5px;color:var(--red);font-weight:700}
  h2{font-size:18px;margin:34px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line)}
  .cand{
    background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
    padding:16px 18px;margin-bottom:12px;cursor:pointer;transition:.16s;
  }
  .cand:hover{border-color:#cfcac2}
  .cand[data-active="true"]{border-color:var(--red);box-shadow:0 0 0 3px var(--red-soft)}
  .cand .top{display:flex;justify-content:space-between;align-items:baseline;gap:14px;flex-wrap:wrap}
  .cand .rank{font-size:12px;color:var(--ink3);font-weight:600}
  .cand .name{font-size:16px;font-weight:600;flex:1;min-width:180px}
  .cand .pay{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums}
  .cand .pay small{font-size:12px;font-weight:500;color:var(--ink3);margin-left:2px}
  .cand .nums{display:flex;gap:18px;flex-wrap:wrap;margin-top:8px;font-size:13px;color:var(--ink2)}
  .cand .nums b{color:var(--ink);font-variant-numeric:tabular-nums}
  .cand .why{font-size:12.5px;color:var(--ink3);margin-top:8px}
  .delta{color:var(--red);font-weight:600}

  .compare{
    background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
    overflow:hidden;margin-top:8px;
  }
  .compare .head{
    padding:12px 18px;border-bottom:1px solid var(--line);font-size:13px;color:var(--ink2);
    background:var(--grey-soft);
  }
  .cols{display:grid;grid-template-columns:1fr 1fr}
  .col{padding:20px 18px}
  .col+.col{border-left:1px solid var(--line)}
  .col .tag{font-size:12px;font-weight:600;letter-spacing:.06em;margin-bottom:6px}
  .col.naive .tag{color:var(--ink3)}
  .col.official .tag{color:var(--red)}
  .col .amount{font-size:32px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
  .col.naive .amount{color:var(--ink3);text-decoration:line-through;text-decoration-thickness:1.5px}
  .col .detail{font-size:12.5px;color:var(--ink2);margin-top:6px;min-height:34px}
  .gapbar{
    border-top:1px solid var(--line);padding:14px 18px;font-size:14px;background:var(--red-soft);
  }
  .gapbar b{font-size:17px;color:var(--red)}
  .reasons{padding:0 18px 18px;margin:0;list-style:none}
  .reasons li{font-size:12.5px;color:var(--ink2);padding:5px 0 5px 16px;position:relative}
  .reasons li::before{content:"↳";position:absolute;left:0;color:var(--ink3)}
  .verdict{
    margin-top:12px;font-size:14px;color:var(--ink2);padding:12px 16px;
    border-radius:10px;background:var(--grey-soft);
  }
  footer{
    margin-top:44px;padding-top:18px;border-top:1px solid var(--line);
    font-size:12px;color:var(--ink3);line-height:1.8;
  }
  footer a{color:var(--ink3)}
  @media (max-width:560px){
    h1{font-size:23px;line-height:1.34}
    .wrap{padding:26px 16px 56px}
    .cols{grid-template-columns:1fr}
    .col+.col{border-left:0;border-top:1px solid var(--line)}
    .col .amount{font-size:27px}
    .say{font-size:17px}
    .cand .name{min-width:0;font-size:15px}
    .cand .pay{font-size:17px}
    .cand .top{gap:8px}
    .thesis{font-size:15px;padding:14px 15px}
    .switcher button{font-size:12px;padding:7px 12px}
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="kicker">许愿式点餐</div>
    <h1>你说一句愿望，它交一份付得起的订单</h1>
    <p class="sub">不用选套餐、不用填预算表、不用在两张券之间做算术。</p>
  </header>

  <div class="thesis">
    AI 可以替你许愿，但<b>不能替你算钱</b>。<br>
    所以每一单的账都算两遍：一遍是模型式的口算，一遍是官方核价。
  </div>

  <div class="switcher" id="switcher"></div>

  <div class="saywrap">
    <p class="say" id="say"></p>
  </div>

  <p class="label">愿望拆开看</p>
  <div class="grid" id="fields"></div>
  <ul class="notes" id="notes"></ul>

  <h2>给了几个方案</h2>
  <div id="cands"></div>

  <h2>选中那一单，账算两遍</h2>
  <div class="compare">
    <div class="head">同一单商品 · 两种算法</div>
    <div class="cols">
      <div class="col naive">
        <div class="tag">AI 式口算</div>
        <div class="amount" id="naivePay"></div>
        <div class="detail" id="naiveDetail"></div>
      </div>
      <div class="col official">
        <div class="tag">官方口径</div>
        <div class="amount" id="officialPay"></div>
        <div class="detail" id="officialDetail"></div>
      </div>
    </div>
    <div class="gapbar" id="gapbar"></div>
    <ul class="reasons" id="reasons"></ul>
  </div>
  <div class="verdict" id="verdict"></div>

  <footer>
    <span id="src"></span><br>
    本页面为麦当劳程序员创意开发大赛参赛作品的演示页，由参赛者独立开发，非麦当劳官方产品。<br>
    生成时间：<span id="ts"></span>
  </footer>
</div>

<script>
const DATA = __DATA__;
const SLOT = {breakfast:"早餐", lunch:"午餐", dinner:"晚餐", latenight:"夜宵"};
const SRC = {explicit:"你说的", inferred:"我推断的", default:"我替你定的"};
let cur = 0, active = 0;

function money(n){ return Number(n).toFixed(2); }
function el(id){ return document.getElementById(id); }

function buildSwitcher(){
  el("switcher").innerHTML = "";
  DATA.cases.forEach((c, i) => {
    const b = document.createElement("button");
    b.textContent = c.wish.raw.length > 22 ? c.wish.raw.slice(0, 21) + "…" : c.wish.raw;
    b.setAttribute("aria-pressed", i === cur ? "true" : "false");
    b.onclick = () => { cur = i; active = 0; render(); };
    el("switcher").appendChild(b);
  });
}

function render(){
  const c = DATA.cases[cur];
  const w = c.wish;
  buildSwitcher();
  el("say").textContent = w.raw;

  const fields = [
    ["总预算", money(w.budget) + " 元", SRC[w.source.budget]],
    ["人数", w.people + " 人", SRC[w.source.people]],
    ["餐段", SLOT[w.meal_slot] || w.meal_slot, SRC[w.source.meal_slot]],
  ];
  if (w.moods && w.moods.length){
    fields.push(["情绪", w.moods.join("、"), (w.taste||[]).join(" / ") || "无特定倾向"]);
  }
  el("fields").innerHTML = fields.map(f =>
    `<div class="cell"><div class="k">${f[0]}</div><div class="v">${f[1]}</div><div class="s">${f[2]||""}</div></div>`
  ).join("");

  el("notes").innerHTML = (w.notes || []).map(n => `<li>${n}</li>`).join("");

  el("cands").innerHTML = "";
  if (!c.candidates.length){
    el("cands").innerHTML = `<div class="cand">预算内没有找到方案。可以放宽预算，或把「想吃」说得更具体一点。</div>`;
    return;
  }
  c.candidates.forEach((cd, i) => {
    const d = cd.price;
    const div = document.createElement("div");
    div.className = "cand";
    div.dataset.active = (i === active) ? "true" : "false";
    div.innerHTML = `
      <div class="top">
        <span class="rank">${i+1}</span>
        <span class="name">${cd.name}</span>
        <span class="pay">${money(cd.payable)}<small>元 实付</small></span>
      </div>
      <div class="nums">
        <span>口算 <b>${money(d.naive.payable)}</b></span>
        <span>差 <b class="delta">${d.gap > 0 ? "+" : ""}${money(d.gap)}</b></span>
      </div>
      ${cd.reasons && cd.reasons.length ? `<div class="why">${cd.reasons.join("；")}</div>` : ""}
    `;
    div.onclick = () => { active = i; render(); };
    el("cands").appendChild(div);
  });

  const pick = c.candidates[Math.min(active, c.candidates.length - 1)];
  const p = pick.price;
  el("naivePay").textContent = money(p.naive.payable);
  el("naiveDetail").textContent = `商品 ${money(p.naive.subtotal)}，直接扣掉面额最大的券 ${money(p.naive.discount)}`;
  el("officialPay").textContent = money(p.official.payable);
  el("officialDetail").textContent =
    `商品 ${money(p.official.subtotal)}` +
    (p.official.delivery_fee ? ` + 配送 ${money(p.official.delivery_fee)}` : "") +
    `，券实际生效 ${money(p.official.discount)}`;

  if (Math.abs(p.gap) > 0.005){
    el("gapbar").innerHTML = `两种算法的差额 <b>${p.gap > 0 ? "+" : ""}${money(p.gap)} 元</b>，占实付的 ${p.gap_pct}%`;
  } else {
    el("gapbar").innerHTML = `这一单两种算法结果一致。口算不一定错，<b>错的时候你看不出来</b>。`;
  }

  const reasons = [];
  (p.naive.notes || []).forEach(n => reasons.push("口算：" + n));
  if (p.official.applied && p.official.applied.length) reasons.push("官方真正生效的券：" + p.official.applied.join("、"));
  (p.official.rejected || []).slice(0,3).forEach(n => reasons.push("用不了：" + n));
  el("reasons").innerHTML = reasons.map(r => `<li>${r}</li>`).join("");

  el("verdict").textContent = p.gap > 0.005
    ? "结论：口算报了更低的价，但结账时付的是右边那个数。"
    : "结论：这一单口算没有出错，但它没有验证过程，下一次未必还对。";
}

el("ts").textContent = DATA.generated_at || "示例快照";
if (DATA.source_note) el("src").textContent = DATA.source_note;
render();
</script>
</body>
</html>
"""


def source_note(snap: dict) -> str:
    """页脚的数据来源说明。有真实快照就说真的，只有示例就说示例。"""
    meta = snap.get("meta", {})
    if meta.get("source") == "captured":
        return (
            f'菜单与价格来自麦当劳 MCP 的 query-meals 真实返回（{meta.get("captured_at")} 抓取，'
            f'门店 {meta.get("store_name")}）；券为示例数据，因测试账号券包为空。'
            "价格以麦当劳官方渠道实时结果为准。"
        )
    return "数据为结构对齐官方 MCP 返回的示例快照，非门店实时报价；价格与供应状态以麦当劳官方渠道为准。"


def write_html(payload: dict, out_path: str) -> None:
    import datetime

    payload = dict(payload)
    payload["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    sys_path_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from planner import build_candidates, load_snapshot
    from wish_engine import parse_wish

    DEMO = [
        "今天被领导骂了一顿，想吃点能治愈的，别超过30块",
        "明天要带三个同事吃饭，我不想显得抠，也不想超预算",
        "减肥第三周，馋得不行，给我找个能骗过自己的",
        "加班到现在，很饿，想吃饱一点，不吃辣",
    ]
    snap = load_snapshot()
    note = source_note(snap)

    cases = []
    for t in DEMO:
        w = parse_wish(t)
        cases.append({"wish": w.to_dict(), "candidates": [c.as_dict() for c in build_candidates(w, snap, 3)]})
    out = os.path.join(sys_path_root, "docs", "index.html")
    write_html({"cases": cases, "source_note": note}, out)
    print("已生成", out)
