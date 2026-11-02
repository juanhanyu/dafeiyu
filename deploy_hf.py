"""一键部署 / 更新到 Hugging Face Space：python deploy_hf.py
需要 .env 里有 HF_TOKEN（Write 权限）。会创建私有 Space「<用户名>/dafeiyu」和私有数据集「<用户名>/dafeiyu-data」，
把 .env 里的配置写成 Space 的 secrets / variables，再上传代码。
"""
import sys
from pathlib import Path

from dotenv import dotenv_values
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent
SECRETS = ["QQ_APPID", "QQ_SECRET", "LLM_API_KEY", "OWNER_ID", "HF_TOKEN"]
VARIABLES = ["LLM_BASE_URL", "LLM_MODEL", "LLM_VISION", "BOT_NAME", "BOT_PERSONA", "TIMEZONE",
             "HISTORY_TURNS", "QQ_SANDBOX"]


def main():
    env = dotenv_values(ROOT / ".env")
    token = env.get("HF_TOKEN")
    if not token:
        sys.exit("请先在 .env 里填 HF_TOKEN")
    api = HfApi(token=token)
    user = api.whoami()["name"]
    space = f"{user}/dafeiyu"
    data_repo = f"{user}/dafeiyu-data"

    api.create_repo(space, repo_type="space", space_sdk="docker", private=True, exist_ok=True)
    api.create_repo(data_repo, repo_type="dataset", private=True, exist_ok=True)

    for key in SECRETS:
        if env.get(key):
            api.add_space_secret(space, key, env[key])
    for key in VARIABLES:
        if env.get(key):
            api.add_space_variable(space, key, env[key])
    api.add_space_variable(space, "HF_BACKUP_REPO", data_repo)

    api.upload_folder(
        repo_id=space, repo_type="space", folder_path=str(ROOT),
        ignore_patterns=[".env", ".venv/*", "data/*", "bot.log", "**/__pycache__/*", "*.bat", "run.ps1",
                         "stickers/avatar_user.jpg", "stickers/avatar_small.jpg"],
        commit_message="deploy",
    )
    print(f"已部署：https://huggingface.co/spaces/{space}")


if __name__ == "__main__":
    main()
