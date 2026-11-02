"""联网搜索（Tavily）：平时不搜，遇到不懂又关键的信息才查。没配置 TAVILY_API_KEY 时自动关闭。"""
import aiohttp

API = "https://api.tavily.com/search"

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "上网查资料。一般不用！只有遇到你不知道、又对回答很关键的信息才用："
                       "最近的新闻和事件、具体的人/作品/游戏/梗、比赛结果、价格、天气这类你不确定或可能过时的事实。"
                       "闲聊、常识、讲道理、你本来就知道的东西都不要搜。",
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
            "note": "照这些资料用你自己的口吻简短回答；资料里没有就说不确定，不要编；不要贴网址。",
        }
