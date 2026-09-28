#!/usr/bin/env bash
# Phase 8 Step 10 benchmark wrapper: records host-side facts (git commit, Docker
# version, host OS/CPU/RAM) that are not visible inside the container, samples
# `docker stats` while the benchmark runs, and executes
# `python -m logforge_bench <args>` inside the running backend container.
#
#   bash scripts/bench.sh suite [--quick]
#   bash scripts/bench.sh sustained --mode http --workers 4 --scheduler --duration 300
#   bash scripts/bench.sh run --mode inprocess --events 1000
#   bash scripts/bench.sh report --suite logforge_bench/results/<suite> --out logforge_bench/results/report.md
#
# Requires the dev stack to be up (docker compose up -d). No internet access needed.
set -euo pipefail
cd "$(dirname "$0")/.."

cmd="${1:-}"
[ -n "$cmd" ] || { sed -n '2,12p' "$0"; exit 2; }

commit="$(git rev-parse HEAD 2>/dev/null || echo 'NOT RECORDED')"
dirty_files="$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
dirty=0; [ "$dirty_files" != "0" ] && dirty=1
docker_version="$(docker version --format 'client {{.Client.Version}} / server {{.Server.Version}}' 2>/dev/null || echo 'NOT RECORDED')"
docker_info="$(docker info --format '{{.OperatingSystem}}; {{.NCPU}} CPUs; {{.MemTotal}} bytes; kernel {{.KernelVersion}}' 2>/dev/null || echo 'NOT RECORDED')"
host_os="$(uname -srm 2>/dev/null || echo 'NOT RECORDED')"
host_cpu="NOT RECORDED"; host_ram="NOT RECORDED"
if command -v powershell.exe >/dev/null 2>&1; then
  host_os="$(powershell.exe -NoProfile -Command '(Get-CimInstance Win32_OperatingSystem).Caption + " " + (Get-CimInstance Win32_OperatingSystem).Version' 2>/dev/null | tr -d '\r' || echo "$host_os")"
  host_cpu="$(powershell.exe -NoProfile -Command '$c = Get-CimInstance Win32_Processor | Select-Object -First 1; "$($c.Name.Trim()); $($c.NumberOfCores) cores / $($c.NumberOfLogicalProcessors) logical"' 2>/dev/null | tr -d '\r' || echo 'NOT RECORDED')"
  host_ram="$(powershell.exe -NoProfile -Command '"{0:N1} GiB" -f ((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)' 2>/dev/null | tr -d '\r' || echo 'NOT RECORDED')"
elif [ -r /proc/cpuinfo ]; then
  host_cpu="$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ //'); $(nproc) logical"
  host_ram="$(awk '/MemTotal/ {printf "%.1f GiB", $2/1048576}' /proc/meminfo)"
fi

# docker stats sampler (host side) — only for measuring commands
stats_file=""
sampler_pid=""
if [ "$cmd" = "suite" ] || [ "$cmd" = "sustained" ] || [ "$cmd" = "run" ]; then
  mkdir -p backend/logforge_bench/results
  stats_file="backend/logforge_bench/results/docker_stats-$(date -u +%Y%m%dT%H%M%SZ).jsonl"
  ( while true; do
      docker stats --no-stream --format '{{json .}}' logforge-db logforge-backend 2>/dev/null \
        | sed "s/^{/{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",/" >> "$stats_file" || true
      sleep 1
    done ) &
  sampler_pid=$!
  trap '[ -n "$sampler_pid" ] && kill "$sampler_pid" 2>/dev/null || true' EXIT
fi

MSYS_NO_PATHCONV=1 docker compose exec -T \
  -e LOGFORGE_BENCH_GIT_COMMIT="$commit" \
  -e LOGFORGE_BENCH_GIT_DIRTY="$dirty" \
  -e LOGFORGE_BENCH_GIT_DIRTY_FILES="$dirty_files" \
  -e LOGFORGE_BENCH_DOCKER_VERSION="$docker_version" \
  -e LOGFORGE_BENCH_DOCKER_INFO="$docker_info" \
  -e LOGFORGE_BENCH_HOST_OS="$host_os" \
  -e LOGFORGE_BENCH_HOST_CPU="$host_cpu" \
  -e LOGFORGE_BENCH_HOST_RAM="$host_ram" \
  backend python -m logforge_bench "$@"
status=$?

if [ -n "$stats_file" ]; then
  echo "[bench] docker stats samples: $stats_file"
fi
exit $status
