# 🎮 astrbot_plugin_PlayStationGames

> AstrBot 的 PlayStation Network (PSN) 玩家数据可视化插件。
> 支持按群启用、绑定 PSN 账号、查询游戏库 / 游戏时间 / 奖杯、群内排行与对比，所有结果均以**图片**形式返回。

灵感参考：[astrbot_plugin_steam_status_monitor](https://github.com/Maoer233/astrbot_plugin_steam_status_monitor)、[astrbot_plugin_steamgame](https://github.com/bvzrays/astrbot_plugin_steamgame)。

---

## ✨ 功能特性

1. **白名单群聊**：可自定义启用插件的群聊名单（配置文件 + 管理员指令动态增删）。
2. **账号绑定**：群友通过 `/绑定psn <PSN在线ID>` 绑定自己的 PlayStation 账号。
3. **个人数据查询**：
   - `/psn`：个人资料、在线状态、正在游玩、奖杯等级与各稀有度数量；
   - `/psn游戏库`：游戏库列表、总游戏时长、各平台游戏数量、游戏封面墙；
   - `/psn奖杯`：每个游戏的奖杯完成度、各稀有度进度条；
   - `/psn游戏 <游戏名关键词>`：**指定某一款游戏**的详情——总游玩时长/小时、启动次数、首次与最近游玩日期，以及该游戏的奖杯完成度与白金/金/银/铜进度。**游戏名支持强模糊匹配**：俗称/简称/英文名/中文正式名互通（如「老头环」「法环」「黑悟空」「战神5」「美末2」「大镖客2」），自动忽略大小写、全半角、音标、版本后缀（Remake/Remaster/PS5 等），并带版本号校验（查「2」不会误中前作）；找不到时会提示最接近的游戏名。
4. **群内排行榜**：`/psn排行` 支持按
   - **游戏时长（肝度）**
   - **游戏数量**
   - **奖杯总分**（白金/金/银/铜加权）
   - **白金杯数**
   多维度排行。
5. **群友对比**：`/psn对比 @某人` 对比游戏数、总时长、奖杯、白金数，以及共同拥有的游戏。
6. **群内联动**：
   - `/psn联动`：分析群友之间的 PSN 好友关系，以及「大家都在玩」的共同游戏；
   - `/psn在线`：群内谁在线、正在玩什么游戏。
7. **可视化图片输出**：所有数据均使用 AstrBot 的 HTML 渲染能力生成精美深色风格图片。
8. **缓存与日志**：内置内存缓存减少 PSN 请求；使用情况写入数据目录下的 `usage.log`。
9. **💬 自然语言支持（不依赖 LLM、可绕过其他插件）**：直接 @机器人 说「查下我的奖杯」「群里谁最肝」「看看 @某人 在玩什么」「艾尔登法环玩了多久」「和 @某人 对比一下」即可触发。自然语言意图在**插件层确定性识别**（`@filter.regex`），运行在 Agent/LLM 阶段之前，因此**无需配置 LLM** 也能用，且不受 `memory_companion`、`private_companion` 等接管 Agent 的插件干扰；同时保留 LLM 函数调用（`@filter.llm_tool`）作为兜底。

---

## 🧩 支持平台

插件仅使用 AstrBot 的平台无关通用接口（指令 / 文字 / 图片 / 群号 / @ 消息段），因此理论上兼容所有**支持群聊、图片与 @ 功能**的消息平台。

- **已实测**：`aiocqhttp`（NapCat / Lagrange / go-cqhttp 等 OneBot 实现，即 QQ）。
- **通用接口推断兼容（未逐一实测）**：QQ 官方（`qq_official` / `qq_official_webhook`）、Telegram、Discord、Satori、飞书、企业微信、钉钉、KOOK、Slack、Misskey、Mattermost。
- **不适用**：微信公众号（无群聊、被动回复受限）、企业微信智能机器人等无群成员/排行模型的渠道，故未声明支持。

在未实测平台如遇到图片或 @ 解析差异，欢迎提 issue 反馈。

---

## 📦 安装

### 方式一：插件市场（推荐）

在 AstrBot 面板的「插件市场」中搜索 `astrbot_plugin_PlayStationGames` 安装即可。

### 方式二：手动安装

```bash
# 在 AstrBot 的 plugins 目录下
cd plugins
git clone https://github.com/Eason4869/astrbot_plugin_PlayStationGames.git
```

随后在 AstrBot 面板中重启 / 重载插件，插件会自动安装依赖：

- [`PSNAWP`](https://github.com/isFakeAccount/psnawp) —— PSN 的非官方 Python API 封装；
- `aiohttp` —— 异步下载封面/头像。

> 如果自动安装失败，请在插件目录手动执行：
> ```bash
> pip install -r requirements.txt
> ```

---

## 🔑 获取 NPSSO 令牌

本插件通过 PSNAWP 访问 PlayStation Network，**必须**配置一个有效的 NPSSO 令牌（64 位字符）：

1. 用浏览器打开并登录 [PlayStation 中国官网](https://www.playstation.com/zh-hans-cn/)（点击右上角「登录」，登录你的 PSN 账号）；
2. 保持登录状态，在同一浏览器访问 [`https://ca.account.sony.com/api/v1/ssocookie`](https://ca.account.sony.com/api/v1/ssocookie)；
3. 页面会返回类似 `{"npsso":"xxxxxxxx...（64 位字符）..."}` 的内容，复制其中的字符串；
4. 在 AstrBot 插件配置中粘贴到 **NPSSO 令牌** 项。

⚠️ **注意**：

- NPSSO 是你账号的会话凭证，请勿泄露；
- 令牌有效期约几个月，过期后查询会报「认证失败」，重新获取并更新即可；
- 插件仅用该令牌读取公开资料、游戏时间和奖杯信息，不会进行任何账号写操作；
- 服务器若位于无法直连 PSN 的地区，请在配置中设置 **代理（proxy）**。

---

## ⚙️ 配置项

在 AstrBot 管理面板的插件配置页可修改：

| 配置项 | 说明 | 默认值 |
| --- | --- | --- |
| `npsso_token` | PSN 的 NPSSO 令牌（必填） | 空 |
| `enabled_groups` | 启用插件的群聊名单（群号列表）；留空表示所有群启用 | `[]` |
| `proxy` | HTTP/HTTPS 代理，如 `http://127.0.0.1:7890` | 空 |
| `image_quality` | 输出 JPEG 图片质量（10-100） | `90` |
| `cache_ttl` | 个人/游戏库/奖杯数据的缓存时间（秒） | `300` |
| `max_titles` | 游戏库/排行最多统计的游戏数量 | `200` |
| `command_prefix` | 指令前缀（仅作展示用，指令已开启 `prefix_optional`） | `/` |

运行期通过管理员指令添加的群聊会保存到数据目录 `enabled_groups.json`，与配置名单合并生效。

---

## 🎯 指令一览

所有指令均支持带 `/` 或不带前缀触发（`prefix_optional=True`）。

| 指令 | 说明 | 权限 |
| --- | --- | --- |
| `/psn帮助` | 查看指令帮助 | 所有人 |
| `/绑定psn <PSN在线ID>` | 绑定你的 PSN 账号 | 所有人 |
| `/解绑psn` | 解除绑定 | 所有人 |
| `/psn同步` | 将已有绑定同步到当前群聊 | 所有人 |
| `/psn [@某人或ID]` | 查看个人资料 / 在线状态 / 奖杯总览 | 所有人 |
| `/psn游戏库 [@某人]` | 查看游戏库与游戏时间 | 所有人 |
| `/psn奖杯 [@某人]` | 查看各游戏奖杯进度 | 所有人 |
| `/psn游戏 <游戏名关键词> [@某人]` | 查看指定游戏的时长 / 奖杯详情 | 所有人 |
| `/psn排行 [游戏数\|时长\|奖杯\|白金]` | 本群排行榜 | 所有人 |
| `/psn对比 @某人` | 与群友对比数据 | 所有人 |
| `/psn联动` | 群内好友关系与共同游戏 | 所有人 |
| `/psn在线` | 群内谁在线、正在玩什么 | 所有人 |
| `/psn刷新` | 强制刷新自己的数据缓存 | 所有人 |
| `/psn启用` | 在当前群启用插件 | 仅管理员 |
| `/psn禁用` | 在当前群禁用插件 | 仅管理员 |

> `@某人` 表示可以 @ 群内其他成员来查询 TA（前提是 TA 已绑定）；也可直接写对方的 PSN 在线 ID。
>
> `/psn游戏` 示例：`/psn游戏 艾尔登法环`（查自己）、`/psn游戏 黑神话悟空 @某人`（查群友）、`/psn游戏 老头环`、`/psn游戏 战神5`、`/psn游戏 美末2`。游戏名支持**俗称/简称/英文名/中文正式名**模糊互通，自动忽略大小写、全半角、音标与版本后缀（Remake/Remaster/PS5 等）；带版本号的查询不会误中前作；找不到时会列出最接近的游戏名。展示该游戏的总时长、启动次数、首次/最近游玩日期与奖杯进度。
>
> 🧹 **进度提示自动撤回**：较慢的查询（资料/游戏库/奖杯/指定游戏/排行/对比/联动/在线/绑定）会先发一条「正在获取…」提示，结果发出后自动撤回该提示（OneBot 系协议，如 NapCat/Lagrange/go-cqhttp）。非 OneBot 平台会自动降级为普通提示（保留不撤回）。

---

## 💬 自然语言用法

本插件支持用日常说话的方式查询，**不必记忆指令**。自然语言在**插件层做确定性意图识别**（`@filter.regex`），运行在 Agent / LLM 阶段**之前**，因此：

- ✅ **不配置 LLM 也能用**（只要 @ 机器人 / 私聊唤醒）；
- ✅ **不受其他 Agent 类插件干扰**——即使同时安装了 `memory_companion`、`private_companion` 等会接管对话的插件，PSN 请求也会在进入它们之前被本插件直接处理并终止事件，不会出现「LLM 不调用工具、直接回接口有问题」的情况；
- 同时仍注册了一组 **函数调用工具（`@filter.llm_tool`）** 作为意图模糊时的兜底。

示例（直接 @机器人 说）：

- 「查一下我的 PSN 资料」/「我在玩什么？」
- 「@某人 现在在玩什么游戏？」
- 「看看我的奖杯」/「我的游戏库有哪些？」
- 「**艾尔登法环我玩了多久？**」/「**老头环的游戏信息**」/「**战神5奖杯进度**」/「黑神话悟空玩了多久」/「这个游戏奖杯进度」
- 「**@小明 艾尔登法环玩了多久？**」（查 **@小明** 的这款游戏）/「@小明 的游戏库」/「@小明 奖杯进度」
- 「群里谁最肝？」/「本群游戏时长排行」/「谁的白金多？」
- 「我和 @某人 谁的奖杯多？」
- 「现在群里谁在线？」
- 「我想绑定 PSN，ID 是 XiaoMing」

被 @ 的人会被自动识别为查询目标（同样要求 TA 已绑定）。

> 提示：意图识别基于关键词，措辞清晰时最可靠；若未命中，会交还给 LLM（已配置时）或直接用 `/psn` 等指令最稳妥。绑定/解绑等操作也可直接用自然语言完成。

---

## 🗂️ 数据存储

插件数据保存在 AstrBot 的数据目录下（通常是 `data/astrbot_plugin_PlayStationGames/`）：

```
data/astrbot_plugin_PlayStationGames/
├── psn_bindings.json      # 用户绑定与群成员映射
├── enabled_groups.json    # 运行时增删的启用群聊
├── usage.log              # 指令使用日志
└── image_cache/           # 头像、游戏封面缓存
```

- `psn_bindings.json` 结构：
  ```json
  {
    "users": { "QQ号": "PSN在线ID" },
    "groups": { "群号": { "QQ号": "PSN在线ID" } }
  }
  ```
- 所有数据仅保存在本地，不会上传到任何第三方服务器。

---

## ❓ 常见问题

**Q：为什么游戏库里看不到某些游戏？**
A：PSN 的游戏时间接口（`titleStats`）只返回 **PS4 及以上**且有游玩记录的游戏，PS3、PSV 及从未启动过的数字版游戏不会出现。

**Q：为什么查不到某个用户 / 提示私密？**
A：对方可能在 PSN 隐私设置中关闭了「活动状态 / 游戏历史 / 奖杯」的公开可见性。本插件只能读取公开数据。

**Q：排行很慢？**
A：排行需要为群内每位成员分别请求游戏库与奖杯，而 PSNAWP 默认遵循 PSN 的速率限制（约每 3 秒 1 个请求）。人多时请耐心等待；可适当调小 `max_titles` 减少单次拉取量。

**Q：报「认证失败 / NPSSO 无效」？**
A：令牌过期或被吊销。重新按上文步骤获取新的 NPSSO 并更新配置即可。

**Q：网络连接错误？**
A：请在配置中设置可用的 `proxy`；同时确认服务器能访问 `*.playstation.com`、`*.playstation.net` 等域名。

---

## 🛠️ 开发说明

项目结构：

```
astrbot_plugin_PlayStationGames/
├── main.py            # 插件主入口，注册所有指令
├── psn_client.py      # 对 PSNAWP 的异步封装 + 缓存
├── media.py           # 图片/封面下载与 data-uri 缓存
├── metadata.yaml      # 插件元信息
├── _conf_schema.json  # AstrBot 配置面板 schema
├── requirements.txt
├── logo.png
├── templates/         # HTML 渲染模板（Jinja2 风格，由 AstrBot 渲染）
│   ├── profile.html
│   ├── library.html
│   ├── trophies.html
│   ├── game.html
│   ├── ranking.html
│   ├── compare.html
│   ├── network.html
│   └── online.html
└── README.md
```

PSNAWP 是同步库，内部使用 `requests`。`psn_client.py` 通过 `asyncio` 线程池执行阻塞调用，并加锁串行化以遵守 PSN 的速率限制。

---

## 📄 许可证

[MIT License](./LICENSE)

---

## 🙏 致谢

- [PSNAWP](https://github.com/isFakeAccount/psnawp) —— PlayStation Network API 封装；
- [AstrBot](https://github.com/Soulter/AstrBot) —— 多平台聊天机器人框架；
- 参考插件作者 [@Maoer233](https://github.com/Maoer233) 与 [@bvzrays](https://github.com/bvzrays)。
