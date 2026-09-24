FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依赖安装（构建阶段完成，启动无需额外步骤）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# SQLite 数据目录（可挂卷持久化）；建表在服务启动时自动执行
ENV PORT=8000 \
    DATABASE_PATH=/data/weave.db
RUN mkdir -p /data

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
