# wechat-persona-profile

> 一个聊天心理学画像技能：读取指定微信联系人的聊天记录，自动生成「心理学画像 + Q 版形象 + 对机主好感度」单文件 HTML 报告。

## 它能做什么

- **拉取清洗**：通过 `wechat-cli` 读取指定联系人（私聊，或群聊中指定成员 `--who`）的聊天记录，输出清洗后的消息流与基础统计。
- **心理学画像**：大五人格（开放性 / 尽责性 / 外向性 / 宜人性 / 神经质）量化打分 + 行为标签，每一项都附聊天原话证据，禁止纯印象标签。
- **Q 版形象**：有照片走图生图转动漫 Q 版；无照片按大五分数 + 标签驱动文生图（三头身、动漫风、无文字无水印）。
- **好感度评估**：五维可解释模型——主动性 / 回应度 / 表达投入 / 情感温度 / 亲密度行为，每维附原话证据 + 样本量置信度，并给出等级（冷淡 / 平淡 / 友好 / 亲近 / 亲密）。

## 目录结构

| 路径 | 说明 |
|---|---|
| `SKILL.md` | 技能主文档（五步执行流程） |
| `scripts/fetch_chat.py` | 拉取 + 清洗 + 基础统计（确定性脚本，仅用标准库） |
| `references/affinity-model.md` | 好感度五维评分模型（权重 / 分桶 / 修正 / 等级） |
| `references/big-five.md` | 大五人格聊天推断锚点 + 标签触发线 |
| `assets/report-template.html` | 单文件报告模板（Chart.js 深色主题，占位符版） |
| `requirements.txt` | 依赖清单（本 skill 仅标准库；含 wechat-cli 依赖声明） |

---

## 依赖与配置

### 1. 本 skill 的依赖

| 项 | 要求 |
|---|---|
| Python | 3.10+（脚本用 `argparse/json/os/re/subprocess/datetime/collections`，均为标准库） |
| 第三方 Python 包 | **无**（fetch_chat.py 不 import 任何第三方库） |
| 外部程序 | `wechat-cli`（读取微信数据，见下） |
| 报告阶段 | 图像生成能力（文生图，如 ImageGen 工具 / OpenAI / 本地 SD）+ 现代浏览器（渲染 HTML，Chart.js 已内联） |

### 2. wechat-cli（核心依赖）

> 本 skill 的聊天数据完全来自 `wechat-cli`。**必须先装好并初始化**，否则 `fetch_chat.py` 无法运行。

**版本**：实测 `wechat-cli 0.2.4`（compatible：微信 4.0+ 桌面版；本机 4.1.12 已适配）

**Python 依赖（wechat-cli 自带的，会自动安装）**：

| 包 | 版本约束 | 用途 |
|---|---|---|
| `click` | `>=8.1,<9` | CLI 框架 |
| `pycryptodome` | `>=3.19,<4` | 微信数据库解密 |
| `zstandard` | `>=0.22,<1` | 微信数据解压 |

**安装（PyPI 已下架，需从 GitHub 源码装）**：

```bash
# 注意：不是 freestylefly/wechat-cli（该仓库已 404）
git clone --depth 1 https://github.com/huohuoer/wechat-cli.git
cd wechat-cli
pip install -e .

# 验证
wechat-cli --version
# 输出: wechat-cli, version 0.2.4
```

> 若 GitHub 直连失败（如网络受限），可改用 codeload 下载 zip 后离线安装：
> `pip install "https://codeload.github.com/huohuoer/wechat-cli/zip/refs/heads/main"`

### 3. 微信客户端要求（环境依赖）

| 要求 | 说明 |
|---|---|
| 微信桌面版 | **仅支持 4.0+**（进程名 Windows 为 `Weixin.exe`，数据目录 `xwechat_files`；3.x 旧版不兼容） |
| 已登录 | 工具读取本地微信数据库，需微信处于登录态 |
| 初始化时微信必须运行 | `wechat-cli init` 需要从微信进程内存提取数据库密钥 |
| 操作系统 | Windows / macOS / Linux 均可（需对应微信 4.0+ 桌面版） |

### 4. 初始化与密钥配置（一次性）

```bash
# 1) 确保微信正在运行且已登录
# 2) 提取密钥（自动检测数据目录；失败可 --db-dir 手动指定）
wechat-cli init
#   成功后在 ~/.wechat-cli/ 生成：
#     config.json      —— 记录 db_dir（数据库路径）
#     all_keys.json    —— 数据库解密密钥
```

配置目录约定：

| 系统 | 路径 |
|---|---|
| Windows | `C:\Users\<用户名>\.wechat-cli\` |
| macOS | `~/.wechat-cli/` |
| Linux | `~/.wechat-cli/` |

> 微信 4.1+ 不再在内存缓存明文密钥，`wechat-cli init` 内存扫描 0 命中属正常；
> 此时需走 Config.Cipher blob 路线（dump `com.Tencent.WCDB.Config.Cipher` → 提取 (key+salt) → 直接作密钥用，勿做 PBKDF2）。
> 本仓库不内置密钥提取脚本，相关适配请参考 wechat-cli 上游文档。

**指定 wechat-cli 路径**：`fetch_chat.py` 默认探测
`C:/Users/ASUS/.workbuddy/binaries/python/envs/default/Scripts/wechat-cli.exe`，
可通过环境变量 `WECHAT_CLI` 或参数 `--wc-cli` 覆盖：

```bash
python scripts/fetch_chat.py --chat "联系人昵称" --wc-cli "/path/to/wechat-cli" --out-dir ./raw
```

### 5. 报告生成阶段的依赖

| 产物 | 依赖 | 说明 |
|---|---|---|
| 好感度 / 大五 / 标签 | LLM（如本 agent） | 读取 `stats.json` + 对方消息做质性分析，按 references 打分 |
| Q 版形象图 | 文生图能力 | 有照片→图生图；无照片→按大五+标签驱动文生图；提示词强制「无文字/无水印」 |
| HTML 报告 | 浏览器 | `assets/report-template.html` 为自包含单文件，Chart.js 已内联，无外部 CDN |

### 6. 本机参考环境（ASUS 实测，供复现）

| 项 | 值 |
|---|---|
| 操作系统 | Windows 11 |
| Python | `C:/Users/ASUS/.workbuddy/binaries/python/envs/default/Scripts/python.exe` (3.13) |
| wechat-cli | 源码装于 `D:\wxcli_src\wechat-cli-main`，可执行 `…\Scripts\wechat-cli.exe`（不在 PATH，需绝对路径） |
| 微信版本 | 4.1.12，账号数据目录 `E:/xwechat_files/wangnuo839053_954c/db_storage` |
| 配置目录 | `C:/Users/ASUS/.wechat-cli/`（all_keys.json / config.json） |

---

## 安装本 skill

把本目录放到技能目录即可，例如：

```
~/.workbuddy/skills/wechat-persona-profile/
```

或在 Claude Code / 其他支持 skills 的客户端对应目录下。

---

## 使用

按 `SKILL.md` 的五步流程执行：

1. 拉取与统计：`python scripts/fetch_chat.py --chat "联系人昵称" --max-msgs 5000 --out-dir ./raw`
2. LLM 质性分析（读 `stats.json` 与对方消息，做大五 + 标签 + 好感度初档）
3. 好感度打分（严格按 `references/affinity-model.md`）
4. 生成 Q 版形象（有照片图生图 / 无照片文生图）
5. 渲染 HTML 报告（复制 `assets/report-template.html` 替换占位符）

---

## 隐私声明

聊天记录属于敏感数据。本技能仅做**只读**分析，不外传、不粘贴到任何外部服务，报告默认存本地；`outputs/`、`*.report.html` 已被 `.gitignore` 排除，不会进版本库。

## 免责声明

好感度是**行为信号推断**，不是读心术。低分可能只是对方聊天习惯冷淡，不等于讨厌机主；高分也需结合关系性质（如「灵魂挚友型」与「恋人型」不可混为一谈）。所有推断性结论以「信号显示 / 倾向」措辞，不做绝对断言。

## License

MIT
