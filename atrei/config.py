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
    # 记住最近几轮对话（一问一答算一轮）
    history_turns: int = int(os.getenv("MEMORY_TURNS", "20"))

    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.anthropic.com/v1/")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
    llm_vision: bool = _bool("LLM_VISION", True)
    # 思考也算在 max_tokens 里，开最高思考时要留足；回复长短由人设里的规则控制
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "8000"))
    # 思考等级（DeepSeek：low / high / max）；留空则不传，换别家模型时用
    llm_reasoning_effort: str = os.getenv("LLM_REASONING_EFFORT", "max")

    # 语音（BreezeBlue）：没填 Key 就不发语音；默认大肥鱼（迷糊版）
    breeze_api_key: str = os.getenv("BREEZE_API_KEY", "")
    voice_id: str = os.getenv("VOICE_ID", "voc_8yud7hz7c9q2")

    # 生图：Codex 登录信息存在这个 HF 私有数据集里（续期后写回去）
    hf_token: str = os.getenv("HF_TOKEN", "")
    hf_repo: str = os.getenv("HF_BACKUP_REPO", "")

    qq_appid: str = os.getenv("QQ_APPID", "")
    qq_secret: str = os.getenv("QQ_SECRET", "")
    qq_sandbox: bool = _bool("QQ_SANDBOX", False)


def load() -> Config:
    cfg = Config()
    return cfg
