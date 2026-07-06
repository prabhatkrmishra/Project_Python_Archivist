FROM python:3.13-slim

RUN useradd -r -s /bin/bash archivist && mkdir -p /app /data /logs
WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ src/

ENV DATA_DIR=/data
ENV VECTORIZER_N_FEATURES=1048576

USER archivist
EXPOSE 8000

CMD ["uvicorn", "archivist.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
