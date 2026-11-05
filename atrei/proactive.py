"""主动找主人：像真人一样偶尔主动发私聊——早安、饭点、好久不见、关心之前说过的事。

只找主人；夜里不打扰；主人正在聊时不插话；每天最多几条、相隔够久。全部是随机触发，免得像定时闹钟。
QQ 对主动消息有额度限制（官方没说清多少），发不出去就当天不再试。
"""
import asyncio
import logging
import random
import time
from datetime import datetime

log = logging.getLogger("atrei")

CHECK_EVERY = 10 * 60      # 每隔多久看一眼要不要主动说话
DAILY_MAX = 2              # 每天最多主动几条
MIN_GAP_HOURS = 4          # 两条主动消息至少隔多久
QUIET_FROM, QUIET_TO = 23.0, 8.5   # 夜里这段时间不打扰
CHATTING_HOURS = 2         # 主人最近这么久内说过话，就不主动插话


class Proactive:
    def __init__(self, brain, send_to_owner):
        self.brain = brain
        self.send_to_owner = send_to_owner  # async (text) -> None，发主动私聊
        self.meta = brain.meta
        self.tz = brain.cfg.tz

    def _today(self) -> str:
        return datetime.now(self.tz).date().isoformat()

    def _state(self) -> dict:
        st = self.meta.setdefault("proactive", {})
        if st.get("day") != self._today():
            st.update(day=self._today(), sent=0, blocked=False, greeted=False)
        return st

    def _pick_reason(self, hour: float, st: dict) -> str | None:
        """按时间和情况随机决定这次要不要说、说什么。"""
        gone = self.brain.life.hours_since_owner()
        followup = self.brain.longterm.pending_followup("owner")
        if followup and 10 <= hour < 22 and random.random() < 0.4:
            followup["asked"] = True
            return f"你记得{followup['fact']}，想{followup['followup']}"
        if not st["greeted"] and 8.5 <= hour < 10 and random.random() < 0.25:
            st["greeted"] = True
            return "早上了，想跟主人说早安，问问睡得好不好"
        if gone and gone > 20 and 10 <= hour < 22 and random.random() < 0.15:
            return f"主人已经{int(gone)}个小时没来找你了，有点想主人"
        if (11.5 <= hour < 12.5 or 18 <= hour < 19) and random.random() < 0.08:
            return "到饭点了，想提醒主人吃饭（顺便惦记一下自己的米饭）"
        return None

    async def tick(self):
        if not self.brain.cfg.owner_id:
            return
        st = self._state()
        now = datetime.now(self.tz)
        hour = now.hour + now.minute / 60
        if st["blocked"] or st["sent"] >= DAILY_MAX or hour >= QUIET_FROM or hour < QUIET_TO:
            return
        if time.time() - st.get("last", 0) < MIN_GAP_HOURS * 3600:
            return
        gone = self.brain.life.hours_since_owner()
        if gone is not None and gone < CHATTING_HOURS:
            return
        reason = self._pick_reason(hour, st)
        if not reason:
            return
        text = await self.brain.proactive_message(reason)
        if not text:
            return
        try:
            await self.send_to_owner(text)
        except Exception as e:
            st["blocked"] = True  # 多半是主动消息额度用完了，今天不再试
            log.warning("主动消息发不出去，今天不再尝试：%s", e)
            self.brain.memory.dirty = True
            return
        st["sent"] += 1
        st["last"] = time.time()
        self.brain.remember_proactive(text)
        log.info("主动找了主人：%s", text[:40])

    async def run_forever(self):
        while True:
            await asyncio.sleep(CHECK_EVERY)
            try:
                await self.tick()
            except Exception:
                log.exception("主动消息出错")


GROUP_DAILY_MAX = 2        # 每个群每天最多主动几条
GROUP_MIN_GAP_HOURS = 5    # 同一个群两条主动消息至少隔多久
GROUP_ACTIVE_DAYS = 3      # 这么多天没人说话的群，不去打扰
GROUP_CHATTING_MINUTES = 30  # 群里正聊着就不冒泡，想说话可以直接接话


class GroupProactive:
    """主动在群里冒泡：早上打招呼、饭点惦记米饭、群里安静久了说一句。
    官方规则：每个群每分钟 20 条、每天 1000 条，群可以拒收。这里的限制比官方严得多，是为了不招人烦：
    上一条主动消息之后没人说过话，就不再发（不自言自语）。"""

    def __init__(self, brain, send):
        self.brain = brain
        self.send = send  # async (group_id, text) -> None
        self.meta = brain.meta
        self.tz = brain.cfg.tz

    def _state(self, group: str, today: str) -> dict:
        st = self.meta.setdefault("group_proactive", {}).setdefault(group, {})
        if st.get("day") != today:
            st.update(day=today, sent=0, blocked=False, greeted=False)
        return st

    def _pick_reason(self, hour: float, quiet_hours: float, st: dict) -> str | None:
        if not st["greeted"] and 8.5 <= hour < 10 and random.random() < 0.1:
            st["greeted"] = True
            return "早上了，想跟群里的大家说声早"
        if (11.5 <= hour < 12.5 or 18 <= hour < 19) and random.random() < 0.05:
            return "到饭点了，想问问大家吃什么（顺便惦记一下自己的米饭）"
        if quiet_hours >= 3 and 10 <= hour < 22 and random.random() < 0.01:
            return "群里安静好一会儿了，有点无聊，想冒个泡找大家玩"
        return None

    async def tick_group(self, group: str, now: datetime):
        hour = now.hour + now.minute / 60
        st = self._state(group, now.date().isoformat())
        if st["blocked"] or st["sent"] >= GROUP_DAILY_MAX:
            return
        heard = self.brain.group_last_heard(group)
        ts = time.time()
        if ts - heard > GROUP_ACTIVE_DAYS * 86400 or ts - heard < GROUP_CHATTING_MINUTES * 60:
            return
        if ts - st.get("last", 0) < GROUP_MIN_GAP_HOURS * 3600 or st.get("last", 0) > heard:
            return
        reason = self._pick_reason(hour, (ts - heard) / 3600, st)
        if not reason:
            return
        text = await self.brain.group_proactive_message(group, reason)
        if not text:
            return
        try:
            await self.send(group, text)
        except Exception as e:
            st["blocked"] = True  # 群拒收、被踢、没权限或超额，今天不再试
            log.warning("群 %s 主动消息发不出去，今天不再尝试：%s", group, e)
            self.brain.memory.dirty = True
            return
        st["sent"] += 1
        st["last"] = ts
        self.brain.memory.dirty = True
        self.brain.log_group_line(group, "你（大肥鱼）", text)
        log.info("主动在群 %s 说：%s", group, text[:40])

    async def tick(self):
        now = datetime.now(self.tz)
        hour = now.hour + now.minute / 60
        if hour >= QUIET_FROM or hour < QUIET_TO:
            return
        rejected = self.meta.get("group_rejected", [])
        for group in list(self.meta.get("group_active", {})):
            if group not in rejected:
                await self.tick_group(group, now)

    async def run_forever(self):
        while True:
            await asyncio.sleep(CHECK_EVERY)
            try:
                await self.tick()
            except Exception:
                log.exception("群主动消息出错")
