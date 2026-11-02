"""对话记忆存档：Render 免费版重启会清空内存，所以把每段对话的最近几轮存到 HF 私有数据集，启动时读回来。

只存文字：图片换成「[之前发过 N 张图片]」，不然一张图的 base64 就有好几 MB。
"""
import asyncio
import json
import logging

log = logging.getLogger("atrei")

MEMORY_FILE = "memory.json"
SAVE_EVERY = 60  # 有新对话时，最多隔这么多秒存一次


def _text_only(history: list[dict]) -> list[dict]:
    out = []
    for m in history:
        content = m["content"]
        if isinstance(content, list):
            text = next((p["text"] for p in content if p["type"] == "text"), "")
            n = sum(p["type"] == "image_url" for p in content)
            content = f"{text}\n[之前发过 {n} 张图片]"
        out.append({"role": m["role"], "content": content})
    return out


class MemoryStore:
    def __init__(self, hf_token: str, hf_repo: str):
        self.hf_token = hf_token
        self.hf_repo = hf_repo
        self.dirty = False

    @property
    def enabled(self) -> bool:
        return bool(self.hf_token and self.hf_repo)

    async def load(self) -> dict[str, list[dict]]:
        if not self.enabled:
            return {}
        try:
            from huggingface_hub import hf_hub_download

            path = await asyncio.to_thread(hf_hub_download, self.hf_repo, MEMORY_FILE, repo_type="dataset",
                                           token=self.hf_token, force_download=True)
            data = json.loads(open(path, encoding="utf-8").read())
            log.info("读回了 %d 段对话的记忆", len(data))
            return data
        except Exception as e:
            log.info("没有记忆存档，从头开始：%s", type(e).__name__)
            return {}

    async def save(self, histories: dict[str, list[dict]]):
        from huggingface_hub import HfApi

        data = {sid: _text_only(h) for sid, h in histories.items() if h}
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        await asyncio.to_thread(HfApi(token=self.hf_token).upload_file, path_or_fileobj=body,
                                path_in_repo=MEMORY_FILE, repo_id=self.hf_repo, repo_type="dataset",
                                commit_message="save chat memory")

    async def run_forever(self, histories: dict[str, list[dict]]):
        """有新对话就定时存一次。"""
        if not self.enabled:
            return
        while True:
            await asyncio.sleep(SAVE_EVERY)
            if not self.dirty:
                continue
            self.dirty = False
            try:
                await self.save(histories)
            except Exception as e:
                self.dirty = True
                log.warning("记忆存档失败：%s", e)
