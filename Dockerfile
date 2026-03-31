FROM gcr.io/oss-fuzz-base/base-image

ARG arch=x86_64
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

RUN unset CXX CC CXXFLAGS CFLAGS

# ────────────────────────────── CMake ─────────────────────────────────
ENV CMAKE_VERSION=3.26.4
RUN apt-get update && apt-get install -y wget sudo && \
    wget -q https://github.com/Kitware/CMake/releases/download/v$CMAKE_VERSION/cmake-$CMAKE_VERSION-Linux-$arch.sh && \
    chmod +x cmake-$CMAKE_VERSION-Linux-$arch.sh && \
    ./cmake-$CMAKE_VERSION-Linux-$arch.sh --skip-license --prefix="/usr/local" && \
    rm cmake-$CMAKE_VERSION-Linux-$arch.sh && \
    SUDO_FORCE_REMOVE=yes apt-get autoremove --purge -y wget sudo && \
    rm -rf /usr/local/doc/cmake /usr/local/bin/cmake-gui

# ────────────────────────────── System packages (merged) ──────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      build-essential make cmake ccache ninja-build meson \
      autoconf automake libtool pkg-config patchelf \
      gcc-multilib g++-multilib \
      git git-lfs subversion curl wget jq sudo unzip zip p7zip rsync \
      clang clang-tools binutils-dev texinfo bison flex yasm \
      python3 python3-pip python3-venv python3-distutils \
      python2-minimal python-dev-is-python2 \
      libssl-dev libkrb5-dev libsasl2-dev libsasl2-modules libldap-dev \
      libcurl4-openssl-dev libc-ares-dev libidn2-dev libnghttp2-dev \
      libssh-dev libssh2-1-dev librtmp-dev libpsl-dev \
      zlib1g-dev libbz2-dev liblzma-dev liblz4-dev libsnappy-dev libzstd-dev \
      libbrotli-dev \
      libsqlite3-dev libprotobuf-dev libprotoc-dev protobuf-compiler \
      libutf8proc-dev libre2-dev libthrift-dev rapidjson-dev nlohmann-json3-dev \
      libncurses5-dev libgdbm-dev libnss3-dev libreadline-dev libffi-dev \
      libgflags-dev libboost-all-dev libpcre3-dev libpcre2-dev \
      libcmocka-dev libbenchmark-dev libjansson-dev libmagic-dev \
      libsystemd-dev libspdlog-dev libpcap-dev libev-dev \
      qtbase5-dev libqt5core5a libqt5gui5 libqt5network5 libqt5widgets5 \
      libqt5x11extras5-dev libpng-dev libjpeg-turbo8-dev libimagequant-dev \
      libde265-dev libwebp-dev libtiff5-dev libx265-dev libheif-dev \
      libfreetype-dev libxpm-dev libraqm-dev \
      libapr1-dev libsvn-dev libopenmpi-dev libp4est-dev openmpi-bin numdiff \
      apache2-utils \
      dnsmasq-base dnsmasq-utils qemu-system-x86 qemu-utils \
      libvirt0 libvirt-dev libapparmor-dev \
      expect iproute2 iptables iputils-ping \
      libradospp-dev rados-objclass-dev \
      vim openssh-server libslang2 xterm libatm1 libxtables12 \
      graphviz libcpptest-dev shellcheck \
      lsb-release software-properties-common gnupg ca-certificates tzdata \
      default-jdk maven \
      redis-server cron gdb \
      tclsh tclx8.4-dev tcl8.6 tclx \
      cdbs dh-autoreconf devscripts check lintian valgrind scons \
    && \
    apt-get autoremove -y && apt-get clean && rm -rf /var/lib/apt/lists/*



RUN apt-get update && \
    apt-get install -y --no-install-recommends \
  autoconf automake libtool pkg-config doxygen \
  gcc g++ make m4 perl flex bison \
	libfontconfig1-dev \
    && \
    apt-get autoremove -y && apt-get clean && rm -rf /var/lib/apt/lists/*


# ────────────────────────────── Python 2/3 alternatives ───────────────
RUN update-alternatives --install /usr/bin/python python /usr/bin/python2 2 && \
    update-alternatives --install /usr/bin/python python /usr/bin/python3 1

RUN wget https://bootstrap.pypa.io/pip/2.7/get-pip.py  -O /tmp/get-pip2.py && \
    wget https://bootstrap.pypa.io/pip/3.8/get-pip.py  -O /tmp/get-pip.py  && \
    python3 /tmp/get-pip.py && \
    python3 -m pip install prettytable jmespath backoff six==1.15.0 && \
    python2 /tmp/get-pip2.py && \
    python2 -m pip install prettytable jmespath backoff six==1.15.0 && \
    rm -rf /tmp/*

# ────────────────────────────── CUDA paths ────────────────────────────
RUN echo "export PATH=/usr/local/cuda/bin:/usr/local/nvidia/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" >> /etc/profile && \
    echo "export LD_LIBRARY_PATH=/usr/local/cuda/lib64:/usr/local/nvidia/lib64:/usr/lib64:/usr/local/lib:/usr/lib:/usr/lib/x86_64-linux-gnu" >> /etc/profile

# ────────────────────────────── LLVM 16 from source ───────────────────
WORKDIR $SRC/
#RUN git clone -b llvmorg-16.0.0 --depth 1 https://github.com/llvm/llvm-project.git $SRC/llvm-project

#RUN cmake -G Ninja \
#      -DCMAKE_BUILD_TYPE=Release \
#      -DLLVM_TARGETS_TO_BUILD="X86" \
#      -DLLVM_ENABLE_PROJECTS="clang;lld;" \
#      -DLLVM_ENABLE_RUNTIMES="libcxx;libcxxabi;compiler-rt" \
#      -DLLVM_BINUTILS_INCDIR="/usr/include/" \
#      -DLLVM_BUILD_TESTS=off \
#      -DLLVM_INCLUDE_TESTS=off \
#      -DCOMPILER_RT_INCLUDE_TESTS=OFF \
#      $SRC/llvm-project/llvm && \
#    ninja && ninja install && \
#    rm -fr $SRC/llvm-project


RUN curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key | apt-key add - && \
    echo "deb http://apt.llvm.org/focal/ llvm-toolchain-focal-16 main" \
      > /etc/apt/sources.list.d/llvm-16.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      clang-16 \
      lld-16 \
      llvm-16-dev \
      libc++-16-dev \
      libc++abi-16-dev \
      libclang-rt-16-dev && \
    ln -sf /usr/bin/clang-16 /usr/bin/clang && \
    ln -sf /usr/bin/clang++-16 /usr/bin/clang++ && \
    ln -sf /usr/bin/lld-16 /usr/bin/lld && \
    ln -sf /usr/bin/ld.lld-16 /usr/bin/ld.lld && \
    rm -rf /var/lib/apt/lists/*



ENV AM_I_IN_A_DOCKER_CONTAINER="Yes" CC="clang" CXX="clang++" CCC="clang++" ARCHITECTURE="x86_64"

# ────────────────────────────── SSH ───────────────────────────────────
RUN mkdir /var/run/sshd && echo "export VISIBLE=now" >> /etc/profile

# ────────────────────────────── depot_tools ────────────────────────────
RUN git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git \
      /work/depot_tools.git && \
    ln -s /work/depot_tools.git /work/depot_tools

# ────────────────────────────── Googletest ─────────────────────────────
RUN git clone -q --depth 1 -b release-1.12.1 \
      https://github.com/google/googletest.git /tmp/googletest && \
    cmake -S /tmp/googletest -B /tmp/googletest/build -GNinja \
          -DBUILD_SHARED_LIBS=ON -DINSTALL_GTEST=ON && \
    cmake --build /tmp/googletest/build && \
    cmake --install /tmp/googletest/build && \
    rm -rf /tmp/googletest && ldconfig

# ────────────────────────────── Python venv ───────────────────────────
RUN mkdir -p /src && \
    pip3 install -q uv && \
    cd /src && python3 -m venv /src/.venv

COPY defectsc_tpl/req.txt /tmp/req.txt


RUN   pip3 install --no-cache-dir -r /tmp/req.txt && \
     pip3 install --no-cache-dir \
      numpy cmake_format jinja2 pandas openai rich \
      fastapi uvicorn jmespath requests python-multipart \
      pytest pytest-asyncio pytest-tornasync pytest-trio pytest-twisted \
      anyio twisted redis asyncio \
      flask gunicorn 

RUN . /src/.venv/bin/activate && \
    uv pip install --no-cache-dir -r /tmp/req.txt && \
    uv pip install --no-cache-dir \
      numpy cmake_format jinja2 pandas openai rich \
      fastapi uvicorn jmespath requests python-multipart \
      pytest pytest-asyncio pytest-tornasync pytest-trio pytest-twisted \
      anyio twisted redis asyncio \
      flask gunicorn && \
    rm /tmp/req.txt

# ────────────────────────────── Cron (log truncation) ─────────────────
RUN printf \
'*/30 * * * * root find /out/**/logs -maxdepth 1 -type f -name '"'"'*.log'"'"' -size +10M -exec truncate --size 0 {} +\n'\
'*/30 * * * * root find /out/**/logs -maxdepth 1 -type f -name '"'"'*.msg'"'"' -size +10M -exec truncate --size 0 {} +\n'\
    > /etc/cron.d/defects4c-logrotate && \
    chmod 0644 /etc/cron.d/defects4c-logrotate && \
    crontab /etc/cron.d/defects4c-logrotate

# ────────────────────────────── Redis config & warm-up data ───────────
RUN mkdir -p /src/build_tools

COPY defectsc_tpl/build_tools/redis.conf /etc/redis/redis.conf

RUN sed -i 's|^dir .*|dir /var/lib/redis|'        /etc/redis/redis.conf && \
    sed -i 's|^supervised .*|supervised no|'        /etc/redis/redis.conf && \
    sed -i 's|^logfile .*|logfile /var/log/redis/redis-server.log|' /etc/redis/redis.conf && \
    mkdir -p /var/lib/redis /var/log/redis && \
    chown -R redis:redis /var/lib/redis /var/log/redis && \
    chmod 750 /var/lib/redis /var/log/redis

RUN pip3 install -q gdown && \
    gdown "https://drive.google.com/uc?id=1yxkR2IrXQ1VTkeRHzpGQ_zqwIotMLvte" \
          --output /var/lib/redis/dump.rdb && \
    chown redis:redis /var/lib/redis/dump.rdb && \
    chmod 660 /var/lib/redis/dump.rdb && \
    cp /var/lib/redis/dump.rdb /var/lib/redis/dump.rdb.bak && \
    chown redis:redis /var/lib/redis/dump.rdb.bak

# ────────────────────────────── Application files ─────────────────────
COPY defectsc_tpl/ /src/
COPY defectsc_tpl/buglist.txt /src/buglist.txt

RUN mkdir -p /out /workspace /patches && chmod 777 /workspace
RUN git config --global safe.directory '*'

# ────────────────────────────── Cleanup ───────────────────────────────
RUN apt-get update && \
    rm -rf /tmp/* && \
    apt-get autoremove --purge -y && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/* /usr/local/python* /usr/local/pip* && \
    rm -fr $SRC/

# ────────────────────────────── Environment ───────────────────────────
ENV SRC_DIR=/src \
    ROOT_DIR=/out \
    D4C_WORKSPACE=/workspace \
    D4C_PORT=11111 \
    D4C_TIMEOUT=1800 \
    PATCH_OUTPUT_DIR=/patches \
    PATH="/src/.venv/bin:${PATH}"

# ────────────────────────────── Entrypoint ────────────────────────────
RUN cat > /usr/local/bin/docker-entrypoint.sh <<'ENTRY'
#!/usr/bin/env bash
set -e

# ── 1. Cron ──
service cron start
echo "[entrypoint] cron started"

# ── 2. Redis ──
REDIS_CONF=/etc/redis/redis.conf
REDIS_DIR=/var/lib/redis
DUMP_FILE="${REDIS_DIR}/dump.rdb"
REDIS_LOG=/var/log/redis/redis-server.log

chown -R redis:redis "${REDIS_DIR}" /var/log/redis
chmod 750 "${REDIS_DIR}"

BACKUP="${REDIS_DIR}/dump.rdb.bak"
if [ -f "${BACKUP}" ] && [ ! -s "${DUMP_FILE}" ]; then
    echo "[entrypoint] dump.rdb is empty — restoring from backup"
    cp "${BACKUP}" "${DUMP_FILE}"
    chown redis:redis "${DUMP_FILE}"
fi

su -s /bin/sh redis -c "redis-server ${REDIS_CONF}"

echo -n "[entrypoint] Waiting for Redis to load RDB"
for i in $(seq 1 60); do
    if redis-cli ping 2>/dev/null | grep -q PONG; then
        echo ""
        break
    fi
    echo -n "."
    sleep 1
    if [ $i -eq 60 ]; then
        echo ""
        echo "[entrypoint] ERROR: Redis did not start in 60s — last log lines:"
        tail -20 "${REDIS_LOG}" || true
        exit 1
    fi
done

KEYS=$(redis-cli dbsize 2>/dev/null || echo 0)
echo "[entrypoint] Redis ready — ${KEYS} keys loaded from ${DUMP_FILE}"

if [ "${KEYS}" -gt 0 ] && [ ! -f "${BACKUP}" ]; then
    cp "${DUMP_FILE}" "${BACKUP}"
    chown redis:redis "${BACKUP}"
    echo "[entrypoint] Backup snapshot saved to ${BACKUP}"
fi

# ── 3. Activate venv & launch webapp ──
if [ -d /src/.venv ]; then
    . /src/.venv/bin/activate
fi

exec "$@"
ENTRY

RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 22 6379 11111

WORKDIR /src
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["bash", "/src/run_web.sh"]
