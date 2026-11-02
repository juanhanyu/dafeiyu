"""QQ 官方机器人入口：python main.py"""
import asyncio
import base64
import json
import logging
import re
import time

import aiohttp
import botpy
from botpy.http import Route
from botpy.message import C2CMessage, DirectMessage, GroupMessage, Message

from atrei import config, hf
from atrei.brain import Brain
from atrei.tools import Context

log = logging.getLogger("atrei")

# botpy 内部的 aiohttp 默认不走系统代理（HTTPS_PROXY），开了代理/加速器时会连不上，这里统一打开
_session_init = aiohttp.ClientSession.__init__


def _session_init_trust_env(self, *args, **kwargs):
    kwargs.setdefault("trust_env", True)
    _session_init(self, *args, **kwargs)


aiohttp.ClientSession.__init__ = _session_init_trust_env

CHUNK = 1500          # 单条消息最大字数，超出分条发


FACE_TAG = re.compile(r'<faceType=\d+,faceId="?(\d+)"?(?:,ext="?([A-Za-z0-9+/=_-]*)"?)?>')


def decode_faces(text: str) -> str:
    """用户发的 QQ 表情会变成 <faceType=1,faceId="14",ext="base64(JSON)">，翻译成 [表情:微笑] 给模型看。"""
    def repl(m):
        name = ""
        if m.group(2):
            try:
                raw = m.group(2) + "=" * (-len(m.group(2)) % 4)
                name = json.loads(base64.b64decode(raw)).get("text", "").strip("/[]")
            except Exception:
                pass
        return f"[表情:{name}]" if name else "[表情]"
    return FACE_TAG.sub(repl, text)


def split_text(text: str, size: int = CHUNK) -> list[str]:
    chunks = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks + [text] if text else chunks


class AtreiClient(botpy.Client):
    def __init__(self, cfg, brain: Brain, **kw):
        super().__init__(**kw)
        self.cfg = cfg
        self.brain = brain
        self.http_session: aiohttp.ClientSession | None = None
        # target_id -> [最近一条用户消息 id, 时间, 已用 msg_seq]，同一条消息的多次回复要递增 msg_seq
        self.last_msg: dict[str, list] = {}

    async def on_ready(self):
        log.info("「%s」上线了", self.robot.name)
        if self.http_session is None:  # 断线重连也会触发 on_ready，只初始化一次
            self.http_session = aiohttp.ClientSession()

    # ---------- 收消息 ----------
    async def on_group_at_message_create(self, message: GroupMessage):
        uid = message.author.member_openid
        ctx = Context(user_id=uid, session_id=f"g:{message.group_openid}:{uid}",
                      target_kind="group", target_id=message.group_openid, is_group=True)
        # msg_type 默认 0（文字），发表情包时传 7（富媒体）覆盖
        await self._handle(ctx, message, lambda **kw: self.api.post_group_message(
            group_openid=message.group_openid, msg_id=message.id, **{"msg_type": 0, **kw}))

    async def on_c2c_message_create(self, message: C2CMessage):
        uid = message.author.user_openid
        ctx = Context(user_id=uid, session_id=f"u:{uid}", target_kind="c2c", target_id=uid)
        await self._handle(ctx, message, lambda **kw: self.api.post_c2c_message(
            openid=uid, msg_id=message.id, **{"msg_type": 0, **kw}))

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
        raw = re.sub(r"<@!?\d+>", "", message.content or "").strip()
        if "<faceType" in raw:  # 记下 QQ 表情的原始格式，方便研究怎么发
            log.info("收到 QQ 表情原文：%s", raw[:300])
        text = decode_faces(raw)
        images = await self._download_images(message.attachments)
        log.info("[%s] %s%s", ctx.session_id, text[:60], f" +{len(images)}图" if images else "")
        self.last_msg[ctx.target_id] = [message.id, time.time(), 0]

        if raw.startswith("/表情测试"):
            await self._face_test(ctx, raw, send)
            return

        reply = await self.brain.handle(ctx, text, images)
        for chunk in split_text(reply):
            await self._send_passive(ctx.target_id, send, chunk)
        for sticker in ctx.stickers:
            try:
                await self._send_sticker(ctx, message, send, sticker)
                log.info("发了表情包 %s", sticker["id"])
            except Exception as e:
                log.warning("表情包 %s 发送失败：%s", sticker["id"], e)

    async def _face_test(self, ctx: Context, raw: str, send):
        """实验第二轮：普通文字发不了 QQ 表情，试 Markdown 消息（msg_type=2），把黄脸图片当行内小图嵌进去。"""
        face = "https://koishi.js.org/QFace/assets/qq_emoji/14"
        variants = [
            ("F", f"鱼鱼笑一个 ![微笑 #24px #24px]({face}/png/14.png) 这是F"),
            ("G", f"动起来的 ![微笑 #24px #24px]({face}/apng/14.png) 这是G"),
            ("H", "Markdown 里的 <emoji:14> 这是H"),
        ]
        state = self.last_msg[ctx.target_id]
        for label, content in variants:
            state[2] += 1
            try:
                await send(msg_type=2, markdown={"content": content}, msg_seq=state[2])
            except Exception as e:
                log.warning("表情测试 %s 失败：%s", label, e)
                state[2] += 1
                await send(content=f"{label} 发送失败：{str(e)[:80]}", msg_seq=state[2])
        log.info("表情测试已发出 %s", [v[0] for v in variants])

    async def _send_sticker(self, ctx: Context, message, send, sticker: dict):
        url = sticker["url"]
        if ctx.target_kind in ("guild", "dm"):
            await message.reply(image=url)
            return
        # 群/私聊：先把图片上传成富媒体，再发 msg_type=7。先让 QQ 按网址自己拉图，拉不到再下载后上传
        route = (Route("POST", "/v2/groups/{group_openid}/files", group_openid=ctx.target_id)
                 if ctx.target_kind == "group" else
                 Route("POST", "/v2/users/{openid}/files", openid=ctx.target_id))
        try:
            uploaded = await self.api._http.request(route, json={"file_type": 1, "url": url, "srv_send_msg": False})
        except Exception as e:
            log.info("QQ 按网址拉图失败（%s），改为下载后上传", e)
            async with self.http_session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                data = await r.read()
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

async def main():
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [atrei] %(message)s", "%H:%M:%S"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    cfg = config.load()
    if not cfg.qq_appid or not cfg.qq_secret:
        raise SystemExit("请先在 .env 里填 QQ_APPID 和 QQ_SECRET（见 README）")
    brain = Brain(cfg)
    intents = botpy.Intents(public_messages=True, public_guild_messages=True, direct_message=True)
    client = AtreiClient(cfg, brain, intents=intents, is_sandbox=cfg.qq_sandbox)
    if hf.HOSTED:  # 在 Render / HF Space 上：开网页、自我保活
        await hf.start_web(cfg.bot_name, lambda: client.http_session is not None)
        keepalive = asyncio.create_task(hf.keepalive_loop())
    async with client:
        await client.start(appid=cfg.qq_appid, secret=cfg.qq_secret)


if __name__ == "__main__":
    asyncio.run(main())
