FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    AFRIBIZ_DB=/data/afribiz.db AFRIBIZ_DATA_DIR=/data AFRIBIZ_CONTENT_DIR=/app/content

WORKDIR /app
COPY pyproject.toml README.md ./
COPY afribiz ./afribiz
COPY content ./content
RUN pip install --no-cache-dir ".[all]" && useradd --create-home afribiz && mkdir -p /data && chown afribiz /data

USER afribiz
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "afribiz.web.app:main_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
