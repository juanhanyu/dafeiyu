"""合并两个大肥鱼表情包库，生成 atrei/stickers.json：python tools/build_stickers.py

- dsh-nekowhale-stickers（Loping151）：322 张动图，标签数据 CC BY-NC 4.0，
  图片由 可以叫我云大人/雾雪 制作，托管在 kiriyuki.com。
- dsh-meme（yyh-001）：24 张大肥鱼静态图，图片托管在 GitHub。

图片本身不进本仓库，发送时直接用原作者的网址。
"""
import json
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "atrei" / "stickers.json"
NEKO_REPO = "https://github.com/Loping151/dsh-nekowhale-stickers.git"
MEME_REPO = "https://github.com/yyh-001/dsh-meme.git"
KIRIYUKI = "https://kiriyuki.com"
MEME_RAW = "https://raw.githubusercontent.com/yyh-001/dsh-meme/main/memes/dafeiyu-001/"
FISH_PACKS = ("大肥鱼", "大肥鱼和GPT娘")

# dsh-meme 的英文分类 → nekowhale 的中文情绪，两边统一
MEME_EMOTION = {
    "happy": "开心", "angry": "生气", "sad": "难过", "shy": "害羞", "confused": "疑惑", "daily": "其他",
    "like": "喜欢", "color": "喜欢", "sleep": "疲惫", "work": "疲惫", "cpu": "疲惫", "surprised": "惊讶",
    "see": "惊讶", "sigh": "无语", "baka": "嘲讽", "fool": "拒绝", "meow": "卖萌", "morning": "打招呼",
    "givemoney": "搞怪",
}


def clone(url: str, dest: Path):
    subprocess.run(["git", "clone", "-q", "--depth", "1", url, str(dest)], check=True)


def main():
    work = Path(tempfile.mkdtemp())
    clone(NEKO_REPO, work / "neko")
    clone(MEME_REPO, work / "meme")

    meta = json.loads((work / "neko" / "meta" / "nekowhale.json").read_text(encoding="utf-8"))
    with urllib.request.urlopen(f"{KIRIYUKI}/api/manifest", timeout=60) as r:
        manifest = json.load(r)
    files = {i["id"]: i for i in manifest["items"]}

    stickers = []
    for s in meta["stickers"]:
        pack = s["id"].split("/")[0]
        item = files.get(s["id"])
        if pack not in FISH_PACKS or not item:
            continue
        url = f"{KIRIYUKI}{manifest['gif_base']}/{quote(pack)}/{quote(item['file'])}?h={item['hash']}"
        stickers.append({
            "id": s["id"], "emotion": s["emotion"], "tags": s["tags"], "use": s["use"],
            "desc": s["desc"], "dir": s["dir"], "url": url, "bytes": item["bytes"],
        })

    db = sqlite3.connect(work / "meme" / "memes" / "dafeiyu-001" / "index.db")
    for path, tag, caption, keywords in db.execute("SELECT path, tag, caption, keywords FROM memes ORDER BY path"):
        stickers.append({
            "id": f"dsh-meme/{path}", "emotion": MEME_EMOTION.get(tag, "其他"),
            "tags": [k for k in keywords.split() if k != "大肥鱼"], "use": "", "desc": caption, "dir": "",
            "url": MEME_RAW + quote(path), "bytes": 0,
        })
    db.close()

    out = {
        "attribution": meta["attribution"]["text"]
        + " 标签数据来自 https://github.com/Loping151/dsh-nekowhale-stickers（CC BY-NC 4.0）"
        "与 https://github.com/yyh-001/dsh-meme 。",
        "emotions": meta["emotions"],
        "stickers": stickers,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写入 {OUT}：{len(stickers)} 张")


if __name__ == "__main__":
    sys.exit(main())
