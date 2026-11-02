"""本地终端里跟机器人聊天（不需要 QQ）：python cli.py
发图片：/图 C:\\path\\to\\图片.png 你看这个
"""
import asyncio
import mimetypes
import sys
from pathlib import Path

from atrei import config
from atrei.brain import Brain
from atrei.tools import Context


async def main():
    cfg = config.load()
    brain = Brain(cfg)
    # 用主人的身份聊，方便试主人专属的撒娇
    ctx = Context(user_id=cfg.owner_id or "local", session_id="local", target_kind="cli", target_id="local")
    print(f"{cfg.bot_name} 本地模式。/图 路径 发图片，/清空 忘掉对话，Ctrl+C 退出。\n")
    while True:
        try:
            line = await asyncio.to_thread(input, "你 > ")
        except (EOFError, KeyboardInterrupt):
            break
        images = []
        if line.startswith("/图 ") or line.startswith("/img "):
            parts = line.split(maxsplit=2)
            path = Path(parts[1].strip('"')) if len(parts) > 1 else None
            if not path or not path.is_file():
                print("找不到图片文件\n")
                continue
            images.append((mimetypes.guess_type(path.name)[0] or "image/png", path.read_bytes()))
            line = parts[2] if len(parts) > 2 else ""
        ctx.stickers.clear()
        reply = await brain.handle(ctx, line, images)
        print(f"{cfg.bot_name} > {reply}")
        for path in ctx.stickers:
            print(f"{cfg.bot_name} > [表情包：{path['desc'][:40]}]")
        print()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
