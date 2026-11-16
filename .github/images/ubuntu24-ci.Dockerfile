FROM ubuntu:24.04

# Only OS dependencies: Python versions, Rust and project dependencies remain
# under the existing workflow's setup actions and lockfiles.
ENV DEBIAN_FRONTEND=noninteractive
ARG UBUNTU_MIRROR=""
COPY bootstrap-ca-certificates.crt /tmp/bootstrap-ca-certificates.crt
RUN sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources \
    && if [ -n "$UBUNTU_MIRROR" ]; then \
        case "$UBUNTU_MIRROR" in https://*) ;; *) echo "UBUNTU_MIRROR must use HTTPS" >&2; exit 1 ;; esac; \
        sed -i \
            "s|https://archive.ubuntu.com/ubuntu|${UBUNTU_MIRROR%/}|g; s|https://security.ubuntu.com/ubuntu|${UBUNTU_MIRROR%/}|g" \
            /etc/apt/sources.list.d/ubuntu.sources; \
       fi \
    && apt-get -o Acquire::https::CaInfo=/tmp/bootstrap-ca-certificates.crt \
        -o Acquire::Retries=2 -o Acquire::https::Timeout=20 \
        -o APT::Update::Error-Mode=any update \
    && apt-get -o Acquire::https::CaInfo=/tmp/bootstrap-ca-certificates.crt \
        -o Acquire::Retries=2 -o Acquire::https::Timeout=20 \
        install -y --no-install-recommends \
        git ca-certificates build-essential pkg-config curl unzip xz-utils \
        python3 libssl-dev cmake ccache ffmpeg lsof procps \
    && rm -rf /var/lib/apt/lists/* /tmp/bootstrap-ca-certificates.crt
