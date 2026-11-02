"""免费托管支持（Render / Hugging Face Spaces）：占位网页 + 自我保活。

在自己电脑上运行（没有 RENDER_EXTERNAL_URL / SPACE_HOST）时，这里的功能都会自动跳过。
"""
import asyncio
import logging
import os

import aiohttp
from aiohttp import web

log = logging.getLogger("atrei")

PORT = int(os.getenv("PORT", "7860"))
SPACE_HOST = os.getenv("SPACE_HOST", "")          # HF 自动注入，如 user-dafeiyu.hf.space
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "")  # Render 自动注入，如 https://dafeiyu.onrender.com
SELF_URL = RENDER_URL or (f"https://{SPACE_HOST}" if SPACE_HOST else "")
HOSTED = bool(SELF_URL)
HF_TOKEN = os.getenv("HF_TOKEN", "")
PING_EVERY = 10 * 60  # Render 免费版 15 分钟没访问就休眠


async def keepalive_loop():
    """定时访问自己的公开网址，防止免费实例闲置休眠。"""
    url = SELF_URL.rstrip("/") + "/"
    # 私有 HF Space 要带令牌；Render 是公开网址，不能把令牌发出去
    headers = {"Authorization": f"Bearer {HF_TOKEN}"} if SPACE_HOST and HF_TOKEN else {}
    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            await asyncio.sleep(PING_EVERY)
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    log.info("保活 ping %s -> %s", url, r.status)
            except Exception as e:
                log.warning("保活 ping 失败：%s", e)


async def start_web(bot_name: str, status):
    """托管平台要求容器监听一个端口，这里放一个小页面顺便显示鱼鱼在不在线。"""
    async def index(request):
        online = "在线 🐟" if status() else "连接中…"
        return web.Response(text=f"{bot_name} {online}", content_type="text/plain", charset="utf-8")

    app = web.Application()
    app.router.add_get("/", index)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("网页已在 %s 端口启动", PORT)
