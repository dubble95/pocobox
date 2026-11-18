FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir sherpa-onnx numpy

WORKDIR /app
COPY stt_server.py /app/stt_server.py

CMD ["python3", "/app/stt_server.py"]
