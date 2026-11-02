"""语音：BreezeBlue 合成大肥鱼的声音 → ffmpeg 转 PCM → 编码成 QQ 要的 silk。

发语音的频率也由代码控制：主人明确要语音时一定发；平时很少自动发一条。
"""
import asyncio
import random
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

import aiohttp

API = "https://api.breeze.blue/v1/text-to-speech/{voice_id}?output_format=mp3"
ASK_FOR_VOICE = re.compile(r"语音|语言听|发个语言|声音|说给我听|念给我听|讲给我听|听你说|说句话|听听|开口|用嘴说|唱|哼(?:首|个|一首|一段)")
ASK_FOR_SONG = re.compile(r"唱|哼(?:首|个|一首|一段)")
COOLDOWN_REPLIES = 15     # 自动发过一条语音后，至少隔这么多条回复才会再自动发
CHANCE = 0.05             # 冷却结束后，每条回复自动发语音的概率
MAX_CHARS = 120           # 太长的话不念，免得费积分
EMOJI = re.compile(r"[\U0001F000-\U0001FFFF☀-➿️]")


class Voice:
    def __init__(self, api_key: str, voice_id: str):
        self.api_key = api_key
        self.voice_id = voice_id
        self.since_last: dict[str, int] = defaultdict(lambda: COOLDOWN_REPLIES)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.voice_id)

    def wanted(self, session_id: str, text: str) -> bool:
        if not self.enabled:
            return False
        if ASK_FOR_VOICE.search(text or ""):
            return True
        return self.since_last[session_id] >= COOLDOWN_REPLIES and random.random() < CHANCE

    def record(self, session_id: str, sent: bool):
        self.since_last[session_id] = 0 if sent else self.since_last[session_id] + 1

    async def synthesize(self, text: str, sing: bool = False) -> bytes:
        """把文字变成 QQ 能播的 silk 语音。"""
        clean = re.sub(r"\[[^\[\]]{1,4}\]", "", EMOJI.sub("", text)).replace("~", "。").strip()[:MAX_CHARS]
        async with aiohttp.ClientSession() as s:
            async with s.post(API.format(voice_id=self.voice_id), json={"text": clean, **({"instructions": "用轻快可爱的调子哼唱出来，像在唱一首小曲"} if sing else {})},
                              # 不带自定义 User-Agent 会被 Cloudflare 当成爬虫拦下（error 1010）
                              headers={"xi-api-key": self.api_key, "user-agent": "dafeiyu-qq-bot/1.0"},
                              timeout=aiohttp.ClientTimeout(total=60)) as r:
                if r.status != 200:
                    raise RuntimeError(f"语音合成失败 {r.status}: {(await r.text())[:200]}")
                mp3 = await r.read()
        return await asyncio.to_thread(mp3_to_silk, mp3)


def mp3_to_silk(mp3: bytes) -> bytes:
    import pilk  # 只在服务器上装（需要 ffmpeg）

    with tempfile.TemporaryDirectory() as d:
        src, pcm, silk = Path(d, "in.mp3"), Path(d, "out.pcm"), Path(d, "out.silk")
        src.write_bytes(mp3)
        # QQ 语音：24kHz 单声道 16bit PCM，再编码成腾讯版 silk
        proc = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-f", "s16le", "-ar", "24000", "-ac", "1", str(pcm)],
            capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg 转码失败：{proc.stderr.decode(errors='ignore')[:200]}")
        pilk.encode(str(pcm), str(silk), pcm_rate=24000, tencent=True)
        return silk.read_bytes()
