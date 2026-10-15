---
name: wechat-persona-profile
description: 读取指定微信联系人的聊天记录，构建心理学画像（大五人格+行为标签）和 Q 版形象，生成单文件 HTML 报告，含对机主的好感度多维评分（主动性/回应度/表达投入/情感温度/亲密度行为，附证据引用与置信度）。使用场景：用户说"分析一下 XX 的聊天记录"、"给 XX 做个画像"、"我和 XX 的关系怎么样"、"TA 对我什么态度"、wechat 聊天心理分析、Q版形象生成等。
---

# 微信人物画像报告（Wechat Persona Profile）

输入：一个微信联系人（昵称/备注）。输出：单文件 HTML 报告（Q版形象图 + 好感度 + 大五人格 + 行为标签 + 数据摘要）。

## 前置条件

- wechat-cli 已配置（`~/.wechat-cli/config.json` + 密钥存在）。若 `history` 报密钥错误，提示用户先运行 `wechat-cli init`（需微信运行中）。
- 本 skill 只做**只读**操作。聊天记录属于敏感数据：不外传、不粘贴到任何外部服务，报告默认存本地 outputs 目录，不自动发布/分享。

## 执行流程

### 步骤 1：拉取与统计

```bash
python scripts/fetch_chat.py --chat "联系人昵称" --max-msgs 5000 --out-dir <输出目录>/raw
```

群聊中只分析某成员时加 `--who "成员昵称"`。
产出 `messages.jsonl`（清洗后消息流）、`stats.json`（量化原料）、`contact.json`。

- 消息 >5000 条时 `--max-msgs` 提到 10000-20000。
- **群聊 + `--who` 场景注意**：目标成员消息占比低时脚本会拉取大量原始消息（单页约 20s），默认 `max_raw=30000` 条封顶并输出 `truncated=true`；建议 `--max-msgs` 降到 500-1000，并告知用户统计仅覆盖近期。
- 若报错"未拉取到消息"：先用 `wechat-cli contacts --query "关键词"` 确认昵称/备注准确。

### 步骤 2：LLM 质性分析（读数据，不猜）

1. 读 `stats.json` 全文。
2. 分块读 `messages.jsonl` 中**对方消息**（每次约 300 条，至少覆盖近 3 个月 + 早期抽样），完成三件事：
   - **大五人格打分**：按 `references/big-five.md` 的锚点与证据要求执行，输出规定 JSON。
   - **行为标签**：按触发线提炼 3-6 个，禁止纯印象标签。
   - **好感度 D4 复核**：词典初档 + 质性调整，必须写明理由。
3. 事务性占比判断：若对方消息 ≥70% 为工作/买卖/通知，记录 `transactional=true`。

### 步骤 3：好感度打分

严格按 `references/affinity-model.md` 执行：
- 用 `stats.json` 的 sessions / reply_latency / sides / by_type / late_night / monthly_counts 计算 D1-D5。
- 应用时间衰减、样本量置信度、事务性基线修正。
- 每维度必须附证据原话（从 messages.jsonl 摘 2-4 条，报告引用时可脱敏：名字→X）。
- 低样本（对方消息 <50 条）不出总分，报告只出维度描述。

### 步骤 4：生成 Q 版形象（一张）

- **有照片**：从 `messages.jsonl` 找 mtype=image 且 is_self=false 的消息（或用 `wechat-cli history "<联系人>" --type image --media` 定位图片路径），选一张面部清晰的正面照作为参考图，走图生图：保持五官与发型，转成动漫 Q 版（三头身、大头、柔和光影）。
- **无照片**：文生图。人设由大五分数 + 标签驱动，映射规则：
  - 外向性高 → 明快配色、张嘴笑/挥手；低 → 抱膝坐姿、安静神情
  - 开放性高 → 异色瞳/星星眼等想象力元素
  - 神经质高 → 雨天/深夜窗边的场景元素；低 → 晴天户外
  - 宜人性高 → 圆润线条、暖色；低 → 锐利线条、冷色
  - 标志性标签融入道具（如"语音选手"画成举着麦克风的形象）
- 提示词一律写明："无文字、无水印、无 logo"（中文生成中文字极易糊）。

### 步骤 5：渲染 HTML 报告

复制 `assets/report-template.html`，替换全部占位符：

| 占位符 | 内容 |
|---|---|
| `__AVATAR_B64__` | Q版图 base64 data URI（`data:image/png;base64,...`） |
| `__NAME__` `__ONE_LINER__` | 昵称；一句话人设（≤25字，由标签+大五生成） |
| `__DATE_RANGE__` `__TOTAL_MSGS__` `__ACTIVE_DAYS__` `__SESSIONS__` `__OTHER_INIT__` `__GEN_TIME__` | stats.json |
| `__SCORE__` `__LEVEL__` `__CONFIDENCE__` `__DIM_ITEMS__` `__AFFINITY_EVIDENCE__` | 好感度结果（DIM_ITEMS 按模板 dim-item 结构生成） |
| `__FIVE_JSON__` `__FIVE_TABLE__` `__FIVE_EVIDENCE__` `__TAGS__` | 大五结果（FIVE_TABLE 五维分数表，TAGS 用 `.tag` 结构） |
| `__HOUR_JSON__` `__MONTHLY_JSON__` | stats.json 直传（JSON 对象字面量） |
| `__SUMMARY_TABLE__` | 消息数/平均字数/表情包占比/深夜占比等摘要表 |
| `__METHOD_HTML__` | 方法论与局限：维度定义一句话版 + 样本量 + 事务性基线 + 词典局限声明 |

替换后用 present_files 交付 HTML（自动预览）。

## 输出纪律

- 报告结论必须有数据或引用支撑；推断性内容（人格、好感）用"倾向""信号显示"措辞，不做绝对断言。
- 好感度不是读心术：低分可能是对方聊天习惯冷淡，不等于讨厌机主。报告结语必须保留这一限定。
- 不确定的维度宁可写"样本不足，无法判断"。

## 资源索引

- `scripts/fetch_chat.py` — 拉取+清洗+基础统计（确定性，勿手改逻辑，可传参）
- `references/affinity-model.md` — 好感度五维评分模型（权重/分桶/修正/等级）
- `references/big-five.md` — 大五聊天推断锚点 + 标签触发线
- `assets/report-template.html` — 单文件报告模板（Chart.js CDN，深色主题）
