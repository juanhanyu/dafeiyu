"""QQ 官方机器人入口：python main.py"""
import asyncio
import base64
import logging
import re
import time

import aiohttp
import botpy
from botpy.http import Route
from botpy.message import C2CMessage, DirectMessage, GroupMessage, Message

from atrei import config, hf
from atrei.brain import Brain
from atrei.scheduler import Scheduler
from atrei.storage import Storage
from atrei.tools import Context

log = logging.getLogger("atrei")

# botpy 内部的 aiohttp 默认不走系统代理（HTTPS_PROXY），开了代理/加速器时会连不上，这里统一打开
_session_init = aiohttp.ClientSession.__init__


def _session_init_trust_env(self, *args, **kwargs):
    kwargs.setdefault("trust_env", True)
    _session_init(self, *args, **kwargs)


aiohttp.ClientSession.__init__ = _session_init_trust_env

CHUNK = 1500          # 单条消息最大字数，超出分条发
REPLY_WINDOW = 290    # 被动回复有效期（秒），群聊官方限制约 5 分钟


def split_text(text: str, size: int = CHUNK) -> list[str]:
    chunks = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks + [text] if text else chunks


class AtreiClient(botpy.Client):
    def __init__(self, cfg, brain: Brain, scheduler: Scheduler, **kw):
        super().__init__(**kw)
        self.cfg = cfg
        self.brain = brain
        self.scheduler = scheduler
        self.http_session: aiohttp.ClientSession | None = None
        # target_id -> [最近一条用户消息 id, 时间, 已用 msg_seq]，用于在窗口内"被动"推送提醒
        self.last_msg: dict[str, list] = {}
        scheduler.register("group", self.push_group)
        scheduler.register("c2c", self.push_c2c)
        scheduler.register("guild", self.push_guild)
        scheduler.register("dm", self.push_dm)

    async def on_ready(self):
        log.info("「%s」上线了", self.robot.name)
        if self.http_session is None:  # 断线重连也会触发 on_ready，只初始化一次
            self.http_session = aiohttp.ClientSession()
            self.scheduler_task = asyncio.create_task(self.scheduler.run_forever())

    # ---------- 收消息 ----------
    async def on_group_at_message_create(self, message: GroupMessage):
        uid = message.author.member_openid
        ctx = Context(user_id=uid, session_id=f"g:{message.group_openid}:{uid}",
                      target_kind="group", target_id=message.group_openid, is_group=True)
        await self._handle(ctx, message, lambda **kw: self.api.post_group_message(
            group_openid=message.group_openid, msg_type=0, msg_id=message.id, **kw))

    async def on_c2c_message_create(self, message: C2CMessage):
        uid = message.author.user_openid
        ctx = Context(user_id=uid, session_id=f"u:{uid}", target_kind="c2c", target_id=uid)
        await self._handle(ctx, message, lambda **kw: self.api.post_c2c_message(
            openid=uid, msg_type=0, msg_id=message.id, **kw))

    async def on_at_message_create(self, message: Message):  # QQ 频道里 @ 机器人
        uid = message.author.id
        ctx = Context(user_id=uid, session_id=f"c:{message.channel_id}:{uid}",
                      target_kind="guild", target_id=message.channel_id, is_group=True)
        await self._handle(ctx, message, lambda **kw: message.reply(**kw))

    async def on_direct_message_create(self, message: DirectMessage):  # 频道私信
        uid = message.author.id
        ctx = Context(user_id=uid, session_id=f"d:{uid}", target_kind="dm", target_id=message.guild_id)
        await self._handle(ctx, message, lambda **kw: message.reply(**kw))

    async def _handle(self, ctx: Context, message, send):
        text = re.sub(r"<@!?\d+>", "", message.content or "").strip()
        images = await self._download_images(message.attachments)
        log.info("[%s] %s%s", ctx.session_id, text[:60], f" +{len(images)}图" if images else "")
        self.last_msg[ctx.target_id] = [message.id, time.time(), 0]

        reply = await self.brain.handle(ctx, text, images)
        for chunk in split_text(reply):
            await self._send_passive(ctx.target_id, send, chunk)
        for path in ctx.stickers:
            try:
                await self._send_image(ctx, message, send, path)
            except Exception as e:
                log.warning("表情包发送失败：%s", e)

    async def _send_image(self, ctx: Context, message, send, path):
        data = path.read_bytes()
        if ctx.target_kind in ("guild", "dm"):
            await message.reply(file_image=data)
            return
        # 群/私聊：先把图片上传成富媒体，再发 msg_type=7
        route = (Route("POST", "/v2/groups/{group_openid}/files", group_openid=ctx.target_id)
                 if ctx.target_kind == "group" else
                 Route("POST", "/v2/users/{openid}/files", openid=ctx.target_id))
        uploaded = await self.api._http.request(route, json={
            "file_type": 1, "file_data": base64.b64encode(data).decode(), "srv_send_msg": False})
        state = self.last_msg[ctx.target_id]
        state[2] += 1
        await send(msg_type=7, content=" ", media={"file_info": uploaded["file_info"]}, msg_seq=state[2])

    async def _send_passive(self, target_id, send, content):
        state = self.last_msg[target_id]
        state[2] += 1
        try:
            await send(content=content, msg_seq=state[2])
        except Exception as e:
            log.warning("发送失败：%s", e)
            if "url" in str(e).lower() or "链接" in str(e):
                await send(content=re.sub(r"https?://\S+|\b\w+\.(com|cn|net|org)\S*", "[链接已隐藏]", content),
                           msg_seq=state[2] + 1)
                state[2] += 1

    async def _download_images(self, attachments) -> list[tuple[str, bytes]]:
        images = []
        for a in attachments or []:
            if not (a.content_type or "").startswith("image") or not a.url:
                continue
            url = a.url if a.url.startswith("http") else "https://" + a.url.lstrip("/")
            try:
                async with self.http_session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    data = await r.read()
                mime = a.content_type if "/" in a.content_type else "image/png"
                images.append((mime, data))
            except Exception as e:
                log.warning("图片下载失败：%s", e)
        return images

    # ---------- 主动推送（提醒用） ----------
    def _recent(self, target_id):
        state = self.last_msg.get(target_id)
        if state and time.time() - state[1] < REPLY_WINDOW:
            state[2] += 1
            return state[0], state[2]
        return None, 1

    async def push_group(self, target_id, user_id, text):
        msg_id, seq = self._recent(target_id)
        await self.api.post_group_message(group_openid=target_id, msg_type=0, content=text,
                                          msg_id=msg_id, msg_seq=seq)
        return True

    async def push_c2c(self, target_id, user_id, text):
        msg_id, seq = self._recent(target_id)
        await self.api.post_c2c_message(openid=target_id, msg_type=0, content=text, msg_id=msg_id, msg_seq=seq)
        return True

    async def push_guild(self, target_id, user_id, text):
        await self.api.post_message(channel_id=target_id, content=f"<@{user_id}> {text}")
        return True

    async def push_dm(self, target_id, user_id, text):
        await self.api.post_dms(guild_id=target_id, content=text)
        return True


async def main():
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [atrei] %(message)s", "%H:%M:%S"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    cfg = config.load()
    if not cfg.qq_appid or not cfg.qq_secret:
        raise SystemExit("请先在 .env 里填 QQ_APPID 和 QQ_SECRET（见 README）")
    hf.restore_db(cfg.db_path)
    storage = Storage(cfg.db_path)
    brain = Brain(cfg, storage)
    scheduler = Scheduler(storage, cfg.tz)
    intents = botpy.Intents(public_messages=True, public_guild_messages=True, direct_message=True)
    client = AtreiClient(cfg, brain, scheduler, intents=intents, is_sandbox=cfg.qq_sandbox)
    if hf.HOSTED:  # 在 Render / HF Space 上：开网页、保活、定时备份
        await hf.start_web(cfg.bot_name, lambda: client.http_session is not None)
        background = [asyncio.create_task(hf.keepalive_loop()), asyncio.create_task(hf.backup_loop(storage))]
    async with client:
        await client.start(appid=cfg.qq_appid, secret=cfg.qq_secret)


if __name__ == "__main__":
    asyncio.run(main())
