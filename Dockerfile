FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 TZ=Asia/Shanghai
WORKDIR /app

COPY requirements.txt .
# ffmpeg 用来把语音转成 QQ 要的格式；pilk（silk 编码）没有预编译包，装完依赖再把编译器删掉
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg gcc g++ \
 && pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y gcc g++ && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY . .

CMD ["python", "main.py"]
