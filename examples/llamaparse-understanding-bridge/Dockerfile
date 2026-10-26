FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY openviking_llamaparse_bridge ./openviking_llamaparse_bridge
RUN pip install --no-cache-dir .
RUN useradd --create-home bridge

USER bridge

EXPOSE 8080

CMD ["python", "-m", "openviking_llamaparse_bridge"]
