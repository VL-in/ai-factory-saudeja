FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ ./requirements/
RUN pip install --no-cache-dir -r requirements/train.txt

COPY src/ ./src/
COPY tests/ ./tests/
COPY pytest.ini .
COPY params.yaml .

# Sem CMD/ENV de stage default -- imagem compartilhada pelas 3 etapas do
# pipeline (preprocess/train/validate), cada uma invocada explicitamente
# via "docker run ... saudeja-train python src/<etapa>.py" (ver dvc.yaml).
ENTRYPOINT ["python"]
