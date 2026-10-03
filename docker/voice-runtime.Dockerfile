FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/stay
COPY voice_runtime/requirements.txt /srv/stay/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app/schemas/self_hosted_voice.py /srv/stay/app/schemas/self_hosted_voice.py
COPY voice_runtime /srv/stay/voice_runtime
RUN useradd --uid 10001 --create-home voice
USER 10001
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
CMD ["uvicorn", "voice_runtime.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
