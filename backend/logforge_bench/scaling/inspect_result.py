"""Print the Step 10B-relevant parts of result.json files (benchmark helper, read-only).

    python logforge_bench/scaling/inspect_result.py <result.json or directory> [...]
"""
import glob
import json
import sys
from pathlib import Path

paths = []
for a in sys.argv[1:]:
    p = Path(a)
    paths += sorted(glob.glob(str(p / "**" / "result.json"), recursive=True)) if p.is_dir() else [str(p)]
for f in paths:
    r = json.load(open(f))
    s = r.get("summary", {})
    tp, lat = s.get("throughput", {}), s.get("latency", {})
    print(f"== {r['label']} {r['status']} {r.get('failure') or ''}")
    print(f"   events={tp.get('total_events')} eps={tp.get('events_per_sec')} p50={lat.get('p50')} p95={lat.get('p95')} "
          f"p99={lat.get('p99')} statuses={s.get('status_distribution')} http={r.get('http', {}).get('http_status_distribution')}")
    lb = r.get("load_balancer") or {}
    print(f"   lb: requests={lb.get('requests')} status={lb.get('client_status')} retried={lb.get('requests_retried_on_another_replica')} "
          f"upstream_errors={lb.get('upstream_attempt_errors')} max/min={lb.get('max_min_ratio')}")
    for k, v in (lb.get("per_replica") or {}).items():
        print(f"      {k}: {v}")
    print(f"   client_upstreams={r.get('client_upstreams')}")
    i = r.get("integrity") or {}
    keys = ("expected_events", "events_in_db", "missing_events", "unexpected_extra_events", "raw_hash_multiset_equals_sent",
            "raw_hash_multiset_consistent", "ambiguous_requests_events", "ambiguous_events_stored", "sha256_mismatches",
            "all_passed")
    print(f"   integrity: { {k: i.get(k) for k in keys if k in i} } vault={ {k: (i.get('vault') or {}).get(k) for k in ('stored', 'rehash_ok', 'rehash_mismatch', 'object_missing')} } "
          f"merkle={ {k: (i.get('merkle') or {}).get(k) for k in ('chain_valid', 'events_sealed')} }")
    st = r.get("statelessness")
    if st:
        print(f"   statelessness: { {k: v for k, v in st.items() if k != 'rows'} }")
    if r.get("failure_events"):
        print(f"   failure_events: {r['failure_events']}")
    if r.get("sustained"):
        su = r["sustained"]
        print(f"   sustained steady={su['steady_state']['events_per_sec']} verdict={su.get('verdict')}")
