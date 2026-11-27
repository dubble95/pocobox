FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# sherpa-onnx >= 2.0.2 preserves spaces in Korean ASR output (k2-fsa/sherpa-onnx#4132)
# and fixes the Supertonic "na" language tag (k2-fsa/sherpa-onnx#3958).
RUN pip install --no-cache-dir "sherpa-onnx>=2.0.2" numpy

WORKDIR /app
COPY stt_server.py /app/stt_server.py

CMD ["python3", "/app/stt_server.py"]
