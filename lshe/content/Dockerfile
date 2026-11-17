# Tools for preparing LSHE video channels: ffmpeg (conversion) and
# ricecooker (upload to Kolibri Studio). Build once:
#   docker build -t lshe-content-tools lshe/content
FROM python:3.11-slim

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir ricecooker==0.8.0

WORKDIR /work
