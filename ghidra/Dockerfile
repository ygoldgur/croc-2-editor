# Ghidra in a container, so the host needs no JDK and the repo stays clean.
#
# The image holds only Ghidra itself. The binary under analysis is mounted read-only
# at run time and never copied in, which keeps copyrighted game code out of any image
# layer as well as out of the repo.
#
# Pinned to amd64 deliberately. Ghidra's decompiler is a native binary and 12.1.3 ships
# only linux_x86_64 and win_x86_64, so on Apple Silicon this runs emulated. Croc2.exe is
# 737 KB, so the emulation cost is irrelevant, and it beats building the decompiler from
# source. The check below fails the build loudly if a future version changes this.
FROM --platform=linux/amd64 eclipse-temurin:21-jdk

ARG GHIDRA_VERSION=12.1.3
ARG GHIDRA_BUILD=20260817

RUN apt-get update \
 && apt-get install -y --no-install-recommends unzip curl ca-certificates python3 \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/ghidra.zip \
      "https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${GHIDRA_VERSION}_build/ghidra_${GHIDRA_VERSION}_PUBLIC_${GHIDRA_BUILD}.zip" \
 && unzip -q /tmp/ghidra.zip -d /opt \
 && rm /tmp/ghidra.zip \
 && mv "/opt/ghidra_${GHIDRA_VERSION}_PUBLIC" /opt/ghidra

# The decompiler is a native binary; fail loudly at build time if this platform is
# not one Ghidra ships, rather than midway through an analysis run.
RUN set -eu; \
    arch="$(uname -m)"; \
    case "$arch" in \
      x86_64)  want=linux_x86_64 ;; \
      aarch64) want=linux_arm_64 ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    test -x "/opt/ghidra/Ghidra/Features/Decompiler/os/$want/decompile" \
      || { echo "Ghidra ${GHIDRA_VERSION} ships no decompiler for $want" >&2; \
           ls /opt/ghidra/Ghidra/Features/Decompiler/os/ >&2; exit 1; }; \
    echo "decompiler present for $want"

ENV GHIDRA_HOME=/opt/ghidra
ENV PATH="/opt/ghidra/support:${PATH}"

# Headless Ghidra writes here; both are bind-mounted from the host.
VOLUME ["/project", "/out"]
WORKDIR /out

ENTRYPOINT ["analyzeHeadless"]
