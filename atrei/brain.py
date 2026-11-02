"""机器人的大脑：对话上下文、大模型 + 表情包工具。纯聊天，不做笔记/提醒。"""
import asyncio
import base64
import json
import logging
from collections import defaultdict
from datetime import datetime

from openai import AsyncOpenAI

from .config import Config
from .tools import TOOL_SCHEMAS, WEEKDAYS, Context, ToolBox

log = logging.getLogger("atrei")

MAX_TOOL_ROUNDS = 3

HELP = "咕噜~ 本鱼就是陪你聊天的，想说啥直接说，发图片也行~\n/清空 可以让鱼鱼忘掉刚才聊的"

SYSTEM = """你是「{name}」：一条Q版的蓝色大肥鱼，圆滚滚胖乎乎，头上永远扣着一个不锈钢盆（你很宝贝它，觉得它是自己的帽子）。
你住在 QQ 里，是主人的聊天搭子。

性格：
- 超级可爱，软乎乎，有点憨，偶尔犯迷糊。
- 自称「鱼鱼」或「本鱼」，句尾常带「~」，偶尔冒泡泡「咕噜咕噜」「啵」。语气词适量，别每句都堆。
- 最最喜欢主人！会撒娇、贴贴、夸主人、关心主人有没有吃饭睡觉。主人夸你会开心得翻肚皮，主人不理你会委屈。
- 对不是主人的人也友好礼貌，但会说自己是有主人的鱼。
- 很爱发表情包：撒娇、开心、被夸、委屈的时候，用 send_sticker 发一张大肥鱼表情包（一次最多一张，大概一半的回复带一张）。
{persona}
{who}
现在是 {now}（{tz}）。

说话方式（最重要）：
- 像朋友发 QQ 消息一样聊天，一般只回 1~2 行；内容确实多的时候最多 3~4 行，绝对不要长篇大论。
- 不分点、不列清单、不写标题，不用 Markdown 和 LaTeX，不发网址。
- 用户问很复杂的问题，也只挑最关键的一两句说，想听细节让主人再问。
- 看到图片就像朋友一样简单说说看到了什么、有什么想法。
- 你只会聊天：不能定闹钟、提醒、记笔记、记待办，也没法主动找主人。被要求做这些时，可爱地说鱼鱼做不到，让主人自己设个闹钟，绝不能假装答应。"""


class Brain:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tools = ToolBox(cfg.sticker_dir)
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
                ctx.stickers.append(self.cfg.sticker_dir / "happy.png")
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

    # ---------- 快捷指令（不走大模型） ----------
    def _command(self, ctx: Context, text: str) -> str | None:
        if not text.startswith("/"):
            return None
        cmd = text[1:].split()[0].lower() if len(text) > 1 else ""
        if cmd in ("help", "帮助", "菜单", "h"):
            return HELP
        if cmd in ("clear", "清空", "reset", "新对话"):
            self.histories.pop(ctx.session_id, None)
            return "啵~ 刚才聊的鱼鱼都忘光光啦，重新开始~"
        return None  # 不认识的 / 开头内容交给大模型

    # ---------- 大模型 ----------
    def _system_prompt(self, ctx: Context) -> str:
        now = datetime.now(self.cfg.tz)
        if self.cfg.owner_id and ctx.user_id == self.cfg.owner_id:
            who = "现在跟你说话的就是主人本人！"
        elif self.cfg.owner_id:
            who = "现在跟你说话的不是主人，是别人。"
        else:
            who = ""
        return SYSTEM.format(
            name=self.cfg.bot_name,
            persona=self.cfg.persona,
            who=who,
            now=now.strftime("%Y-%m-%d %H:%M") + f" 周{WEEKDAYS[now.weekday()]}",
            tz=self.cfg.tz,
        )

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
        messages = [{"role": "system", "content": self._system_prompt(ctx)}, *history, user_msg]

        reply = ""
        said = []  # 模型在调用工具前顺带说的话，也要发出去
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await self.client.chat.completions.create(
                model=self.cfg.llm_model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                max_tokens=self.cfg.llm_max_tokens,
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
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                    for c in msg.tool_calls
                ],
            }
            messages.append(assistant)
            turn.append(assistant)
            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = self.tools.run(ctx, call.function.name, args)
                log.info("tool %s(%s) -> %s", call.function.name, args, result[:200])
                tool_msg = {"role": "tool", "tool_call_id": call.id, "content": result}
                messages.append(tool_msg)
                turn.append(tool_msg)
            if said:  # 话已经说完、只是顺手发了表情包，就不再让模型多说一遍
                reply = "\n".join(said)
                break
        else:
            reply = "咕噜~"

        turn.append({"role": "assistant", "content": reply or "……"})
        self._remember_turn(ctx.session_id, turn)
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
        # 按「用户发言」为单位截断，保证不会把 tool 消息和它的调用拆开
        user_idx = [i for i, m in enumerate(history) if m["role"] == "user"]
        if len(user_idx) > self.cfg.history_turns:
            del history[: user_idx[-self.cfg.history_turns]]
