---
title: 大肥鱼
emoji: 🐟
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# 大肥鱼~ —— QQ 私人助理机器人

（上面 `---` 之间是 Hugging Face Space 的配置，别删。）

一个 QQ 官方机器人：能聊天、看图（题目/笔记截图）、设提醒、记待办、把内容归类进笔记本、记住你的事，还能出题考你。

## 能干什么

| 功能 | 怎么说 |
|---|---|
| 聊天 / 看图 | 直接发，或者发图问「这道题怎么做」 |
| 提醒（支持重复） | 「明早8点提醒我交作业」「每个工作日7点叫我」「半小时后提醒我关火」 |
| 待办 | 「记个待办：刷二项式定理，重要」「我还有啥没做」「第3个做完了」 |
| 笔记归类 | 「这条归哪边？」「存到 数学竞赛 > 公式与定理」「搜一下柯西」「把#5挪到知识点」 |
| 复习 | 「从数学竞赛里抽一道题考我」 |
| 长期记忆 | 「记住我高二，在准备数学联赛」（以后每次对话都会记得） |
| 小工具 | 精确计算、倒数日、帮你做选择、掷骰子 |

快捷指令（不耗 AI 额度）：`/帮助` `/提醒` `/待办` `/笔记` `/记忆` `/统计` `/清空`

## 第一步：先在电脑上玩（不用 QQ）

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

把 `.env.example` 复制成 `.env`，填上 `LLM_API_KEY`（和对应的 `LLM_BASE_URL`、`LLM_MODEL`），然后：

```bash
.venv\Scripts\python cli.py
```

发图片：`/图 C:\Users\你\Desktop\题目.png 这道题怎么做`

## 第二步：接到 QQ

1. 打开 https://q.qq.com 用 QQ 扫码，注册开发者（个人就行），创建一个机器人，起名、上传头像。
2. 「开发 > 开发设置」里拿到 **AppID** 和 **AppSecret**，填进 `.env` 的 `QQ_APPID`、`QQ_SECRET`。
3. 同一页的 **IP 白名单** 里加上运行机器人那台电脑的公网 IP（百度「IP」就能看到）。
4. 「开发 > 沙箱配置」里把你自己的 QQ 号和一个测试群加进去。没审核上线前，机器人只能在这里用（`.env` 里 `QQ_SANDBOX=true`）。
5. 运行：

```bash
.venv\Scripts\python main.py
```

看到「上线了」之后，私聊机器人，或者在测试群里 @ 它。

6. 想让别人也能用：在 q.qq.com 提交审核，过了以后把 `QQ_SANDBOX` 改成 `false`。

## 要知道的几个限制（QQ 官方的规矩）

- **群里要 @ 它才会回**，这是官方机器人的规定。私聊不用。
- **主动发消息受限**：QQ 现在基本不让机器人在群/私聊里"突然"发消息。所以到点的提醒：
  - 如果你最近 5 分钟内跟它说过话，会直接发出来；
  - 否则会先存着，**你下次找它说话时，它会先把错过的提醒告诉你**。
  - 在 QQ 频道里没有这个限制。
- **回复里不能带网址**（没在后台登记的链接会被拦），所以它不会发链接。
- 电脑关了机器人就下线了。想 24 小时在线，可以把整个文件夹放到一台云服务器上跑。

## 24 小时在线（Hugging Face Spaces）

鱼鱼部署在一个私有的 Hugging Face Docker Space 上，不需要开电脑：
- 密钥（QQ_APPID、QQ_SECRET、LLM_API_KEY 等）放在 Space 的 Settings → Variables and secrets 里，不进代码。
- 每 20 分钟访问一次自己的网址，防止闲置休眠。
- Space 重启会清空硬盘，所以数据库每 5 分钟备份到私有数据集 `HF_BACKUP_REPO`，启动时自动恢复。
- 更新代码：`python deploy_hf.py` 重新上传即可。

## 在自己电脑上长期运行（备选）

双击 `安装开机自启.bat`：鱼鱼在后台运行（没有窗口），开机自动启动，崩了 15 秒后自动重启，日志在 `bot.log`。
双击 `停止鱼鱼.bat` 让它下线。电脑睡眠/关机时鱼鱼也会下线，想真正 24 小时在线需要放到云服务器上。

## 表情包

`stickers/` 里是大肥鱼表情包（`python stickers/make_stickers.py` 重新生成），想换成自己的图，直接用同名 png 覆盖即可。

## 文件说明

- `main.py` QQ 机器人入口；`cli.py` 本地终端版
- `atrei/brain.py` 人设、对话、调用 AI；想改性格就改这里的 `SYSTEM`，或者 `.env` 的 `BOT_PERSONA`
- `atrei/tools.py` AI 能用的工具（想加新功能就在这加一个）
- `atrei/scheduler.py` 提醒调度；`atrei/storage.py` 数据库（存在 `data/atrei.db`）
