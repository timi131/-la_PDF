FROM python:3.12-slim

# шрифты с кириллицей — для водяного знака, номеров страниц и документов;
# LibreOffice Writer — для превращения документов Word (.docx) в PDF.
# Если apt недоступен, сборка не падает: документы будут только в DOCX.
RUN (apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core fonts-liberation2 \
       fonts-crosextra-carlito libreoffice-writer-nogui \
    && rm -rf /var/lib/apt/lists/*) || echo "WARNING: шрифты/LibreOffice не установлены"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8501 PYTHONUNBUFFERED=1
EXPOSE 8501

# слушаем 0.0.0.0 и порт из переменной PORT (по умолчанию 8501)
CMD ["sh", "-c", "streamlit run app.py --server.address=0.0.0.0 --server.port=${PORT:-8501} --server.headless=true"]
