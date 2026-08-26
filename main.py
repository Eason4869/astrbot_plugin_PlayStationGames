"""AstrBot PlayStation (PSN) 玩家数据可视化插件。

功能：
- 按群聊白名单启用；
- 绑定 PSN 在线 ID；
- 查询个人资料 / 游戏库 / 游戏时间 / 奖杯；
- 群内排行（游戏数 / 时长 / 奖杯）；
- 两人对比、群内联动（好友关系、共同游戏）；
- 所有查询结果以图片形式返回。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .media import MediaCache
from .psn_client import PSNAuthError, PSNClient, PSNClientError, PSNForbidden, PSNNotFound

PLUGIN_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PLUGIN_DIR / "templates"

# 奖杯等级 -> 颜色 / 中文名
TROPHY_META = {
    "platinum": {"name": "白金", "color": "#e5e7eb", "emoji": "🏆"},
    "gold": {"name": "金", "color": "#fbbf24", "emoji": "🥇"},
    "silver": {"name": "银", "color": "#cbd5e1", "emoji": "🥈"},
    "bronze": {"name": "铜", "color": "#d97706", "emoji": "🥉"},
}

ONLINE_STATUS_TEXT = {
    "online": "在线",
    "offline": "离线",
    "standby": "待机",
    "availableToPlay": "可加入",
    "doNotDisturb": "勿扰",
    "away": "离开",
}


@register(
    "astrbot_plugin_PlayStationGames",
    "Eason4869",
    "PlayStation玩家数据 — 绑定PSN账号，查询游戏库/游戏时间/奖杯、群内排行与对比（图片可视化）",
    "1.0.1",
    "https://github.com/Eason4869/astrbot_plugin_PlayStationGames",
)
class PlayStationGamesPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config or {}

        self.npsso_token: str = (self.config.get("npsso_token", "") or "").strip()
        self.proxy: str = (self.config.get("proxy", "") or "").strip()
        self.image_quality: int = max(10, min(100, int(self.config.get("image_quality", 90) or 90)))
        self.cache_ttl: int = int(self.config.get("cache_ttl", 300) or 300)
        self.max_titles: int = int(self.config.get("max_titles", 200) or 200)

        plugin_name = PLUGIN_DIR.name
        self.data_dir: Path = StarTools.get_data_dir(plugin_name)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.bindings_file: Path = self.data_dir / "psn_bindings.json"
        self.enabled_groups_file: Path = self.data_dir / "enabled_groups.json"
        self.log_file: Path = self.data_dir / "usage.log"
        self.cover_cache_dir: Path = self.data_dir / "image_cache"

        # 配置里的初始白名单 + 运行时持久化的增删
        cfg_groups = self.config.get("enabled_groups", []) or []
        self._enabled_groups: set[str] = {str(g) for g in cfg_groups}
        self._load_enabled_groups()

        # 绑定数据：users: {qq_id: online_id}; groups: {group_id: {qq_id: online_id}}
        self.bindings: Dict[str, str] = {}
        self.group_bindings: Dict[str, Dict[str, str]] = {}
        self._load_bindings()

        self.media = MediaCache(self.cover_cache_dir, proxy=self.proxy, logger=logger)

        self._psn_client: Optional[PSNClient] = None
        self._psn_client_lock = asyncio.Lock()

        logger.info(
            f"[PSN] 插件加载完成：已载入 {len(self.bindings)} 个绑定，"
            f"{len(self._enabled_groups)} 个启用群聊，数据目录 {self.data_dir}"
        )

    # -------------------- 数据持久化 --------------------

    def _load_bindings(self) -> None:
        if not self.bindings_file.exists():
            return
        try:
            data = json.loads(self.bindings_file.read_text(encoding="utf-8"))
            self.bindings = data.get("users", {}) or {}
            self.group_bindings = data.get("groups", {}) or {}
        except Exception as e:
            logger.error(f"[PSN] 读取绑定数据失败：{e}")
            self.bindings, self.group_bindings = {}, {}

    def _save_bindings(self) -> None:
        try:
            self.bindings_file.write_text(
                json.dumps(
                    {"users": self.bindings, "groups": self.group_bindings},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"[PSN] 保存绑定数据失败：{e}")

    def _load_enabled_groups(self) -> None:
        if not self.enabled_groups_file.exists():
            return
        try:
            data = json.loads(self.enabled_groups_file.read_text(encoding="utf-8"))
            for g in data.get("groups", []) or []:
                self._enabled_groups.add(str(g))
        except Exception as e:
            logger.error(f"[PSN] 读取启用群聊名单失败：{e}")

    def _save_enabled_groups(self) -> None:
        try:
            self.enabled_groups_file.write_text(
                json.dumps({"groups": sorted(self._enabled_groups)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"[PSN] 保存启用群聊名单失败：{e}")

    def _log_usage(self, event: AstrMessageEvent, command: str, detail: str = "") -> None:
        try:
            gid = event.get_group_id() or "private"
            uid = event.get_sender_id()
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            line = f"[{ts}] group={gid} user={uid} cmd={command} {detail}\n"
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    # -------------------- 工具方法 --------------------

    async def _get_client(self) -> PSNClient:
        if self._psn_client is not None:
            return self._psn_client
        async with self._psn_client_lock:
            if self._psn_client is None:
                self._psn_client = PSNClient(
                    npsso_token=self.npsso_token,
                    proxy=self.proxy,
                    cache_ttl=self.cache_ttl,
                    max_titles=self.max_titles,
                    logger=logger,
                )
            return self._psn_client

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        try:
            return event.is_admin()
        except Exception:
            # 兼容不同适配器/平台
            try:
                return bool(getattr(event, "admin", False))
            except Exception:
                return False

    def _group_enabled(self, group_id: Optional[str]) -> bool:
        """群聊是否在白名单内。白名单为空表示不限制。"""
        if not self._enabled_groups:
            return True
        if not group_id:
            return False
        return str(group_id) in self._enabled_groups

    def _gate(self, event: AstrMessageEvent, admin_only: bool = False):
        """返回 (ok, error_message)。"""
        gid = event.get_group_id()
        if admin_only and not self._is_admin(event):
            return False, "该指令需要管理员权限。"
        if gid and not self._group_enabled(gid):
            if admin_only:
                return True, ""  # 管理员可以在未启用群里执行启用命令
            return False, "本群未启用 PSN 插件。请管理员使用 /psn启用 开启。"
        if not self.npsso_token:
            return False, "未配置 NPSSO 令牌，插件无法工作，请先在管理面板配置。"
        return True, ""

    @staticmethod
    def _format_seconds(seconds: int) -> str:
        if seconds <= 0:
            return "0 分钟"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} 分钟"
        hours = minutes / 60
        if hours < 48:
            return f"{hours:.1f} 小时"
        return f"{hours / 24:.1f} 天"

    @staticmethod
    def _trophy_total(ts: Dict[str, int]) -> int:
        return int(ts.get("bronze", 0) + ts.get("silver", 0) + ts.get("gold", 0) + ts.get("platinum", 0))

    @staticmethod
    def _trophy_score(ts: Dict[str, int]) -> int:
        """奖杯加权分：白金 90、金 30、银 15、铜 15? 用通用计分 15/30/90/300."""
        return (
            int(ts.get("bronze", 0)) * 15
            + int(ts.get("silver", 0)) * 30
            + int(ts.get("gold", 0)) * 90
            + int(ts.get("platinum", 0)) * 300
        )

    def _link_user_to_group(self, user_id: str, group_id: Optional[str]) -> bool:
        if not group_id:
            return False
        online_id = self.bindings.get(user_id)
        if not online_id:
            return False
        gmap = self.group_bindings.setdefault(str(group_id), {})
        if gmap.get(user_id) != online_id:
            gmap[user_id] = online_id
            return True
        return False

    def _resolve_target(self, event: AstrMessageEvent, arg: str, fallback: bool = True) -> Optional[str]:
        """从 @提及 / 参数 / 自身绑定解析目标 PSN online_id。"""
        # @ 提及
        try:
            from astrbot.api import message_components as Comp

            for comp in event.message_obj.message:
                if isinstance(comp, Comp.At):
                    target = str(getattr(comp, "qq", "") or getattr(comp, "target", "") or "")
                    if target and target in self.bindings:
                        return self.bindings[target]
        except Exception:
            pass
        if arg:
            arg = arg.strip()
            # @ 的人可能直接以 qq 号传入
            if arg in self.bindings:
                return self.bindings[arg]
            return arg  # 视作 online_id
        if fallback:
            return self.bindings.get(str(event.get_sender_id()))
        return None

    async def _render(self, template_name: str, data: Dict[str, Any], width: int = 820) -> str:
        path = TEMPLATES_DIR / template_name
        # html_render 第一个参数需要模板「内容字符串」，而不是文件路径
        template_content = path.read_text(encoding="utf-8")
        return await self.html_render(
            template_content,
            data,
            options={
                "width": width,
                "full_page": True,
                "omit_background": True,
                "type": "jpeg",
                "quality": self.image_quality,
            },
        )

    async def _safe_client_call(self, coro):
        """执行 PSN 调用，把内部异常转成用户可读文字。"""
        try:
            return await coro
        except PSNAuthError as e:
            return f"PSN 认证失败：{e}\n请重新获取 NPSSO 令牌并更新配置。"
        except PSNNotFound as e:
            return f"未找到该 PSN 用户：{e}"
        except PSNForbidden as e:
            return f"该用户资料私密，无法查询：{e}"
        except PSNClientError as e:
            logger.error(f"[PSN] 接口错误：{e}")
            return f"查询失败：{e}"
        except Exception as e:
            logger.error(f"[PSN] 未预期错误：{e}", exc_info=True)
            return f"查询出错：{e}"

    # -------------------- 群组开关 --------------------

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("psn启用", prefix_optional=True)
    async def cmd_enable(self, event: AstrMessageEvent):
        """管理员：在当前群启用 PSN 插件。"""
        self._log_usage(event, "psn启用")
        gid = str(event.get_group_id() or "")
        if not gid:
            yield event.plain_result("请在群聊中使用该指令。")
            return
        if gid in self._enabled_groups and not self.npsso_token:
            yield event.plain_result("已在本群启用，但尚未配置 NPSSO 令牌。")
            return
        self._enabled_groups.add(gid)
        self._save_enabled_groups()
        yield event.plain_result("✅ 已在本群启用 PSN 插件，使用 /psn帮助 查看指令。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("psn禁用", prefix_optional=True)
    async def cmd_disable(self, event: AstrMessageEvent):
        """管理员：在当前群禁用 PSN 插件。"""
        self._log_usage(event, "psn禁用")
        gid = str(event.get_group_id())
        if gid in self._enabled_groups:
            self._enabled_groups.discard(gid)
            self._save_enabled_groups()
        yield event.plain_result("🚫 已在本群禁用 PSN 插件。")

    # -------------------- 帮助 --------------------

    @filter.command("psn帮助", prefix_optional=True)
    async def cmd_help(self, event: AstrMessageEvent):
        """查看 PSN 插件指令帮助。"""
        self._log_usage(event, "psn帮助")
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return
        help_text = (
            "🎮 PlayStation (PSN) 插件指令\n"
            "——————————————\n"
            "【绑定】\n"
            "  /绑定psn <PSN在线ID>   绑定你的 PSN 账号\n"
            "  /解绑psn              解除绑定\n"
            "  /psn同步              在本群同步已有绑定\n"
            "【查询】\n"
            "  /psn [@某人或ID]      查看个人资料/在线状态/奖杯总览\n"
            "  /psn游戏库 [@某人]    查看游戏库与游戏时间\n"
            "  /psn奖杯 [@某人]      查看各游戏奖杯进度\n"
            "【群互动】\n"
            "  /psn排行 [游戏数|时长|奖杯|白金]  本群排行榜\n"
            "  /psn对比 @某人        与群友对比游戏库/奖杯\n"
            "  /psn联动             群友 PSN 好友关系与共同游戏\n"
            "  /psn在线             群内当前在线/正在玩什么\n"
            "【管理】\n"
            "  /psn启用 / /psn禁用   开关本群插件（管理员）\n"
            "  /psn刷新             强制刷新自己的缓存\n"
            "——————————————\n"
            "首次使用请先 /绑定psn 你的PSN ID。"
        )
        yield event.plain_result(help_text)

    # -------------------- 绑定 --------------------

    @filter.command("绑定psn", prefix_optional=True)
    async def cmd_bind(self, event: AstrMessageEvent, online_id: str = ""):
        """绑定 PSN 在线 ID。"""
        self._log_usage(event, "绑定psn", online_id)
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return
        user_id = str(event.get_sender_id())
        group_id = event.get_group_id()
        online_id = (online_id or "").strip()

        if not online_id:
            current = self.bindings.get(user_id)
            if current:
                yield event.plain_result(f"你当前已绑定 PSN ID：{current}。使用 /绑定psn <新ID> 可更换。")
            else:
                yield event.plain_result("用法：/绑定psn <你的PSN在线ID>。例如：/绑定psn XiaoMing")
            return

        client = await self._get_client()
        yield event.plain_result(f"正在验证 PSN 账号 {online_id}，请稍候...")

        def _fallback():
            return {"profile": {"online_id": online_id, "avatar": ""}, "presence": {}, "trophy_summary": {}}

        try:
            profile = await client.get_full_profile(online_id)
        except PSNNotFound:
            yield event.plain_result(f"未找到 PSN 用户「{online_id}」，请检查在线 ID 是否正确（注意大小写）。")
            return
        except PSNAuthError as e:
            yield event.plain_result(f"认证失败：{e}\n请让管理员更新 NPSSO 令牌。")
            return
        except PSNForbidden:
            # 资料私密也允许绑定，至少 ID 存在
            profile = _fallback()
        except PSNClientError as e:
            # 某些接口（在线/奖杯）被设为私密但资料可见时，仍允许绑定
            logger.warning(f"[PSN] 绑定验证时部分数据获取失败，允许绑定：{e}")
            profile = _fallback()

        self.bindings[user_id] = online_id
        changed = self._link_user_to_group(user_id, group_id)
        self._save_bindings()
        name = profile.get("profile", {}).get("online_id", online_id)
        yield event.plain_result(f"✅ 绑定成功！已关联 PSN 账号：{name}。\n现在可以使用 /psn 查看资料啦。")

    @filter.command("解绑psn", prefix_optional=True)
    async def cmd_unbind(self, event: AstrMessageEvent):
        """解除 PSN 绑定。"""
        self._log_usage(event, "解绑psn")
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return
        user_id = str(event.get_sender_id())
        if user_id in self.bindings:
            del self.bindings[user_id]
        for gmap in self.group_bindings.values():
            gmap.pop(user_id, None)
        self._save_bindings()
        yield event.plain_result("已解除你的 PSN 绑定。")

    @filter.command("psn同步", prefix_optional=True)
    async def cmd_sync_group(self, event: AstrMessageEvent):
        """把自己的已有绑定同步到当前群聊。"""
        self._log_usage(event, "psn同步")
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return
        user_id = str(event.get_sender_id())
        gid = event.get_group_id()
        if user_id not in self.bindings:
            yield event.plain_result("你还没有绑定 PSN ID，请使用 /绑定psn <ID>。")
            return
        if self._link_user_to_group(user_id, gid):
            self._save_bindings()
        yield event.plain_result(f"已将你（{self.bindings[user_id]}）同步到本群排行。")

    @filter.command("psn刷新", prefix_optional=True)
    async def cmd_refresh(self, event: AstrMessageEvent):
        """强制刷新自己的数据缓存。"""
        self._log_usage(event, "psn刷新")
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return
        user_id = str(event.get_sender_id())
        online_id = self.bindings.get(user_id)
        if not online_id:
            yield event.plain_result("你还没有绑定 PSN ID。")
            return
        client = await self._get_client()
        client.invalidate(online_id)
        yield event.plain_result(f"已刷新 {online_id} 的缓存，下次查询将获取最新数据。")

    # -------------------- 个人资料 --------------------

    async def _collect_avatar(self, data: Dict[str, Any]) -> None:
        avatars = []
        prof = data.get("profile") or {}
        if prof.get("avatar"):
            avatars.append(prof["avatar"])
        cg = (data.get("presence") or {}).get("current_game")
        if cg and cg.get("icon_url"):
            avatars.append(cg["icon_url"])
        mapping = await self.media.fetch_many(avatars)
        if prof.get("avatar"):
            prof["avatar_uri"] = mapping.get(prof["avatar"], "")
        if cg and cg.get("icon_url"):
            cg["icon_uri"] = mapping.get(cg["icon_url"], "")

    @filter.command("psn", prefix_optional=True)
    async def cmd_profile(self, event: AstrMessageEvent, arg: str = ""):
        """查看 PSN 个人资料与奖杯总览。"""
        self._log_usage(event, "psn", arg)
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return
        online_id = self._resolve_target(event, arg, fallback=True)
        if not online_id:
            yield event.plain_result("未找到绑定的 PSN ID。请先 /绑定psn <ID>。")
            return

        client = await self._get_client()
        yield event.plain_result(f"正在查询 {online_id} 的资料...")
        data = await self._safe_client_call(client.get_full_profile(online_id))
        if isinstance(data, str):
            yield event.plain_result(data)
            return

        await self._collect_avatar(data)
        pres = data.get("presence", {})
        trophy = data.get("trophy_summary", {})
        render = {
            "profile": data.get("profile", {}),
            "presence": pres,
            "trophy": trophy,
            "trophy_meta": TROPHY_META,
            "status_text": ONLINE_STATUS_TEXT.get(pres.get("online_status", ""), pres.get("online_status", "未知")),
        }
        img_url = await self._render("profile.html", render, width=820)
        yield event.image_result(img_url)

    # -------------------- 游戏库 --------------------

    @filter.command("psn游戏库", prefix_optional=True)
    async def cmd_library(self, event: AstrMessageEvent, arg: str = ""):
        """查看游戏库与游戏时间。"""
        self._log_usage(event, "psn游戏库", arg)
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return
        online_id = self._resolve_target(event, arg, fallback=True)
        if not online_id:
            yield event.plain_result("未找到绑定的 PSN ID。")
            return

        client = await self._get_client()
        yield event.plain_result(f"正在统计 {online_id} 的游戏库，请稍候...")
        titles = await self._safe_client_call(client.get_title_stats(online_id))
        if isinstance(titles, str):
            yield event.plain_result(titles)
            return

        # 也取资料以展示头像和在线状态
        profile_data = None
        try:
            profile_data = await client.get_full_profile(online_id)
        except Exception:
            profile_data = None

        total_seconds = sum(t.get("play_seconds", 0) for t in titles)
        total_count = len(titles)
        top = titles[:30]
        # 下载封面
        icon_urls = [t.get("image_url") for t in top if t.get("image_url")]
        avatar_url = (profile_data or {}).get("profile", {}).get("avatar", "")
        if avatar_url:
            icon_urls.append(avatar_url)
        await self.media.fetch_many(icon_urls)
        for t in top:
            t["image_uri"] = await self.media.fetch(t.get("image_url"))
            t["play_time_str"] = self._format_seconds(t.get("play_seconds", 0))

        # 按平台统计
        platform_stats: Dict[str, int] = {}
        for t in titles:
            p = t.get("platform") or "unknown"
            platform_stats[p] = platform_stats.get(p, 0) + 1

        avatar_uri = ""
        if avatar_url:
            avatar_uri = await self.media.fetch(avatar_url)
        render = {
            "online_id": online_id,
            "avatar_uri": avatar_uri,
            "total_count": total_count,
            "total_time_str": self._format_seconds(total_seconds),
            "platform_stats": platform_stats,
            "titles": top,
            "show_all_note": total_count > len(top),
        }
        img_url = await self._render("library.html", render, width=880)
        yield event.image_result(img_url)

    # -------------------- 奖杯 --------------------

    @filter.command("psn奖杯", prefix_optional=True)
    async def cmd_trophies(self, event: AstrMessageEvent, arg: str = ""):
        """查看各游戏奖杯进度。"""
        self._log_usage(event, "psn奖杯", arg)
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return
        online_id = self._resolve_target(event, arg, fallback=True)
        if not online_id:
            yield event.plain_result("未找到绑定的 PSN ID。")
            return

        client = await self._get_client()
        yield event.plain_result(f"正在获取 {online_id} 的奖杯数据...")

        async def _job():
            trophy_titles = await client.get_trophy_titles(online_id)
            try:
                full = await client.get_full_profile(online_id)
            except Exception:
                full = None
            return trophy_titles, full

        result = await self._safe_client_call(_job())
        if isinstance(result, str):
            yield event.plain_result(result)
            return
        trophy_titles, full = result
        if not trophy_titles:
            yield event.plain_result("该账号暂无可显示的奖杯数据（可能是私密或从未获得过奖杯）。")
            return

        summary = (full or {}).get("trophy_summary", {})
        top = trophy_titles[:25]
        icons = [t.get("title_icon_url") for t in top if t.get("title_icon_url")]
        if full and full.get("profile", {}).get("avatar"):
            icons.append(full["profile"]["avatar"])
        await self.media.fetch_many(icons)
        for t in top:
            t["icon_uri"] = await self.media.fetch(t.get("title_icon_url"))
            t["earned_total"] = self._trophy_total(t.get("earned", {}))
            t["defined_total"] = self._trophy_total(t.get("defined", {}))
            t["platforms_str"] = " / ".join(t.get("platforms", []) or [])

        render = {
            "online_id": online_id,
            "avatar_uri": await self.media.fetch((full or {}).get("profile", {}).get("avatar", "")),
            "summary": summary,
            "trophy_meta": TROPHY_META,
            "titles": top,
            "total_trophy_games": len(trophy_titles),
            "show_all_note": len(trophy_titles) > len(top),
        }
        img_url = await self._render("trophies.html", render, width=880)
        yield event.image_result(img_url)

    # -------------------- 群排行 --------------------

    DIM_MAP = {
        "游戏数": "count",
        "数量": "count",
        "游戏": "count",
        "时长": "time",
        "时间": "time",
        "肝度": "time",
        "奖杯": "trophy",
        "奖杯分": "trophy",
        "白金": "platinum",
        "白金数": "platinum",
    }

    @filter.command("psn排行", prefix_optional=True)
    async def cmd_ranking(self, event: AstrMessageEvent, dimension: str = "时长"):
        """群内 PSN 排行（时长/游戏数/奖杯/白金）。"""
        self._log_usage(event, "psn排行", dimension)
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return
        gid = str(event.get_group_id() or "")
        if not gid:
            yield event.plain_result("请在群聊中使用该指令。")
            return

        # 把发起人同步进群
        if self._link_user_to_group(str(event.get_sender_id()), gid):
            self._save_bindings()

        sort_by = self.DIM_MAP.get(dimension.strip(), "time")
        title_map = {
            "time": "群内 PSN 游戏时长排行",
            "count": "群内 PSN 游戏数量排行",
            "trophy": "群内 PSN 奖杯总分解禁排行",
            "platinum": "群内 PSN 白金杯排行",
        }
        title = title_map[sort_by]

        group_map = self.group_bindings.get(gid, {})
        if not group_map:
            yield event.plain_result("本群还没有人绑定 PSN，请先使用 /绑定psn。")
            return

        yield event.plain_result(f"正在统计「{title}」，共 {len(group_map)} 人，请稍候...")

        client = await self._get_client()
        sem = asyncio.Semaphore(5)

        async def _fetch(qq_id: str, oid: str):
            async with sem:
                try:
                    full = await client.get_full_profile(oid)
                    titles = await client.get_title_stats(oid)
                    trophy_titles = None
                    if sort_by in ("trophy", "platinum"):
                        try:
                            trophy_titles = await client.get_trophy_titles(oid)
                        except Exception:
                            trophy_titles = []
                    return qq_id, oid, full, titles, trophy_titles
                except Exception as e:
                    logger.warning(f"[PSN] 排行查询 {oid} 失败：{e}")
                    return qq_id, oid, None, [], []

        tasks = [_fetch(q, o) for q, o in group_map.items()]
        results = await asyncio.gather(*tasks)

        rank_rows = []
        for qq_id, oid, full, titles, trophy_titles in results:
            if full is None:
                continue
            prof = full.get("profile", {})
            trophy_summary = full.get("trophy_summary", {})
            total_sec = sum(t.get("play_seconds", 0) for t in titles)
            trophy_score = 0
            platinum = 0
            if trophy_titles is not None:
                for tt in trophy_titles:
                    trophy_score += self._trophy_score(tt.get("earned", {}))
                    platinum += int(tt.get("earned", {}).get("platinum", 0))
            else:
                # 用 summary 的累计奖杯兜底
                trophy_score = self._trophy_score(trophy_summary.get("earned", {}))
                platinum = int(trophy_summary.get("earned", {}).get("platinum", 0))

            titles_sorted = sorted(titles, key=lambda x: x.get("play_seconds", 0), reverse=True)
            top_games = titles_sorted[:3]
            for g in top_games:
                g["image_uri"] = await self.media.fetch(g.get("image_url"))
                g["play_time_str"] = self._format_seconds(g.get("play_seconds", 0))

            rank_rows.append(
                {
                    "qq_id": qq_id,
                    "online_id": prof.get("online_id", oid),
                    "avatar_uri": await self.media.fetch(prof.get("avatar", "")),
                    "trophy_level": trophy_summary.get("level", 0),
                    "game_count": len(titles),
                    "total_seconds": total_sec,
                    "total_time_str": self._format_seconds(total_sec),
                    "trophy_score": trophy_score,
                    "platinum": platinum,
                    "top_games": top_games,
                }
            )

        if not rank_rows:
            yield event.plain_result("未能获取到任何群友的数据，请检查 NPSSO 与网络。")
            return

        key_map = {
            "time": "total_seconds",
            "count": "game_count",
            "trophy": "trophy_score",
            "platinum": "platinum",
        }
        rank_rows.sort(key=lambda r: r.get(key_map[sort_by], 0), reverse=True)
        for i, r in enumerate(rank_rows[:10], 1):
            r["rank"] = i

        render = {
            "title": title,
            "dimension": sort_by,
            "rows": rank_rows[:10],
            "member_count": len(rank_rows),
        }
        img_url = await self._render("ranking.html", render, width=820)
        yield event.image_result(img_url)

    # -------------------- 对比 --------------------

    @filter.command("psn对比", prefix_optional=True)
    async def cmd_compare(self, event: AstrMessageEvent, arg: str = ""):
        """与群友对比游戏库和奖杯。"""
        self._log_usage(event, "psn对比", arg)
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return
        my_id = self.bindings.get(str(event.get_sender_id()))
        if not my_id:
            yield event.plain_result("你还没有绑定 PSN ID，请先 /绑定psn。")
            return
        target_id = self._resolve_target(event, arg, fallback=False)
        if not target_id:
            yield event.plain_result("请指定对比对象，例如 /psn对比 @某人 或 /psn对比 对方PSNID。")
            return
        if target_id.lower() == my_id.lower():
            yield event.plain_result("不能和自己对比哦。")
            return

        client = await self._get_client()
        yield event.plain_result(f"正在对比 {my_id} 与 {target_id}...")

        async def _fetch(oid):
            full = await client.get_full_profile(oid)
            titles = await client.get_title_stats(oid)
            tt = await client.get_trophy_titles(oid)
            return full, titles, tt

        result = await self._safe_client_call(asyncio.gather(_fetch(my_id), _fetch(target_id)))
        if isinstance(result, str):
            yield event.plain_result(result)
            return
        (my_full, my_titles, my_tt), (tg_full, tg_titles, tg_tt) = result

        def _summarize(full, titles, trophy_titles):
            sec = sum(t.get("play_seconds", 0) for t in titles)
            tscore = sum(self._trophy_score(t.get("earned", {})) for t in trophy_titles)
            plat = sum(int(t.get("earned", {}).get("platinum", 0)) for t in trophy_titles)
            return {
                "online_id": full.get("profile", {}).get("online_id", ""),
                "avatar_uri": "",
                "game_count": len(titles),
                "total_seconds": sec,
                "total_time_str": self._format_seconds(sec),
                "trophy_score": tscore,
                "platinum": plat,
                "trophy_level": (full.get("trophy_summary") or {}).get("level", 0),
                "title_ids": {t.get("title_id") for t in titles if t.get("title_id")},
            }

        me = _summarize(my_full, my_titles, my_tt)
        tg = _summarize(tg_full, tg_titles, tg_tt)
        me["avatar_uri"] = await self.media.fetch(my_full.get("profile", {}).get("avatar", ""))
        tg["avatar_uri"] = await self.media.fetch(tg_full.get("profile", {}).get("avatar", ""))

        common_ids = me["title_ids"] & tg["title_ids"]
        my_map = {t.get("title_id"): t for t in my_titles}
        tg_map = {t.get("title_id"): t for t in tg_titles}
        common_games = []
        for cid in list(common_ids)[:12]:
            gm = my_map.get(cid) or tg_map.get(cid)
            if gm:
                g = dict(gm)
                g["image_uri"] = await self.media.fetch(g.get("image_url"))
                g["my_time"] = self._format_seconds(my_map.get(cid, {}).get("play_seconds", 0))
                g["tg_time"] = self._format_seconds(tg_map.get(cid, {}).get("play_seconds", 0))
                common_games.append(g)
        common_games.sort(key=lambda x: x.get("play_seconds", 0), reverse=True)

        def _metric(label, a, b, a_str=None, b_str=None):
            a_str = a_str or str(a)
            b_str = b_str or str(b)
            if a > b:
                ar, br = "win", "lose"
            elif a < b:
                ar, br = "lose", "win"
            else:
                ar = br = "draw"
            return {"label": label, "a": {"v": a_str, "r": ar}, "b": {"v": b_str, "r": br}}

        metrics = [
            _metric("游戏数量", me["game_count"], tg["game_count"]),
            _metric("总游戏时长", me["total_seconds"], tg["total_seconds"], me["total_time_str"], tg["total_time_str"]),
            _metric("奖杯总分", me["trophy_score"], tg["trophy_score"]),
            _metric("白金杯数", me["platinum"], tg["platinum"]),
        ]

        render = {
            "me": me,
            "target": tg,
            "metrics": metrics,
            "common_count": len(common_ids),
            "common_games": common_games,
        }
        img_url = await self._render("compare.html", render, width=860)
        yield event.image_result(img_url)

    # -------------------- 群联动 --------------------

    @filter.command("psn联动", prefix_optional=True)
    async def cmd_network(self, event: AstrMessageEvent):
        """群内 PSN 联动：好友关系与共同游戏。"""
        self._log_usage(event, "psn联动")
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return
        gid = str(event.get_group_id() or "")
        if not gid:
            yield event.plain_result("请在群聊中使用该指令。")
            return
        group_map = self.group_bindings.get(gid, {})
        if len(group_map) < 2:
            yield event.plain_result("群内至少需要 2 人绑定才能分析联动。")
            return

        yield event.plain_result("正在分析群内 PSN 联动，可能需要一些时间...")
        client = await self._get_client()

        # 取每个人的游戏库 title_id 集合
        sem = asyncio.Semaphore(4)

        async def _titles(oid):
            async with sem:
                try:
                    return oid, await client.get_title_stats(oid)
                except Exception as e:
                    logger.warning(f"[PSN] 联动查询 {oid} 失败：{e}")
                    return oid, []

        title_results = await asyncio.gather(*[_titles(o) for o in group_map.values()])
        lib_map = {oid: {t.get("title_id"): t for t in titles if t.get("title_id")} for oid, titles in title_results}
        oids = list(lib_map.keys())

        # 共同游戏统计：被多少人拥有 + 总时长
        game_owners: Dict[str, set] = {}
        for oid, tmap in lib_map.items():
            for tid in tmap.keys():
                game_owners.setdefault(tid, set()).add(oid)
        shared = [(tid, owners) for tid, owners in game_owners.items() if len(owners) >= 2]
        shared.sort(key=lambda x: len(x[1]), reverse=True)
        shared_games = []
        for tid, owners in shared[:10]:
            rep = next(iter(lib_map[oid][tid] for oid in owners if tid in lib_map[oid]))
            g = {
                "name": rep.get("name"),
                "image_uri": await self.media.fetch(rep.get("image_url")),
                "owners_count": len(owners),
                "total_time_str": self._format_seconds(
                    sum(lib_map[o][tid].get("play_seconds", 0) for o in owners if tid in lib_map[o])
                ),
            }
            shared_games.append(g)

        # 好友关系：PSNAWP 的 friends_list 是同步生成器，仅对非私密用户有效
        friend_pairs: List[Tuple[str, str]] = []
        friend_cache: Dict[str, set] = {}

        async def _friends(oid):
            async with sem:
                def _job():
                    psn = client._get_psnawp_sync()  # noqa: SLF001
                    user = psn.user(online_id=oid)
                    try:
                        return {f.online_id for f in user.friends_list(limit=1000)}
                    except Exception:
                        return set()
                return oid, await client._run(_job)  # noqa: SLF001

        friend_results = await asyncio.gather(*[_friends(o) for o in oids], return_exceptions=True)
        for r in friend_results:
            if isinstance(r, tuple):
                friend_cache[r[0]] = r[1]
        for i in range(len(oids)):
            for j in range(i + 1, len(oids)):
                a, b = oids[i], oids[j]
                if a in friend_cache and b in friend_cache.get(a, set()):
                    friend_pairs.append((a, b))

        render = {
            "member_count": len(oids),
            "friend_pairs": friend_pairs[:15],
            "friend_count": len(friend_pairs),
            "shared_games": shared_games,
            "shared_count": len(shared),
        }
        img_url = await self._render("network.html", render, width=820)
        yield event.image_result(img_url)

    # -------------------- 群内在线 --------------------

    @filter.command("psn在线", prefix_optional=True)
    async def cmd_online(self, event: AstrMessageEvent):
        """查看群内谁在线、正在玩什么。"""
        self._log_usage(event, "psn在线")
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return
        gid = str(event.get_group_id() or "")
        if not gid:
            yield event.plain_result("请在群聊中使用该指令。")
            return
        group_map = self.group_bindings.get(gid, {})
        if not group_map:
            yield event.plain_result("本群还没有人绑定 PSN。")
            return

        yield event.plain_result("正在查看群友在线状态...")
        client = await self._get_client()
        sem = asyncio.Semaphore(5)

        async def _fetch(qq_id, oid):
            async with sem:
                try:
                    full = await client.get_full_profile(oid)
                    return qq_id, full
                except Exception:
                    return qq_id, None

        results = await asyncio.gather(*[_fetch(q, o) for q, o in group_map.items()])
        rows = []
        for qq_id, full in results:
            if not full:
                continue
            pres = full.get("presence", {})
            prof = full.get("profile", {})
            cg = pres.get("current_game")
            rows.append(
                {
                    "online_id": prof.get("online_id", ""),
                    "avatar_uri": await self.media.fetch(prof.get("avatar", "")),
                    "status": ONLINE_STATUS_TEXT.get(pres.get("online_status", ""), pres.get("online_status", "未知")),
                    "is_online": pres.get("online_status") == "online",
                    "platform": pres.get("platform", ""),
                    "game": cg.get("title_name") if cg else "",
                    "game_icon_uri": await self.media.fetch(cg.get("icon_url")) if cg else "",
                }
            )
        rows.sort(key=lambda r: (not r["is_online"], r["online_id"]))
        online_count = sum(1 for r in rows if r["is_online"])
        render = {"rows": rows, "online_count": online_count, "total": len(rows)}
        img_url = await self._render("online.html", render, width=760)
        yield event.image_result(img_url)

    # -------------------- 生命周期 --------------------

    async def terminate(self):
        logger.info("[PSN] 插件已卸载。")
