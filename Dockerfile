FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./

RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev \
    && rm -rf /root/.cache

ENV PATH="/app/.venv/bin:$PATH"

COPY . .

RUN playwright install firefox

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
