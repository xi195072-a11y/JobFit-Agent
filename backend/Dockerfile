# JobFit Agent backend（Phase 5）。最小镜像：只装 runtime 依赖，不装 dev extras。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖（利用 layer cache）：pyproject 是唯一依赖来源
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# 规则/提示词配置与迁移脚本（执行期按 config_dir 读取）
COPY config ./config
COPY alembic ./alembic
COPY alembic.ini ./
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

# 生产入口：先幂等 migration（保证 schema 就绪），再 uvicorn（无 fake provider；凭证经 env 注入）
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "jobfit.main:app", "--host", "0.0.0.0", "--port", "8000"]
