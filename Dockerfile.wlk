FROM python:3.12-slim

RUN pip install --no-cache-dir whisperlivekit python-multipart

EXPOSE 8000

ENTRYPOINT ["wlk", "--host", "0.0.0.0", "--pcm-input", "--log-level", "WARNING"]
CMD ["--model", "small.en", "--language", "en"]