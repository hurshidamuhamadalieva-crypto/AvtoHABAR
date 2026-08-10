# ─── 1-bosqich: kutubxonalarni yig'ish (gcc shu yerda qoladi, final image'ga tushmaydi) ───
FROM python:3.10-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --user --no-cache-dir -r requirements.txt

# ─── 2-bosqich: yakuniy, yengil image (faqat kerakli narsalar) ───────────────
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/root/.local/bin:$PATH

WORKDIR /app

# Faqat o'rnatilgan Python paketlarini ko'chiramiz — gcc/build-essential
# va apt keshi umuman final image'da qolmaydi, hajm sezilarli kichrayadi.
COPY --from=builder /root/.local /root/.local

COPY . .

CMD ["python", "main.py"]
