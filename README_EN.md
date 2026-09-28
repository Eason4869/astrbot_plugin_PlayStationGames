# 🎮 astrbot_plugin_PlayStationGames

> A PSN (PlayStation Network) player-data visualization plugin for AstrBot.
> Enable per group, bind PSN accounts, query game library / play time / trophies, group rankings and comparisons — all results are returned as **images**.

> **English** · [中文 README](./README.md)

Inspired by: [astrbot_plugin_steam_status_monitor](https://github.com/Maoer233/astrbot_plugin_steam_status_monitor), [astrbot_plugin_steamgame](https://github.com/bvzrays/astrbot_plugin_steamgame).

---

## ✨ Features

1. **Allowlisted groups** — choose which groups this plugin responds in (config file + admin commands to add/remove at runtime).
2. **Account binding** — group members bind their own PlayStation account with `/绑定psn <PSN在线ID>`.
3. **Personal data queries**:
   - `/psn` — profile, online status, now-playing, trophy level and counts by rarity;
   - `/psn游戏库` — game library list, total play time, per-platform game counts, cover wall;
   - `/psn奖杯` — trophy progress per game with per-rarity progress bars;
   - `/psn游戏 <game keyword>` — details for **one specific game**: total hours played, play count, first/last played dates, and its trophy progress (platinum/gold/silver/bronze). The game name supports **strong fuzzy matching**: nicknames / abbreviations / English / official Chinese names all interop (e.g. 「老头环」「法环」「黑悟空」「战神5」「美末2」「大镖客2」). It ignores case, full/half-width forms, diacritics and version suffixes (Remake/Remaster/PS5, etc.), and validates version numbers so querying “2” won’t wrongly match the previous entry; if nothing matches, it suggests the closest titles.
4. **Group leaderboard**: `/psn排行` supports multiple dimensions —
   - **play time (grind)**
   - **number of games**
   - **total trophy score** (platinum/gold/silver/bronze weighted)
   - **platinum count**
5. **Peer comparison**: `/psn对比 @someone` — compares games, total time, trophies, platinums, and games you both own.
6. **Group insights**:
   - `/psn联动` — analyzes PSN friend relations between group members and “everyone is playing” common games;
   - `/psn在线` — who is online in the group and what they are playing.
7. **Visualized image output** — all data is rendered as clean dark-style images via AstrBot’s HTML-rendering capability.
8. **Cache & logging** — built-in in-memory cache reduces PSN requests; usage is written to `usage.log` in the data directory.
9. **💬 Natural language (deterministic-first, optional LLM assist)** — just @ the bot and ask about PSN trophies, a group PSN ranking, or a named game's play time. Intent is recognized **deterministically at the plugin layer** (`@filter.regex`) before the Agent/LLM stage. If a recognized single-game query cannot match a title, `nl_llm_assist` (default on) can ask the configured LLM to choose from the first 60 library titles. LLM function-calling tools (`@filter.llm_tool`) remain registered as a fallback.

---

## 🧩 Supported platforms

The plugin only uses AstrBot’s platform-agnostic generic APIs (commands / text / images / group id / @ message segments), so it is *theoretically* compatible with any messaging platform that supports **group chat, images and @ mentions**.

- **Tested**: `aiocqhttp` (NapCat / Lagrange / go-cqhttp and other OneBot implementations, i.e. QQ).
- **Inferred compatible via generic APIs (not individually tested)**: QQ official (`qq_official` / `qq_official_webhook`), Telegram, Discord, Satori, Feishu/Lark, WeCom (WeChat Work), DingTalk, KOOK, Slack, Misskey, Mattermost.
- **Not applicable**: WeChat Official Accounts (no group chat, restricted passive replies), WeCom smart bots, and other channels without a group-member/ranking model — hence not listed as supported.

If you hit image or @-parsing differences on an untested platform, feel free to open an issue.

---

## 📦 Installation

### Option 1: Plugin Market (recommended)

Search `astrbot_plugin_PlayStationGames` in the "Plugin Market" of the AstrBot dashboard and install it.

### Option 2: Manual install

Download `astrbot_plugin_PlayStationGames-1.4.2.zip` from [Releases](https://github.com/Eason4869/astrbot_plugin_PlayStationGames/releases), extract it, and place the `astrbot_plugin_PlayStationGames` folder under AstrBot's `data/plugins/` directory. Alternatively, use Git:

```bash
# Inside AstrBot's data/plugins directory
cd data/plugins
git clone https://github.com/Eason4869/astrbot_plugin_PlayStationGames.git
```

To upgrade, use Update in the Plugin Market or run `git pull` in the Git installation directory, then reload the plugin in the AstrBot dashboard. Existing bindings and group settings in `data/plugin_data/astrbot_plugin_PlayStationGames/` are retained. The plugin auto-installs its dependencies:

- [`PSNAWP`](https://github.com/isFakeAccount/psnawp) — unofficial Python API wrapper for PSN;
- `aiohttp` — async downloads of covers/avatars;
- `requests` — detection and retry of PSN network errors.

> If auto-install fails, run manually inside the plugin directory:
> ```bash
> pip install -r requirements.txt
> ```

---

## 🔑 Getting the NPSSO token

The plugin accesses PlayStation Network through PSNAWP and **requires** a valid NPSSO token (64 characters):

1. Open and sign in at the [PlayStation China official site](https://www.playstation.com/zh-hans-cn/) (click “登录/Sign in” in the top-right and log in with your PSN account);
2. While still logged in, visit [`https://ca.account.sony.com/api/v1/ssocookie`](https://ca.account.sony.com/api/v1/ssocookie) in the same browser;
3. The page returns something like `{"npsso":"xxxxxxxx...(64 chars)..."}` — copy that string;
4. Paste it into the **NPSSO token** field in the AstrBot plugin config.

⚠️ **Note**:

- NPSSO is your account’s session credential — do not share it;
- The token is valid for roughly a few months; when it expires you’ll see “认证失败 / auth failed”, so just fetch a fresh one and update it;
- The plugin only uses the token to read **public** profile / play-time / trophy data — it never performs any write operation on your account;
- If your server cannot reach PSN directly, set a **proxy** in the config.

---

## ⚙️ Configuration

Editable from the plugin config page in the AstrBot dashboard:

| Key            | Description | Default |
| ---            | ---         | ---     |
| `npsso_token`  | Your PSN NPSSO token (required) | empty |
| `enabled_groups` | Group ids the plugin responds in (list); empty = all groups | `[]` |
| `proxy`        | HTTP/HTTPS proxy, e.g. `http://127.0.0.1:7890` | empty |
| `image_quality`| JPEG output quality (10–100) | `90` |
| `cache_ttl`    | Cache TTL (seconds) for profile/library/trophy data | `300` |
| `max_titles`   | Max games counted for library/leaderboard | `200` |
| `command_prefix` | Command prefix (display only; commands already use `prefix_optional`) | `/` |
| `nl_llm_assist` | If a recognized single-game query fails deterministic title matching, ask the LLM to choose from the first 60 library titles. It does not recognize intent or un-@-mentioned members. | `true` |

Admin `/psn禁用` persists a group in `enabled_groups.json` under `disabled_groups`, overriding the configured allowlist. `/psn启用` removes that block. With an empty allowlist, every other group remains enabled.

---

## 🎯 Command reference

All commands can be triggered with or without the `/` prefix (`prefix_optional=True`).

| Command | Description | Permission |
| ---     | ---         | ---        |
| `/psn帮助` | View command help | everyone |
| `/绑定psn <PSN在线ID>` | Bind your PSN account | everyone |
| `/解绑psn` | Unbind | everyone |
| `/psn同步` | Sync your existing binding into the current group | everyone |
| `/psn [@someone or ID]` | Profile / online status / trophy overview | everyone |
| `/psn游戏库 [@someone]` | Game library & play time | everyone |
| `/psn奖杯 [@someone]` | Per-game trophy progress | everyone |
| `/psn游戏 <game keyword> [@someone]` | One specific game’s time / trophy details | everyone |
| `/psn排行 [游戏数\|时长\|奖杯\|白金]` | Group leaderboard | everyone |
| `/psn对比 @someone` | Compare data with a group member | everyone |
| `/psn联动` | Group friend relations & common games | everyone |
| `/psn在线` | Who is online & playing what | everyone |
| `/psn刷新` | Force-refresh one’s own data cache | everyone |
| `/psn启用` | Enable the plugin in this group | admin only |
| `/psn禁用` | Disable the plugin in this group | admin only |

> `@someone` means you can @ another group member to query them (they must be bound); you can also write their PSN online ID directly.
>
> `/psn游戏` examples: `/psn游戏 艾尔登法环` (yourself), `/psn游戏 黑神话悟空 @someone` (a member), `/psn游戏 老头环`, `/psn游戏 战神5`, `/psn游戏 美末2`. Game names support fuzzy interop of **nicknames / abbreviations / English / official Chinese names**, ignoring case, half/full-width, diacritics and version suffixes (Remake/Remaster/PS5, etc.); a version-numbered query won’t match the wrong predecessor; the closest titles are suggested when nothing matches. It shows the game’s total play time, launch count, first/last played dates, and trophy progress.
>
> 🧹 **Auto-recall of progress prompts**: slower queries (profile/library/trophies/specific game/leaderboard/compare/insights/online/bind) first send a “fetching…” prompt and then auto-recall it once the result is out (OneBot-family protocols, e.g. NapCat/Lagrange/go-cqhttp). On non-OneBot platforms this gracefully degrades to a plain prompt (kept, not recalled).

---

## 💬 Natural-language usage

The plugin supports daily conversational queries — **no need to memorize commands**. Natural language is first recognized **deterministically at the plugin layer** (`@filter.regex`), running **before** the Agent/LLM stage, because:

- ✅ **No chat-capable LLM is required** (just @ the bot / DM-wake it); recognition relies primarily on deterministic rules — fast and stable;
- ✅ **Unaffected by other Agent plugins** — even if `memory_companion`, `private_companion` or similar conversation-hijacking plugins are installed, PSN requests are handled and their event terminated by this plugin *before* reaching them, so you won’t get the “LLM didn’t call the tool / API is broken” issue;
- 💡 **Optional LLM assist**: when a recognized single-game query cannot match a title, `nl_llm_assist` asks the configured provider to choose from the first 60 library titles. Failures fall back to deterministic results. Member nicknames are resolved from the group roster instead;
- The **function-calling tools (`@filter.llm_tool`)** are also registered as a fallback.

Examples (just @ the bot and say — the examples use Chinese phrasings, shown with rough translations):

- 「查一下我的 PSN 资料」(check my PSN profile) / 「我的 PSN 现在在玩什么？」(what am I playing on PSN?)
- 「@某人 的 PSN 现在在玩什么游戏？」(what is @someone playing on PSN?)
- 「看看我的奖杯」(look at my trophies) / 「我的游戏库有哪些？」(what’s in my library?)
- 「**我大镖客2玩了多久？**/ **我r6玩了多久**」— a leading “我” glued to the game name is automatically trimmed, so it searches “Red Dead Redemption 2”/“r6” (“我r6” → “r6” → Rainbow Six Siege) / 「**老头环的游戏信息**」(Elden Ring info) / 「**战神5奖杯进度**」(GoW Ragnarök trophy progress)
- 「**@小明 艾尔登法环玩了多久？**」(how long has @XiaoMing played Elden Ring — queries @XiaoMing’s game) / 「@小明 的游戏库」(Zhang Xiaoming’s library) / 「@小明 奖杯进度」(…trophies)
- 「**看看 小明 的奖杯**」(check Xiaoming’s trophies — @ not required on platforms that expose the member list)
- 「群里谁最肝？」(who’s the grindiest in the group) / 「本群游戏时长排行」/「群里谁的白金多？」
- 「我和 @某人 谁的奖杯多？」(who has more trophies, me or @someone?)
- 「现在群里谁在线？」(who is online now?)
- 「我想绑定 PSN，ID 是 XiaoMing」(I want to bind PSN, ID is XiaoMing)

Anyone who is @-mentioned is auto-recognized as the query target (they must be bound); if not @-mentioned but you typed their name/nickname, the plugin tries to resolve them by the group nickname (most reliable when the typed name matches the actual nickname).

> Tip: generic words such as “bind”, “compare”, “online”, or “how is this game” do not by themselves trigger a PSN query. Mention PSN or ask a specific game-data question. Binding/unbinding in natural language must mention PSN. If the phrasing is ambiguous, use an explicit `/psn…` command.

**🤔 How do I search a niche game nickname that isn’t in the preset list?**
Fuzzy matching does not rely on a fixed “all titles on the internet” table. It falls back through three layers, getting smarter at each:
1. **Alias table (`GAME_ALIAS_KEYWORDS`) + strong fuzzy match**: it first matches your phrasing (including variants with the leading subject removed) against the list of games **you actually own**. As long as the game is in your library, it usually hits via Chinese/English name, substring, or content-token similarity;
2. **Your real library is the source of truth**: whether a match succeeds ultimately depends on the games **you actually own**, not on some pre-recorded “global game list” — so any game you’ve played is covered;
3. **Optional LLM decision**: when a recognized single-game query fails deterministic matching, the configured LLM receives the original wording and chooses among the first 60 library titles (sorted by play time). Titles outside that set are not considered in this step.

If you want a particular phrase to work even better up front, the simplest is to tell the author to add that nickname into the first `GAME_ALIAS_KEYWORDS` layer (we can always add it), but layers 1 and 3 usually suffice.

---

## 🗂️ Data storage

The plugin uses `StarTools.get_data_dir()` for storage, usually under `data/plugin_data/astrbot_plugin_PlayStationGames/`:

```
data/plugin_data/astrbot_plugin_PlayStationGames/
├── psn_bindings.json      # user bindings & group-member mapping
├── enabled_groups.json    # groups enabled or disabled at runtime
├── usage.log              # command usage log
└── image_cache/           # avatar / game-cover cache
```

- `psn_bindings.json` structure:
  ```json
  {
    "users": { "QQ号": "PSN在线ID" },
    "groups": { "群号": { "QQ号": "PSN在线ID" } }
  }
  ```
- All data stays local — nothing is uploaded to any third-party server.

---

## ❓ FAQ

**Q: Why can’t I see some games in my library?**
A: PSN’s play-time API (`titleStats`) only returns **PS4 and above** games with play records; PS3/PSV titles and digital games that were never launched won’t appear.

**Q: Why “user not found” / “private”?**
A: That user may have turned off public visibility of 「activity status / game history / trophies」in their PSN privacy settings. This plugin can only read public data.

**Q: The leaderboard is slow?**
A: It requests each member’s library & trophies separately, and PSNAWP obeys PSN’s rate limit (~1 request per 3s) by default. Please be patient with many members; you can lower `max_titles` to reduce per-request payload.

**Q: “认证失败 / NPSSO invalid”?**
A: The token expired or was revoked. Get a fresh NPSSO as described above and update the config.

**Q: Network/connection errors?**
A: Set a reachable `proxy` in the config and make sure the server can reach domains such as `*.playstation.com`, `*.playstation.net`.

---

## 🛠️ Development notes

Project structure:

```
astrbot_plugin_PlayStationGames/
├── main.py            # plugin entry, registers all commands
├── psn_client.py      # async wrapper over PSNAWP + cache
├── media.py           # image/cover download & data-uri cache
├── metadata.yaml      # plugin metadata
├── _conf_schema.json  # AstrBot config-panel schema
├── requirements.txt
├── logo.png
├── templates/         # HTML render templates (Jinja2-style, rendered by AstrBot)
│   ├── profile.html
│   ├── library.html
│   ├── trophies.html
│   ├── game.html
│   ├── ranking.html
│   ├── compare.html
│   ├── network.html
│   └── online.html
├── README.md          # Chinese README
└── README_EN.md       # English README
```

PSNAWP is a synchronous library using `requests` internally. `psn_client.py` runs blocking calls in an `asyncio` thread pool and serializes them with a lock to respect PSN’s rate limit.

---

## 📄 License

[MIT License](./LICENSE)

---

## 🙏 Credits

- [PSNAWP](https://github.com/isFakeAccount/psnawp) — PlayStation Network API wrapper;
- [AstrBot](https://github.com/Soulter/AstrBot) — the multi-platform chatbot framework;
- Inspiration from [@Maoer233](https://github.com/Maoer233) and [@bvzrays](https://github.com/bvzrays).
