FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY companion ./companion
COPY web ./web
RUN useradd --create-home companion && mkdir /app/data && chown -R companion:companion /app
USER companion
ENV COMPANION_HOST=0.0.0.0 COMPANION_PORT=8765 COMPANION_DB_PATH=/app/data/companion.sqlite3
EXPOSE 8765
CMD ["python", "-m", "companion"]
