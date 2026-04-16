"""Find local history files whose run_ids aren't in Supabase, re-ingest them.

Run after a backfill to close any gaps from transient DB failures.
"""
import json
import os
import sys
from pathlib import Path

from supabase import create_client

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.config import OUTPUT_DIR
from src.db.supabase_sink import _do_ingest

client = create_client(
    os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
)

# What run_ids does Supabase already have? (Use pagination — .execute() caps at 1000)
existing: set[str] = set()
offset, step = 0, 1000
while True:
    page = (
        client.table("runs")
        .select("run_id")
        .range(offset, offset + step - 1)
        .execute()
    )
    rows = page.data
    if not rows:
        break
    existing.update(r["run_id"] for r in rows)
    if len(rows) < step:
        break
    offset += step

print(f"Supabase has {len(existing)} runs already.")

# Walk local historical files
hist_dir = OUTPUT_DIR / "history"
missing: list[Path] = []
for fp in sorted(hist_dir.glob("202[45]-*.json")):
    run_id = fp.stem.replace("-", ":", 2)  # 2024-07-15T12-00-00Z → 2024-07-15T12:00:00Z
    # The second - is NOT supposed to be a colon... let's just read the file.
    with open(fp) as f:
        payload = json.load(f)
    if payload["run_id"] not in existing:
        missing.append(fp)

print(f"Found {len(missing)} local historical files missing from Supabase.")

if not missing:
    sys.exit(0)

# Re-ingest each missing file
ok = 0
failed: list[str] = []
for i, fp in enumerate(missing, 1):
    with open(fp) as f:
        payload = json.load(f)
    run_id = payload["run_id"]
    try:
        if _do_ingest(client, payload, skip_cells=True):
            ok += 1
            print(f"[{i}/{len(missing)}] OK {run_id}")
        else:
            failed.append(run_id)
    except Exception as e:
        failed.append(run_id)
        print(f"[{i}/{len(missing)}] FAIL {run_id}: {e}")

print(f"\nReingest complete: ok={ok}, failed={len(failed)}")
if failed:
    for f in failed:
        print(f"  failed: {f}")
    sys.exit(2)
