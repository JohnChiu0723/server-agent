# MCDReforged + Java 21 — 主服 / 鏡像服共用映像
FROM eclipse-temurin:21-jdk-jammy

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && pip3 install --no-cache-dir "mcdreforged>=2.13.0" \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# 預設：主服 MCDR（鏡像服在 compose 中覆寫 working_dir 與 command）
CMD ["python3", "-m", "mcdreforged"]
