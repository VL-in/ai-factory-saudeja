FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV DATA_PATH=/app/data/consultas-historicas.csv
ENV MODEL_PATH=/app/model.pkl

CMD ["python", "src/train.py"]