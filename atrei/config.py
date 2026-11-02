"""从 .env 读取配置。"""
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    bot_name: str = os.getenv("BOT_NAME", "Atrei")
    persona: str = os.getenv("BOT_PERSONA", "")
    owner_id: str = os.getenv("OWNER_ID", "")
    tz: ZoneInfo = ZoneInfo(os.getenv("TIMEZONE", "Asia/Shanghai"))
    history_turns: int = int(os.getenv("HISTORY_TURNS", "10"))

    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.anthropic.com/v1/")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
    llm_vision: bool = _bool("LLM_VISION", True)
    # 思考也算在 max_tokens 里，开最高思考时要留足；回复长短由人设里的规则控制
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "8000"))
    # 思考等级（DeepSeek：low / high / max）；留空则不传，换别家模型时用
    llm_reasoning_effort: str = os.getenv("LLM_REASONING_EFFORT", "max")

    qq_appid: str = os.getenv("QQ_APPID", "")
    qq_secret: str = os.getenv("QQ_SECRET", "")
    qq_sandbox: bool = _bool("QQ_SANDBOX", False)


def load() -> Config:
    cfg = Config()
    return cfg
