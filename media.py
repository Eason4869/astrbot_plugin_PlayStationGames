"""图片/资源下载与缓存工具。

把 PSN/PlayStation CDN 上的图片下载到本地并转成 data URI，
供 HTML 渲染使用（避免渲染引擎无法访问外网或需要代理）。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from pathlib import Path
from typing import Dict, Optional

import aiohttp


class MediaCache:
    def __init__(self, cache_dir: Path, proxy: str = "", logger=None) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.proxy = proxy or None
        self.logger = logger
        self._mem: Dict[str, str] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    @staticmethod
    def _guess_mime(url: str, default: str = "image/png") -> str:
        low = url.lower().split("?")[0]
        if low.endswith(".jpg") or low.endswith(".jpeg"):
            return "image/jpeg"
        if low.endswith(".webp"):
            return "image/webp"
        if low.endswith(".gif"):
            return "image/gif"
        if low.endswith(".png"):
            return "image/png"
        return default

    @staticmethod
    def to_data_uri(data: bytes, mime: str) -> str:
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _path_for(self, url: str, mime: str) -> Path:
        ext = ".png"
        if mime == "image/jpeg":
            ext = ".jpg"
        elif mime == "image/webp":
            ext = ".webp"
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}{ext}"

    async def fetch(self, url: Optional[str]) -> str:
        """下载图片并返回 data URI；失败返回空串。"""
        if not url:
            return ""
        if url.startswith("data:"):
            return url
        if url in self._mem:
            return self._mem[url]

        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            if url in self._mem:
                return self._mem[url]
            mime = self._guess_mime(url)
            dest = self._path_for(url, mime)
            if dest.exists():
                try:
                    data = dest.read_bytes()
                    uri = self.to_data_uri(data, mime)
                    self._mem[url] = uri
                    return uri
                except Exception:
                    pass
            data = await self._download(url)
            if not data:
                return ""
            try:
                dest.write_bytes(data)
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"写入图片缓存失败 {url}: {e}")
            uri = self.to_data_uri(data, mime)
            self._mem[url] = uri
            return uri

    async def _download(self, url: str) -> Optional[bytes]:
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, proxy=self.proxy) as resp:
                    if resp.status != 200:
                        if self.logger:
                            self.logger.warning(f"图片下载失败 {resp.status}: {url}")
                        return None
                    return await resp.read()
        except Exception as e:
            if self.logger:
                self.logger.warning(f"图片下载异常 {url}: {e}")
            return None

    async def fetch_many(self, urls):
        """并发下载一批图片，返回 url->data_uri 的 dict。"""
        urls = [u for u in urls if u]
        if not urls:
            return {}
        results = await asyncio.gather(*[self.fetch(u) for u in urls], return_exceptions=True)
        mapping = {}
        for u, r in zip(urls, results):
            mapping[u] = r if isinstance(r, str) else ""
        return mapping
