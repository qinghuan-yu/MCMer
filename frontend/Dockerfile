FROM docker.m.daocloud.io/library/node:20-alpine

WORKDIR /app

RUN npm install -g pnpm

# 先安装依赖，提升构建缓存命中率
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile || pnpm install

# 再复制前端源码
COPY frontend/ ./

EXPOSE 5173

CMD ["pnpm", "run", "dev", "--host", "0.0.0.0", "--port", "5173"]
