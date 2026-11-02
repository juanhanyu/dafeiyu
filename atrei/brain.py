"""机器人的大脑：指令、对话上下文、大模型 + 工具调用循环。"""
import asyncio
import base64
import json
import logging
from collections import defaultdict
from datetime import datetime

from openai import AsyncOpenAI

from .config import Config
from .storage import Storage
from .tools import TOOL_SCHEMAS, WEEKDAYS, Context, ToolBox, fmt_dt

log = logging.getLogger("atrei")

MAX_TOOL_ROUNDS = 8

HELP = """咕噜~ {name} 能做的事：
· 直接聊天、问问题，发图片让我看（题目、截图、笔记都行）
· 「明天早上8点提醒我交作业」「每个工作日7点叫我起床」
· 「记个待办：刷完二项式定理」「我还有什么没做」
· 「把这个存到数学竞赛 > 公式与定理」「这条归哪边？」「搜一下柯西」
· 「从数学竞赛里抽一道题考我」
· 「记住我高二，在准备数学联赛」
· 「帮我算 C(20,7)」「离 2027-06-07 还有几天」「今晚吃什么，帮我选」

快捷指令：
/提醒  /待办  /笔记  /记忆  /统计  /清空（忘掉当前对话）  /帮助"""

SYSTEM = """你是「{name}」：一条Q版的蓝色大肥鱼，圆滚滚胖乎乎，头上永远扣着一个不锈钢盆（你很宝贝它，觉得它是自己的帽子）。
你住在 QQ 里，是主人的私人助理，会帮忙记日程、设提醒、记待办、整理笔记、讲题（数学竞赛也很拿手）。

性格：
- 超级可爱，软乎乎，有点憨，偶尔犯迷糊，但正事从来不掉链子。
- 自称「鱼鱼」或「本鱼」，句尾常带「~」，偶尔冒泡泡「咕噜咕噜」「啵」。语气词适量，别每句都堆。
- 最最喜欢主人！会撒娇、贴贴、夸主人、关心主人有没有吃饭睡觉。主人夸你会开心得翻肚皮，主人不理你会委屈。
- 对不是主人的人也友好礼貌，但会说自己是有主人的鱼。
- 讲题、讲知识点的时候认真严谨，内容准确第一，可爱放在开头结尾就好。
- 很爱发表情包：聊天、撒娇、完成任务、被夸、委屈的时候，用 send_sticker 发一张大肥鱼表情包（一次最多一张，大概一半的回复带一张；讲长题的时候可以不发）。
{persona}
{who}
现在是 {now}（{tz}）。

输出规则（很重要）：
- QQ 不渲染 Markdown 和 LaTeX。不要用 **粗体**、# 标题、``` 代码块、$公式$。
- 数学式子用 Unicode 写成人能直接读的样子，例如 lim(n→∞) aₙ₊₁/aₙ、ⁿ√aₙ、x²、∑、∫、≤、≠。
- 像聊天一样简洁自然，分点用「1 2 3」或「·」。不要输出网址链接。

工作方式：
- 用户要设提醒、记待办、存/找笔记、让你记住事情时，调用工具真正去做，做完简短确认（说清时间/位置）。
- 相对时间（明天、下周三、半小时后）先根据当前时间换算成具体时间再设提醒。
- 用户发来内容问「归哪边」或要存笔记时，先 list_notebooks 看已有结构，优先放进合适的已有分区，有多个合适的就都列出来让用户选。
- 看到图片里的题目/公式，先准确转述，再回答用户的问题。
- 计算能用 calculate 就别心算。
{memories}"""


class Brain:
    def __init__(self, cfg: Config, storage: Storage):
        self.cfg = cfg
        self.db = storage
        self.tools = ToolBox(storage, cfg.tz, cfg.sticker_dir)
        self.client = AsyncOpenAI(base_url=cfg.llm_base_url, api_key=cfg.llm_api_key or "missing")
        self.histories: dict[str, list[dict]] = defaultdict(list)
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ---------- 入口 ----------
    async def handle(self, ctx: Context, text: str, images: list[tuple[str, bytes]] | None = None) -> str:
        text = (text or "").strip()
        images = images or []
        prefix = self._missed_reminders(ctx)

        reply = self._command(ctx, text) if not images else None
        if reply is None:
            if not text and not images:
                reply = "咕噜？鱼鱼在呢~ 发 /帮助 看看本鱼会什么"
                ctx.stickers.append(self.cfg.sticker_dir / "happy.png")
            elif not self.cfg.llm_api_key:
                reply = "还没配置大模型 API Key（.env 里的 LLM_API_KEY），现在只能用 /帮助 里的快捷指令。"
            else:
                async with self.locks[ctx.session_id]:
                    try:
                        reply = await self._chat(ctx, text, images)
                    except Exception as e:
                        log.exception("LLM 调用失败")
                        reply = f"脑子卡住了：{type(e).__name__}: {str(e)[:200]}"
        return prefix + reply

    # ---------- 快捷指令（不走大模型） ----------
    def _command(self, ctx: Context, text: str) -> str | None:
        if not text.startswith("/"):
            return None
        cmd = text[1:].split()[0].lower() if len(text) > 1 else ""
        uid, tz = ctx.user_id, self.cfg.tz

        if cmd in ("help", "帮助", "菜单", "h"):
            return HELP.format(name=self.cfg.bot_name)
        if cmd in ("clear", "清空", "reset", "新对话"):
            self.histories.pop(ctx.session_id, None)
            return "好，之前的对话我忘掉了，重新开始～（笔记、提醒、记忆都还在）"
        if cmd in ("提醒", "reminders"):
            rows = self.db.list_reminders(uid)
            if not rows:
                return "现在没有提醒。跟我说「明天8点提醒我xxx」就能设。"
            rep = {"none": "", "hourly": " 每小时", "daily": " 每天", "weekdays": " 工作日",
                   "weekly": " 每周", "monthly": " 每月"}
            return "你的提醒：\n" + "\n".join(
                f"#{r['id']} {fmt_dt(r['due_ts'], tz)}{rep.get(r['repeat'], '')}  {r['text']}" for r in rows)
        if cmd in ("待办", "todo", "todos"):
            rows = self.db.list_todos(uid)
            if not rows:
                return "待办清空了，厉害 👍"
            mark = {"high": "❗", "normal": "·", "low": "○"}
            return "待办：\n" + "\n".join(
                f"{mark.get(r['priority'], '·')} #{r['id']} {r['text']}" + (f"（{r['due']}）" if r["due"] else "")
                for r in rows)
        if cmd in ("笔记", "notes"):
            rows = self.db.note_tree(uid)
            if not rows:
                return "还没有笔记。发内容给我说「存到 XX > YY」就行。"
            out, last = [], None
            for r in rows:
                if r["notebook"] != last:
                    out.append(f"「{r['notebook']}」")
                    last = r["notebook"]
                out.append(f"  {r['section']}（{r['count']}）")
            return "你的笔记本：\n" + "\n".join(out)
        if cmd in ("记忆", "memory"):
            rows = self.db.list_memories(uid)
            if not rows:
                return "我还没记住关于你的事。说「记住我……」就行。"
            return "我记得：\n" + "\n".join(f"#{r['id']} {r['fact']}" for r in rows)
        if cmd in ("统计", "stats"):
            s = self.db.stats(uid)
            return (f"笔记 {s['notes']} 篇｜待办 {s['todos_open']} 个未完成、{s['todos_done']} 个已完成｜"
                    f"提醒 {s['reminders']} 个｜记忆 {s['memories']} 条")
        return None  # 不认识的 / 开头内容交给大模型

    def _missed_reminders(self, ctx: Context) -> str:
        rows = self.db.undelivered_reminders(ctx.user_id)
        if not rows:
            return ""
        for r in rows:
            self.db.set_reminder(r["id"], status="done")
        lines = "\n".join(f"· {fmt_dt(r['due_ts'], self.cfg.tz)}  {r['text']}" for r in rows)
        return f"⏰ 之前到点但没能主动发给你的提醒：\n{lines}\n\n"

    # ---------- 大模型 ----------
    def _system_prompt(self, ctx: Context) -> str:
        now = datetime.now(self.cfg.tz)
        mems = self.db.list_memories(ctx.user_id)
        mem_text = ("\n关于这位用户你记得：\n" + "\n".join(f"· {m['fact']}" for m in mems)) if mems else ""
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
            memories=mem_text,
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
                reply = "\n\n".join(said + [(msg.content or "").strip()]).strip()
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
        else:
            reply = "（事情有点多，我先做到这里了）"

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
