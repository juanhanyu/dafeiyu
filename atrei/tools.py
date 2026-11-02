"""给大模型调用的工具：提醒、待办、笔记、记忆、计算、随机……"""
import ast
import json
import math
import operator
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .storage import Storage

WEEKDAYS = "一二三四五六日"


@dataclass
class Context:
    user_id: str        # 谁在说话（笔记/待办/提醒都按人隔离）
    session_id: str     # 对话上下文的 key（群聊里每人各一份）
    target_kind: str    # 提醒发回哪里：group / c2c / guild / dm / cli
    target_id: str
    is_group: bool = False
    stickers: list[Path] = field(default_factory=list)  # 本轮要发的表情包


def fmt_dt(ts: float, tz: ZoneInfo) -> str:
    d = datetime.fromtimestamp(ts, tz)
    return d.strftime("%Y-%m-%d %H:%M") + f" 周{WEEKDAYS[d.weekday()]}"


def next_due(ts: float, repeat: str, tz: ZoneInfo) -> float | None:
    """重复提醒的下一次时间；不重复则返回 None。"""
    d = datetime.fromtimestamp(ts, tz)
    if repeat == "hourly":
        return (d + timedelta(hours=1)).timestamp()
    if repeat == "daily":
        return (d + timedelta(days=1)).timestamp()
    if repeat == "weekly":
        return (d + timedelta(weeks=1)).timestamp()
    if repeat == "weekdays":
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d.timestamp()
    if repeat == "monthly":
        month = d.month % 12 + 1
        year = d.year + (d.month == 12)
        day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
                          31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        return d.replace(year=year, month=month, day=day).timestamp()
    return None


# ---------- 安全计算器 ----------
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_NAMES = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
_NAMES.update(abs=abs, round=round, min=min, max=max, sum=sum)


def safe_eval(expr: str):
    expr = expr.replace("^", "**").replace("×", "*").replace("÷", "/")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 10000:
                raise ValueError("指数太大")
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Name) and node.id in _NAMES:
            return _NAMES[node.id]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _NAMES:
            return _NAMES[node.func.id](*[ev(a) for a in node.args])
        if isinstance(node, (ast.List, ast.Tuple)):
            return [ev(e) for e in node.elts]
        raise ValueError(f"不支持的表达式: {ast.dump(node)[:40]}")

    return ev(ast.parse(expr, mode="eval"))


def _fn(name, desc, props=None, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": required or []},
        },
    }


S = {"type": "string"}
I = {"type": "integer"}

TOOL_SCHEMAS = [
    _fn("get_time", "获取当前日期、时间和星期。"),
    _fn("calculate", "精确计算数学表达式，支持 + - * / ** % 和 math 库函数（sqrt, sin, log, factorial, comb…）。",
        {"expression": S}, ["expression"]),
    _fn("days_until", "计算距离某个日期还有多少天（倒数日，如考试、比赛）。",
        {"date": {"type": "string", "description": "YYYY-MM-DD"}}, ["date"]),
    _fn("random_pick", "帮用户做选择：从选项里随机挑一个；或掷骰子（不给 options 时）。",
        {"options": {"type": "array", "items": S}, "dice_sides": I, "dice_count": I}),

    _fn("add_reminder", "设置提醒，到点主动提醒用户。时间用用户所在时区。",
        {"text": {"type": "string", "description": "提醒内容"},
         "time": {"type": "string", "description": "YYYY-MM-DD HH:MM"},
         "repeat": {"type": "string", "enum": ["none", "hourly", "daily", "weekdays", "weekly", "monthly"]}},
        ["text", "time"]),
    _fn("list_reminders", "列出用户所有未完成的提醒。"),
    _fn("cancel_reminder", "取消一个提醒。", {"id": I}, ["id"]),

    _fn("add_todo", "添加待办事项。", {"text": S, "due": {"type": "string", "description": "可选截止日期"},
                                   "priority": {"type": "string", "enum": ["high", "normal", "low"]}}, ["text"]),
    _fn("list_todos", "列出待办事项。", {"include_done": {"type": "boolean"}}),
    _fn("complete_todo", "把待办标记为完成。", {"id": I}, ["id"]),
    _fn("delete_todo", "删除待办。", {"id": I}, ["id"]),

    _fn("list_notebooks", "列出用户笔记本的目录结构（笔记本 > 分区 > 篇数）。归类前先看这个，尽量放进已有分区。"),
    _fn("save_note", "把内容保存为笔记，放进某个笔记本的某个分区。",
        {"notebook": {"type": "string", "description": "如：数学竞赛"},
         "section": {"type": "string", "description": "如：函数、极限、连续 / 公式与定理"},
         "title": S, "content": S}, ["notebook", "section", "title", "content"]),
    _fn("list_notes", "列出某个笔记本（可选某分区）里的笔记。", {"notebook": S, "section": S}, ["notebook"]),
    _fn("search_notes", "按关键字搜索笔记。", {"keyword": S}, ["keyword"]),
    _fn("get_note", "读取一篇笔记的全文。", {"id": I}, ["id"]),
    _fn("move_note", "把笔记移到别的笔记本/分区。", {"id": I, "notebook": S, "section": S}, ["id", "notebook", "section"]),
    _fn("delete_note", "删除笔记。", {"id": I}, ["id"]),
    _fn("quiz_me", "随机抽一篇笔记用来出题复习（可指定笔记本）。拿到后根据内容出题，先别给答案。", {"notebook": S}),

    _fn("remember", "把关于用户的长期信息记下来（偏好、年级、目标、生日等），以后的对话都会记得。", {"fact": S}, ["fact"]),
    _fn("list_memories", "列出记住的关于用户的信息。"),
    _fn("forget", "删除一条记忆。", {"id": I}, ["id"]),

    _fn("my_stats", "查看用户的数据统计（笔记数、待办完成数等）。"),

    _fn("send_sticker", "发一张大肥鱼表情包，会跟在你的文字后面发出去。聊天时经常用，一次回复最多一张。"
        "love=贴贴主人 happy=嘿嘿 cry=呜呜 shock=震惊 sleep=睡觉 hungry=饿饿 cheer=冲鸭 ok=收到 confused=不懂 angry=哼",
        {"mood": {"type": "string", "enum": ["love", "happy", "cry", "shock", "sleep",
                                              "hungry", "cheer", "ok", "confused", "angry"]}}, ["mood"]),
]


class ToolBox:
    def __init__(self, storage: Storage, tz: ZoneInfo, sticker_dir: Path | None = None):
        self.db = storage
        self.tz = tz
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

    # ---- 通用 ----
    def t_get_time(self, ctx):
        now = datetime.now(self.tz)
        return {"now": now.strftime("%Y-%m-%d %H:%M:%S"), "weekday": "周" + WEEKDAYS[now.weekday()], "tz": str(self.tz)}

    def t_calculate(self, ctx, expression):
        return {"expression": expression, "result": safe_eval(expression)}

    def t_days_until(self, ctx, date):
        target = datetime.strptime(date, "%Y-%m-%d").date()
        return {"date": date, "days": (target - datetime.now(self.tz).date()).days}

    def t_random_pick(self, ctx, options=None, dice_sides=6, dice_count=1):
        if options:
            return {"picked": random.choice(options)}
        rolls = [random.randint(1, max(2, dice_sides)) for _ in range(min(max(1, dice_count), 20))]
        return {"rolls": rolls, "total": sum(rolls)}

    # ---- 提醒 ----
    def t_add_reminder(self, ctx, text, time, repeat="none"):
        dt = datetime.strptime(time.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=self.tz)
        if dt.timestamp() < datetime.now(self.tz).timestamp() - 60:
            return {"error": "这个时间已经过去了，请确认日期"}
        rid = self.db.add_reminder(ctx.user_id, ctx.target_kind, ctx.target_id, text, dt.timestamp(), repeat)
        return {"ok": True, "id": rid, "when": fmt_dt(dt.timestamp(), self.tz), "repeat": repeat}

    def t_list_reminders(self, ctx):
        return [{"id": r["id"], "text": r["text"], "when": fmt_dt(r["due_ts"], self.tz), "repeat": r["repeat"]}
                for r in self.db.list_reminders(ctx.user_id)]

    def t_cancel_reminder(self, ctx, id):
        return {"ok": self.db.cancel_reminder(ctx.user_id, id)}

    # ---- 待办 ----
    def t_add_todo(self, ctx, text, due=None, priority="normal"):
        return {"ok": True, "id": self.db.add_todo(ctx.user_id, text, due, priority)}

    def t_list_todos(self, ctx, include_done=False):
        return self.db.list_todos(ctx.user_id, include_done)

    def t_complete_todo(self, ctx, id):
        return {"ok": self.db.complete_todo(ctx.user_id, id)}

    def t_delete_todo(self, ctx, id):
        return {"ok": self.db.delete_todo(ctx.user_id, id)}

    # ---- 笔记 ----
    def t_list_notebooks(self, ctx):
        return self.db.note_tree(ctx.user_id) or {"empty": True, "hint": "还没有任何笔记"}

    def t_save_note(self, ctx, notebook, section, title, content):
        nid = self.db.add_note(ctx.user_id, notebook.strip(), section.strip(), title.strip(), content)
        return {"ok": True, "id": nid, "path": f"{notebook} > {section} > {title}"}

    def t_list_notes(self, ctx, notebook, section=None):
        return self.db.notes_in(ctx.user_id, notebook, section)

    def t_search_notes(self, ctx, keyword):
        return self.db.search_notes(ctx.user_id, keyword)

    def t_get_note(self, ctx, id):
        return self.db.get_note(ctx.user_id, id) or {"error": "找不到这篇笔记"}

    def t_move_note(self, ctx, id, notebook, section):
        return {"ok": self.db.move_note(ctx.user_id, id, notebook, section)}

    def t_delete_note(self, ctx, id):
        return {"ok": self.db.delete_note(ctx.user_id, id)}

    def t_quiz_me(self, ctx, notebook=None):
        return self.db.random_note(ctx.user_id, notebook) or {"error": "没有可以复习的笔记"}

    # ---- 记忆 ----
    def t_remember(self, ctx, fact):
        return {"ok": True, "id": self.db.add_memory(ctx.user_id, fact)}

    def t_list_memories(self, ctx):
        return self.db.list_memories(ctx.user_id)

    def t_forget(self, ctx, id):
        return {"ok": self.db.delete_memory(ctx.user_id, id)}

    def t_my_stats(self, ctx):
        return self.db.stats(ctx.user_id)

    def t_send_sticker(self, ctx, mood):
        path = self.sticker_dir / f"{mood}.png" if self.sticker_dir else None
        if not path or not path.is_file():
            return {"error": f"没有 {mood} 这张表情包"}
        if ctx.stickers:
            return {"error": "这次已经发过一张了"}
        ctx.stickers.append(path)
        return {"ok": True, "note": "已加入。你之前说过的话会照常发出；如果还没说完就接着说，没有要补充的就回复空字符串。不要提到表情包本身。"}
