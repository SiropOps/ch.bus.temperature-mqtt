FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgpiod3 gcc libc6-dev \
    && pip install --no-cache-dir \
        paho-mqtt==2.1.0 adafruit-circuitpython-dht RPi.GPIO \
    && apt-get purge -y --auto-remove gcc libc6-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py /app/app.py

CMD ["python", "app.py"]
