FROM python:3.12-slim

# шрифт с кириллицей — для водяного знака и номеров страниц
# (если apt недоступен, сборка не падает — просто будет шрифт без кириллицы)
RUN (apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*) || echo "WARNING: fonts-dejavu-core не установлен"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8501 PYTHONUNBUFFERED=1
EXPOSE 8501

# слушаем 0.0.0.0 и порт из переменной PORT (по умолчанию 8501)
CMD ["sh", "-c", "streamlit run app.py --server.address=0.0.0.0 --server.port=${PORT:-8501} --server.headless=true"]
