# Phone gateway: AudioSocket <-> VAD, STT, voice secretary, TTS.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 VAD_MODEL=/models/silero_vad.onnx
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && useradd --create-home --uid 10004 gateway
# Silero VAD (MIT), pinned by checksum.
ADD --checksum=sha256:9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6 https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx /models/silero_vad.onnx
RUN chmod 644 /models/silero_vad.onnx
COPY audio.py earcons.py gateway.py ./
USER gateway
EXPOSE 9092
CMD ["python", "gateway.py"]
