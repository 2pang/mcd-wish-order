# MCP 接入说明

本文说明本作品实际使用的麦当劳 MCP Server、Tool、调用流程，以及每一步的业务价值。

标注「已接入」的，仓库里有对应代码可复现；标注「已设计」的，调用参数已按官方工具描述写好但尚未在代码中启用，原因是它们需要真实账号数据才能验证，不做未经验证的声明。

---

## 一、Server

| 项 | 值 |
|---|---|
| 名称 | 麦当劳中国 MCP（`mcd-mcp`） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 鉴权 | `Authorization: Bearer ${MCD_MCP_TOKEN}` |
| 限流 | 单 Token 600 次/分钟（超限 429） |
| Token 申请 | <https://open.mcd.cn/mcp>，手机号登录后在控制台激活，免费 |
| 客户端位置 | `scripts/mcd_client.py`（零依赖，仅用 `urllib`） |

客户端只实现四个动作：`initialize`、`notifications/initialized`、`tools/list`、`tools/call`。响应同时兼容纯 JSON 与 SSE（`data:` 行）两种形态，工具返回的业务数据会从 `content[].text` 中解析出 JSON。

自检命令：

```bash
python wish.py --probe
```

输出示例（Token 有效时）：

```json
{
  "ok": true,
  "server": {"name": "...", "version": "..."},
  "protocolVersion": "2025-06-18",
  "tool_count": 33,
  "tools": ["list-nutrition-foods", "query-meals", "..."]
}
```

---

## 二、Tool 使用清单

### 2.1 主链路（已接入）

| Tool | 用途 | 在本作品中的位置 |
|---|---|---|
| `tools/list` | 握手后确认工具可用性 | `--probe` 与 `--live` 的前置检查 |
| `query-meals` | 拉当前门店可售卖餐品（分类、编码、标签） | 替换快照中的 `meals`，提供候选池 |
| `now-time-info` | 取当前时间 | 判断餐段（早餐 / 午餐 / 晚餐 / 夜宵），供券的时段校验使用 |

### 2.2 主链路（已设计，需真实账号验证）

| Tool | 用途 | 设计中的位置 |
|---|---|---|
| `query-nearby-stores` | 按地址查附近门店 | 许愿后第一步：先确定「在哪吃」，避免拿别家门店的菜单算价 |
| `available-coupons` | 查可领取的麦麦省券 | 券池来源之一（可领券） |
| `query-my-coupons` | 查已入卡包的券 | 券池来源之二（已持有券） |
| `query-store-coupons` | 查当前门店可用券 | 券池来源之三（门店券） |
| `query-meal-detail` | 查套餐组成与可替换项 | 套餐候选展开，以及「换小食 / 换饮料」的重算 |
| `list-nutrition-foods` | 查餐品营养数据 | 低卡类愿望（减肥 / 控卡）的打分依据 |
| `calculate-price` | 按商品列表 + 券计算应付 | **官方口径核价的唯一来源**，与本地口算并排对照 |
| `create-order` | 创建订单并返回支付链接 | 用户确认后才会调用，本版本默认不启用 |

### 2.3 明确未使用

`cancel-order`、`query-order`、`order-list`、`query-my-account`、`mall-*`、`query-lottery-info`、`draw-lottery`、`query-party-*`、`party-order-create`、`query-meal-assistance`。

不用它们的理由是本作品的定位：**只做「从一句话到一份核价方案」这一段**，不做订单管理、不做积分、不做派对预约。功能边界收窄，是为了让每一层都能被验证。

---

## 三、调用流程

### 3.1 完整链路

```
用户输入：一句愿望
        │
        ▼
[本地] wish_engine.parse_wish()
   把「被领导骂了，想吃点治愈的，30 块以内」
   翻译成 {budget: 30, people: 1, slot: lunch, taste: [sweet, warm, comfort]}
   每个字段标注来源：explicit / inferred / default
        │
        ▼
[真实] now-time-info ──────────── 校验餐段，决定哪些券的时段限制会生效
        │
        ▼
[真实] query-nearby-stores ────── 确定门店（决定菜单与门店券的有效范围）
        │
        ▼
[真实] query-meals ───────────── 拿菜单：编码、名称、分类、标签
        │
        ├──[真实] query-meal-detail ── 套餐展开与可替换项
        │
        ├──[真实] available-coupons ── 可领券
        ├──[真实] query-my-coupons ── 已持有券
        └──[真实] query-store-coupons ─ 门店券
                    │
                    ▼  券池归一化（统一成 threshold / second_half / bundle 三类）
        │
        ▼
[本地] planner.build_candidates()
   枚举候选（套餐 + 单品自由组合），按愿望打分，按实付价排序
        │
        ▼
[本地+真实] pricing.compare()
   同一单商品，两套算法：
     ├─ naive_price()    ：面额最大券直接减，不看门槛 / 互斥 / 渠道 / 配送费
     └─ official_price() ：门槛、互斥、渠道、时段、配送费全校验
                           （实盘由 calculate-price 返回，本地实现作为离线版）
        │
        ▼
[本地] 输出：三个方案 + 两张账单 + 差额
        │
        ▼
（可选，需用户明确确认）create-order → 支付链接
```

### 3.2 为什么核价要做两遍

`calculate-price` 是官方唯一的权威口径。本作品保留一份本地 `official_price()` 实现，用途有两个：

1. **离线可用**：没有 Token 也能跑通完整演示，评审与路人三分钟能看懂。
2. **可对拍**：有 Token 时，把本地的 `official_price()` 结果和 `calculate-price` 的真实返回放在一起比对。如果两者不一致，说明本作品对券规则的理解有偏差，这是一个可以自己抓自己的 bug 的机制，而不是靠人肉检查。

第二点是这套设计里最有价值的部分：**它允许作品证明自己错在哪。**

---

## 四、字段映射

| MCP 返回字段（示例） | 本作品内部字段 | 用途 |
|---|---|---|
| `meals[].code` | `meal.code` | 组合去重、下单时传参 |
| `meals[].name` | `meal.name` | 展示 |
| `meals[].category` | `meal.category` | 品类多样性打分（汉堡 / 小食 / 饮料 / 甜品） |
| `meals[].price` | `meal.price` | 口算基准 |
| 由名称与营养推断的标签 | `meal.tags` | 愿望匹配（beef / chicken / spicy / lowcal …） |
| `coupons[].threshold` / `off` | `coupon.threshold` / `off` | 门槛校验与优惠计算 |
| `coupons[].stackable` | `coupon.stackable` | 互斥处理（本作品保守：一律只叠一张） |
| `coupons[].channel` / `slot` | 同名字段 | 渠道与时段校验 |
| `calculate-price` 的应付金额 | `PriceResult.payable` | 官方口径核价结果 |

> 说明：`meals[].tags` 是本地推断字段，不是 MCP 返回的原始字段。推断规则写在 `scripts/planner.py` 与 `data/snapshot_menu.json` 中，可以逐条核对。

---

## 五、业务价值

对用户：

1. **省掉一次翻译**：不用把自己的处境翻译成「预算、热量、品类」三个参数，说人话就行。
2. **省掉一次被误导**：券面额不等于实际节省，差额被明确标出。用户看到的不是一个「省了 12 元」的漂亮结论，而是「你以为省 12，其实这张券用不了」。
3. **省掉一次返工**：方案给到实付价，不是商品原价。到柜台不会发现价格对不上。

对麦当劳：

1. **把模糊意图变成有效订单**：「随便吃点」这类需求原本很难承接，现在可以被翻译成具体组合并完成核价。
2. **提升券的真实核销率**：用户知道哪张券真的能用，减少到了支付环节才发现不可用而放弃的情况。
3. **暴露券规则的理解成本**：双算法差额本身就是一份关于「券规则有多容易被误解」的实测数据。
