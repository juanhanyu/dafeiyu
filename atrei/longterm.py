"""长期记忆：鱼鱼自己从聊天里挑值得记住的事（喜好、近况、计划、重要日子），像朋友一样记着、偶尔提起。

每个人攒够几句新对话，就在后台让模型提炼一次，不耽误回复。记下的事存在 meta["facts"] 里，
跟对话记忆一起存档。带日期的事（比如明天考试）会成为主动关心的话题。
"""
import json
import logging
import re
from datetime import datetime

log = logging.getLogger("atrei")

EXTRACT_EVERY = 6     # 每个人每说这么多句，提炼一次
MAX_FACTS = 40        # 每个人最多记这么多条，旧的挤掉
SHOW_FACTS = 15       # 每次聊天带上最近这么多条

EXTRACT_PROMPT = """你是大肥鱼的记忆助手。下面是大肥鱼和「{who}」最近的聊天，以及大肥鱼已经记得的事。
请找出值得长期记住的、关于对方的新信息：喜好和讨厌的东西、身份和近况、计划和重要日子、在意的人和事、对鱼鱼的约定。
不要记：闲聊客套、鱼鱼自己说的话、已经记得的事、一次性的小事。
今天是 {today}。有明确日期的事，把日期换算成 YYYY-MM-DD 写进 when；之后可以主动关心的，写一句 followup（比如「问问考试考得怎么样」）。

已经记得：
{known}

最近的聊天：
{chat}

只输出 JSON 数组，没有新信息就输出 []，格式：
[{{"fact": "主人在准备数学联赛", "when": "", "followup": ""}}]"""


def person_key(is_owner: bool, user_id: str) -> str:
    return "owner" if is_owner else user_id


class LongTermMemory:
    def __init__(self, meta: dict, memory, tz):
        self.meta = meta
        self.memory = memory
        self.tz = tz
        self.counters: dict[str, int] = {}

    def facts(self, key: str) -> list[dict]:
        return self.meta.setdefault("facts", {}).setdefault(key, [])

    def prompt_block(self, key: str) -> str:
        facts = self.facts(key)[-SHOW_FACTS:]
        if not facts:
            return ""
        lines = "\n".join(f"· {f['fact']}" + (f"（{f['when']}）" if f.get("when") else "") for f in facts)
        return f"\n关于对方你记得（自然地用，别一股脑全说出来）：\n{lines}"

    def due(self, key: str) -> bool:
        """这个人又说了一句，攒够了就该提炼一次。"""
        self.counters[key] = self.counters.get(key, 0) + 1
        if self.counters[key] >= EXTRACT_EVERY:
            self.counters[key] = 0
            return True
        return False

    async def extract(self, client, model: str, key: str, who: str, history: list[dict]):
        """后台跑：让模型从最近的聊天里挑新事实记下来。"""
        chat = "\n".join(f"{'对方' if m['role'] == 'user' else '鱼鱼'}：{m['content'] if isinstance(m['content'], str) else '[图片]'}"
                         for m in history[-EXTRACT_EVERY * 2:])
        known = "\n".join(f"· {f['fact']}" for f in self.facts(key)) or "（还没有）"
        prompt = EXTRACT_PROMPT.format(who=who, today=datetime.now(self.tz).date().isoformat(), known=known, chat=chat)
        try:
            resp = await client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}], max_tokens=1500,
                reasoning_effort="low", extra_body={"thinking": {"type": "enabled"}})
            text = resp.choices[0].message.content or "[]"
            items = json.loads(re.search(r"\[.*\]", text, re.S).group(0))
        except Exception as e:
            log.warning("长期记忆提炼失败：%s", e)
            return
        facts = self.facts(key)
        for it in items:
            if isinstance(it, dict) and it.get("fact"):
                facts.append({"fact": str(it["fact"])[:80], "when": str(it.get("when") or "")[:10],
                              "followup": str(it.get("followup") or "")[:60], "asked": False})
        del facts[:-MAX_FACTS]
        if items:
            self.memory.dirty = True
            log.info("记住了 %d 件关于 %s 的事", len(items), who)

    def pending_followup(self, key: str) -> dict | None:
        """找一件到日子了、还没主动问过的事。"""
        today = datetime.now(self.tz).date().isoformat()
        for f in reversed(self.facts(key)):
            if f.get("followup") and not f.get("asked") and f.get("when") and f["when"] <= today:
                return f
        return None
