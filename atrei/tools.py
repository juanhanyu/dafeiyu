"""表情包：从合并后的大肥鱼表情库（atrei/stickers.json）里挑图，并控制发图频率。"""
import json
import random
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

WEEKDAYS = "一二三四五六日"
INDEX = Path(__file__).resolve().parent / "stickers.json"

# 像真人一样发表情包：刚发过就先不发；平时大概四五条消息发一张；用户点名要表情包时随时可以发
COOLDOWN_REPLIES = 2      # 发过一张后，至少隔这么多条回复才能再发
CHANCE = 0.45             # 冷却结束后，每条回复发图的概率（平均四五条消息一张）
ASK_FOR_STICKER = re.compile(r"表情|斗图|发个图|发张图|来张图|来个图")


@dataclass
class Context:
    user_id: str        # 谁在说话
    session_id: str     # 对话上下文的 key（群聊里每人各一份）
    target_kind: str    # 消息来自哪里：group / c2c / guild / dm / cli
    target_id: str
    is_group: bool = False
    stickers: list[dict] = field(default_factory=list)  # 本轮要发的表情包（stickers.json 里的条目）
    voice: bool = False  # 本轮回复要念成语音发


class StickerLibrary:
    def __init__(self, path: Path = INDEX):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.items: list[dict] = data["stickers"]
        self.emotions: list[str] = data["emotions"]
        self.recent: dict[str, deque] = defaultdict(lambda: deque(maxlen=30))  # 每个会话最近发过的，避免重复
        self.since_last: dict[str, int] = defaultdict(lambda: COOLDOWN_REPLIES)  # 距上次发图隔了几条回复

    # ---------- 频率 ----------
    def allowed(self, session_id: str, text: str) -> bool:
        if ASK_FOR_STICKER.search(text or ""):
            return True
        return self.since_last[session_id] >= COOLDOWN_REPLIES and random.random() < CHANCE

    def record(self, session_id: str, sent: bool):
        self.since_last[session_id] = 0 if sent else self.since_last[session_id] + 1

    # ---------- 挑图 ----------
    def search(self, session_id: str, emotion: str, keywords: str) -> dict | None:
        words = [w for w in re.split(r"[\s,，、]+", keywords or "") if w]
        scored = []
        for s in self.items:
            score = 3 if s["emotion"] == emotion else 0
            hay = " ".join(s["tags"]) + s["use"] + s["desc"] + s["id"]
            score += sum(2 if w in s["tags"] else 1 for w in words if w in hay)
            if s["dir"]:  # 双人图（和 GPT娘）只在明确相关时才用
                score -= 2
            if s["id"] in self.recent[session_id]:
                score -= 5
            if score > 0:
                scored.append((score, s))
        if not scored:
            return None
        scored.sort(key=lambda x: -x[0])
        top = scored[0][0]
        pool = [s for score, s in scored if score >= top - 1][:8]  # 在差不多好的几张里随机挑，免得每次都一样
        choice = random.choice(pool)
        self.recent[session_id].append(choice["id"])
        return choice


def tool_schemas(emotions: list[str]) -> list[dict]:
    return [{
        "type": "function",
        "function": {
            "name": "send_sticker",
            "description": "按心情从大肥鱼表情库里挑一张表情包，跟在你的文字后面发出去。一次回复最多一张。",
            "parameters": {
                "type": "object",
                "properties": {
                    "emotion": {"type": "string", "enum": emotions, "description": "这张表情包要表达的情绪"},
                    "keywords": {"type": "string", "description": "2~4 个描述动作或场景的词，空格分开，如「抱抱 安慰」「干饭 饿」「晚安 睡觉」"},
                },
                "required": ["emotion", "keywords"],
            },
        },
    }]


class ToolBox:
    def __init__(self, library: StickerLibrary):
        self.library = library

    def run(self, ctx: Context, name: str, args: dict) -> str:
        fn = getattr(self, "t_" + name, None)
        if fn is None:
            return json.dumps({"error": f"没有这个工具: {name}"}, ensure_ascii=False)
        try:
            result = fn(ctx, **args)
        except Exception as e:  # 把错误交给模型，让它自己纠正
            result = {"error": f"{type(e).__name__}: {e}"}
        return json.dumps(result, ensure_ascii=False, default=str)

    def t_send_sticker(self, ctx, emotion, keywords=""):
        if ctx.stickers:
            return {"error": "这次已经发过一张了"}
        sticker = self.library.search(ctx.session_id, emotion, keywords)
        if not sticker:
            return {"error": "没找到合适的表情包，这次就不发了"}
        ctx.stickers.append(sticker)
        return {"ok": True, "sent": sticker["desc"][:60],
                "note": "已加入。你之前说过的话会照常发出；没说完就接着说，说完了就回复空字符串。不要描述表情包本身。"}
