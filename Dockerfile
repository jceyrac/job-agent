FROM python:3.11-slim

WORKDIR /app

# System deps for lxml, JobSpy build tools
RUN apt-get update && apt-get install -y \
    gcc \
    libxml2-dev \
    libxslt-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN pip install --no-deps -e .

RUN mkdir -p /app/data

# Bake the commit SHA into the image so /health can report the running version
# (the image has no `.git`). Kept last so apt/pip layers stay cached across
# commits — only this final ENV layer varies with GIT_SHA.
ARG GIT_SHA
ENV GIT_SHA=${GIT_SHA}
