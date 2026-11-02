"""存档：Render 免费版重启会清空内存，所以把要长期保留的东西存到 HF 私有数据集，启动时读回来。

存两部分：
- histories：每段对话最近几轮（只存文字，图片换成「[之前发过 N 张图片]」，不然一张图的 base64 就有好几 MB）
- meta：认过的主人编号、已经自我介绍过的群、当天画了几张图
"""
import asyncio
import json
import logging

log = logging.getLogger("atrei")

MEMORY_FILE = "memory.json"
SAVE_EVERY = 60  # 有变化时，最多隔这么多秒存一次（没变化不存）


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

    async def load(self) -> tuple[dict[str, list[dict]], dict]:
        if not self.enabled:
            return {}, {}
        try:
            from huggingface_hub import hf_hub_download

            path = await asyncio.to_thread(hf_hub_download, self.hf_repo, MEMORY_FILE, repo_type="dataset",
                                           token=self.hf_token, force_download=True)
            data = json.loads(open(path, encoding="utf-8").read())
        except Exception as e:
            log.info("没有存档，从头开始：%s", type(e).__name__)
            return {}, {}
        if "histories" not in data:  # 旧格式：整个文件就是对话
            data = {"histories": data, "meta": {}}
        log.info("读回了 %d 段对话的记忆", len(data["histories"]))
        return data["histories"], data.get("meta", {})

    async def save(self, histories: dict[str, list[dict]], meta: dict):
        from huggingface_hub import HfApi

        data = {"histories": {sid: _text_only(h) for sid, h in histories.items() if h}, "meta": meta}
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        await asyncio.to_thread(HfApi(token=self.hf_token).upload_file, path_or_fileobj=body,
                                path_in_repo=MEMORY_FILE, repo_id=self.hf_repo, repo_type="dataset",
                                commit_message="save chat memory")

    async def run_forever(self, histories: dict[str, list[dict]], meta: dict):
        """有变化就定时存一次。"""
        if not self.enabled:
            return
        while True:
            await asyncio.sleep(SAVE_EVERY)
            if not self.dirty:
                continue
            self.dirty = False
            try:
                await self.save(histories, meta)
            except Exception as e:
                self.dirty = True
                log.warning("存档失败：%s", e)
