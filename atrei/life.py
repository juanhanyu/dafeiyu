"""心情和作息：让鱼鱼像在过日子——按时间犯困、饿了想米饭，每天有个基础心情，主人好久不来会想主人。"""
import random
import time
from datetime import datetime

# 每天随机一个基础心情（按日期固定，一天内不变）
DAILY_MOODS = ["元气满满", "开心", "有点懒洋洋", "黏人", "有点小傲娇", "心情不错但有点馋", "有点小郁闷，需要人哄", "兴奋得冒泡泡"]


def schedule(hour: float) -> str:
    if hour < 7:
        return "这个点本该在睡觉，被吵醒了迷迷糊糊的，说话带点困意"
    if hour < 9:
        return "刚起床不久，还有点没睡醒"
    if 11.5 <= hour < 13:
        return "到午饭时间了，满脑子想着米饭"
    if 13 <= hour < 14.5:
        return "吃饱了犯午困"
    if 17.5 <= hour < 19.5:
        return "到晚饭时间了，饿饿，想米饭"
    if 22.5 <= hour < 24:
        return "夜深了有点困，准备睡觉"
    return "精神挺好"


class Life:
    def __init__(self, meta: dict, tz):
        self.meta = meta
        self.tz = tz

    def mood_today(self) -> str:
        today = datetime.now(self.tz).date().isoformat()
        return random.Random(today).choice(DAILY_MOODS)

    def seen_owner(self):
        self.meta["owner_last_seen"] = time.time()

    def hours_since_owner(self) -> float | None:
        last = self.meta.get("owner_last_seen")
        return (time.time() - last) / 3600 if last else None

    def state(self, talking_to_owner: bool) -> str:
        now = datetime.now(self.tz)
        parts = [schedule(now.hour + now.minute / 60), f"今天的心情：{self.mood_today()}"]
        gone = self.hours_since_owner()
        if talking_to_owner and gone is not None:
            if gone > 72:
                parts.append(f"主人已经{int(gone // 24)}天没理你了，很委屈，见到主人又开心又想撒娇抱怨")
            elif gone > 20:
                parts.append("主人一整天没来找你，有点想主人，见到主人很开心")
        return "此刻你的状态（自然地带出来，别生硬地报告）：" + "；".join(parts) + "。"
