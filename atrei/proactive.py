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
