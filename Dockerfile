FROM python:3.13-slim

RUN useradd -r -s /bin/bash user && \
    mkdir -p /app /data /logs /home/user/archivist && \
    chown -R user:user /home/user /app /data /logs
WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

ENV DATA_DIR=/home/user/archivist
ENV CONFIG_DIR=/home/user/archivist
ENV VECTORIZER_N_FEATURES=1048576
ENV HOME=/home/user

USER user
EXPOSE 8000

CMD ["uvicorn", "archivist.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
