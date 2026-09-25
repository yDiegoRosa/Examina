FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    pkg-config \
    default-libmysqlclient-dev \
    libpq-dev \
    # ── WeasyPrint: dependências nativas (Pango / Cairo / GDK-Pixbuf) ──
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    libglib2.0-0 \
    fontconfig \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Coleta os estáticos na imagem (servidos pelo WhiteNoise). As variáveis abaixo existem
# só para o settings carregar durante o build — não são usadas em execução.
RUN SECRET_KEY=build-only-collectstatic DATABASE_URL=sqlite:////tmp/build.sqlite3 \
    python manage.py collectstatic --noinput

EXPOSE 8000

# Produção: gunicorn (configuração em gunicorn.conf.py, ajustável por variáveis GUNICORN_*).
# Em desenvolvimento, o docker-compose.yml sobrescreve com `runserver`.
CMD ["gunicorn", "Examina.wsgi:application"]
