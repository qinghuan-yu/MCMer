FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
RUN pip install --no-cache-dir uv

# 复制依赖文件
COPY pyproject.toml ./

# 安装 Python 依赖
RUN uv sync --frozen || uv sync

# 复制应用代码
COPY . .

# 创建虚拟环境和激活脚本
ENV PATH="/app/.venv/bin:$PATH"
ENV ENV=PROD

# 创建工作目录
RUN mkdir -p /app/project/work_dir

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
