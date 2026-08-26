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
   - `/psn奖杯`：每个游戏的奖杯完成度、各稀有度进度条。
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

1. 用浏览器登录 [PlayStation 官网](https://www.playstation.com/) 或 [my.account.sony.com](https://my.account.sony.com/)；
2. 确保你能正常访问 [`https://ca.account.sony.com/api/v1/ssocookie`](https://ca.account.sony.com/api/v1/ssocookie)；
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
| `/psn排行 [游戏数\|时长\|奖杯\|白金]` | 本群排行榜 | 所有人 |
| `/psn对比 @某人` | 与群友对比数据 | 所有人 |
| `/psn联动` | 群内好友关系与共同游戏 | 所有人 |
| `/psn在线` | 群内谁在线、正在玩什么 | 所有人 |
| `/psn刷新` | 强制刷新自己的数据缓存 | 所有人 |
| `/psn启用` | 在当前群启用插件 | 管理员 |
| `/psn禁用` | 在当前群禁用插件 | 管理员 |

> `@某人` 表示可以 @ 群内其他成员来查询 TA（前提是 TA 已绑定）；也可直接写对方的 PSN 在线 ID。

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
