# Image for `ate run --mode docker`: the simulator bench runs in a container made from it.
# Build once, in this folder:  docker build -t optical-module-test-automation .
# It provides Python and the libraries; the run shares the current `ate` code into the container,
# so code changes don't need a rebuild.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install the package first (this layer is reused until the code changes).
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[plots]"

CMD ["ate", "--help"]
