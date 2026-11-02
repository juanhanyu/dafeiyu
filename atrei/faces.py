"""QQ 小黄脸：官方机器人的普通文字发不了 QQ 表情，但 Markdown 消息里可以嵌网络图片，
所以把回复里的 [可爱] 这类标记换成黄脸小图（图片来自 koishijs/QFace，版权归腾讯）。

挑的都是可爱/日常的表情；[微笑] 在网上是嘲讽的意思，不用。手机 QQ 不显示 APNG 动图，所以只用静态 png。
"""
import re

BASE = "https://koishi.js.org/QFace/assets/qq_emoji/"
SIZE = 22  # 显示大小（像素），跟文字差不多高

# 名字 → 图片路径（QFace 索引里的静态 png）
FACES = {
    "可爱": "21/png/21.png", "害羞": "6/png/6.png", "呲牙": "13/png/13.png", "偷笑": "20/png/20.png",
    "憨笑": "28/png/28.png", "调皮": "12/png/12.png", "得意": "4/png/4.png", "坏笑": "101/png/101.png",
    "斜眼笑": "178/png/178.png", "笑哭": "182/png/182.png", "捂脸": "264/png/264.png", "期待": "294/png/294.png",
    "托腮": "212/png/212.png", "喵喵": "307/png/307.png", "汪汪": "277/png/277.png", "比心": "319/png/319_0.png",
    "爱心": "66/png/66.png", "拥抱": "49/png/49.png", "赞": "76/png/76.png", "打call": "311/png/311_0.png",
    "奋斗": "30/png/30.png", "流泪": "5/png/5_0.png", "大哭": "9/png/9.png", "委屈": "106/png/106.png",
    "心碎": "67/png/67.png", "撇嘴": "1/png/1.png", "疑问": "32/png/32.png", "惊讶": "0/png/0.png",
    "发呆": "3/png/3.png", "晕": "34/png/34.png", "尴尬": "10/png/10.png", "无语": "462/png/462_0.png",
    "哦哟": "266/png/266.png", "暗中观察": "269/png/269.png", "吃瓜": "271/png/271.png", "生气": "326/png/326_0.png",
    "敲打": "38/png/38.png", "酷": "16/png/16.png", "饥饿": "24/png/24.png", "困": "25/png/25.png", "睡": "8/png/8.png",
}
TOKEN = re.compile(r"\[(" + "|".join(map(re.escape, sorted(FACES, key=len, reverse=True))) + r")\]")


def has_faces(text: str) -> bool:
    return bool(TOKEN.search(text))


def strip_unknown(text: str) -> str:
    """模型偶尔会编出不在表里的 [xx]，比如 [微笑]，这类直接去掉。"""
    return re.sub(r"\[([^\[\]\n]{1,4})\]", lambda m: m.group(0) if m.group(1) in FACES else "", text)


def to_markdown(text: str) -> str:
    """把 [可爱] 换成行内黄脸小图。不做转义（QQ 不一定认反斜杠），只防 ~~ 变成删除线。"""
    text = re.sub(r"~{2,}", "~", text)
    text = TOKEN.sub(lambda m: f"![{m.group(1)} #{SIZE}px #{SIZE}px]({BASE}{FACES[m.group(1)]})", text)
    return text.replace("\n", "  \n")
