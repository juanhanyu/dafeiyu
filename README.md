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

一条 Q 版蓝色大肥鱼，头上扣着不锈钢盆，最喜欢主人。纯聊天：

- 像朋友发消息一样回复，一般 1～2 行，内容多时最多 3～4 行
- 能看图，会发大肥鱼表情包（`stickers/`）
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
- `atrei/tools.py` 表情包工具
- `atrei/hf.py` 托管平台用的状态页和保活
- `stickers/make_stickers.py` 重新画表情包；用同名 png 覆盖即可换成自己的图
