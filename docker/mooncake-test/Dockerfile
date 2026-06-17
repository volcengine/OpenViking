FROM public.ecr.aws/ubuntu/ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG MOONCAKE_COMMIT=1352bbec43081e461356aaecf6c70cddd826b455
ARG RUST_TOOLCHAIN=1.91.1

ENV PATH=/root/.cargo/bin:${PATH} \
    CARGO_NET_GIT_FETCH_WITH_CLI=true

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    clang \
    cmake \
    curl \
    git \
    libasio-dev \
    libboost-all-dev \
    libcurl4-openssl-dev \
    libgflags-dev \
    libgoogle-glog-dev \
    libgrpc++-dev \
    libgrpc-dev \
    libgtest-dev \
    libhiredis-dev \
    libibverbs-dev \
    libjemalloc-dev \
    libjsoncpp-dev \
    libmsgpack-dev \
    libnuma-dev \
    libprotobuf-dev \
    libpython3-dev \
    libssl-dev \
    libunwind-dev \
    liburing-dev \
    libxxhash-dev \
    libyaml-cpp-dev \
    libzstd-dev \
    ninja-build \
    pkg-config \
    protobuf-compiler-grpc \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --profile minimal --default-toolchain "${RUST_TOOLCHAIN}"

RUN git clone --filter=blob:none https://github.com/kvcache-ai/Mooncake.git /opt/Mooncake \
    && cd /opt/Mooncake \
    && git checkout "${MOONCAKE_COMMIT}" \
    && git submodule update --init --recursive

RUN cmake -S /opt/Mooncake/extern/yalantinglibs \
        -B /opt/Mooncake/extern/yalantinglibs/build \
        -G Ninja \
        -DBUILD_EXAMPLES=OFF \
        -DBUILD_BENCHMARK=OFF \
        -DBUILD_UNIT_TESTS=OFF \
    && cmake --build /opt/Mooncake/extern/yalantinglibs/build --parallel 2 \
    && cmake --install /opt/Mooncake/extern/yalantinglibs/build

RUN cmake -S /opt/Mooncake -B /opt/Mooncake/build -G Ninja \
        -DWITH_STORE=ON \
        -DWITH_STORE_RUST=ON \
        -DWITH_P2P_STORE=OFF \
        -DUSE_TCP=ON \
        -DUSE_HTTP=ON \
        -DUSE_ETCD=OFF \
        -DSTORE_USE_ETCD=OFF \
        -DUSE_REDIS=OFF \
        -DSTORE_USE_REDIS=OFF \
        -DUSE_CUDA=OFF \
        -DUSE_MNNVL=OFF \
        -DUSE_UB=OFF \
        -DUSE_CXL=OFF \
        -DBUILD_UNIT_TESTS=OFF \
        -DBUILD_EXAMPLES=OFF \
        -DBUILD_BENCHMARK=OFF \
        -DWITH_METRICS=OFF \
        -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /opt/Mooncake/build \
        --target build_mooncake_store_rust mooncake_master \
        --parallel 2

RUN python3 -m pip install --no-cache-dir aiohttp

COPY run-smoke.sh /usr/local/bin/run-openviking-mooncake-smoke
RUN chmod +x /usr/local/bin/run-openviking-mooncake-smoke

WORKDIR /workspace/OpenViking
ENTRYPOINT ["/usr/local/bin/run-openviking-mooncake-smoke"]
