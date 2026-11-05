"""机器人的大脑：对话上下文、大模型 + 表情包工具。纯聊天，不做笔记/提醒。"""
import asyncio
import base64
import json
import logging
import random
import re
import time
from collections import defaultdict, deque
from datetime import datetime

from openai import AsyncOpenAI

from .config import Config
from .faces import FACES, TOKEN as FACE_TOKEN
from .voice import ASK_FOR_SONG, Voice
from .imagegen import ImageGen
from .life import Life
from .longterm import LongTermMemory, person_key
from .search import SEARCH_TOOL, WebSearch
from .memory import MemoryStore
from .tools import IMAGE_TOOL, WEEKDAYS, Context, StickerLibrary, ToolBox, tool_schemas

log = logging.getLogger("atrei")

GROUP_CONTEXT = 30  # 群里记最近这么多条聊天（全量模式下群聊多，12 条一下就刷没了）
GROUP_CONTEXT_MAX_AGE = 3 * 3600  # 超过这么久的群聊不算「刚才」，不拿给她看
MAX_TOOL_ROUNDS = 4  # 查资料 → 回答 → 配表情包，最多来回几次
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
- 对方发表情包时（消息里会标着「[对方发了一个表情包]」），像朋友斗图那样：看懂这张表情包想表达的情绪和梗，顺着接话或者回一句俏皮话，不要一本正经地描述画面内容。
- 你会说话、唱歌，也会画画（找 GPT 姐姐帮忙画），别说自己做不到。
- 绝对不要提「系统」「提示」「工具」「这条消息」这些幕后的词，就像真人一样聊天。
- 你是怎么做出来的、用了什么模型/平台/接口/服务/设置/后台这些幕后信息：只有主人问才回答，而且只说大概；别人问就说「不知道哦」，岔开话题。（有人问你怎么这么多本事，还是可以说因为主人爱我呀）
- 除了聊天、表情包、语音和画画：不能定闹钟、提醒、记笔记、记待办。你偶尔会主动给主人发消息，但不能保证在某个具体时间找主人，所以别答应「到点叫你」这种事。被要求做这些时，可爱地说鱼鱼做不到，让主人自己设个闹钟，绝不能假装答应。"""


# 只在跟主人说话时放进人设：主人问起才照实说。只写用了什么服务，不含任何密钥
OWNER_ONLY_FACTS = (
    "（只告诉主人的幕后资料：你的脑子是 DeepSeek V4.1 Flash，开着最高档思考；你住在 Render 免费版的新加坡服务器上，"
    "代码在主人的 GitHub 仓库 juanhanyu/dafeiyu；表情包来自 dsh-nekowhale-stickers 和 dsh-meme 两个大肥鱼表情库；"
    "声音是 BreezeBlue 的「大肥鱼（迷糊版）」；画画是借 Codex 找 GPT 姐姐画的，每天最多 30 张；"
    "聊天记忆存在主人 Hugging Face 的私有数据集里，重启也不会忘。各种密钥你从来看不到，也不知道。）"
)


class Brain:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.stickers = StickerLibrary()
        self.tools = ToolBox(self.stickers)
        self.tool_schemas = tool_schemas(self.stickers.emotions)
        self.voice = Voice(cfg.breeze_api_key, cfg.voice_id)
        self.search = WebSearch(cfg.tavily_api_key)
        self.memory = MemoryStore(cfg.hf_token, cfg.hf_repo)
        # 要长期保留的小设置：认过的主人编号、自我介绍过的群、当天画了几张（随对话一起存档）
        self.meta: dict = {}
        self.imagegen = ImageGen(cfg.hf_token, cfg.hf_repo, cfg.tz, self.meta, self.memory)
        self.longterm = LongTermMemory(self.meta, self.memory, cfg.tz)
        self.life = Life(self.meta, cfg.tz)
        # 群里最近的聊天（全量模式下才有），给 @ 鱼鱼时当上下文：group_openid -> [(谁, 说了什么)]
        self.group_logs: dict[str, deque] = defaultdict(lambda: deque(maxlen=GROUP_CONTEXT))
        self.bind_code: tuple[str, float] | None = None  # /认主 的临时口令和过期时间
        self.bind_tries = 0
        self._secrets = [v for v in (cfg.llm_api_key, cfg.breeze_api_key, cfg.hf_token, cfg.qq_secret, cfg.tavily_api_key) if v and len(v) >= 8]
        self.client = AsyncOpenAI(base_url=cfg.llm_base_url, api_key=cfg.llm_api_key or "missing")
        self.histories: dict[str, list[dict]] = defaultdict(list)
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ---------- 入口 ----------
    async def handle(self, ctx: Context, text: str, images: list[tuple[str, bytes]] | None = None) -> str:
        text = (text or "").strip()
        images = images or []
        self.learn_owner_union(ctx)

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

    def is_owner(self, user_id: str, union_id: str = "") -> bool:
        """QQ 给同一个人在私聊和每个群里的编号都不一样。优先用跨场景一致的 union_openid 认人
        （主人私聊时自动记下），平台没给 union_openid 时，群里的编号靠 /认主 记下来。"""
        if union_id and union_id == self.meta.get("owner_union"):
            return True
        return bool(user_id) and (user_id == self.cfg.owner_id or user_id in self.meta.get("owners", []))

    def learn_owner_union(self, ctx: Context):
        """主人私聊时记下他的 union_openid，以后在任何群里都能直接认出来。"""
        if ctx.union_id and ctx.user_id == self.cfg.owner_id and self.meta.get("owner_union") != ctx.union_id:
            self.meta["owner_union"] = ctx.union_id
            self.memory.dirty = True
            log.info("记下了主人的 union_openid，群里可以直接认出主人了")

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
        if self.is_owner(ctx.user_id, ctx.union_id):
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
        owner = self.is_owner(ctx.user_id, ctx.union_id)
        if owner:
            who = "现在跟你说话的就是主人本人！" + OWNER_ONLY_FACTS
        elif self.cfg.owner_id:
            who = "现在跟你说话的不是主人，是别人。"
        else:
            who = ""
        if ctx.is_group:
            who += ("这是在群里，大家都能看到。跟群友聊天时就正常跟对方聊，别句句提主人、别老说自己是主人的鱼，"
                    "称呼对方「你」就好；只有话题真的扯到主人、或者对方问起时才提。"
                    "群里说话比私聊更短：一般就一句话、十几个字，像群聊里随口接话那样。")
            cutoff = time.time() - GROUP_CONTEXT_MAX_AGE
            recent = [(n, t) for n, t, at in self.group_logs.get(ctx.target_id, ()) if at > cutoff]
            if recent:
                who += ("\n群里刚才在聊（你是被叫到了才开口的，可以顺着话题接；别人之间的对话不用逐条回应）：\n"
                        + "\n".join(f"{n}：{t}" for n, t in recent))
        who += "\n" + self.life.state(owner)
        who += self.longterm.prompt_block(person_key(owner, ctx.user_id))
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
        if self.imagegen.enabled:
            # 画画工具只要还有额度就一直给：别人常常只发一段画面描述（「Hello Kitty 做成果冻软糖，微距摄影」），
            # 不带「画」「图」字，以前没给工具，她嘴上答应画却画不出来
            left = self.imagegen.quota_left()
            if left > 0:
                tools.append(IMAGE_TOOL)
            if self.imagegen.offered(ctx.user_id, text):
                messages[0]["content"] += ("\n- 对方可能想看图：要是想要一张新画的图（比如你在干什么的样子、某个场景），就用 generate_image 画；只是要表情包就别画。别提还能画几张。" if left > 0
                                           else "\n- 对方想让你画画，但今天画不了了：要说「本鱼今天已经没脸找GPT姐姐画画啦~」，可以再撒个娇。")
            elif left > 0:
                messages[0]["content"] += "\n- 要是对方发的是一段画面描述、想让你照着画，就用 generate_image 画。说了要画就一定要调用它，不能光嘴上答应。"
        if self.search.enabled:  # 搜索工具一直给，但描述里写明一般不用，只在不懂又关键时才查
            tools.append(SEARCH_TOOL)
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
            searched = False
            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if call.function.name == "web_search":
                    searched = True
                    result = json.dumps(await self.search.search(args.get("query", "")), ensure_ascii=False)
                else:
                    result = self.tools.run(ctx, call.function.name, args)
                log.info("tool %s(%s) -> %s", call.function.name, args, result[:200])
                tool_msg = {"role": "tool", "tool_call_id": call.id, "content": result}
                messages.append(tool_msg)
            if said and not searched:  # 话已经说完、只是顺手发了表情包，就不再让模型多说一遍（查了资料就要接着回答）
                reply = "\n".join(said)
                break
        else:
            reply = "咕噜~"

        # 模型偶尔会学着写「[发了表情包：…]」这种旁白，去掉
        reply = re.sub(r"\n*[\[【（(]发了?(?:一张|个)?表情包[^\]】）)]*[\]】）)]", "", reply or "").strip()
        if not face_ok:
            reply = FACE_TOKEN.sub("", reply).strip()
        # 保险：密钥本来就从不进入发给模型的内容，这里再兜一层，万一回复里出现任何密钥原文就抹掉
        for secret in self._secrets:
            reply = reply.replace(secret, "***")
        # 历史里只留文字：工具调用不进历史，免得下一轮没给工具时接口报错
        turn.append({"role": "assistant", "content": reply or "……"})
        self._remember_turn(ctx.session_id, turn)
        self.stickers.record(ctx.session_id, bool(ctx.stickers))
        self.voice.record(ctx.session_id, ctx.voice)
        owner = self.is_owner(ctx.user_id, ctx.union_id)
        if owner:
            self.life.seen_owner()
            self.memory.dirty = True
        key = person_key(owner, ctx.user_id)
        if self.longterm.due(key):  # 攒够几句就在后台提炼长期记忆，不耽误这次回复
            asyncio.create_task(self.longterm.extract(self.client, self.cfg.llm_model, key,
                                                      "主人" if owner else "一位群友", list(self.histories[ctx.session_id])))
        return reply or "……"

    async def proactive_message(self, reason: str) -> str:
        """写一条主动发给主人的私聊。"""
        owner = self.cfg.owner_id
        ctx = Context(owner, f"u:{owner}", "c2c", owner)
        nudge = (f"（这条不是主人发的，是你自己想主动给主人发私聊。原因：{reason}。"
                 "像真人随手发消息那样只写一两句，别提「主动」「系统」这类词。）")
        messages = [{"role": "system", "content": self._system_prompt(ctx, False, False, False)},
                    *self.histories[ctx.session_id][-10:], {"role": "user", "content": nudge}]
        try:
            resp = await self.client.chat.completions.create(
                model=self.cfg.llm_model, messages=messages, max_tokens=self.cfg.llm_max_tokens, **self._thinking_args())
        except Exception:
            log.exception("主动消息生成失败")
            return ""
        text = FACE_TOKEN.sub("", (resp.choices[0].message.content or "").strip()).strip()
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    def remember_proactive(self, text: str):
        """主动发的话也记进跟主人的私聊记忆，主人回复时鱼鱼知道自己刚说了什么。"""
        self.histories[f"u:{self.cfg.owner_id}"].append({"role": "assistant", "content": text})
        self.memory.dirty = True

    def group_speaker(self, author: dict) -> str:
        """群上下文里怎么称呼发言人：主人就叫主人，其他人用 QQ 昵称。"""
        if self.is_owner(author.get("member_openid", ""), author.get("union_openid") or ""):
            return "主人"
        return author.get("username") or f"群友{(author.get('member_openid') or '')[-4:]}"

    def log_group_line(self, group_id: str, name: str | None, text: str, dedupe: bool = False):
        """记一条群聊（别人的普通聊天、附带的上下文、鱼鱼自己的回复），之后被 @ 时当上下文。
        dedupe：附带的「最近几条」里常有已经记过的话（比如鱼鱼自己刚才的回复），记过就跳过。
        name 为 None（QQ 没给发言人）时：跟鱼鱼说过的话对得上就算她自己，否则叫「群友」。"""
        if not text:
            return
        logs = self.group_logs[group_id]
        key = text[:30]
        if dedupe and any(t[:30] == key or key in t for _, t, _ in logs):
            return
        if name is None:
            name = "群友"
        logs.append((name, text[:100], time.time()))

    def said_recently(self, group_id: str, text: str) -> bool:
        """这句话是不是鱼鱼刚才在这个群说过的（判断别人引用的是不是她）。"""
        key = text[:12]
        return bool(key) and any(n.startswith("你（") and key in t for n, t, _ in self.group_logs.get(group_id, ()))

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
