"""生图：借用一个专门给鱼鱼登录的 Codex（ChatGPT 订阅）账号，调用 Codex 的生图接口。

做法参考 https://github.com/jdmnk/codex-imagegen-cli 。用的是 OpenAI 没公开的接口，随时可能失效，
账号也有被风控的风险，所以只让主人用、每天限量。

Codex 的续期令牌每用一次就换新的，而 Render 免费版重启会清空硬盘，所以登录信息（auth.json）
存在 Hugging Face 私有数据集里：启动时读出来，续期后写回去。
"""
import asyncio
import base64
import json
import logging
import os
import platform
import re
import time
from datetime import date

import aiohttp

log = logging.getLogger("atrei")

BASE = "https://chatgpt.com/backend-api/codex"
REFRESH_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"   # Codex CLI 的公开 OAuth client id
ORIGINATOR = "codex_cli_rs"
CODEX_VERSION = os.getenv("CODEX_VERSION", "0.80.0")
AUTH_FILE = "codex_auth.json"               # 在 HF 数据集里的文件名
DAILY_LIMIT = int(os.getenv("IMAGE_DAILY_LIMIT", "10"))
ASK_FOR_IMAGE = re.compile(r"画|生图|生成.{0,4}图|做.{0,2}张图|来张图|出图")


class ImageGen:
    def __init__(self, hf_token: str, hf_repo: str, owner_id: str):
        self.hf_token = hf_token
        self.hf_repo = hf_repo
        self.owner_id = owner_id
        self.auth: dict | None = None
        self.used_today = 0
        self.day = date.today()
        self.lock = asyncio.Lock()

    # ---------- 状态 ----------
    @property
    def enabled(self) -> bool:
        return self.auth is not None

    def offered(self, user_id: str, text: str) -> bool:
        """只有主人提到画图时，才把生图工具交给模型。"""
        return self.enabled and user_id == self.owner_id and bool(ASK_FOR_IMAGE.search(text or ""))

    def quota_left(self) -> int:
        if self.day != date.today():
            self.day, self.used_today = date.today(), 0
        return DAILY_LIMIT - self.used_today

    # ---------- 登录信息的存取 ----------
    async def load(self):
        if not (self.hf_token and self.hf_repo):
            return
        try:
            from huggingface_hub import hf_hub_download

            path = await asyncio.to_thread(hf_hub_download, self.hf_repo, AUTH_FILE, repo_type="dataset",
                                           token=self.hf_token, force_download=True)
            self.auth = json.loads(open(path, encoding="utf-8").read())
            log.info("生图已就绪（Codex 登录信息来自 %s）", self.hf_repo)
        except Exception as e:
            log.info("没有 Codex 登录信息，生图关闭：%s", type(e).__name__)

    async def _save(self):
        from huggingface_hub import HfApi

        data = json.dumps(self.auth, ensure_ascii=False, indent=2).encode("utf-8")
        await asyncio.to_thread(HfApi(token=self.hf_token).upload_file, path_or_fileobj=data,
                                path_in_repo=AUTH_FILE, repo_id=self.hf_repo, repo_type="dataset",
                                commit_message="refresh codex auth")

    # ---------- 令牌 ----------
    def _token(self) -> str:
        return self.auth["tokens"]["access_token"]

    def _expiring(self) -> bool:
        try:
            part = self._token().split(".")[1]
            exp = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("exp", 0)
        except Exception:
            return False
        return exp <= time.time() + 120

    async def _refresh(self, session: aiohttp.ClientSession):
        payload = {"client_id": CLIENT_ID, "grant_type": "refresh_token",
                   "refresh_token": self.auth["tokens"]["refresh_token"]}
        async with session.post(REFRESH_URL, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status != 200:
                raise RuntimeError(f"Codex 令牌续期失败 {r.status}，需要重新登录")
            fresh = await r.json()
        for key in ("access_token", "refresh_token", "id_token"):
            if fresh.get(key):
                self.auth["tokens"][key] = fresh[key]
        self.auth["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        await self._save()  # 续期令牌只能用一次，必须马上存好新的
        log.info("Codex 令牌已续期")

    def _headers(self) -> dict:
        system = f"{platform.system()} {platform.release()}; {platform.machine()}"
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "originator": ORIGINATOR,
            "User-Agent": f"{ORIGINATOR}/{CODEX_VERSION} ({system})",
            "version": CODEX_VERSION,
        }
        if self.auth["tokens"].get("account_id"):
            headers["ChatGPT-Account-ID"] = self.auth["tokens"]["account_id"]
        return headers

    # ---------- 生图 ----------
    async def generate(self, prompt: str) -> bytes:
        if self.quota_left() <= 0:
            raise RuntimeError("今天的画画次数用完了")
        payload = {"model": "gpt-image-2", "prompt": prompt, "size": "1024x1024",
                   "quality": "medium", "background": "auto"}
        async with self.lock, aiohttp.ClientSession() as session:
            if self._expiring():
                await self._refresh(session)
            for attempt in range(2):
                async with session.post(f"{BASE}/images/generations", json=payload, headers=self._headers(),
                                        timeout=aiohttp.ClientTimeout(total=180)) as r:
                    if r.status == 401 and attempt == 0:
                        await self._refresh(session)
                        continue
                    if r.status != 200:
                        raise RuntimeError(f"生图失败 {r.status}: {(await r.text())[:200]}")
                    data = await r.json()
                    break
        self.used_today += 1
        return base64.b64decode(data["data"][0]["b64_json"])
