FROM ubuntu:20.04

# Preserve the wheel's glibc 2.31 baseline. Python versions, Rust and project
# dependencies remain under the workflow's setup actions and lockfiles.
ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC
ARG UBUNTU_MIRROR=""
COPY bootstrap-ca-certificates.crt /tmp/bootstrap-ca-certificates.crt
RUN sed -i 's|http://|https://|g' /etc/apt/sources.list \
    && if [ -n "$UBUNTU_MIRROR" ]; then \
        case "$UBUNTU_MIRROR" in https://*) ;; *) echo "UBUNTU_MIRROR must use HTTPS" >&2; exit 1 ;; esac; \
        sed -i \
            "s|https://archive.ubuntu.com/ubuntu|${UBUNTU_MIRROR%/}|g; s|https://security.ubuntu.com/ubuntu|${UBUNTU_MIRROR%/}|g" \
            /etc/apt/sources.list; \
       fi \
    && apt-get -o Acquire::https::CaInfo=/tmp/bootstrap-ca-certificates.crt \
        -o Acquire::Retries=2 -o Acquire::https::Timeout=20 \
        -o APT::Update::Error-Mode=any update \
    && apt-get -o Acquire::https::CaInfo=/tmp/bootstrap-ca-certificates.crt \
        -o Acquire::Retries=2 -o Acquire::https::Timeout=20 \
        install -y --no-install-recommends \
        git ca-certificates cmake build-essential gcc-10 g++-10 ccache tzdata curl \
        pkg-config unzip xz-utils python3 libicu66 \
        libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
        libffi-dev liblzma-dev libgdbm-dev libnss3-dev libncurses5-dev \
        libncursesw5-dev tk-dev uuid-dev libexpat1-dev \
    && ln -fs /usr/share/zoneinfo/Etc/UTC /etc/localtime \
    && dpkg-reconfigure -f noninteractive tzdata \
    && rm -rf /var/lib/apt/lists/* /tmp/bootstrap-ca-certificates.crt
