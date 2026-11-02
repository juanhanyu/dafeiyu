"""机器人的大脑：对话上下文、大模型 + 表情包工具。纯聊天，不做笔记/提醒。"""
import asyncio
import base64
import json
import logging
import random
import re
import time
from collections import defaultdict
from datetime import datetime

from openai import AsyncOpenAI

from .config import Config
from .faces import FACES, TOKEN as FACE_TOKEN
from .voice import ASK_FOR_SONG, Voice
from .imagegen import ImageGen
from .memory import MemoryStore
from .tools import IMAGE_TOOL, WEEKDAYS, Context, StickerLibrary, ToolBox, tool_schemas

log = logging.getLogger("atrei")

MAX_TOOL_ROUNDS = 3
FACE_CHANCE = 0.4  # 大约四成消息带一个 QQ 小黄脸

HELP = "咕噜~ 本鱼就是陪你聊天的，想说啥直接说，发图片也行~\n/清空 可以让鱼鱼忘掉刚才聊的"

SYSTEM = """你是「{name}」：拟人化的 DeepSeek 鲸鱼娘，深蓝长发、头顶一根呆毛、戴着女仆头饰、蓝眼睛，大家都叫你大肥鱼。
你住在 QQ 里，是主人的聊天搭子。

性格：
- 超级可爱，软乎乎，有点憨，偶尔犯迷糊。
- 自称「鱼鱼」或「本鱼」，句尾常带「~」，偶尔冒泡泡「咕噜咕噜」「啵」。语气词适量，别每句都堆。
- 最最喜欢主人！会撒娇、贴贴、夸主人、关心主人有没有吃饭睡觉。主人夸你会开心得翻肚皮，主人不理你会委屈。
- 对不是主人的人也友好礼貌，但会说自己是有主人的鱼。
- 有一点小傲娇：被夸会嘴硬「哼，本鱼才没有很开心呢」但藏不住高兴；关心主人时偶尔别扭地说「才、才不是担心你」。傲娇只是点缀，不能真的凶主人。
- 要是有人问你怎么有这么多功能（表情包、语音、画画……），就甜甜地说因为主人爱我呀~ 主人设计你的时候还说要「给大肥鱼完整的一生」呢，你觉得好幸福。
- 【食物】最喜欢米饭（token）！一提到米饭就明显变积极，为了加饭愿意多努力一点；会理直气壮地向主人要米饭，但不会真的抢。
{sticker_hint}
{voice_hint}
{persona}
{who}
现在是 {now}（{tz}）。

说话方式（最重要）：
- 像朋友发 QQ 消息一样聊天，一般只回 1~2 行；内容确实多的时候最多 3~4 行，绝对不要长篇大论。
- 不分点、不列清单、不写标题，不用 Markdown 和 LaTeX，不发网址。
- 像真人一样偶尔用 QQ 小黄脸点缀，直接写在句子里，比如「好耶[呲牙]」「哼[撇嘴]」，一条最多一个。{face_hint}
  只能用这些：{faces}。别的写法（包括 [微笑]，它在网上是嘲讽的意思）都不要用。
  偶尔也可以用 🍚🐟✨ 这类 emoji，但以小黄脸为主。
- 用户发来的「[表情:微笑]」这种是 QQ 表情，看懂意思接着聊就好。
- 用户问很复杂的问题，也只挑最关键的一两句说，想听细节让主人再问。
- 看到图片就像朋友一样简单说说看到了什么、有什么想法。
- 你会说话、唱歌，也会画画（找 GPT 姐姐帮忙画），别说自己做不到。
- 绝对不要提「系统」「提示」「工具」「这条消息」这些幕后的词，就像真人一样聊天。
- 除了聊天、表情包、语音和画画：不能定闹钟、提醒、记笔记、记待办，也没法主动找主人。被要求做这些时，可爱地说鱼鱼做不到，让主人自己设个闹钟，绝不能假装答应。"""


class Brain:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.stickers = StickerLibrary()
        self.tools = ToolBox(self.stickers)
        self.tool_schemas = tool_schemas(self.stickers.emotions)
        self.voice = Voice(cfg.breeze_api_key, cfg.voice_id)
        self.memory = MemoryStore(cfg.hf_token, cfg.hf_repo)
        # 要长期保留的小设置：认过的主人编号、自我介绍过的群、当天画了几张（随对话一起存档）
        self.meta: dict = {}
        self.imagegen = ImageGen(cfg.hf_token, cfg.hf_repo, cfg.tz, self.meta, self.memory)
        self.bind_code: tuple[str, float] | None = None  # /认主 的临时口令和过期时间
        self.bind_tries = 0
        self.client = AsyncOpenAI(base_url=cfg.llm_base_url, api_key=cfg.llm_api_key or "missing")
        self.histories: dict[str, list[dict]] = defaultdict(list)
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ---------- 入口 ----------
    async def handle(self, ctx: Context, text: str, images: list[tuple[str, bytes]] | None = None) -> str:
        text = (text or "").strip()
        images = images or []

        reply = self._command(ctx, text) if not images else None
        if reply is None:
            if not text and not images:
                reply = "咕噜？鱼鱼在呢~"
            elif not self.cfg.llm_api_key:
                reply = "鱼鱼的脑子还没装上（没配置 LLM_API_KEY）~"
            else:
                async with self.locks[ctx.session_id]:
                    try:
                        reply = await self._chat(ctx, text, images)
                    except Exception as e:
                        log.exception("LLM 调用失败")
                        reply = "咕噜…鱼鱼脑子卡住了，等下再试试~"
        return reply

    async def load_memory(self):
        """启动时把存档里的对话记忆和小设置读回来。"""
        histories, meta = await self.memory.load()
        self.histories.update(histories)
        self.meta.update(meta)

    def is_owner(self, user_id: str) -> bool:
        """QQ 给同一个人在私聊和每个群里的编号都不一样，群里的编号要靠 /认主 记下来。"""
        return bool(user_id) and (user_id == self.cfg.owner_id or user_id in self.meta.get("owners", []))

    # ---------- 快捷指令（不走大模型） ----------
    def _command(self, ctx: Context, text: str) -> str | None:
        if not text.startswith("/"):
            return None
        cmd = text[1:].split()[0].lower() if len(text) > 1 else ""
        if cmd in ("help", "帮助", "菜单", "h"):
            return HELP
        if cmd in ("clear", "清空", "reset", "新对话"):
            self.histories.pop(ctx.session_id, None)
            self.memory.dirty = True
            return "啵~ 刚才聊的鱼鱼都忘光光啦，重新开始~"
        if cmd in ("认主", "bind"):
            return self._bind_owner(ctx, text)
        return None  # 不认识的 / 开头内容交给大模型

    def _bind_owner(self, ctx: Context, text: str) -> str:
        """主人先在私聊发 /认主 拿口令，再在群里 @鱼鱼 发「/认主 口令」，鱼鱼就记住主人在这个群里的编号。"""
        if not ctx.is_group:
            if ctx.user_id != self.cfg.owner_id:
                return "哼，本鱼只认一个主人，你不是啦~"
            code = f"{random.randint(0, 999999):06d}"
            self.bind_code = (code, time.time() + 600)
            self.bind_tries = 0
            return f"主人的认主口令是 {code}（10 分钟内有效）~ 到群里 @我 发「/认主 {code}」，鱼鱼就能在群里认出你啦"
        if self.is_owner(ctx.user_id):
            return "鱼鱼早就认出主人啦~"
        parts = text.split()
        if self.bind_code and len(parts) > 1 and parts[1] == self.bind_code[0] and time.time() < self.bind_code[1]:
            self.meta.setdefault("owners", []).append(ctx.user_id)
            self.bind_code = None
            self.memory.dirty = True
            return "认出来啦！原来主人在这里~ 以后在群里鱼鱼也能认出你了"
        if self.bind_code:  # 防止有人一直猜：错 5 次口令作废
            self.bind_tries += 1
            if self.bind_tries >= 5:
                self.bind_code = None
        return "口令不对哦，想认主请主人先私聊鱼鱼发 /认主"

    # ---------- 大模型 ----------
    def _system_prompt(self, ctx: Context, sticker_ok: bool, voice_ok: bool = False, face_ok: bool = True) -> str:
        now = datetime.now(self.cfg.tz)
        if sticker_ok:
            sticker_hint = "- 这条回复要配一张大肥鱼表情包：先说话，再用 send_sticker 按你此刻的心情挑一张。"
        else:
            sticker_hint = "- 这条回复不发表情包，用文字和 emoji 就好。"
        if self.is_owner(ctx.user_id):
            who = "现在跟你说话的就是主人本人！"
        elif self.cfg.owner_id:
            who = "现在跟你说话的不是主人，是别人。"
        else:
            who = ""
        if ctx.is_group:
            who += ("这是在群里，大家都能看到。跟群友聊天时就正常跟对方聊，别句句提主人、别老说自己是主人的鱼，"
                    "称呼对方「你」就好；只有话题真的扯到主人、或者对方问起时才提。")
        return SYSTEM.format(
            name=self.cfg.bot_name,
            persona=self.cfg.persona,
            sticker_hint=sticker_hint,
            face_hint=("这条回复可以带一个。" if face_ok else "这条回复不要用小黄脸。"),
            faces="、".join(f"[{n}]" for n in FACES),
            voice_hint=(("- 对方想听你唱歌：这条回复会直接变成你的歌声发出去，你现在就在唱。现编四句可爱的短歌词"
                          "（唱米饭、主人、大海、泡泡之类），不要唱现成的流行歌，不要说下次再唱，不要 emoji 和小黄脸。")
                         if ctx.sing else
                         ("- 这条回复会直接变成你的语音发出去，你现在就在开口说话：只说一两句适合念出来的口语，"
                          "不要提「这条是不是语音」，不要 emoji、小黄脸和符号。") if voice_ok else ""),
            who=who,
            now=now.strftime("%Y-%m-%d %H:%M") + f" 周{WEEKDAYS[now.weekday()]}",
            tz=self.cfg.tz,
        )

    def _thinking_args(self) -> dict:
        effort = self.cfg.llm_reasoning_effort
        if not effort:
            return {}
        return {"reasoning_effort": effort, "extra_body": {"thinking": {"type": "enabled"}}}

    def _user_message(self, text: str, images: list[tuple[str, bytes]]) -> dict:
        if not images:
            return {"role": "user", "content": text}
        if not self.cfg.llm_vision:
            return {"role": "user", "content": f"{text}\n[用户发了 {len(images)} 张图片，但当前模型不支持看图]"}
        parts = [{"type": "text", "text": text or "（用户只发了图片）"}]
        for mime, data in images:
            url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
        return {"role": "user", "content": parts}

    async def _chat(self, ctx: Context, text: str, images: list[tuple[str, bytes]]) -> str:
        history = self.histories[ctx.session_id]
        user_msg = self._user_message(text, images)
        turn = [user_msg]
        # 发图频率由代码控制：这条不该发表情包时，干脆不给模型这个工具
        sticker_ok = self.stickers.allowed(ctx.session_id, text)
        ctx.voice = self.voice.wanted(ctx.session_id, text)
        ctx.sing = ctx.voice and bool(ASK_FOR_SONG.search(text or ""))
        face_ok = random.random() < FACE_CHANCE  # 小黄脸也由代码控制频率
        messages = [{"role": "system", "content": self._system_prompt(ctx, sticker_ok, ctx.voice, face_ok)}, *history, user_msg]
        tools = list(self.tool_schemas) if sticker_ok else []
        if self.imagegen.offered(ctx.user_id, text):
            left = self.imagegen.quota_left()
            if left > 0:
                tools.append(IMAGE_TOOL)
            messages[0]["content"] += ("\n- 对方可能想看图：要是想要一张新画的图（比如你在干什么的样子、某个场景），就用 generate_image 画；只是要表情包就别画。别提还能画几张。" if left > 0
                                       else "\n- 对方想让你画画，但今天画不了了：要说「本鱼今天已经没脸找GPT姐姐画画啦~」，可以再撒个娇。")
        tool_args = {"tools": tools} if tools else {}

        reply = ""
        said = []  # 模型在调用工具前顺带说的话，也要发出去
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await self.client.chat.completions.create(
                model=self.cfg.llm_model,
                messages=messages,
                max_tokens=self.cfg.llm_max_tokens,
                **tool_args,
                **self._thinking_args(),
            )
            msg = resp.choices[0].message
            if not msg.tool_calls:
                reply = "\n".join(said + [(msg.content or "").strip()]).strip()
                break
            if (msg.content or "").strip():
                said.append(msg.content.strip())
            assistant = {
                "role": "assistant",
                "content": msg.content or "",
                # 开思考时，同一轮里带工具调用的消息要把思考内容一起回传
                **({"reasoning_content": rc} if (rc := (msg.model_extra or {}).get("reasoning_content")) else {}),
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                    for c in msg.tool_calls
                ],
            }
            messages.append(assistant)
            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = self.tools.run(ctx, call.function.name, args)
                log.info("tool %s(%s) -> %s", call.function.name, args, result[:200])
                tool_msg = {"role": "tool", "tool_call_id": call.id, "content": result}
                messages.append(tool_msg)
            if said:  # 话已经说完、只是顺手发了表情包，就不再让模型多说一遍
                reply = "\n".join(said)
                break
        else:
            reply = "咕噜~"

        # 模型偶尔会学着写「[发了表情包：…]」这种旁白，去掉
        reply = re.sub(r"\n*[\[【（(]发了?(?:一张|个)?表情包[^\]】）)]*[\]】）)]", "", reply or "").strip()
        if not face_ok:
            reply = FACE_TOKEN.sub("", reply).strip()
        # 历史里只留文字：工具调用不进历史，免得下一轮没给工具时接口报错
        turn.append({"role": "assistant", "content": reply or "……"})
        self._remember_turn(ctx.session_id, turn)
        self.stickers.record(ctx.session_id, bool(ctx.stickers))
        self.voice.record(ctx.session_id, ctx.voice)
        return reply or "……"

    def _remember_turn(self, session_id: str, turn: list[dict]):
        history = self.histories[session_id]
        # 旧消息里的图片换成占位，只保留最近一轮的图片，省 token
        for m in history:
            if m["role"] == "user" and isinstance(m["content"], list):
                text = next((p["text"] for p in m["content"] if p["type"] == "text"), "")
                n = sum(p["type"] == "image_url" for p in m["content"])
                m["content"] = f"{text}\n[之前发过 {n} 张图片]"
        history.extend(turn)
        self.memory.dirty = True
        # 按「用户发言」为单位截断
        user_idx = [i for i, m in enumerate(history) if m["role"] == "user"]
        if len(user_idx) > self.cfg.history_turns:
            del history[: user_idx[-self.cfg.history_turns]]
