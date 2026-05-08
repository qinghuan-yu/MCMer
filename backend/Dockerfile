FROM docker.m.daocloud.io/library/python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 数学建模执行、文档导出和部分 Python 包编译依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pandoc \
    && rm -rf /var/lib/apt/lists/*

# 复制后端源码并安装依赖
COPY backend/ ./
RUN python -m pip install --upgrade pip && pip install .

# 预创建持久化目录
RUN mkdir -p /app/project/work_dir /app/.local /app/logs

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
