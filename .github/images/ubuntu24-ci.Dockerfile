FROM ubuntu:24.04

# Only OS dependencies: Python versions, Rust and project dependencies remain
# under the existing workflow's setup actions and lockfiles.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=20 update \
    && apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=20 \
        install -y --no-install-recommends \
        git ca-certificates build-essential pkg-config curl unzip xz-utils \
        python3 libssl-dev cmake ccache ffmpeg lsof procps \
    && rm -rf /var/lib/apt/lists/*

