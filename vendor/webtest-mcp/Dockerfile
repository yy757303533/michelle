# webtest-mcp-server - 读取 Excel 用例的 MCP 服务
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY projects/ ./projects/
COPY scripts/ ./scripts/
RUN pip install --no-cache-dir -e .

RUN chmod +x /app/scripts/docker-entrypoint.sh

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD []
