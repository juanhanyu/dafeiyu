"""Hugging Face Spaces 托管支持：占位网页、自我保活、数据库备份到私有数据集。

Space 的硬盘重启就清空，所以把 SQLite 定时备份到 HF_BACKUP_REPO（私有 dataset），启动时再拉回来。
不在 Space 上运行（没有 SPACE_HOST / HF_TOKEN）时，这里的功能都会自动跳过。
"""
import asyncio
import hashlib
import logging
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import aiohttp
from aiohttp import web

log = logging.getLogger("atrei")

PORT = int(os.getenv("PORT", "7860"))
SPACE_HOST = os.getenv("SPACE_HOST", "")          # HF 自动注入，如 user-dafeiyu.hf.space
HF_TOKEN = os.getenv("HF_TOKEN", "")
BACKUP_REPO = os.getenv("HF_BACKUP_REPO", "")      # 如 user/dafeiyu-data
BACKUP_NAME = "atrei.db"
PING_EVERY = 20 * 60
BACKUP_EVERY = 5 * 60


def backup_enabled() -> bool:
    return bool(HF_TOKEN and BACKUP_REPO)


def restore_db(db_path: str):
    """启动前从数据集拉回数据库（还没有备份就跳过）。"""
    if not backup_enabled():
        return
    from huggingface_hub import HfApi, hf_hub_download

    try:
        HfApi(token=HF_TOKEN).create_repo(BACKUP_REPO, repo_type="dataset", private=True, exist_ok=True)
        src = hf_hub_download(BACKUP_REPO, BACKUP_NAME, repo_type="dataset", token=HF_TOKEN)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, db_path)
        log.info("已从 %s 恢复数据库", BACKUP_REPO)
    except Exception as e:
        log.info("没有可恢复的备份（%s），从空数据库开始", type(e).__name__)


def _snapshot(storage) -> tuple[str, str]:
    """用 SQLite 备份 API 拷一份一致的快照，返回 (路径, 内容哈希)。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    dest = sqlite3.connect(path)
    with storage.lock:
        storage.conn.backup(dest)
    dest.close()
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return path, digest


async def backup_loop(storage):
    if not backup_enabled():
        return
    from huggingface_hub import HfApi

    api = HfApi(token=HF_TOKEN)
    last = None
    while True:
        await asyncio.sleep(BACKUP_EVERY)
        try:
            path, digest = await asyncio.to_thread(_snapshot, storage)
            if digest != last:
                await asyncio.to_thread(api.upload_file, path_or_fileobj=path, path_in_repo=BACKUP_NAME,
                                        repo_id=BACKUP_REPO, repo_type="dataset", commit_message="backup")
                last = digest
                log.info("数据库已备份")
            os.remove(path)
        except Exception:
            log.exception("数据库备份失败")


async def keepalive_loop():
    """定时访问自己的公开网址，防止免费 Space 闲置休眠。"""
    if not SPACE_HOST:
        return
    url = f"https://{SPACE_HOST}/"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"} if HF_TOKEN else {}  # 私有 Space 要带令牌
    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            await asyncio.sleep(PING_EVERY)
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    log.info("保活 ping %s -> %s", url, r.status)
            except Exception as e:
                log.warning("保活 ping 失败：%s", e)


async def start_web(bot_name: str, status):
    """HF Space 要求容器监听一个端口，这里放一个小页面顺便显示鱼鱼在不在线。"""
    async def index(request):
        online = "在线 🐟" if status() else "连接中…"
        return web.Response(text=f"{bot_name} {online}", content_type="text/plain", charset="utf-8")

    app = web.Application()
    app.router.add_get("/", index)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("网页已在 %s 端口启动", PORT)
