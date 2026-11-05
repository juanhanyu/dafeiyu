"""全量模式下，没人 @ 时鱼鱼要不要自己接话：像群友一样偶尔插一句，不刷屏。

这里只决定「要不要想一想」，真开口前模型还能回「不说话」，所以实际说话的频率比这些概率还低。
"""
import random
import re
import time
from collections import defaultdict, deque

FOLLOWUP_WINDOW = 120   # 她刚回过某人，这么多秒内这个人接着说，多半还在跟她聊
FOLLOWUP_CHANCE = 0.6
TOPIC_CHANCE = 0.5      # 提到她或者她最爱的米饭
TOPIC = re.compile(r"大肥鱼|肥鱼|鱼鱼|米饭|机器人")
BASE_CHANCE = 0.06      # 别的话题，偶尔凑个热闹
NIGHT_FACTOR = 0.3      # 半夜她困，少说话
COOLDOWN = 120          # 自己插过话或回过话后，这么多秒内不再主动插别的话题
HOUR_MAX = 10           # 每个群每小时最多插这么多次嘴


class GroupPace:
    def __init__(self):
        self.last_reply: dict[str, tuple[str, float]] = {}  # 群 -> (刚回过谁, 什么时候)
        self.unprompted: dict[str, deque] = defaultdict(deque)  # 群 -> 最近几次插嘴的时间
        self.busy: set[str] = set()  # 正在想要不要插嘴的群

    def note_reply(self, group: str, user: str):
        self.last_reply[group] = (user, time.time())

    def note_unprompted(self, group: str):
        self.unprompted[group].append(time.time())

    def should_chime(self, group: str, user: str, text: str, hour: float) -> bool:
        if not text or text.startswith("/") or group in self.busy:
            return False
        now = time.time()
        times = self.unprompted[group]
        while times and now - times[0] > 3600:
            times.popleft()
        if len(times) >= HOUR_MAX:
            return False
        who, at = self.last_reply.get(group, ("", 0))
        if who == user and now - at < FOLLOWUP_WINDOW:
            chance = FOLLOWUP_CHANCE
        elif now - max(at, times[-1] if times else 0) < COOLDOWN:
            return False
        else:
            chance = TOPIC_CHANCE if TOPIC.search(text) else BASE_CHANCE
        if hour < 8:
            chance *= NIGHT_FACTOR
        return random.random() < chance
