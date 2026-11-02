FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 TZ=Asia/Shanghai DB_PATH=/data/atrei.db
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt huggingface_hub

COPY . .
# 数据库放在 /data，部署时把持久化存储挂到这里
VOLUME /data

CMD ["python", "main.py"]
