"""Aggregate native NGINX JSON access logs into request/upstream evidence."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from benchmark import latency_report


def summarize(path: Path, offset: int = 0) -> dict:
    addresses = Counter()
    statuses = Counter()
    latency = []
    upstream_latency = defaultdict(list)
    invalid_lines = 0
    with path.open(encoding="utf-8") as stream:
        stream.seek(offset)
        for line in stream:
            try:
                row = json.loads(line)
                statuses[str(row["status"])] += 1
                latency.append(float(row["request_seconds"]) * 1000)
                address = row.get("upstream", "")
                if address:
                    addresses[address] += 1
                    try:
                        upstream_latency[address].append(float(row["upstream_seconds"]) * 1000)
                    except ValueError:
                        pass  # NGINX uses comma-separated values for retried upstreams.
            except (ValueError, KeyError, TypeError):
                invalid_lines += 1
    return {"requests": sum(statuses.values()), "http_status": dict(statuses), "upstream_requests": dict(addresses),
            "request_latency": latency_report(latency),
            "upstream_latency": {key: latency_report(values) for key, values in upstream_latency.items()},
            "invalid_log_lines": invalid_lines}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, nargs="?", default=Path("deploy/runtime/logs/access.log"))
    args = parser.parse_args()
    print(json.dumps(summarize(args.path), indent=2))


if __name__ == "__main__":
    main()
