"""CLI: python -m app.demo [--reset | --status] [--pause] [--api URL]

Runs the deterministic Demo Mode against a running LogForge API over HTTP.
Every run begins with a scoped reset, so reruns never accumulate demo state.
"""
from __future__ import annotations

import argparse
import json
import sys

import httpx

from app.demo.runner import DemoRunner, reset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.demo", description="LogForge AI reproducible Demo Mode")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL (default: %(default)s)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--reset", action="store_true", help="remove demo-owned state only, then exit")
    group.add_argument("--status", action="store_true", help="print the demo progress status and exit")
    parser.add_argument("--pause", action="store_true", help="wait for Enter at every human decision")
    args = parser.parse_args(argv)

    with httpx.Client(base_url=args.api, timeout=120) as client:
        if args.reset:
            return 0 if reset(client) else 1
        if args.status:
            st = client.get("/api/v1/demo/status").json()
            for s in st["steps"]:
                print(f"  {s['state']:<8} {s['title']}" + (f"  ({s['detail']})" if s["detail"] else ""))
            print(json.dumps({"next": st["next"], "complete": st["complete"]}))
            return 0

        def decide(prompt: str) -> None:
            if args.pause:
                input(f"  >>> HUMAN DECISION REQUIRED: {prompt}. Press Enter to approve as demo-operator... ")
            else:
                print(f"  >>> HUMAN DECISION: {prompt} (scripted by demo-operator)")

        return 0 if DemoRunner(client, decide=decide).run() else 1


if __name__ == "__main__":
    sys.exit(main())
