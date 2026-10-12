#!/bin/bash
# MCDR 容器啟動腳本（Temurin 21 + MCDReforged，免 docker build）
set -e

MARKER="/workspace/.mcdr-deps-installed"
RUNTIME="/workspace/.runtime"

mkdir -p "$RUNTIME"

# permission.yml 不可直接 bind-mount 單檔（MCDR 需 atomic rename）
if [ -f /workspace/permission.yml.template ]; then
  cp -f /workspace/permission.yml.template "$RUNTIME/permission.yml"
  ln -sf "$RUNTIME/permission.yml" /workspace/permission.yml
fi

# 鏡像服 MCDR 根目錄也需要可寫 permission.yml
if [ -d /workspace/Mirror/server ]; then
  mkdir -p /workspace/Mirror/server/.runtime
  if [ -f /workspace/permission.yml.template ]; then
    cp -f /workspace/permission.yml.template /workspace/Mirror/server/.runtime/permission.yml
    ln -sf /workspace/Mirror/server/.runtime/permission.yml /workspace/Mirror/server/permission.yml
  fi
fi

if [ ! -f "$MARKER" ]; then
  echo "[entrypoint] Installing Python3 + MCDReforged ..."
  apt-get update -qq
  apt-get install -y --no-install-recommends python3 python3-pip ca-certificates
  pip3 install --no-cache-dir --break-system-packages "mcdreforged>=2.13.0" 2>/dev/null \
    || pip3 install --no-cache-dir "mcdreforged>=2.13.0"
  touch "$MARKER"
  echo "[entrypoint] Dependencies installed."
fi

# Prime Backup 必需依賴（官方 requirements.txt）
# https://github.com/TISUnion/PrimeBackup
if ! python3 -c "import apscheduler, sqlalchemy, pydantic, zstandard, xxhash" 2>/dev/null; then
  echo "[entrypoint] Installing Prime Backup dependencies ..."
  pip3 install --no-cache-dir --break-system-packages \
    "APScheduler>=3.8,<4" "SQLAlchemy>=2" blake3 pathspec psutil \
    "pydantic>=2" pytz typing_extensions "xxhash>=3" zstandard \
    2>/dev/null || pip3 install --no-cache-dir \
    "APScheduler>=3.8,<4" "SQLAlchemy>=2" blake3 pathspec psutil \
    "pydantic>=2" pytz typing_extensions "xxhash>=3" zstandard
  echo "[entrypoint] Prime Backup dependencies installed."
fi

WORKDIR="${MCDR_WORKDIR:-/workspace}"
cd "$WORKDIR"
echo "[entrypoint] Starting MCDR in $WORKDIR"
exec python3 -m mcdreforged
