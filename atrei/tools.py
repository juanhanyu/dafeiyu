"""给大模型调用的工具：现在只剩发大肥鱼表情包。"""
import json
from dataclasses import dataclass, field
from pathlib import Path

WEEKDAYS = "一二三四五六日"
MOODS = ["love", "happy", "cry", "shock", "sleep", "hungry", "cheer", "ok", "confused", "angry"]


@dataclass
class Context:
    user_id: str        # 谁在说话
    session_id: str     # 对话上下文的 key（群聊里每人各一份）
    target_kind: str    # 消息来自哪里：group / c2c / guild / dm / cli
    target_id: str
    is_group: bool = False
    stickers: list[Path] = field(default_factory=list)  # 本轮要发的表情包


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "send_sticker",
            "description": "发一张大肥鱼表情包，跟在你的文字后面发出去。一次回复最多一张。"
                           "love=贴贴主人 happy=嘿嘿 cry=呜呜 shock=震惊 sleep=睡觉 hungry=饿饿 cheer=冲鸭 ok=收到 "
                           "confused=不懂 angry=哼",
            "parameters": {
                "type": "object",
                "properties": {"mood": {"type": "string", "enum": MOODS}},
                "required": ["mood"],
            },
        },
    },
]


class ToolBox:
    def __init__(self, sticker_dir: Path | None = None):
        self.sticker_dir = sticker_dir

    def run(self, ctx: Context, name: str, args: dict) -> str:
        fn = getattr(self, "t_" + name, None)
        if fn is None:
            return json.dumps({"error": f"没有这个工具: {name}"}, ensure_ascii=False)
        try:
            result = fn(ctx, **args)
        except Exception as e:  # 把错误交给模型，让它自己纠正
            result = {"error": f"{type(e).__name__}: {e}"}
        return json.dumps(result, ensure_ascii=False, default=str)

    def t_send_sticker(self, ctx, mood):
        path = self.sticker_dir / f"{mood}.png" if self.sticker_dir else None
        if not path or not path.is_file():
            return {"error": f"没有 {mood} 这张表情包"}
        if ctx.stickers:
            return {"error": "这次已经发过一张了"}
        ctx.stickers.append(path)
        return {"ok": True, "note": "已加入。你之前说过的话会照常发出；如果还没说完就接着说，没有要补充的就回复空字符串。不要提到表情包本身。"}
