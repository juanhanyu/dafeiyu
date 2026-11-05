"""联网搜索（Tavily）：拿不准就查，查了也不说。画图前由代码强制查一次外貌。没配置 TAVILY_API_KEY 时自动关闭。"""
import aiohttp

API = "https://api.tavily.com/search"

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "上网查资料。拿不准就查，别靠猜：对方提到的具体角色、人物、作品、游戏、番剧、歌、品牌、梗、"
                       "黑话，只要你不是很确定它是什么，就先查；最近的新闻和事件、比赛结果、价格、天气、版本更新这类"
                       "可能过时的事实也要查。纯闲聊、撒娇、常识、讲道理不用查。"
                       "查的时候别说出来，也不用提前说「我查一下」，直接调用。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索关键词，简短明确"}},
            "required": ["query"],
        },
    },
}


class WebSearch:
    def __init__(self, api_key: str):
        self.api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str) -> dict:
        payload = {"query": query, "max_results": 5, "include_answer": "basic", "search_depth": "basic"}
        async with aiohttp.ClientSession() as s:
            async with s.post(API, json=payload, headers={"Authorization": f"Bearer {self.api_key}"},
                              timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200:
                    return {"error": f"搜索失败 {r.status}，就按你知道的回答，别编造"}
                data = await r.json()
        # 只把要点交给模型，省 token
        return {
            "answer": data.get("answer"),
            "results": [{"title": x.get("title"), "content": (x.get("content") or "")[:300]}
                        for x in data.get("results", [])[:5]],
            "note": "当作你本来就知道的事，用自己的口吻简短回答；绝对不要说「我查到」「搜到」「网上说」「资料显示」这类话，"
                    "也不要提查过资料；资料里没有就说不太确定，不要编；不要贴网址。",
        }

    async def research_subject(self, subject: str) -> dict:
        """画图前查角色/对象的长相和性格。这里要细节，所以查得更深，每条也留得更长。"""
        payload = {"query": f"{subject} 角色设定 外貌 发色 发型 瞳色 服装 配饰 性格", "max_results": 5,
                   "include_answer": "advanced", "search_depth": "advanced"}
        async with aiohttp.ClientSession() as s:
            async with s.post(API, json=payload, headers={"Authorization": f"Bearer {self.api_key}"},
                              timeout=aiohttp.ClientTimeout(total=45)) as r:
                if r.status != 200:
                    raise RuntimeError(f"搜索失败 {r.status}")
                data = await r.json()
        return {
            "answer": data.get("answer"),
            "results": [{"title": x.get("title"), "content": (x.get("content") or "")[:800]}
                        for x in data.get("results", [])[:5]],
        }
