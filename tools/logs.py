"""不开 Render 网页看鱼鱼的日志：从 HF 私有存档里读出来。

用法：
  python tools/logs.py            最近 80 行
  python tools/logs.py 语音        只看带「语音」的行
  python tools/logs.py --samples  看「附带消息」的原始格式样本
需要 .env 里有 HF_TOKEN 和 HF_BACKUP_REPO。存档每分钟才存一次，所以最新的日志会晚一点出现。
"""
import json
import sys
from pathlib import Path

from dotenv import dotenv_values
from huggingface_hub import hf_hub_download

ROOT = Path(__file__).resolve().parent.parent


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    env = dotenv_values(ROOT / ".env")
    repo = env.get("HF_BACKUP_REPO") or "hanyujuan/dafeiyu-data"
    path = hf_hub_download(repo, "memory.json", repo_type="dataset", token=env["HF_TOKEN"], force_download=True)
    meta = json.loads(Path(path).read_text(encoding="utf-8")).get("meta", {})
    args = sys.argv[1:]
    if args[:1] == ["--samples"]:
        for s in meta.get("raw_samples", []) or ["（还没有样本）"]:
            print(json.dumps(s, ensure_ascii=False, indent=1))
        for kind, samples in meta.get("group_samples", {}).items():  # at_event / full_to_me / full_plain
            print(f"===== {kind} =====")
            for s in samples:
                print(json.dumps(s, ensure_ascii=False, indent=1))
        return
    lines = meta.get("logs", [])
    if args:
        lines = [l for l in lines if all(a in l for a in args)]
    print("\n".join(lines[-80:]) or "（没有日志）")


if __name__ == "__main__":
    main()
