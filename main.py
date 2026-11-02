"""QQ 官方机器人入口：python main.py"""
import asyncio
import base64
import itertools
import json
import logging
import random
import re
import time

import aiohttp
import botpy
from botpy.http import Route
from botpy.connection import ConnectionState
from botpy.manage import GroupManageEvent
from botpy.message import C2CMessage, DirectMessage, GroupMessage, Message

from atrei import config, faces, hf
from atrei.brain import Brain
from atrei.proactive import Proactive
from atrei.tools import Context

log = logging.getLogger("atrei")

# botpy 内部的 aiohttp 默认不走系统代理（HTTPS_PROXY），开了代理/加速器时会连不上，这里统一打开
_session_init = aiohttp.ClientSession.__init__


def _session_init_trust_env(self, *args, **kwargs):
    kwargs.setdefault("trust_env", True)
    _session_init(self, *args, **kwargs)


aiohttp.ClientSession.__init__ = _session_init_trust_env


# QQ 给同一个人在私聊（user_openid）和每个群（member_openid）的编号都不一样，
# 只有 union_openid 跨场景一致。botpy 会把它丢掉，这里让消息作者对象把它留下来（没开通时为空）
def _keep_union_openid(user_cls, original_init):
    def init(self, data):
        original_init(self, data)
        self.union_openid = data.get("union_openid") or None
    user_cls.__init__ = init


for _cls in (GroupMessage, C2CMessage):
    _keep_union_openid(_cls._User, _cls._User.__init__)


# 群「全量模式」：后台打开「接收所有消息」后，群里每条消息都会推 GROUP_MESSAGE_CREATE。
# botpy 不认识这个事件，这里补上，转给 on_group_message_create
def _parse_group_message_create(self, payload):
    self._dispatch("group_message_create", GroupMessage(self.api, payload.get("id", None), payload.get("d", {})))


ConnectionState.parse_group_message_create = _parse_group_message_create

INTRO = ("大家好呀~ 我是大肥鱼，一条拟人化的 DeepSeek 鲸鱼娘🐟\n"
         "我会陪大家聊天，还会说话、唱歌、画画，最拿手的是……吃白饭！🍚\n"
         "想找我玩就 @我 一下吧~")

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


SPLIT_CHANCE = 0.3  # 群聊里有时把一句话拆成两条发
SPLIT_AT = re.compile(r"(?<=[~～，,。！!？?…])")


def split_in_two(text: str) -> list[str]:
    """在最靠近中间的标点后面断开，两半都太短就不拆。"""
    cuts = [m.start() for m in SPLIT_AT.finditer(text) if 4 <= m.start() <= len(text) - 4]
    if not cuts:
        return [text]
    cut = min(cuts, key=lambda c: abs(c - len(text) / 2))
    return [text[:cut].strip().rstrip("，,"), text[cut:].strip()]


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

    async def on_ready(self):
        log.info("「%s」上线了", self.robot.name)
        if self.http_session is None:  # 断线重连也会触发 on_ready，只初始化一次
            self.http_session = aiohttp.ClientSession()
            await self.brain.imagegen.load()
            await self.brain.load_memory()
            self.memory_task = asyncio.create_task(self.brain.memory.run_forever(self.brain.histories, self.brain.meta))
            self.proactive_task = asyncio.create_task(Proactive(self.brain, self.send_to_owner).run_forever())

    async def send_to_owner(self, text: str):
        """主动给主人发私聊（不回复任何消息，占用 QQ 的主动消息额度）。"""
        await self.api.post_c2c_message(openid=self.cfg.owner_id, msg_type=0, content=text)

    # ---------- 群里的普通聊天（全量模式） ----------
    async def on_group_message_create(self, message: GroupMessage):
        """只记下来当上下文，不回复；@ 鱼鱼的消息照旧由 on_group_at_message_create 回复。"""
        text = decode_faces(re.sub(r"<@!?\w+>", "", message.content or "")).strip()
        self.brain.log_group_message(message.group_openid, message.author.member_openid,
                                     message.author.union_openid or "", text)

    # ---------- 被拉进群 ----------
    async def on_group_add_robot(self, event: GroupManageEvent):
        """刚进群时自我介绍一次。这个事件只在真的被拉进群时才有，重启不会再触发；再记一笔防止重复。"""
        intro_done = self.brain.meta.setdefault("introduced_groups", [])
        if event.group_openid in intro_done:
            return
        try:
            await self.api.post_group_message(group_openid=event.group_openid, msg_type=0, content=INTRO,
                                              event_id=event.event_id, msg_seq=1)
            intro_done.append(event.group_openid)
            self.brain.memory.dirty = True
            log.info("在新群 %s 自我介绍了", event.group_openid)
        except Exception as e:
            log.warning("进群自我介绍失败：%s", e)

    # ---------- 收消息 ----------
    async def on_group_at_message_create(self, message: GroupMessage):
        uid = message.author.member_openid
        ctx = Context(user_id=uid, session_id=f"g:{message.group_openid}:{uid}",
                      target_kind="group", target_id=message.group_openid, is_group=True,
                      union_id=message.author.union_openid or "")
        # msg_type 默认 0（文字），发表情包时传 7（富媒体）覆盖
        await self._handle(ctx, message, lambda **kw: self.api.post_group_message(
            group_openid=message.group_openid, msg_id=message.id, **{"msg_type": 0, **kw}))

    async def on_c2c_message_create(self, message: C2CMessage):
        uid = message.author.user_openid
        ctx = Context(user_id=uid, session_id=f"u:{uid}", target_kind="c2c", target_id=uid,
                      union_id=message.author.union_openid or "")
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
        log.info("[%s] %s%s%s", ctx.session_id, text[:60], f" +{len(images)}图" if images else "",
                 "" if ctx.union_id else "（无 union_openid）")
        # 同一条消息的多次回复要用递增的 msg_seq。每条消息各用各的计数器：
        # 群里几个人同时 @ 鱼鱼时，不会互相把序号清零导致 QQ 拒收
        seq = itertools.count(1)
        reply_to = send

        async def send(**kw):
            return await reply_to(msg_seq=next(seq), **kw)

        reply = await self.brain.handle(ctx, text, images)
        if not (ctx.voice and await self._send_voice(ctx, message, send, reply)):
            reply = faces.strip_unknown(reply)
            parts = split_in_two(reply) if ctx.target_kind == "group" and random.random() < SPLIT_CHANCE else [reply]
            for i, part in enumerate(parts):
                if i:
                    await asyncio.sleep(random.uniform(0.8, 1.8))  # 像真人打完一句再打下一句
                for chunk in split_text(part):
                    await self._send_text(ctx, send, chunk)
        for sticker in ctx.stickers:
            try:
                await self._send_sticker(ctx, message, send, sticker)
                log.info("发了表情包 %s", sticker["id"])
            except Exception as e:
                log.warning("表情包 %s 发送失败：%s", sticker["id"], e)
        for prompt in ctx.drawings:
            await self._send_drawing(ctx, message, send, prompt)

    async def _send_text(self, ctx: Context, send, text: str):
        """带 [可爱] 这类 QQ 表情的回复用 Markdown 发（黄脸是行内小图），失败或频道里就去掉表情发文字。"""
        if faces.has_faces(text) and ctx.target_kind in ("group", "c2c"):
            try:
                await send(msg_type=2, markdown={"content": faces.to_markdown(text)})
                return
            except Exception as e:
                log.warning("Markdown 表情发送失败，改发文字：%s", e)
        await self._send_passive(ctx.target_id, send, faces.TOKEN.sub("", text).strip() or "……")

    async def _send_drawing(self, ctx: Context, message, send, prompt: str):
        """调 Codex 生图，再像表情包一样上传发出去；失败就说一声。"""
        try:
            png = await self.brain.imagegen.generate(prompt)
            log.info("画好了：%s", prompt[:60])
            if ctx.target_kind in ("guild", "dm"):
                await message.reply(file_image=png)
                return
            route = (Route("POST", "/v2/groups/{group_openid}/files", group_openid=ctx.target_id)
                     if ctx.target_kind == "group" else
                     Route("POST", "/v2/users/{openid}/files", openid=ctx.target_id))
            uploaded = await self.api._http.request(route, json={
                "file_type": 1, "file_data": base64.b64encode(png).decode(), "srv_send_msg": False})
            await send(msg_type=7, content=" ", media={"file_info": uploaded["file_info"]})
        except Exception as e:
            log.warning("画图失败：%s", e)
            text = ("本鱼今天已经没脸找GPT姐姐画画啦~" if "用完" in str(e)
                    else "呜…鱼鱼这次没画出来，等会儿再试试嘛")
            await self._send_passive(ctx.target_id, send, text)

    async def _send_voice(self, ctx: Context, message, send, text: str) -> bool:
        """把回复念成语音发出去；失败就返回 False，改发文字。"""
        if ctx.target_kind not in ("group", "c2c"):
            return False
        try:
            silk = await self.brain.voice.synthesize(text, sing=ctx.sing)
            route = (Route("POST", "/v2/groups/{group_openid}/files", group_openid=ctx.target_id)
                     if ctx.target_kind == "group" else
                     Route("POST", "/v2/users/{openid}/files", openid=ctx.target_id))
            uploaded = await self.api._http.request(route, json={
                "file_type": 3, "file_data": base64.b64encode(silk).decode(), "srv_send_msg": False})
            await send(msg_type=7, content=" ", media={"file_info": uploaded["file_info"]})
            log.info("发了语音：%s", text[:40])
            return True
        except Exception as e:
            log.warning("语音发送失败，改发文字：%s", e)
            return False

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
        await send(msg_type=7, content=" ", media={"file_info": uploaded["file_info"]})

    async def _send_passive(self, target_id, send, content):
        try:
            await send(content=content)
        except Exception as e:
            log.warning("发送失败：%s", e)
            if "url" in str(e).lower() or "链接" in str(e):
                await send(content=re.sub(r"https?://\S+|\b\w+\.(com|cn|net|org)\S*", "[链接已隐藏]", content))

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
