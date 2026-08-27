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
import re
import time
import traceback
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
    "1.2.1",
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

    def _extract_at_targets(self, event: AstrMessageEvent) -> List[str]:
        """从消息链中提取 @ 提及的目标用户 id（已排除机器人自身与 @全体成员）。

        不同适配器的 At 组件字段统一为 ``qq``，但值可能是 int/str。
        """
        targets: List[str] = []
        try:
            from astrbot.api import message_components as Comp

            self_id = ""
            try:
                self_id = str(event.get_self_id() or "")
            except Exception:
                self_id = ""
            for comp in event.message_obj.message:
                if isinstance(comp, Comp.At):
                    qid = str(getattr(comp, "qq", "") or "").strip()
                    if not qid or qid.lower() == "all":
                        continue
                    if self_id and qid == self_id:
                        # 群聊里唤醒机器人的第一个 @ 不能当作查询目标
                        continue
                    if qid not in targets:
                        targets.append(qid)
                elif isinstance(comp, Comp.AtAll):
                    continue
        except Exception:
            pass
        return targets

    @staticmethod
    def _strip_at_text(text: str) -> str:
        """清理参数里残留的 @ 文本，如 ``@昵称(123456)`` / ``[At:123456]``。"""
        if not text:
            return ""
        text = text.strip()
        # aiocqhttp 适配器的 message_str 中 @ 形如：@昵称(qq号)
        text = re.sub(r"@[^\s()@]+\((\d+)\)", r"\1", text)
        # 其他适配器可能使用 [At:qq号] 占位
        text = re.sub(r"\[At:(\d+)\]", r"\1", text)
        # 去掉残留的纯 @ 前缀（无括号）
        text = re.sub(r"^@\S+", "", text).strip()
        return text

    def _resolve_target(
        self,
        event: AstrMessageEvent,
        arg: str = "",
        fallback: bool = True,
    ) -> Tuple[Optional[str], Optional[str]]:
        """解析查询目标。

        返回 ``(online_id, error)``：
        - 成功时 ``error`` 为 ``None``；
        - 显式指定了目标但无法解析（@了未绑定的人）时，``online_id`` 为 ``None``
          并返回可读错误，避免把 ``@昵称(qq号)`` 之类的脏字符串当成 online_id 发给 PSN。
        """
        # 1) 消息链里的 @ 提及（最可靠）
        at_targets = self._extract_at_targets(event)
        if at_targets:
            for qid in at_targets:
                online_id = self.bindings.get(qid)
                if online_id:
                    return online_id, None
            # 明确 @ 了人但 TA 没绑定
            return None, "被 @ 的用户还没有绑定 PSN 账号，请提醒 TA 先使用 /绑定psn <PSN在线ID>。"

        # 2) 文本参数：可能是 qq 号、PSN online_id，或残留的 @ 文本
        arg = self._strip_at_text(arg or "")
        if arg:
            # 纯数字：优先当作 qq 号查绑定
            if arg.isdigit() and arg in self.bindings:
                return self.bindings[arg], None
            # 否则视为 PSN online_id（交由 PSN 校验）
            return arg, None

        # 3) 回退到发起人自身绑定
        if fallback:
            online_id = self.bindings.get(str(event.get_sender_id()))
            if online_id:
                return online_id, None
            return None, "未找到绑定的 PSN ID。请先使用 /绑定psn <PSN在线ID> 绑定账号。"
        return None, None

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
            "  /psn游戏 <游戏名>     查看指定游戏的时长/奖杯详情\n"
            "【群互动】\n"
            "  /psn排行 [游戏数|时长|奖杯|白金]  本群排行榜\n"
            "  /psn对比 @某人        与群友对比游戏库/奖杯\n"
            "  /psn联动             群友 PSN 好友关系与共同游戏\n"
            "  /psn在线             群内当前在线/正在玩什么\n"
            "【管理】\n"
            "  /psn启用 / /psn禁用   开关本群插件（管理员）\n"
            "  /psn刷新             强制刷新自己的缓存\n"
            "——————————————\n"
            "💬 也支持自然语言：直接 @机器人 说「查下我的奖杯」「群里谁最肝」\n"
            "   「看看 @某人 在玩什么」即可，不必记忆指令。\n"
            "首次使用请先 /绑定psn 你的PSN ID。"
        )
        yield event.plain_result(help_text).use_t2i(False)

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

    # -------------------- 核心业务（命令与 LLM 共用） --------------------

    async def _do_profile(self, online_id: str) -> Tuple[Optional[str], Optional[str]]:
        """查询个人资料，返回 (图片路径, 错误信息)。"""
        client = await self._get_client()
        data = await self._safe_client_call(client.get_full_profile(online_id))
        if isinstance(data, str):
            return None, data
        await self._collect_avatar(data)
        pres = data.get("presence", {})
        trophy = data.get("trophy_summary", {})
        render = {
            "profile": data.get("profile", {}),
            "presence": pres,
            "trophy": trophy,
            "trophy_meta": TROPHY_META,
            "status_text": ONLINE_STATUS_TEXT.get(
                pres.get("online_status", ""), pres.get("online_status", "未知")
            ),
        }
        img_url = await self._render("profile.html", render, width=820)
        return img_url, None

    @filter.command("psn", prefix_optional=True)
    async def cmd_profile(self, event: AstrMessageEvent, arg: str = ""):
        """查看 PSN 个人资料与奖杯总览。"""
        self._log_usage(event, "psn", arg)
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return
        online_id, err = self._resolve_target(event, arg, fallback=True)
        if not online_id:
            yield event.plain_result(err or "未找到绑定的 PSN ID。请先 /绑定psn <ID>。")
            return

        yield event.plain_result(f"正在查询 {online_id} 的资料...")
        img_url, err = await self._do_profile(online_id)
        if err:
            yield event.plain_result(err)
            return
        yield event.image_result(img_url)

    # -------------------- 游戏库 --------------------

    async def _do_library(self, online_id: str) -> Tuple[Optional[str], Optional[str]]:
        """查询游戏库，返回 (图片路径, 错误信息)。"""
        client = await self._get_client()
        titles = await self._safe_client_call(client.get_title_stats(online_id))
        if isinstance(titles, str):
            return None, titles

        profile_data = None
        try:
            profile_data = await client.get_full_profile(online_id)
        except Exception:
            profile_data = None

        total_seconds = sum(t.get("play_seconds", 0) for t in titles)
        total_count = len(titles)
        top = titles[:30]
        icon_urls = [t.get("image_url") for t in top if t.get("image_url")]
        avatar_url = (profile_data or {}).get("profile", {}).get("avatar", "")
        if avatar_url:
            icon_urls.append(avatar_url)
        await self.media.fetch_many(icon_urls)
        for t in top:
            t["image_uri"] = await self.media.fetch(t.get("image_url"))
            t["play_time_str"] = self._format_seconds(t.get("play_seconds", 0))

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
        return img_url, None

    @filter.command("psn游戏库", prefix_optional=True)
    async def cmd_library(self, event: AstrMessageEvent, arg: str = ""):
        """查看游戏库与游戏时间。"""
        self._log_usage(event, "psn游戏库", arg)
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return
        online_id, err = self._resolve_target(event, arg, fallback=True)
        if not online_id:
            yield event.plain_result(err or "未找到绑定的 PSN ID。")
            return

        yield event.plain_result(f"正在统计 {online_id} 的游戏库，请稍候...")
        img_url, err = await self._do_library(online_id)
        if err:
            yield event.plain_result(err)
            return
        yield event.image_result(img_url)


    # -------------------- 奖杯 --------------------

    async def _do_trophies(self, online_id: str) -> Tuple[Optional[str], Optional[str]]:
        """查询奖杯进度，返回 (图片路径, 错误信息)。"""
        client = await self._get_client()

        async def _job():
            trophy_titles = await client.get_trophy_titles(online_id)
            try:
                full = await client.get_full_profile(online_id)
            except Exception:
                full = None
            return trophy_titles, full

        result = await self._safe_client_call(_job())
        if isinstance(result, str):
            return None, result
        trophy_titles, full = result
        if not trophy_titles:
            return None, "该账号暂无可显示的奖杯数据（可能是私密或从未获得过奖杯）。"

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
            "avatar_uri": await self.media.fetch(
                (full or {}).get("profile", {}).get("avatar", "")
            ),
            "summary": summary,
            "trophy_meta": TROPHY_META,
            "titles": top,
            "total_trophy_games": len(trophy_titles),
            "show_all_note": len(trophy_titles) > len(top),
        }
        img_url = await self._render("trophies.html", render, width=880)
        return img_url, None

    # -------------------- 指定游戏详情 --------------------

    @staticmethod
    def _game_match_score(game_name: str, kw: str) -> int:
        """按匹配质量打分，0 表示不匹配。分数越高越优先。"""
        gn = (game_name or "").strip().lower()
        k = (kw or "").strip().lower()
        if not gn or not k:
            return 0
        if gn == k:
            return 1000
        if gn.startswith(k):
            return 800
        if k in gn:
            # 关键词越短越容易误命中，用长度做权重
            return 500 + len(k)
        # 分词子串匹配（中文/英文/数字段）
        gnt = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", gn)
        kt = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", k).strip()
        if kt and kt in gnt:
            return 300 + len(kt)
        return 0

    async def _find_game(self, online_id: str, keyword: str):
        """在用户游戏库中按关键词查找游戏，合并奖杯信息。

        返回 (title_dict, trophy_dict_or_None, all_titles)；未找到时 title_dict 为 None。
        """
        client = await self._get_client()
        titles = await client.get_title_stats(online_id)
        trophy_titles = await client.get_trophy_titles(online_id)

        # 奖杯数据按游戏名建索引（两个接口的游戏名一致；title_id 与
        # np_communication_id 是不同标识，不能直接关联）
        trophy_by_name = {}
        for tt in trophy_titles:
            nm = (tt.get("title_name") or "").strip().lower()
            if nm:
                trophy_by_name[nm] = tt

        scored = []
        for t in titles:
            score = self._game_match_score(t.get("name"), keyword)
            if score > 0:
                scored.append((score, t))
        if not scored:
            return None, None, titles
        scored.sort(key=lambda x: (x[0], x[1].get("play_seconds", 0)), reverse=True)
        best = scored[0][1]

        # 精确名 -> 包含式回退匹配奖杯
        best_name = (best.get("name") or "").strip().lower()
        trophy = trophy_by_name.get(best_name)
        if not trophy and best_name:
            for tn, tt in trophy_by_name.items():
                if best_name in tn or tn in best_name:
                    trophy = tt
                    break
        return best, trophy, titles

    async def _do_game(
        self, online_id: str, keyword: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """查询指定游戏详情，返回 (图片路径, 错误信息)。"""
        client = await self._get_client()
        keyword = (keyword or "").strip()
        if not keyword:
            return None, "请告诉我想查询的游戏名称，例如「查询 艾尔登法环 的游戏信息」。"

        try:
            title, trophy, _titles = await self._find_game(online_id, keyword)
        except PSNAuthError as e:
            return None, f"PSN 认证失败：{e}\n请重新获取 NPSSO 令牌并更新配置。"
        except PSNNotFound as e:
            return None, f"未找到该 PSN 用户：{e}"
        except PSNForbidden as e:
            return None, f"该用户资料私密，无法查询：{e}"
        except PSNClientError as e:
            logger.error(f"[PSN] 查询游戏详情接口错误：{e}")
            return None, f"查询失败：{e}"
        except Exception as e:
            logger.error(f"[PSN] 查询游戏详情失败：{e}", exc_info=True)
            return None, f"查询游戏信息出错：{e}"

        if not title:
            return (
                None,
                f"在 {online_id} 的游戏库中没有找到包含「{keyword}」的游戏。\n"
                "PSN 仅统计 PS4 及以上且有游玩记录的游戏；也可能是名称不完全一致，换个关键词试试。",
            )

        # 头像（取资料）
        avatar_uri = ""
        avatar_url = ""
        try:
            full = await client.get_full_profile(online_id)
            avatar_url = (full.get("profile") or {}).get("avatar", "")
        except Exception:
            full = None
        if avatar_url:
            avatar_uri = await self.media.fetch(avatar_url)

        icon_uri = await self.media.fetch(title.get("image_url"))
        play_seconds = title.get("play_seconds", 0)

        trophy_block = None
        if trophy:
            icon_uri = icon_uri or await self.media.fetch(trophy.get("title_icon_url"))
            earned = trophy.get("earned", {})
            defined = trophy.get("defined", {})
            trophy_block = {
                "progress": trophy.get("progress", 0) or 0,
                "platforms_str": " / ".join(trophy.get("platforms", []) or []),
                "earned_total": self._trophy_total(earned),
                "defined_total": self._trophy_total(defined),
                "earned": earned,
                "defined": defined,
                "trophy_meta": TROPHY_META,
                "score": self._trophy_score(earned),
            }

        render = {
            "online_id": online_id,
            "avatar_uri": avatar_uri,
            "keyword": keyword,
            "game": {
                "name": title.get("name"),
                "icon_uri": icon_uri,
                "platform": title.get("platform") or "未知",
                "play_time_str": self._format_seconds(play_seconds),
                "play_hours": title.get("play_hours", 0),
                "play_count": title.get("play_count"),
                "first_played": title.get("first_played") or "—",
                "last_played": title.get("last_played") or "—",
            },
            "trophy": trophy_block,
        }
        img_url = await self._render("game.html", render, width=820)
        return img_url, None

    @filter.command("psn奖杯", prefix_optional=True)
    async def cmd_trophies(self, event: AstrMessageEvent, arg: str = ""):
        """查看各游戏奖杯进度。"""
        self._log_usage(event, "psn奖杯", arg)
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return
        online_id, err = self._resolve_target(event, arg, fallback=True)
        if not online_id:
            yield event.plain_result(err or "未找到绑定的 PSN ID。")
            return

        yield event.plain_result(f"正在获取 {online_id} 的奖杯数据...")
        img_url, err = await self._do_trophies(online_id)
        if err:
            yield event.plain_result(err)
            return
        yield event.image_result(img_url)

    # -------------------- 指定游戏 --------------------

    @filter.command("psn游戏", prefix_optional=True)
    async def cmd_game(self, event: AstrMessageEvent, args: str = ""):
        """查询指定游戏的游玩/奖杯信息。

        用法：
          /psn游戏 <游戏名关键词>              查自己
          /psn游戏 @某人 <游戏名关键词>        查被@的人
          /psn游戏 <PSN在线ID> <游戏名关键词>   查指定 ID
        """
        self._log_usage(event, "psn游戏", args)
        ok, msg = self._gate(event)
        if not ok:
            yield event.plain_result(msg)
            return

        online_id, keyword, perr = self._parse_game_args(event, args or "")
        if perr:
            yield event.plain_result(perr)
            return

        yield event.plain_result(f"正在查找 {online_id} 的游戏「{keyword}」...")
        img_url, err = await self._do_game(online_id, keyword)
        if err:
            yield event.plain_result(err)
            return
        yield event.image_result(img_url)

    def _parse_game_args(
        self, event: AstrMessageEvent, text: str
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """解析「目标 + 游戏名」参数，返回 (online_id, keyword, error)。"""
        # 1) 优先处理消息链里的 @ 提及
        at_targets = self._extract_at_targets(event)
        cleaned = self._strip_at_text(text or "").strip()
        if at_targets:
            target_id = None
            for qid in at_targets:
                if self.bindings.get(qid):
                    target_id = self.bindings[qid]
                    break
            if not target_id:
                return None, None, "被 @ 的用户还没有绑定 PSN 账号，请提醒 TA 先使用 /绑定psn <PSN在线ID>。"
            keyword = cleaned.strip()
            if not keyword:
                return None, None, "请告诉我想查询的游戏名称，例如「/psn游戏 艾尔登法环」。"
            return target_id, keyword, None

        # 2) 纯文本：可能首段是 PSN ID / QQ 号，其余是游戏名
        parts = [p for p in cleaned.split() if p]
        if not parts:
            online_id = self.bindings.get(str(event.get_sender_id()))
            if not online_id:
                return None, None, "请先使用 /绑定psn <PSN在线ID> 绑定账号，或告诉我游戏名称，例如「/psn游戏 艾尔登法环」。"
            return None, None, "请告诉我想查询的游戏名称，例如「/psn游戏 艾尔登法环」。"

        # 整段作为游戏名、查自己（最常见）
        if len(parts) == 1:
            online_id = self.bindings.get(str(event.get_sender_id()))
            if not online_id:
                return None, None, "你还没有绑定 PSN ID。请先 /绑定psn <PSN在线ID>，或使用「/psn游戏 @某人 游戏名」。"
            return online_id, parts[0], None

        # 多段：尝试把第一段当作目标
        first = parts[0]
        rest = " ".join(parts[1:]).strip()
        target_oid = None
        if first.isdigit() and first in self.bindings:
            target_oid = self.bindings[first]
        else:
            # 第一段若是已知的群成员 PSN ID 则用之，否则把整段都当游戏名查自己
            known_ids = {v.lower(): v for v in self.bindings.values()}
            target_oid = known_ids.get(first.lower())
        if target_oid and rest:
            return target_oid, rest, None

        # 兜底：整段都是游戏名，查自己
        online_id = self.bindings.get(str(event.get_sender_id()))
        if not online_id:
            return None, None, "你还没有绑定 PSN ID。请先 /绑定psn <PSN在线ID>。"
        return online_id, cleaned, None

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

    async def _do_ranking(
        self, gid: str, sort_by: str
    ) -> Tuple[Optional[str], Optional[str], str, int]:
        """统计群排行，返回 (图片路径, 错误信息, 标题, 人数)。"""
        title_map = {
            "time": "群内 PSN 游戏时长排行",
            "count": "群内 PSN 游戏数量排行",
            "trophy": "群内 PSN 奖杯总分解禁排行",
            "platinum": "群内 PSN 白金杯排行",
        }
        title = title_map.get(sort_by, title_map["time"])

        group_map = self.group_bindings.get(gid, {})
        if not group_map:
            return None, "本群还没有人绑定 PSN，请先使用 /绑定psn。", title, 0

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
                trophy_score = self._trophy_score(trophy_summary.get("earned", {}))
                platinum = int(trophy_summary.get("earned", {}).get("platinum", 0))

            titles_sorted = sorted(
                titles, key=lambda x: x.get("play_seconds", 0), reverse=True
            )
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
            return None, "未能获取到任何群友的数据，请检查 NPSSO 与网络。", title, 0

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
        return img_url, None, title, len(rank_rows)

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

        sort_by = self.DIM_MAP.get((dimension or "时长").strip(), "time")
        yield event.plain_result("正在统计群排行，请稍候...")
        img_url, err, title, count = await self._do_ranking(gid, sort_by)
        if err:
            yield event.plain_result(err)
            return
        yield event.image_result(img_url)

    # -------------------- 对比 --------------------

    async def _do_compare(
        self, my_id: str, target_id: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """对比两人数据，返回 (图片路径, 错误信息)。"""
        client = await self._get_client()

        async def _fetch(oid):
            full = await client.get_full_profile(oid)
            titles = await client.get_title_stats(oid)
            tt = await client.get_trophy_titles(oid)
            return full, titles, tt

        result = await self._safe_client_call(
            asyncio.gather(_fetch(my_id), _fetch(target_id))
        )
        if isinstance(result, str):
            return None, result
        (my_full, my_titles, my_tt), (tg_full, tg_titles, tg_tt) = result

        def _summarize(full, titles, trophy_titles):
            sec = sum(t.get("play_seconds", 0) for t in titles)
            tscore = sum(
                self._trophy_score(t.get("earned", {})) for t in trophy_titles
            )
            plat = sum(
                int(t.get("earned", {}).get("platinum", 0)) for t in trophy_titles
            )
            return {
                "online_id": full.get("profile", {}).get("online_id", ""),
                "avatar_uri": "",
                "game_count": len(titles),
                "total_seconds": sec,
                "total_time_str": self._format_seconds(sec),
                "trophy_score": tscore,
                "platinum": plat,
                "trophy_level": (full.get("trophy_summary") or {}).get("level", 0),
                "title_ids": {
                    t.get("title_id") for t in titles if t.get("title_id")
                },
            }

        me = _summarize(my_full, my_titles, my_tt)
        tg = _summarize(tg_full, tg_titles, tg_tt)
        me["avatar_uri"] = await self.media.fetch(
            my_full.get("profile", {}).get("avatar", "")
        )
        tg["avatar_uri"] = await self.media.fetch(
            tg_full.get("profile", {}).get("avatar", "")
        )

        common_ids = me["title_ids"] & tg["title_ids"]
        my_map = {t.get("title_id"): t for t in my_titles}
        tg_map = {t.get("title_id"): t for t in tg_titles}
        common_games = []
        for cid in list(common_ids)[:12]:
            gm = my_map.get(cid) or tg_map.get(cid)
            if gm:
                g = dict(gm)
                g["image_uri"] = await self.media.fetch(g.get("image_url"))
                g["my_time"] = self._format_seconds(
                    my_map.get(cid, {}).get("play_seconds", 0)
                )
                g["tg_time"] = self._format_seconds(
                    tg_map.get(cid, {}).get("play_seconds", 0)
                )
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
            _metric(
                "总游戏时长",
                me["total_seconds"],
                tg["total_seconds"],
                me["total_time_str"],
                tg["total_time_str"],
            ),
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
        return img_url, None

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
        target_id, err = self._resolve_target(event, arg, fallback=False)
        if not target_id:
            yield event.plain_result(
                err or "请指定对比对象，例如 /psn对比 @某人 或 /psn对比 对方PSNID。"
            )
            return
        if target_id.lower() == my_id.lower():
            yield event.plain_result("不能和自己对比哦。")
            return

        yield event.plain_result(f"正在对比 {my_id} 与 {target_id}...")
        img_url, err = await self._do_compare(my_id, target_id)
        if err:
            yield event.plain_result(err)
            return
        yield event.image_result(img_url)

    # -------------------- 群联动 --------------------

    async def _do_network(self, gid: str) -> Tuple[Optional[str], Optional[str]]:
        """群联动分析，返回 (图片路径, 错误信息)。"""
        group_map = self.group_bindings.get(gid, {})
        if len(group_map) < 2:
            return None, "群内至少需要 2 人绑定才能分析联动。"

        client = await self._get_client()
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
        return img_url, None

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

        yield event.plain_result("正在分析群内 PSN 联动，可能需要一些时间...")
        img_url, err = await self._do_network(gid)
        if err:
            yield event.plain_result(err)
            return
        yield event.image_result(img_url)

    # -------------------- 群内在线 --------------------

    async def _do_online(
        self, gid: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """统计群内在线状态，返回 (图片路径, 错误信息)。"""
        group_map = self.group_bindings.get(gid, {})
        if not group_map:
            return None, "本群还没有人绑定 PSN。"

        client = await self._get_client()
        sem = asyncio.Semaphore(5)

        async def _fetch(qq_id, oid):
            async with sem:
                try:
                    full = await client.get_full_profile(oid)
                    return qq_id, full
                except Exception:
                    return qq_id, None

        results = await asyncio.gather(
            *[_fetch(q, o) for q, o in group_map.items()]
        )
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
                    "status": ONLINE_STATUS_TEXT.get(
                        pres.get("online_status", ""),
                        pres.get("online_status", "未知"),
                    ),
                    "is_online": pres.get("online_status") == "online",
                    "platform": pres.get("platform", ""),
                    "game": cg.get("title_name") if cg else "",
                    "game_icon_uri": (
                        await self.media.fetch(cg.get("icon_url")) if cg else ""
                    ),
                }
            )
        rows.sort(key=lambda r: (not r["is_online"], r["online_id"]))
        online_count = sum(1 for r in rows if r["is_online"])
        render = {"rows": rows, "online_count": online_count, "total": len(rows)}
        img_url = await self._render("online.html", render, width=760)
        return img_url, None

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

        yield event.plain_result("正在查看群友在线状态...")
        img_url, err = await self._do_online(gid)
        if err:
            yield event.plain_result(err)
            return
        yield event.image_result(img_url)

    # -------------------- 自然语言意图分发（LLM 之前拦截，绕过其他插件） --------------------
    #
    # 说明：@filter.llm_tool 依赖 Agent 阶段。若用户同时安装了 memory_companion /
    # private_companion 等会接管 Agent 的插件，它们可能在 LLM 选择工具前就拦截请求
    # （例如用记忆里的历史失败话术直接回复，或用私有 Agent 屏蔽本插件工具）。
    #
    # 这里在「插件处理阶段」（star_request，早于 Agent/LLM 阶段）用 @filter.regex
    # 做确定性的中文意图识别：命中 PSN 意图就直接处理并 stop_event()，事件不会再流入
    # 任何 LLM/Agent 插件，从而彻底绕过干扰。llm_tool 仍保留，作为意图模糊时的兜底。

    # 已知指令名（用于判断消息是不是显式指令，避免与 command 重复处理）
    _KNOWN_COMMANDS = (
        "psn帮助", "绑定psn", "解绑psn", "psn同步", "psn刷新",
        "psn游戏库", "psn游戏", "psn奖杯", "psn排行", "psn对比",
        "psn联动", "psn在线", "psn启用", "psn禁用", "psn",
    )

    # 意图识别规则：(动作, 必须包含的任一关键词)。按特异性从高到低排序。
    _INTENT_RULES = (
        # 帮助 / 绑定管理（最具体，先判）
        ("help",      ("psn帮助", "psn指令", "psn命令", "psn怎么用", "psn功能",
                       "playstation帮助")),
        ("sync",      ("同步绑定", "同步到本群", "同步到群", "psn同步")),
        ("unbind",    ("解绑", "解除绑定", "取消绑定", "不绑定了")),
        ("bind",      ("绑定psn", "绑定 psn", "绑定playstation", "绑定play station",
                       "我的psn id", "psn id是", "psnid", "帮我绑定", "绑定psn账号")),
        # 群功能
        ("network",   ("联动", "共同游戏", "共同好友", "好友关系", "一起玩的游戏")),
        ("online",    ("谁在线", "谁在玩", "在线状态", "现在有人", "都在线",
                       "群里在线", "谁在线上", "正在玩什么", "在玩啥",
                       "在玩什么游戏", "群里谁在", "有人在线")),
        ("ranking",   ("排行", "排名", "排行榜", "最肝", "谁玩得最多", "谁游戏最多",
                       "谁的游戏多", "谁奖杯多", "谁白金多", "谁的奖杯多",
                       "谁的白金", "榜单", "rank", "游戏时长排行", "游戏数量排行",
                       "游戏数排行", "奖杯排行", "白金排行", "谁最能玩", "玩得最多")),
        ("compare",   ("对比", "比较", "比分", "和我谁", "跟我谁", "谁更肝", "谁厉害",
                       "battle", "vs", "比一比", "谁更强", "和谁比")),
        # 指定某款游戏（在「游戏库/奖杯」之前，且关键词要足够具体）
        ("game",      ("游戏信息", "游戏详情", "游戏资料", "这个游戏", "该游戏",
                       "这款游戏", "那款游戏", "游戏怎么样", "玩了多久",
                       "玩了多长", "玩了多少小时", "游戏进度", "玩了多久",
                       "游戏时长多少", "这个游戏玩了", "那款游戏玩了")),
        # 个人数据
        ("trophies",  ("奖杯进度", "奖杯情况", "奖杯列表", "我的奖杯", "我的白金",
                       "白金杯", "成就", "trophy", "trophies", "奖杯")),
        ("library",   ("游戏库", "游戏列表", "玩过哪些游戏", "玩过什么游戏", "有哪些游戏",
                       "游戏数量", "多少款游戏", "几个游戏", "游戏时长统计",
                       "总游戏时长", "总时长", "library", "玩过的游戏")),
        ("profile",   ("资料", "主页", "个人主页", "psn资料", "在线吗", "在不在线",
                       "在玩什么", "个人资料", "psn主页", "我的psn", "我的资料",
                       "状态怎么样")),
    )

    _DIMENSION_KEYWORDS = (
        (("白金",), "白金"),
        (("游戏数", "游戏数量", "多少款", "游戏最多", "数量"), "游戏数"),
        (("奖杯", "成就", "白金杯"), "奖杯"),
        (("时长", "时间", "肝", "小时", "玩得最多"), "时长"),
    )

    def _is_explicit_command(self, text: str) -> bool:
        """判断消息是否是显式指令（以指令前缀或指令名开头），交给 command 处理。"""
        t = (text or "").strip()
        if not t:
            return True
        # 去除常见唤醒前缀
        stripped = t
        for pref in ("/", "!", "／", "。", "，", ",", "#"):
            if stripped.startswith(pref):
                stripped = stripped[len(pref):].strip()
                break
        low = stripped.lower()
        for cmd in self._KNOWN_COMMANDS:
            if low.startswith(cmd.lower()):
                return True
        return False

    @staticmethod
    def _detect_dimension(text: str) -> str:
        for kws, dim in PlayStationGamesPlugin._DIMENSION_KEYWORDS:
            if any(k in text for k in kws):
                return dim
        return "时长"

    def _detect_intent(self, text: str) -> Optional[str]:
        """识别 PSN 意图，返回动作名；非 PSN 请求返回 None。"""
        t = (text or "").strip()
        if not t:
            return None
        low = t.lower()
        # 强相关词：PSN/PlayStation/奖杯/游戏库/排行/绑定 等
        psn_hint = any(k in t for k in (
            "psn", "playstation", "奖杯", "白金", "游戏库", "排行", "排名",
            "绑定", "解绑", "联动", "游戏信息", "游戏详情", "游戏资料",
            "游戏时长", "游戏进度", "玩了多久", "玩了多长", "玩了多少",
            "游戏数量", "多少款游戏",
        )) or "ps" in low
        # 群功能词：在群聊中被 @ 时，这些也应识别为 PSN 请求
        group_hint = any(k in t for k in (
            "最肝", "谁在线", "谁在玩", "在线吗", "在线状态", "在玩啥",
            "在玩什么", "对比", "比较", "比一比", "谁厉害", "游戏最多",
            "玩得最多", "谁的游戏", "谁奖杯", "谁白金",
        ))
        game_hint = ("游戏" in t) or ("玩" in t and ("ps" in low or "游戏" in t))
        if not (psn_hint or group_hint or game_hint):
            return None

        for action, kws in self._INTENT_RULES:
            for kw in kws:
                hit = (kw.lower() in low) if kw.isascii() else (kw in t)
                if hit:
                    return action
        # 仅有泛泛的 "我的 psn" 但无明确动作 -> 视为个人资料
        if ("psn" in low or "playstation" in low) and any(
            w in t for w in ("我的", "我", "资料", "看看", "查", "多少")
        ):
            return "profile"
        return None

    @staticmethod
    def _extract_game_keyword(text: str) -> str:
        """从自然语言中提取游戏名关键词。"""
        t = text
        # 去掉引号内的强调直接取引号内容
        m = re.search(r"[「\"'“‘]([^」\"'”’]{1,40})[」\"'”’]", t)
        if m:
            return m.group(1).strip()
        # 去掉意图/动作相关措辞，保留游戏名
        patterns = [
            r"(?:查一下|查下|查询|查|看看|看下|看一下|帮我看|帮我查|告诉我|想知道|请问|一下|"
            r"这个游戏|该游戏|这款游戏|那款游戏|这个|那款|的|游戏信息|游戏详情|游戏资料|"
            r"游戏时长|游戏时间|玩了多久|玩了多长时间|玩了多长|玩了多少小时|玩了多少个小时|"
            r"游戏进度|游戏怎么样|怎么样|如何|是什么|叫什么|"
            r"在|呢|吗|啊|呀|吧|哦|呗|嘛|么|时间|多长|多久|多少小时)",
        ]
        for pat in patterns:
            t = re.sub(pat, " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t.strip(" 的了吗呢啊呀吧哦呗嘛")

    async def _dispatch_nl(self, event: AstrMessageEvent, text: str):
        """自然语言意图处理（在插件阶段运行）。"""
        self._log_usage(event, "nl_dispatch", text[:40])
        ok, gmsg = self._gate(event)
        if not ok:
            yield event.plain_result(gmsg)
            event.stop_event()
            return

        action = self._detect_intent(text)
        if action is None:
            # 非本插件意图：不做任何事，也不停止事件，交还给后续 LLM/插件
            return

        # 群功能需要群聊
        need_group = action in ("ranking", "online", "network")
        gid = str(event.get_group_id() or "")
        if need_group and not gid:
            event.stop_event()
            yield event.plain_result("该功能需要在群聊中使用哦。")
            return

        try:
            if action == "help":
                event.stop_event()
                yield event.plain_result(
                    "直接 @我 用自然语言即可，例如：\n"
                    "「查我的 PSN 资料」「看看我的游戏库」「我的奖杯」\n"
                    "「群里谁最肝」「群里谁在线」「和 @某人 对比」\n"
                    "「查 艾尔登法环 游戏信息」「绑定psn，ID是 xxx」"
                ).use_t2i(False)
                return

            if action == "bind":
                # 提取 ID：优先引号，否则取 psn id 后的内容
                m = re.search(r"(?:id\s*[是为:：]?\s*)([A-Za-z0-9_\-]{2,30})", text, re.I)
                online_id = m.group(1) if m else ""
                if not online_id:
                    event.stop_event()
                    yield event.plain_result(
                        "请告诉我你的 PSN 在线 ID，例如「绑定psn，ID是 XiaoMing」。"
                    ).use_t2i(False)
                    return
                event.stop_event()
                async for r in self._nl_bind(event, online_id):
                    yield r
                return

            if action == "unbind":
                user_id = str(event.get_sender_id())
                if user_id in self.bindings:
                    del self.bindings[user_id]
                    for gmap in self.group_bindings.values():
                        gmap.pop(user_id, None)
                    self._save_bindings()
                event.stop_event()
                yield event.plain_result("已解除你的 PSN 绑定。")
                return

            if action == "sync":
                user_id = str(event.get_sender_id())
                if user_id not in self.bindings:
                    event.stop_event()
                    yield event.plain_result("你还没有绑定 PSN ID，请先说「绑定psn，ID是 xxx」。")
                    return
                if self._link_user_to_group(user_id, gid):
                    self._save_bindings()
                event.stop_event()
                yield event.plain_result(f"已将你（{self.bindings[user_id]}）同步到本群排行。")
                return

            if action == "ranking":
                if self._link_user_to_group(str(event.get_sender_id()), gid):
                    self._save_bindings()
                dim = self._detect_dimension(text)
                sort_by = self.DIM_MAP.get(dim, "time")
                event.stop_event()
                yield event.plain_result("正在统计群排行，请稍候...")
                img_url, err, _t, _c = await self._do_ranking(gid, sort_by)
                if err:
                    yield event.plain_result(err)
                else:
                    yield event.image_result(img_url)
                return

            if action == "online":
                event.stop_event()
                yield event.plain_result("正在查看群友在线状态...")
                img_url, err = await self._do_online(gid)
                if err:
                    yield event.plain_result(err)
                else:
                    yield event.image_result(img_url)
                return

            if action == "network":
                group_map = self.group_bindings.get(gid, {})
                if len(group_map) < 2:
                    event.stop_event()
                    yield event.plain_result("群内至少需要 2 人绑定才能分析联动。")
                    return
                event.stop_event()
                yield event.plain_result("正在分析群内 PSN 联动，可能需要一些时间...")
                img_url, err = await self._do_network(gid)
                if err:
                    yield event.plain_result(err)
                else:
                    yield event.image_result(img_url)
                return

            if action == "compare":
                my_id = self.bindings.get(str(event.get_sender_id()))
                if not my_id:
                    event.stop_event()
                    yield event.plain_result("你还没有绑定 PSN ID，请先说「绑定psn，ID是 xxx」。")
                    return
                target_id, rerr = self._resolve_target(event, "", fallback=False)
                if not target_id:
                    event.stop_event()
                    yield event.plain_result(
                        rerr or "请 @ 你想对比的群友（对方需已绑定 PSN）。"
                    )
                    return
                if target_id.lower() == my_id.lower():
                    event.stop_event()
                    yield event.plain_result("不能和自己对比哦。")
                    return
                event.stop_event()
                yield event.plain_result(f"正在对比 {my_id} 与 {target_id}...")
                img_url, err = await self._do_compare(my_id, target_id)
                if err:
                    yield event.plain_result(err)
                else:
                    yield event.image_result(img_url)
                return

            if action == "game":
                online_id, rerr = await self._tool_resolve_target(event, "")
                if not online_id:
                    event.stop_event()
                    yield event.plain_result(rerr)
                    return
                keyword = self._extract_game_keyword(text)
                if not keyword:
                    event.stop_event()
                    yield event.plain_result("请告诉我想查询的游戏名称，例如「查 艾尔登法环 的游戏信息」。")
                    return
                event.stop_event()
                yield event.plain_result(f"正在查找 {online_id} 的游戏「{keyword}」...")
                img_url, err = await self._do_game(online_id, keyword)
                if err:
                    yield event.plain_result(err)
                else:
                    yield event.image_result(img_url)
                return

            # 资料 / 游戏库 / 奖杯：目标优先取 @，其次自己
            online_id, rerr = await self._tool_resolve_target(event, "")
            if not online_id:
                event.stop_event()
                yield event.plain_result(rerr)
                return

            if action == "library":
                event.stop_event()
                yield event.plain_result(f"正在统计 {online_id} 的游戏库，请稍候...")
                img_url, err = await self._do_library(online_id)
            elif action == "trophies":
                event.stop_event()
                yield event.plain_result(f"正在获取 {online_id} 的奖杯数据...")
                img_url, err = await self._do_trophies(online_id)
            else:  # profile
                event.stop_event()
                yield event.plain_result(f"正在查询 {online_id} 的资料...")
                img_url, err = await self._do_profile(online_id)
            if err:
                yield event.plain_result(err)
            else:
                yield event.image_result(img_url)
            return

        except Exception as e:
            logger.error(f"[PSN] 自然语言分发异常：{e}\n{traceback.format_exc()}")
            event.stop_event()
            yield event.plain_result(self._friendly_tool_error("处理 PSN 请求", e))

    async def _nl_bind(self, event: AstrMessageEvent, online_id: str):
        """自然语言绑定（复用校验逻辑）。"""
        online_id = self._strip_at_text(online_id.strip())
        client = await self._get_client()
        try:
            try:
                profile = await client.get_full_profile(online_id)
            except PSNNotFound:
                yield event.plain_result(
                    f"未找到 PSN 用户「{online_id}」，请检查在线 ID 是否正确（注意大小写）。"
                )
                return
            except PSNAuthError as e:
                yield event.plain_result(f"认证失败：{e}\n请让管理员更新 NPSSO 令牌。")
                return
            except (PSNForbidden, PSNClientError) as e:
                logger.warning(f"[PSN] 自然语言绑定时部分数据获取失败，允许绑定：{e}")
                profile = {"profile": {"online_id": online_id}}
            user_id = str(event.get_sender_id())
            self.bindings[user_id] = online_id
            self._link_user_to_group(user_id, event.get_group_id())
            self._save_bindings()
            name = (profile.get("profile", {}) or {}).get("online_id", online_id)
            yield event.plain_result(
                f"✅ 绑定成功！已关联 PSN 账号：{name}。现在可以直接问我 PSN 相关问题啦。"
            ).use_t2i(False)
        except Exception as e:
            yield event.plain_result(self._friendly_tool_error("绑定 PSN 账号", e))

    # 用一个足够宽的正则挂载分发器：覆盖 PSN / PlayStation / 奖杯 / 游戏库 / 绑定 等。
    # 是否真正命中由 _detect_intent 二次判定；不命中则不停止事件，正常交还给 LLM。
    @filter.regex(
        r"(?i)(psn|play\s*station|奖杯|白金杯?|游戏库|游戏信息|游戏详情|游戏资料|游戏时长|"
        r"游戏进度|玩了多久|玩了多长|游戏数量|多少款游戏|排行|排名|排行榜|"
        r"绑定|解绑|同步|联动|谁在线|谁在玩|在玩啥|在玩什么|在线吗|在线状态|"
        r"对比|比较|比一比|最肝|肝|游戏|玩)"
    )
    async def on_natural_language(self, event: AstrMessageEvent):
        """自然语言入口：在 LLM/Agent 阶段之前确定性地处理 PSN 请求。"""
        # 显式指令交给 command handler，避免重复处理
        text = event.get_message_str().strip()
        if self._is_explicit_command(text):
            return
        # 仅在被唤醒（@机器人 / 私聊 / 唤醒前缀）时响应，避免群里闲聊误触发
        try:
            if not event.is_at_or_wake_command:
                return
        except Exception:
            pass
        async for r in self._dispatch_nl(event, text):
            yield r

    # -------------------- LLM 自然语言工具 --------------------
    #
    # 以下工具通过 @filter.llm_tool 注册给 AstrBot 的函数调用（function-calling）能力。
    # 用户不必输入严格的「/psn ...」指令，用自然语言（如「帮我看看小明在玩什么」
    # 「查下我的奖杯」「群里谁最肝」）也能触发。工具内部复用上面的核心业务方法，
    # 与指令路径行为保持一致。
    #
    # 终止事件的标准写法（见 AstrBot 文档）：先 event.stop_event() 再裸 yield，
    # 这样工具产出的消息会被发送，同时阻止事件继续传播给 LLM 生成多余回复。

    def _tool_gate(self, event: AstrMessageEvent, need_group: bool = False) -> Optional[str]:
        """LLM 工具的统一准入检查，返回错误信息；None 表示通过。"""
        ok, msg = self._gate(event)
        if not ok:
            return msg
        if need_group and not event.get_group_id():
            return "该功能需要在群聊中使用。"
        return None

    async def _yield_tool_result(self, event: AstrMessageEvent, result):
        """LLM 工具统一出口：终止事件并产出一条结果。

        核心在调用本地 LLM 工具时会通过 ``event.send(type="tool_direct_result")``
        立即把结果发给用户；若不登记已发送的纯文本，RespondStage 会把同一结果再发一遍，
        造成「机器人连续回复两条一样的消息」。这里复用核心的去重 extra 标记，确保只发一次。
        """
        event.stop_event()
        try:
            plain = (result.get_plain_text() or "").strip()
        except Exception:
            plain = ""
        if plain:
            try:
                sent = event.get_extra("_send_message_to_user_current_session_plain_texts", [])
                if not isinstance(sent, list):
                    sent = []
                if plain not in sent:
                    sent.append(plain)
                event.set_extra("_send_message_to_user_current_session_plain_texts", sent)
            except Exception:
                pass
        yield result

    async def _tool_error(self, event: AstrMessageEvent, action: str, exc: BaseException):
        """LLM 工具统一异常出口：记录完整堆栈，并以用户可读文案返回，避免异常逃逸到 Agent。

        指令路径有独立的发送流程，而 LLM 工具运行在 Agent 的工具执行器中，一旦抛出未捕获异常，
        Agent 只会得到一个工具执行失败，进而回复“接口不稳定/暂时查不了”，真正原因被吞掉。
        这里把异常转成普通消息结果返回给用户，同时把堆栈打到日志便于排查。
        """
        logger.error(f"[PSN] LLM 工具执行异常（{action}）：{exc}\n{traceback.format_exc()}")
        friendly = self._friendly_tool_error(action, exc)
        async for r in self._yield_tool_result(event, event.plain_result(friendly)):
            yield r

    @staticmethod
    def _friendly_tool_error(action: str, exc: BaseException) -> str:
        text = str(exc)
        if isinstance(exc, PSNAuthError):
            return f"PSN 认证失败，{action}失败。请让管理员检查/更新 NPSSO 令牌。"
        if isinstance(exc, PSNNotFound):
            return f"未找到对应的 PSN 用户：{text}。请确认 PSN 在线 ID 正确，或先使用 /绑定psn 绑定。"
        if isinstance(exc, PSNForbidden):
            return f"该用户资料为私密，无法{action}。"
        if isinstance(exc, asyncio.TimeoutError) or "timeout" in text.lower() or "timed out" in text.lower():
            return f"PSN 接口响应超时，暂时{action}失败，请稍后重试。"
        return f"{action}时出现错误：{text or exc.__class__.__name__}"

    def _refers_to_self(self, event: AstrMessageEvent, text: str) -> bool:
        """判断 LLM 传来的目标文本是否其实指的是用户自己。

        LLM 经常把「我的」「我」或发起人自己的群昵称当作 target 传入，这些并非 PSN ID，
        若直接拿去查询会误报「用户不存在」。命中自身时应回退到发起人自己的绑定。
        """
        t = (text or "").strip()
        if not t:
            return True
        if t.lower() in {"我", "自己", "我自己", "我的", "me", "my", "myself", "self"}:
            return True
        sender_id = str(event.get_sender_id())
        # 纯数字且就是发起人自己的 QQ 号
        if t.isdigit() and t == sender_id:
            return True
        # 去掉常见自称前缀后，与发起人群昵称一致
        try:
            sender_name = (event.get_sender_name() or "").strip()
        except Exception:
            sender_name = ""
        cleaned = re.sub(r"^(我的|我|帮我|查一下|查下|看看|看下|查询)", "", t).strip()
        if sender_name and (cleaned == sender_name or t == sender_name):
            return True
        return False

    async def _tool_resolve_target(self, event: AstrMessageEvent, target: str):
        """LLM 工具专用的目标解析：target 指自己时回退到本人绑定。

        返回 (online_id, error)。
        """
        if self._refers_to_self(event, target or ""):
            online_id = self.bindings.get(str(event.get_sender_id()))
            if online_id:
                return online_id, None
            return None, "未找到绑定的 PSN ID。请先使用 /绑定psn <PSN在线ID> 绑定，或直接说「绑定psn，ID是你的PSN在线ID」。"
        return self._resolve_target(event, target or "", fallback=True)

    @filter.llm_tool(name="psn_query_profile")
    async def tool_query_profile(self, event: AstrMessageEvent, target: str = ""):
        '''查询 PlayStation(PSN) 玩家的个人资料、在线状态和奖杯总览。当用户想查看某人或自己的 PSN 资料、在不在线、正在玩什么、奖杯等级时【必须实际调用本工具】。不要因历史对话里的失败说法而跳过调用。

        Args:
            target(string): 要查询的目标。用户查自己时留空或填"我"；否则填被 @ 者、对方的 QQ 号或 PSN 在线 ID。
        '''
        err = self._tool_gate(event)
        if err:
            async for r in self._yield_tool_result(event, event.plain_result(err)):
                yield r
            return
        try:
            online_id, rerr = await self._tool_resolve_target(event, target or "")
            if not online_id:
                async for r in self._yield_tool_result(event, event.plain_result(rerr)):
                    yield r
                return
            img_url, serr = await self._do_profile(online_id)
            if serr:
                async for r in self._yield_tool_result(event, event.plain_result(serr)):
                    yield r
                return
        except Exception as e:
            async for r in self._tool_error(event, "查询 PSN 资料", e):
                yield r
            return
        async for r in self._yield_tool_result(event, event.image_result(img_url)):
            yield r

    @filter.llm_tool(name="psn_query_library")
    async def tool_query_library(self, event: AstrMessageEvent, target: str = ""):
        '''查询 PlayStation(PSN) 玩家的游戏库和游戏时长。当用户想看某人或自己玩过哪些游戏、总游戏时长、各平台游戏数量、游戏封面墙时【必须实际调用本工具】。不要因历史对话里的失败说法而跳过调用。

        Args:
            target(string): 要查询的目标。用户查自己时留空或填"我"；否则填被 @ 者、对方的 QQ 号或 PSN 在线 ID。
        '''
        err = self._tool_gate(event)
        if err:
            async for r in self._yield_tool_result(event, event.plain_result(err)):
                yield r
            return
        try:
            online_id, rerr = await self._tool_resolve_target(event, target or "")
            if not online_id:
                async for r in self._yield_tool_result(event, event.plain_result(rerr)):
                    yield r
                return
            img_url, serr = await self._do_library(online_id)
            if serr:
                async for r in self._yield_tool_result(event, event.plain_result(serr)):
                    yield r
                return
        except Exception as e:
            async for r in self._tool_error(event, "查询游戏库", e):
                yield r
            return
        async for r in self._yield_tool_result(event, event.image_result(img_url)):
            yield r

    @filter.llm_tool(name="psn_query_trophies")
    async def tool_query_trophies(self, event: AstrMessageEvent, target: str = ""):
        '''查询 PlayStation(PSN) 玩家的奖杯进度。当用户想看某人或自己各游戏的奖杯完成度、白金/金/银/铜奖杯进度时【必须实际调用本工具】。不要因历史对话里的失败说法而跳过调用。

        Args:
            target(string): 要查询的目标。用户查自己时留空或填"我"；否则填被 @ 者、对方的 QQ 号或 PSN 在线 ID。
        '''
        err = self._tool_gate(event)
        if err:
            async for r in self._yield_tool_result(event, event.plain_result(err)):
                yield r
            return
        try:
            online_id, rerr = await self._tool_resolve_target(event, target or "")
            if not online_id:
                async for r in self._yield_tool_result(event, event.plain_result(rerr)):
                    yield r
                return
            img_url, serr = await self._do_trophies(online_id)
            if serr:
                async for r in self._yield_tool_result(event, event.plain_result(serr)):
                    yield r
                return
        except Exception as e:
            async for r in self._tool_error(event, "查询奖杯", e):
                yield r
            return
        async for r in self._yield_tool_result(event, event.image_result(img_url)):
            yield r

    @filter.llm_tool(name="psn_query_game")
    async def tool_query_game(self, event: AstrMessageEvent, game_name: str = "", target: str = ""):
        '''查询某一款具体 PlayStation(PSN) 游戏的详细信息：该玩家在这款游戏上的总游玩时长/小时、启动次数、首次与最近游玩日期、奖杯完成度与白金/金/银/铜进度。当用户指定了某个游戏名并询问"这个游戏玩了多久/游戏信息/游戏详情/奖杯进度"时调用。

        Args:
            game_name(string): 要查询的游戏名称关键词（必填），例如"艾尔登法环""黑神话悟空""战神"。
            target(string): 可选，查谁的这款游戏。查用户自己时留空或填"我"；否则填被 @ 者、对方 QQ 号或 PSN 在线 ID。
        '''
        err = self._tool_gate(event)
        if err:
            async for r in self._yield_tool_result(event, event.plain_result(err)):
                yield r
            return
        try:
            online_id, rerr = await self._tool_resolve_target(event, target or "")
            if not online_id:
                async for r in self._yield_tool_result(event, event.plain_result(rerr)):
                    yield r
                return
            keyword = (game_name or "").strip()
            if not keyword:
                async for r in self._yield_tool_result(
                    event, event.plain_result("请告诉我想查询的游戏名称，例如「艾尔登法环玩了多久」。")
                ):
                    yield r
                return
            img_url, serr = await self._do_game(online_id, keyword)
            if serr:
                async for r in self._yield_tool_result(event, event.plain_result(serr)):
                    yield r
                return
        except Exception as e:
            async for r in self._tool_error(event, "查询游戏信息", e):
                yield r
            return
        async for r in self._yield_tool_result(event, event.image_result(img_url)):
            yield r

    @filter.llm_tool(name="psn_ranking")
    async def tool_ranking(self, event: AstrMessageEvent, dimension: str = "时长"):
        '''查看当前群聊的 PlayStation(PSN) 排行榜。当用户说群排行、谁最肝、谁游戏最多、谁奖杯多、谁白金多时【必须实际调用本工具】。不要因历史对话里的失败说法而跳过调用。

        Args:
            dimension(string): 排行维度，可选值："时长"(游戏时长/肝度，默认)、"游戏数"(游戏数量)、"奖杯"(奖杯总分)、"白金"(白金杯数量)。无法判断时填"时长"。
        '''
        err = self._tool_gate(event, need_group=True)
        if err:
            async for r in self._yield_tool_result(event, event.plain_result(err)):
                yield r
            return
        gid = str(event.get_group_id() or "")
        if self._link_user_to_group(str(event.get_sender_id()), gid):
            self._save_bindings()
        try:
            sort_by = self.DIM_MAP.get((dimension or "时长").strip(), "time")
            img_url, serr, _title, _count = await self._do_ranking(gid, sort_by)
            if serr:
                async for r in self._yield_tool_result(event, event.plain_result(serr)):
                    yield r
                return
        except Exception as e:
            async for r in self._tool_error(event, "查询群排行", e):
                yield r
            return
        async for r in self._yield_tool_result(event, event.image_result(img_url)):
            yield r

    @filter.llm_tool(name="psn_compare")
    async def tool_compare(self, event: AstrMessageEvent, target: str = ""):
        '''把用户自己与另一位 PlayStation(PSN) 玩家做对比，比较游戏数量、总时长、奖杯、白金数及共同游戏。当用户要求和某人对比/比较 PSN 数据时【必须实际调用本工具】。

        Args:
            target(string): 对比对象，通常是被 @ 的人、对方的 QQ 号或 PSN 在线 ID。
        '''
        err = self._tool_gate(event)
        if err:
            async for r in self._yield_tool_result(event, event.plain_result(err)):
                yield r
            return
        my_id = self.bindings.get(str(event.get_sender_id()))
        if not my_id:
            async for r in self._yield_tool_result(
                event, event.plain_result("你还没有绑定 PSN ID，请先说「绑定psn，ID是你的PSN在线ID」，或使用 /绑定psn <PSN在线ID>。")
            ):
                yield r
            return
        try:
            target_id, rerr = self._resolve_target(event, target or "", fallback=False)
            if not target_id:
                async for r in self._yield_tool_result(
                    event, event.plain_result(rerr or "请指定对比对象，可以 @ 某人或告诉我对方的 PSN 在线 ID。")
                ):
                    yield r
                return
            if target_id.lower() == my_id.lower():
                async for r in self._yield_tool_result(event, event.plain_result("不能和自己对比哦。")):
                    yield r
                return
            img_url, serr = await self._do_compare(my_id, target_id)
            if serr:
                async for r in self._yield_tool_result(event, event.plain_result(serr)):
                    yield r
                return
        except Exception as e:
            async for r in self._tool_error(event, "对比 PSN 数据", e):
                yield r
            return
        async for r in self._yield_tool_result(event, event.image_result(img_url)):
            yield r

    @filter.llm_tool(name="psn_online")
    async def tool_online(self, event: AstrMessageEvent):
        '''查看当前群聊里哪些 PlayStation(PSN) 玩家在线、正在玩什么游戏。当用户问"群里谁在线""现在有人在玩什么吗"时【必须实际调用本工具】。不要因历史对话里的失败说法而跳过调用。'''
        err = self._tool_gate(event, need_group=True)
        if err:
            async for r in self._yield_tool_result(event, event.plain_result(err)):
                yield r
            return
        gid = str(event.get_group_id() or "")
        try:
            img_url, serr = await self._do_online(gid)
            if serr:
                async for r in self._yield_tool_result(event, event.plain_result(serr)):
                    yield r
                return
        except Exception as e:
            async for r in self._tool_error(event, "查询在线状态", e):
                yield r
            return
        async for r in self._yield_tool_result(event, event.image_result(img_url)):
            yield r

    @filter.llm_tool(name="psn_bind")
    async def tool_bind(self, event: AstrMessageEvent, online_id: str = ""):
        '''绑定用户自己的 PlayStation(PSN) 账号。当用户说"绑定psn""我的PSN ID是xxx""帮我绑定PSN账号xxx"时调用。会校验该 PSN 在线 ID 是否存在，存在则直接完成绑定。

        Args:
            online_id(string): 用户提供的 PSN 在线 ID（必填）。若用户只是问怎么绑定而没给 ID，则留空。
        '''
        err = self._tool_gate(event)
        if err:
            async for r in self._yield_tool_result(event, event.plain_result(err)):
                yield r
            return
        online_id = self._strip_at_text((online_id or "").strip())
        if not online_id:
            async for r in self._yield_tool_result(
                event,
                event.plain_result(
                    "绑定 PSN 账号请告诉我你的 PSN 在线 ID，例如直接说「绑定psn，ID是 XiaoMing」。\n"
                    "绑定后即可用自然语言或 /psn 查询资料、游戏库、奖杯等。"
                ).use_t2i(False),
            ):
                yield r
            return

        client = await self._get_client()
        try:
            try:
                profile = await client.get_full_profile(online_id)
            except PSNNotFound:
                async for r in self._yield_tool_result(
                    event,
                    event.plain_result(f"未找到 PSN 用户「{online_id}」，请检查在线 ID 是否正确（注意大小写）。"),
                ):
                    yield r
                return
            except PSNAuthError as e:
                async for r in self._yield_tool_result(
                    event, event.plain_result(f"认证失败：{e}\n请让管理员更新 NPSSO 令牌。")
                ):
                    yield r
                return
            except (PSNForbidden, PSNClientError) as e:
                # 资料私密或部分接口受限，但 ID 存在，仍允许绑定
                logger.warning(f"[PSN] LLM 绑定时部分数据获取失败，允许绑定：{e}")
                profile = {"profile": {"online_id": online_id}}

            user_id = str(event.get_sender_id())
            self.bindings[user_id] = online_id
            self._link_user_to_group(user_id, event.get_group_id())
            self._save_bindings()
            name = (profile.get("profile", {}) or {}).get("online_id", online_id)
        except Exception as e:
            async for r in self._tool_error(event, "绑定 PSN 账号", e):
                yield r
            return
        async for r in self._yield_tool_result(
            event, event.plain_result(f"✅ 绑定成功！已关联 PSN 账号：{name}。现在可以直接问我 PSN 相关问题啦。").use_t2i(False)
        ):
            yield r

    # -------------------- 生命周期 --------------------

    async def terminate(self):
        logger.info("[PSN] 插件已卸载。")
