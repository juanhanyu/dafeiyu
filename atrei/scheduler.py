"""提醒调度：每隔几秒检查到期提醒，尝试主动发送。"""
import asyncio
import logging
import time
from typing import Awaitable, Callable

from .storage import Storage
from .tools import next_due

log = logging.getLogger("atrei")

# (target_id, user_id, text) -> 是否发送成功
Sender = Callable[[str, str, str], Awaitable[bool]]


class Scheduler:
    def __init__(self, storage: Storage, tz, interval: float = 15):
        self.db = storage
        self.tz = tz
        self.interval = interval
        self.senders: dict[str, Sender] = {}

    def register(self, target_kind: str, sender: Sender):
        self.senders[target_kind] = sender

    async def run_forever(self):
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("提醒调度出错")
            await asyncio.sleep(self.interval)

    async def tick(self):
        for r in self.db.due_reminders(time.time()):
            sender = self.senders.get(r["target_kind"])
            ok = False
            if sender:
                try:
                    ok = await sender(r["target_id"], r["user_id"], f"⏰ 提醒：{r['text']}")
                except Exception as e:
                    log.warning("提醒 #%s 主动发送失败：%s", r["id"], e)

            nxt = next_due(r["due_ts"], r["repeat"], self.tz)
            if nxt is not None:
                # 重复提醒：跳过已经错过的周期（比如机器人停了几天）
                while nxt <= time.time():
                    nxt = next_due(nxt, r["repeat"], self.tz)
                self.db.set_reminder(r["id"], due_ts=nxt)
                if not ok:  # 这一次没送到，留一条待补发
                    missed = self.db.add_reminder(r["user_id"], r["target_kind"], r["target_id"], r["text"], r["due_ts"])
                    self.db.set_reminder(missed, status="undelivered")
            else:
                self.db.set_reminder(r["id"], status="done" if ok else "undelivered")
            log.info("提醒 #%s 已%s", r["id"], "送达" if ok else "记为待补发")
