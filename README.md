---
title: 大肥鱼
emoji: 🐟
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# 大肥鱼~ —— QQ 聊天机器人

（上面 `---` 之间是 Hugging Face Space 的配置，别删。）

大肥鱼：拟人化的 DeepSeek 鲸鱼娘，最喜欢主人。纯聊天：

- 像朋友发消息一样回复，一般 1～2 行，内容多时最多 3～4 行
- 能看图，会发大肥鱼表情包（频率像真人：平均四五条消息一张，发过后至少隔两条；主人要表情包时随时发）
- 认得主人（`OWNER_ID`），对主人特别黏
- 不做提醒、笔记、待办；被要求时会老实说做不到

快捷指令：`/清空`（忘掉当前对话）、`/帮助`

## 24 小时在线（Render 免费版）

部署在 Render 的免费 Web Service（新加坡），代码来自 GitHub 仓库 `juanhanyu/dafeiyu`，推送到 `main` 就会自动重新部署。

- 密钥（`QQ_APPID`、`QQ_SECRET`、`LLM_API_KEY`、`OWNER_ID` 等）放在 Render 的 Environment 里，不进代码。
- 免费版 15 分钟没访问会休眠，所以鱼鱼每 10 分钟访问一次自己的网址保活。
- 状态页：https://dafeiyu.onrender.com

## 在自己电脑上跑

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

把 `.env.example` 复制成 `.env` 填好，然后：

- `.venv\Scripts\python cli.py`：在终端里聊（不连 QQ），`/图 路径` 发图片
- `.venv\Scripts\python main.py`：连 QQ。**Render 上的鱼鱼在线时别再在电脑上开一只**，否则两只会同时回复

## 文件说明

- `main.py` QQ 机器人入口；`cli.py` 终端聊天
- `atrei/brain.py` 人设和说话规则（想改性格、回复长度就改这里的 `SYSTEM`）
- `atrei/tools.py` 表情包挑图和发图频率（`COOLDOWN_REPLIES`、`CHANCE`）
- `atrei/stickers.json` 表情包索引，`python tools/build_stickers.py` 重新生成
- `atrei/hf.py` 托管平台用的状态页和保活

## 表情包来源与授权

表情包图片不在本仓库里，发送时直接引用原作者托管的地址，仅限非商业使用。

- 322 张动图来自 [dsh-nekowhale-stickers](https://github.com/Loping151/dsh-nekowhale-stickers)（标签数据 CC BY-NC 4.0）。
  表情包作者：B站 [可以叫我云大人](https://space.bilibili.com/103739)，[kiriyuki.com](https://kiriyuki.com) 制作，欢迎约原稿同款表情包！
  大肥鱼角色设计基于 [ZipZipPipe](https://space.bilibili.com/4168597) 与 [上善无形](https://space.bilibili.com/4456176)。
- 24 张静态图来自 [dsh-meme](https://github.com/yyh-001/dsh-meme) 的「大肥鱼」包（个人使用）。
