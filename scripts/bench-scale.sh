#!/usr/bin/env bash
# Step 10B horizontal-scaling benchmark wrapper (benchmark-only; the dev stack must be up for its PostgreSQL).
#
#   bash scripts/bench-scale.sh up <N> [--scheduler]     start nginx + N identical API replicas + load generator
#   bash scripts/bench-scale.sh bench <harness args...>  run `python -m logforge_bench ...` in the load-generator
#                                                        container while sampling docker stats of every container
#   bash scripts/bench-scale.sh failure <kill_at_s> <restart_at_s> <replica_index> <harness args...>
#                                                        same as bench, and stop/start one replica at the given
#                                                        seconds after start (timeline -> failure_events.jsonl)
#   bash scripts/bench-scale.sh status | down
set -euo pipefail
cd "$(dirname "$0")/.."

F=backend/logforge_bench/scaling/docker-compose.scaling.yml
DC=(docker compose -f "$F")
OUT=backend/logforge_bench/results/scaling
mkdir -p "$OUT"

replica_map() {
  # IP -> container name on the shared network, read by the harness to label nginx upstream addresses
  local first=1
  printf '{' > "$OUT/replica_map.json"
  for c in $(docker ps --filter "name=logforge-scale-api-" --format '{{.Names}}' | sort); do
    ip=$(docker inspect -f '{{with index .NetworkSettings.Networks "logforge-ai_default"}}{{.IPAddress}}{{end}}' "$c")
    [ $first -eq 1 ] || printf ',' >> "$OUT/replica_map.json"
    printf '"%s":"%s"' "$ip" "$c" >> "$OUT/replica_map.json"
    first=0
  done
  printf '}\n' >> "$OUT/replica_map.json"
  echo "[scale] replica map: $(cat "$OUT/replica_map.json")"
}

host_env() {
  local commit dirty
  commit="$(git rev-parse HEAD 2>/dev/null || echo 'NOT RECORDED')"
  dirty="$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  ENVARGS=(-e "LOGFORGE_BENCH_GIT_COMMIT=$commit" -e "LOGFORGE_BENCH_GIT_DIRTY=$([ "$dirty" != 0 ] && echo 1 || echo 0)"
           -e "LOGFORGE_BENCH_GIT_DIRTY_FILES=$dirty"
           -e "LOGFORGE_BENCH_DOCKER_VERSION=$(docker version --format 'client {{.Client.Version}} / server {{.Server.Version}}' 2>/dev/null)"
           -e "LOGFORGE_BENCH_DOCKER_INFO=$(docker info --format '{{.OperatingSystem}}; {{.NCPU}} CPUs; {{.MemTotal}} bytes; kernel {{.KernelVersion}}' 2>/dev/null); $(docker compose version --short 2>/dev/null | sed 's/^/compose /')"
           -e "LOGFORGE_BENCH_HOST_OS=$(powershell.exe -NoProfile -Command '(Get-CimInstance Win32_OperatingSystem).Caption + " " + (Get-CimInstance Win32_OperatingSystem).Version' 2>/dev/null | tr -d '\r')"
           -e "LOGFORGE_BENCH_HOST_CPU=$(powershell.exe -NoProfile -Command '$c = Get-CimInstance Win32_Processor | Select-Object -First 1; "$($c.Name.Trim()); $($c.NumberOfCores) cores / $($c.NumberOfLogicalProcessors) logical"' 2>/dev/null | tr -d '\r')"
           -e "LOGFORGE_BENCH_HOST_RAM=$(powershell.exe -NoProfile -Command '"{0:N1} GiB" -f ((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)' 2>/dev/null | tr -d '\r')")
}

start_sampler() {
  STATS="$OUT/docker_stats-$(date -u +%Y%m%dT%H%M%SZ).jsonl"
  ( while true; do
      names=$(docker ps --filter "name=logforge-scale-" --format '{{.Names}}'; echo logforge-db)
      docker stats --no-stream --format '{{json .}}' $names 2>/dev/null \
        | sed "s/^{/{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",/" >> "$STATS" || true
      sleep 1
    done ) &
  SAMPLER=$!
  trap 'kill $SAMPLER 2>/dev/null || true' EXIT
  echo "[scale] docker stats -> $STATS"
}

harness() {
  MSYS_NO_PATHCONV=1 "${DC[@]}" exec -T "${ENVARGS[@]}" bench python -m logforge_bench "$@"
}

cmd="${1:-status}"; shift || true
case "$cmd" in
  up)
    n="${1:?replica count}"; shift || true
    sched=false; [ "${1:-}" = "--scheduler" ] && sched=true
    SCHEDULER_ENABLED=$sched "${DC[@]}" up -d --scale api="$n" --wait api
    SCHEDULER_ENABLED=$sched "${DC[@]}" up -d --scale api="$n" --wait lb bench
    "${DC[@]}" restart lb >/dev/null
    sleep 2
    replica_map
    "${DC[@]}" ps --format '{{.Name}} {{.Status}}'
    ;;
  bench)
    host_env; start_sampler
    harness "$@"
    ;;
  failure)
    kill_at="$1"; restart_at="$2"; idx="$3"; shift 3
    host_env; start_sampler
    victim="logforge-scale-api-$idx"
    FEV="$OUT/failure_events.jsonl"
    ( sleep "$kill_at"
      echo "{\"epoch\": $(date +%s.%N | cut -c1-14), \"event\": \"stop_requested\", \"container\": \"$victim\"}" >> "$FEV"
      docker kill "$victim" >/dev/null
      echo "{\"epoch\": $(date +%s.%N | cut -c1-14), \"event\": \"killed\", \"container\": \"$victim\"}" >> "$FEV"
      echo "[scale] $(date -u +%T) KILLED $victim"
      sleep $((restart_at - kill_at))
      echo "{\"epoch\": $(date +%s.%N | cut -c1-14), \"event\": \"start_requested\", \"container\": \"$victim\"}" >> "$FEV"
      docker start "$victim" >/dev/null
      for i in $(seq 1 60); do
        [ "$(docker inspect -f '{{.State.Health.Status}}' "$victim")" = healthy ] && break; sleep 1
      done
      echo "{\"epoch\": $(date +%s.%N | cut -c1-14), \"event\": \"healthy_again\", \"container\": \"$victim\", \"health\": \"$(docker inspect -f '{{.State.Health.Status}}' "$victim")\"}" >> "$FEV"
      echo "[scale] $(date -u +%T) RESTARTED $victim ($(docker inspect -f '{{.State.Health.Status}}' "$victim"))"
    ) &
    INJ=$!
    harness "$@"
    wait $INJ || true
    replica_map
    ;;
  status) "${DC[@]}" ps --format '{{.Name}} {{.Status}}' ;;
  down) "${DC[@]}" down ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
