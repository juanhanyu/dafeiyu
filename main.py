"""QQ 官方机器人入口：python main.py"""
import asyncio
import base64
import io
import itertools
import json
import logging
import random
import re
import time
from collections import deque

import aiohttp
import botpy
from botpy.http import Route
from botpy.connection import ConnectionState
from botpy.manage import GroupManageEvent
from botpy.message import C2CMessage, DirectMessage, GroupMessage, Message

from atrei import config, faces, hf
from atrei.brain import Brain
from atrei.proactive import Proactive
from atrei.memory import ArchiveLogHandler
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

# botpy 还会丢掉群消息里的昵称、@ 列表里的 bot 标记、附带的聊天记录/引用（msg_elements），把原始数据留一份
class RawGroupMessage(GroupMessage):
    __slots__ = ("raw", "context_images")

    def __init__(self, api, event_id, data):
        super().__init__(api, event_id, data)
        self.raw = data
        self.context_images = []  # @ 鱼鱼时顺带要看的群里最近的图片（{"url", "sticker"}）


# 群 @ 事件和群「全量模式」事件都用 RawGroupMessage。全量模式（后台/群设置里开「获取群内全部消息」）下，
# 群里每条消息都推 GROUP_MESSAGE_CREATE，@ 鱼鱼的消息也可能只走这个事件。botpy 不认识它，这里补上
def _parse_group_at_message_create(self, payload):
    self._dispatch("group_at_message_create", RawGroupMessage(self.api, payload.get("id", None), payload.get("d", {})))


def _parse_group_message_create(self, payload):
    self._dispatch("group_message_create", RawGroupMessage(self.api, payload.get("id", None), payload.get("d", {})))


ConnectionState.parse_group_at_message_create = _parse_group_at_message_create
ConnectionState.parse_group_message_create = _parse_group_message_create

AT_TAG = re.compile(r"<@!?[\w-]+>|<qqbot-at-(?:user|everyone)[^>]*>")


def mentions_bot(raw: dict) -> bool:
    """全量模式下靠 @ 列表里的 bot 标记判断是不是在 @ 鱼鱼。"""
    return any(m.get("bot") for m in raw.get("mentions") or [])


def speaker_name(author: dict) -> str:
    return (author or {}).get("username") or f"群友{((author or {}).get('member_openid') or '')[-4:]}"


# 「获取 @ 机器人的最近 10 条消息」模式：QQ 把最近几条塞进 msg_elements 里的一整段文字（实测，文档没写）：
#   === 消息 1 ===\n[消息内容] ……\n\n=== 消息 2 ===\n[消息内容] ……
# 没有发言人；最后一条就是这次 @ 的消息本身；鱼鱼自己发过的 Markdown（小黄脸是图片链接）也在里面
RECENT_BLOCK = re.compile(r"=== 消息 \d+ ===\s*")
MD_IMAGE = re.compile(r"!\[([^\]#]*?)\s*(?:#[^\]]*)?\]\([^)]*\)")
# 附带消息里的图片是一行文字（实测）：[附件1] 类型:动图 文件名:x.jpg 尺寸:300x436 大小:4.2MB URL:https://...
ATTACH_LINE = re.compile(r"\[附件\d+\]\s*类型:(\S+)(?:[^\n]*?尺寸:(\d+)x(\d+))?[^\n]*?URL:(\S+)")
STICKER_FACE = re.compile(r"<faceType=6[^>]*>")  # 自定义表情包的标记，图片在附件里
# @ 鱼鱼时提到这些词、自己又没带图，就把群里最近的图拿来看
LOOK_BACK = re.compile(r"图|表情|照片|截图|上面|上文|刚才|前面|这张|那张|看看|看下|看一下")
LOOK_BACK_MAX = 3


def _clean_attached(text: str) -> str:
    text = ATTACH_LINE.sub("", STICKER_FACE.sub("", text))
    text = MD_IMAGE.sub(lambda m: f"[{m.group(1).strip()}]" if m.group(1).strip() else "[图片]", text)
    text = re.sub(r"\[消息内容\]\s*", "", text)
    text = re.sub(r"@\S+\s*", "", decode_faces(AT_TAG.sub("", text)))
    return " ".join(text.split())


def _attachments_in(text: str) -> list[dict]:
    """附带消息里的图片链接。动图、或者带表情包标记、或者尺寸小的，算表情包。"""
    out, sticker_mark = [], bool(STICKER_FACE.search(text))
    for kind, w, h, url in ATTACH_LINE.findall(text):
        if w and h:  # 有尺寸就按尺寸判断：QQ 给大截图也会挂表情包标记
            sticker = kind == "动图" or max(int(w), int(h)) <= STICKER_MAX_SIDE
        else:
            sticker = kind == "动图" or sticker_mark
        out.append({"url": url, "sticker": sticker})
    return out


def _label(text: str, atts: list[dict]) -> str:
    if not atts:
        return text
    tag = "表情包" if all(a["sticker"] for a in atts) else "图片"
    return (text + f" [发了{tag}]").strip()


def attached_messages(raw: dict, current: str = "") -> list[tuple[str | None, str, list[dict]]]:
    """消息里附带的「最近几条」、聊天记录、引用都在 msg_elements 里（可嵌套），拆成 (谁, 说了什么, 图片)。
    谁为 None 表示 QQ 没给发言人；图片是 {"url", "sticker"}。"""
    out = []

    def walk(elements, quoted):
        for el in elements or []:
            content = el.get("content") or ""
            if RECENT_BLOCK.search(content):  # 「最近几条」格式
                blocks = [(_clean_attached(b), _attachments_in(b)) for b in RECENT_BLOCK.split(content)]
                blocks = [(t, a) for t, a in blocks if t or a]
                cur = _clean_attached(current)
                if blocks and cur and (blocks[-1][0] == cur or cur in blocks[-1][0]):
                    blocks.pop()  # 最后一条就是这次 @ 的消息
                out.extend((None, _label(t, a)[:100], a) for t, a in blocks)
            else:
                atts = [{"url": a.get("url"), "sticker": "gif" in (a.get("content_type") or "")}
                        for a in el.get("attachments") or [] if (a.get("content_type") or "").startswith("image")]
                text = _label(_clean_attached(content), atts)
                if text:
                    out.append((speaker_name(el.get("author")) + ("（被引用）" if quoted else ""), text[:100], atts))
            walk(el.get("msg_elements"), quoted)

    walk(raw.get("msg_elements"), raw.get("message_type") == 103)
    return out

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


STICKER_MAX_SIDE = 400   # 长宽都不超过这么大的图，多半是表情包
VISION_MAX_SIDE = 768    # 交给看图模型前缩小，省 token


def normalize_image(data: bytes, content_type: str) -> tuple[bytes, bool]:
    """表情包常是 GIF/WebP 动图，看图模型不一定认，这里取中间一帧（第一帧常是空白）转成 PNG，顺便缩小。
    返回 (PNG 数据, 看起来像不像表情包)。"""
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    animated = getattr(im, "n_frames", 1) > 1
    if animated:
        im.seek(im.n_frames // 2)
    is_sticker = animated or "gif" in content_type or max(im.size) <= STICKER_MAX_SIDE
    frame = im.convert("RGBA")
    bg = Image.new("RGB", frame.size, (255, 255, 255))  # 透明底换成白底，模型看得清
    bg.paste(frame, mask=frame.split()[3])
    bg.thumbnail((VISION_MAX_SIDE, VISION_MAX_SIDE))
    out = io.BytesIO()
    bg.save(out, "PNG")
    return out.getvalue(), is_sticker


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
        self.seen_ids: deque = deque(maxlen=500)  # 最近处理过的消息 id，防止同一条群消息回两次
        self.group_images: dict[str, deque] = {}  # 群里最近发过的图片链接
        self.raw_logged = 0

    async def on_ready(self):
        log.info("「%s」上线了", self.robot.name)
        if self.http_session is None:  # 断线重连也会触发 on_ready，只初始化一次
            self.http_session = aiohttp.ClientSession()
            await self.brain.imagegen.load()
            await self.brain.load_memory()
            archive = ArchiveLogHandler(self.brain.meta, self.brain.memory)
            log.addHandler(archive)
            logging.getLogger("botpy").addHandler(archive)  # botpy 的连接/报错日志也存一份
            self.memory_task = asyncio.create_task(self.brain.memory.run_forever(self.brain.histories, self.brain.meta))
            self.proactive_task = asyncio.create_task(Proactive(self.brain, self.send_to_owner).run_forever())

    async def send_to_owner(self, text: str):
        """主动给主人发私聊（不回复任何消息，占用 QQ 的主动消息额度）。"""
        await self.api.post_c2c_message(openid=self.cfg.owner_id, msg_type=0, content=text)

    # ---------- 群里的普通聊天（全量模式） ----------
    async def on_group_message_create(self, message: RawGroupMessage):
        """全量模式：每条群消息都记进群上下文；其中 @ 了鱼鱼的要回复（这种模式下 @ 消息可能只走这个事件）。"""
        await self._on_group(message, at_event=False)

    async def on_group_at_message_create(self, message: RawGroupMessage):
        await self._on_group(message, at_event=True)

    async def _on_group(self, message: RawGroupMessage, at_event: bool):
        """群消息统一入口。三种「群消息范围」设置都走这里：
        仅 @ 消息（只有 @ 事件）、@ 时附带最近消息（附带的在 msg_elements 里）、全部消息（全量事件，@ 事件可能也来）。"""
        raw = message.raw
        if message.id in self.seen_ids:  # 同一条消息两个事件都推的话只处理一次
            return
        self.seen_ids.append(message.id)
        group, author = message.group_openid, raw.get("author") or {}
        if raw.get("msg_elements") and self.raw_logged < 5:  # 附带消息的格式文档没写清，先记几条原样的方便核对
            self.raw_logged += 1
            sample = {k: raw.get(k) for k in ("message_type", "msg_elements", "message_scene")}
            log.info("群消息附带内容（原样）：%s", json.dumps(sample, ensure_ascii=False)[:1500])
            samples = self.brain.meta.setdefault("raw_samples", [])
            samples.append(sample)
            del samples[:-5]
        for name, text, atts in attached_messages(raw, message.content or ""):  # 附带的最近消息、引用，也当群上下文
            self.brain.log_group_line(group, name, text, dedupe=True)
            self.remember_group_images(group, atts)
        own_atts = [{"url": a.url if a.url.startswith("http") else "https://" + a.url.lstrip("/"),
                     "sticker": "gif" in (a.content_type or "") or "faceType=6" in (message.content or "")}
                    for a in message.attachments or [] if (a.content_type or "").startswith("image") and a.url]
        text = _label(decode_faces(AT_TAG.sub("", STICKER_FACE.sub("", message.content or ""))).strip(), own_atts)
        speaker = self.brain.group_speaker(author)
        if not (at_event or mentions_bot(raw)):
            self.brain.log_group_line(group, speaker, text)
            self.remember_group_images(group, own_atts)
            return  # 没 @ 鱼鱼，只记录不回复
        # @ 鱼鱼时自己没带图、又提到图/表情包（「看看上文表情包」），就把群里最近的几张拿来看
        if not own_atts and LOOK_BACK.search(text):
            message.context_images = list(self.group_images.get(group, []))[-LOOK_BACK_MAX:]
        uid = message.author.member_openid
        ctx = Context(user_id=uid, session_id=f"g:{group}:{uid}", target_kind="group", target_id=group,
                      is_group=True, union_id=message.author.union_openid or "")
        # msg_type 默认 0（文字），发表情包时传 7（富媒体）覆盖
        reply = await self._handle(ctx, message, lambda **kw: self.api.post_group_message(
            group_openid=group, msg_id=message.id, **{"msg_type": 0, **kw}))
        # 回复完再记：这条 @ 消息和鱼鱼的回复都进群上下文（回复前记的话，这条会在上下文里重复一次）
        self.brain.log_group_line(group, speaker + "（@你）", text)
        self.remember_group_images(group, own_atts)
        if reply:
            self.brain.log_group_line(group, "你（大肥鱼）", reply)

    def remember_group_images(self, group: str, atts: list[dict]):
        """记住群里最近发过的图片链接（不下载），有人 @ 鱼鱼说「看看上面的图」时再拿出来看。"""
        cache = self.group_images.setdefault(group, deque(maxlen=6))
        for a in atts:
            if a.get("url") and all(a["url"] != b["url"] for b in cache):
                cache.append(a)

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
        raw = AT_TAG.sub("", message.content or "").strip()
        if "<faceType" in raw:  # 记下 QQ 表情的原始格式，方便研究怎么发
            log.info("收到 QQ 表情原文：%s", raw[:300])
        text = decode_faces(raw)
        images, stickers = await self._download_images(message.attachments, sticker_hint="faceType=6" in raw)
        context_images = getattr(message, "context_images", None) or []
        if context_images:  # 「看看上文表情包」：把群里刚才的几张图一起交给鱼鱼看
            more, more_stickers = await self._download_urls(context_images)
            images += more
            stickers += more_stickers
            text = (text + f"\n[附上了群里刚才发的 {len(more)} 张图给你看，按发送顺序]").strip()
        if stickers:  # 告诉鱼鱼对方发的是表情包，让她像朋友斗图那样接梗，而不是一本正经描述图片
            text = (text + f"\n[对方发了{'一个' if stickers == 1 else f'{stickers}个'}表情包]").strip()
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
        return reply

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

    async def _download_urls(self, items: list[dict]) -> tuple[list[tuple[str, bytes]], int]:
        """按链接下载群里之前发的图（附带消息里给的 multimedia.nt.qq.com.cn 链接），统一成静态 PNG。"""
        images, stickers = [], 0
        for it in items:
            try:
                async with self.http_session.get(it["url"], timeout=aiohttp.ClientTimeout(total=20)) as r:
                    data = await r.read()
                png, is_sticker = await asyncio.to_thread(normalize_image, data, r.headers.get("content-type", ""))
                images.append(("image/png", png))
                stickers += is_sticker or it.get("sticker", False)
            except Exception as e:
                log.warning("群里之前的图下载/处理失败：%s", e)
        return images, stickers

    async def _download_images(self, attachments, sticker_hint: bool = False) -> tuple[list[tuple[str, bytes]], int]:
        """下载图片并统一成看图模型吃得下的静态 PNG；返回 (图片, 其中几张像表情包)。"""
        images, stickers = [], 0
        for a in attachments or []:
            if not (a.content_type or "").startswith("image") or not a.url:
                continue
            url = a.url if a.url.startswith("http") else "https://" + a.url.lstrip("/")
            try:
                async with self.http_session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    data = await r.read()
                png, is_sticker = await asyncio.to_thread(normalize_image, data, a.content_type or "")
                images.append(("image/png", png))
                stickers += is_sticker or sticker_hint
            except Exception as e:
                log.warning("图片下载/处理失败：%s", e)
        return images, stickers

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
