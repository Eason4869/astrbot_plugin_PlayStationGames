"""PSNAWP 的异步封装。

PSNAWP 底层使用同步的 ``requests``，且内置速率限制（默认每 3 秒 1 次请求）。
本模块将其阻塞调用统一丢到线程池中执行，并加上简单的内存缓存，以适配 AstrBot
的异步事件循环。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

try:  # 延迟导入，便于在未安装 PSNAWP 的环境中给出清晰提示
    from psnawp_api import PSNAWP
    from psnawp_api.core.psnawp_exceptions import (
        PSNAWPAuthenticationError,
        PSNAWPForbiddenError,
        PSNAWPNotFoundError,
    )

    _PSNAWP_AVAILABLE = True
    _PSNAWP_IMPORT_ERROR = None
except Exception as _e:  # pragma: no cover - 仅在依赖缺失时触发
    PSNAWP = None  # type: ignore
    PSNAWPAuthenticationError = Exception  # type: ignore
    PSNAWPForbiddenError = Exception  # type: ignore
    PSNAWPNotFoundError = Exception  # type: ignore
    _PSNAWP_AVAILABLE = False
    _PSNAWP_IMPORT_ERROR = _e


class PSNClientError(Exception):
    """PSN 客户端基础异常。"""


class PSNAuthError(PSNClientError):
    """NPSSO 令牌无效或过期。"""


class PSNNotFound(PSNClientError):
    """未找到该用户。"""


class PSNForbidden(PSNClientError):
    """用户资料私密或无权访问。"""


def _trophy_set_to_dict(ts: Any) -> Dict[str, int]:
    """将 PSNAWP 的 TrophySet dataclass 转为普通 dict。"""
    if ts is None:
        return {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0}
    try:
        return {
            "bronze": int(getattr(ts, "bronze", 0) or 0),
            "silver": int(getattr(ts, "silver", 0) or 0),
            "gold": int(getattr(ts, "gold", 0) or 0),
            "platinum": int(getattr(ts, "platinum", 0) or 0),
        }
    except Exception:
        return {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0}


def _platforms_to_str(platforms: Any) -> List[str]:
    result: List[str] = []
    try:
        for p in platforms or []:
            val = getattr(p, "value", str(p))
            if val and val != "UNKNOWN":
                result.append(str(val))
    except Exception:
        pass
    return result


def _dt_str(dt: Any) -> Optional[str]:
    if isinstance(dt, datetime):
        try:
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return None
    return None


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        try:
            return value.isoformat()
        except Exception:
            return None
    return None


class PSNClient:
    """对 PSNAWP 的异步包装，所有公开方法均为协程。"""

    def __init__(
        self,
        npsso_token: str,
        proxy: str = "",
        cache_ttl: int = 300,
        max_titles: int = 200,
        logger: Any = None,
    ) -> None:
        if not _PSNAWP_AVAILABLE:
            raise PSNClientError(
                "未安装 PSNAWP 依赖，请在插件目录执行 `pip install -r requirements.txt`。"
                f" 原始错误：{_PSNAWP_IMPORT_ERROR}"
            )
        if not npsso_token:
            raise PSNAuthError("未配置 NPSSO 令牌，插件无法工作。")

        self.npsso_token = npsso_token.strip()
        self.proxy = (proxy or "").strip()
        self.cache_ttl = max(30, int(cache_ttl or 300))
        self.max_titles = max(10, int(max_titles or 200))
        self.logger = logger

        # PSNAWP 内部使用 requests，通过环境变量为其设置代理。
        if self.proxy:
            import os

            os.environ.setdefault("HTTP_PROXY", self.proxy)
            os.environ.setdefault("HTTPS_PROXY", self.proxy)
            os.environ.setdefault("http_proxy", self.proxy)
            os.environ.setdefault("https_proxy", self.proxy)

        self._psnawp: Any = None
        self._lock = asyncio.Lock()
        self._cache: Dict[str, Dict[str, Any]] = {}
        # 串行化所有 PSNAWP 调用：其内置速率限制器为每 3 秒 1 个请求，
        # 并发提交到线程池只会在线程池里排队等待，串行更可控。
        self._call_lock = asyncio.Lock()

    # ---------- 内部工具 ----------

    def _log(self, level: str, msg: str) -> None:
        if self.logger:
            getattr(self.logger, level, self.logger.info)(msg)

    def _get_psnawp_sync(self) -> Any:
        if self._psnawp is None:
            try:
                self._psnawp = PSNAWP(npsso_cookie=self.npsso_token)
            except PSNAWPAuthenticationError as e:
                raise PSNAuthError(f"NPSSO 令牌无效或已过期：{e}") from e
            except Exception as e:
                raise PSNClientError(f"初始化 PSNAWP 失败：{e}") from e
        return self._psnawp

    async def _run(self, func, *args, **kwargs):
        """在独立线程中执行同步的 PSNAWP 调用，并统一异常。"""
        async with self._call_lock:
            loop = asyncio.get_event_loop()
            try:
                return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
            except PSNAWPAuthenticationError as e:
                # token 可能过期，重置以便下次重建
                self._psnawp = None
                raise PSNAuthError(f"NPSSO 令牌无效或已过期：{e}") from e
            except PSNAWPNotFoundError as e:
                raise PSNNotFound(f"未找到：{e}") from e
            except PSNAWPForbiddenError as e:
                raise PSNForbidden(f"资料私密或无权访问：{e}") from e
            except PSNClientError:
                raise
            except Exception as e:
                # PSNAWP 较新版本可能对不同错误使用 requests 包装，识别常见文本
                text = str(e).lower()
                if "401" in text or ("403" in text and "token" in text) or "npsso" in text:
                    self._psnawp = None
                    raise PSNAuthError(f"认证失败，请检查/更新 NPSSO：{e}") from e
                raise PSNClientError(f"PSN 请求异常：{e}") from e

    def _cache_get(self, key: str) -> Any:
        item = self._cache.get(key)
        if not item:
            return None
        if time.time() - item["ts"] > self.cache_ttl:
            self._cache.pop(key, None)
            return None
        return item["value"]

    def _cache_set(self, key: str, value: Any) -> None:
        self._cache[key] = {"ts": time.time(), "value": value}

    # ---------- 数据转换 ----------

    @staticmethod
    def _normalize_profile(profile: Dict[str, Any], online_id: str) -> Dict[str, Any]:
        avatars = profile.get("avatars") or []
        avatar = ""
        # 优先 xl / l
        for size in ("xl", "l", "m", "s"):
            for a in avatars:
                if a.get("size") == size:
                    avatar = a.get("url", "")
                    break
            if avatar:
                break
        return {
            "online_id": profile.get("onlineId") or online_id,
            "about_me": profile.get("aboutMe", "") or "",
            "avatar": avatar,
            "is_plus": bool(profile.get("isPlus", False)),
            "is_verified": bool(profile.get("isOfficiallyVerified", False)),
            "languages": profile.get("languages", []) or [],
        }

    def _normalize_presence(self, presence: Dict[str, Any]) -> Dict[str, Any]:
        basic = (presence or {}).get("basicPresence", {}) or {}
        primary = basic.get("primaryPlatformInfo", {}) or {}
        online_status = primary.get("onlineStatus", "offline")
        platform = primary.get("platform", "")
        last_online = basic.get("lastOnlineDate", "") or ""

        game_list = basic.get("gameTitleInfoList") or []
        current_game = None
        if game_list:
            g = game_list[0]
            current_game = {
                "title_name": g.get("titleName", ""),
                "np_title_id": g.get("npTitleId", ""),
                "icon_url": g.get("npTitleIconUrl", ""),
                "format": g.get("format", ""),
                "launch_platform": g.get("launchPlatform", ""),
                "game_status": g.get("gameStatus", ""),
            }

        return {
            "online_status": online_status,
            "platform": platform,
            "last_online": last_online,
            "current_game": current_game,
            "availability": basic.get("availability", ""),
        }

    def _normalize_title(self, title: Any) -> Dict[str, Any]:
        duration = getattr(title, "play_duration", None)
        total_seconds = int(duration.total_seconds()) if duration else 0
        return {
            "title_id": getattr(title, "title_id", None),
            "name": getattr(title, "name", None) or "未知游戏",
            "image_url": getattr(title, "image_url", None),
            "platform": (getattr(getattr(title, "category", None), "value", "") or ""),
            "play_count": getattr(title, "play_count", None),
            "first_played": _dt_str(getattr(title, "first_played_date_time", None)),
            "last_played": _dt_str(getattr(title, "last_played_date_time", None)),
            "play_seconds": total_seconds,
            "play_minutes": total_seconds // 60,
            "play_hours": round(total_seconds / 3600, 1),
        }

    @staticmethod
    def _normalize_trophy_title(tt: Any) -> Dict[str, Any]:
        return {
            "np_communication_id": getattr(tt, "np_communication_id", None),
            "title_name": getattr(tt, "title_name", None) or "未知游戏",
            "title_icon_url": getattr(tt, "title_icon_url", None),
            "platforms": _platforms_to_str(getattr(tt, "title_platform", None)),
            "progress": getattr(tt, "progress", None) or 0,
            "earned": _trophy_set_to_dict(getattr(tt, "earned_trophies", None)),
            "defined": _trophy_set_to_dict(getattr(tt, "defined_trophies", None)),
            "last_updated": _iso(getattr(tt, "last_updated_datetime", None)),
        }

    # ---------- 公开 API ----------

    async def get_full_profile(self, online_id: str) -> Dict[str, Any]:
        """获取用户的完整资料：资料、在线状态、奖杯汇总。"""
        cache_key = f"full_{online_id.lower()}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        def _job() -> Dict[str, Any]:
            psn = self._get_psnawp_sync()
            user = psn.user(online_id=online_id)
            profile = user.profile()
            result: Dict[str, Any] = {"online_id": user.online_id, "account_id": user.account_id}
            result["profile"] = self._normalize_profile(profile, user.online_id)
            # presence 可能因私密抛 Forbidden，降级处理
            try:
                presence = user.get_presence()
                result["presence"] = self._normalize_presence(presence)
            except PSNAWPForbiddenError:
                result["presence"] = self._normalize_presence({})
                result["presence"]["private"] = True
            # 奖杯汇总
            try:
                summary = user.trophy_summary()
                result["trophy_summary"] = {
                    "level": getattr(summary, "trophy_level", -1),
                    "progress": getattr(summary, "progress", 0),
                    "tier": getattr(summary, "tier", -1),
                    "earned": _trophy_set_to_dict(getattr(summary, "earned_trophies", None)),
                }
            except PSNAWPForbiddenError:
                result["trophy_summary"] = {"private": True}
            return result

        data = await self._run(_job)
        self._cache_set(cache_key, data)
        return data

    async def get_title_stats(self, online_id: str) -> List[Dict[str, Any]]:
        """获取用户的游戏时间统计（PS4 及以上），按游戏时长降序。"""
        cache_key = f"titles_{online_id.lower()}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        def _job() -> List[Dict[str, Any]]:
            psn = self._get_psnawp_sync()
            user = psn.user(online_id=online_id)
            titles = []
            iterator = user.title_stats(limit=self.max_titles, page_size=200)
            for t in iterator:
                titles.append(self._normalize_title(t))
            titles.sort(key=lambda x: x.get("play_seconds", 0), reverse=True)
            return titles

        data = await self._run(_job)
        self._cache_set(cache_key, data)
        return data

    async def get_trophy_titles(self, online_id: str) -> List[Dict[str, Any]]:
        """获取用户每个有奖杯的游戏的奖杯进度。"""
        cache_key = f"trophy_titles_{online_id.lower()}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        def _job() -> List[Dict[str, Any]]:
            psn = self._get_psnawp_sync()
            user = psn.user(online_id=online_id)
            result = []
            for tt in user.trophy_titles(limit=self.max_titles, page_size=100):
                result.append(self._normalize_trophy_title(tt))
            # 进度降序、再按白金数降序
            result.sort(
                key=lambda x: (
                    x.get("progress", 0) or 0,
                    x.get("earned", {}).get("platinum", 0),
                ),
                reverse=True,
            )
            return result

        data = await self._run(_job)
        self._cache_set(cache_key, data)
        return data

    async def resolve_online_id(self, online_id: str) -> bool:
        """校验一个 online_id 是否存在/可访问。"""
        try:
            await self.get_full_profile(online_id)
            return True
        except PSNNotFound:
            return False

    def invalidate(self, online_id: Optional[str] = None) -> None:
        """清除缓存，可指定用户。"""
        if not online_id:
            self._cache.clear()
            return
        key = online_id.lower()
        for k in list(self._cache.keys()):
            if key in k:
                self._cache.pop(k, None)
