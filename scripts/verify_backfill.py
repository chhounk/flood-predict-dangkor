"""Quick sanity check on Supabase after the 2-season historical backfill.

Run with SUPABASE_URL + SUPABASE_SERVICE_KEY exported in env.
"""
import os
from supabase import create_client

c = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def fetch_all(table: str, columns: str, filters=None):
    """Page through a table past Supabase's 1000-row default cap."""
    out: list[dict] = []
    offset, step = 0, 1000
    while True:
        q = c.table(table).select(columns)
        if filters:
            for key, val in filters:
                q = getattr(q, key)(*val)
        page = q.range(offset, offset + step - 1).execute()
        rows = page.data
        if not rows:
            break
        out.extend(rows)
        if len(rows) < step:
            break
        offset += step
    return out


# ---- Row counts ---------------------------------------------------------
all_runs = fetch_all("runs", "run_id")
hist_2024 = [r for r in all_runs if r["run_id"].startswith("2024-")]
hist_2025 = [r for r in all_runs if r["run_id"].startswith("2025-")]
live_2026 = [r for r in all_runs if r["run_id"].startswith("2026-")]

print(f"runs total:        {len(all_runs)}")
print(f"  2024 historical: {len(hist_2024)} (expected 184)")
print(f"  2025 historical: {len(hist_2025)} (expected 184)")
print(f"  2026 live:       {len(live_2026)}")

cp_count = c.table("commune_predictions").select("run_id", count="exact").limit(1).execute()
print(f"commune_predictions total: {cp_count.count}  (expected ~{len(all_runs) * 13})")

p_count = c.table("predictions").select("run_id", count="exact").limit(1).execute()
print(f"predictions (per-cell):    {p_count.count}  (live runs × 1956 = {len(live_2026) * 1956})")

# ---- Level-4 events (paginated) ----------------------------------------
l4 = fetch_all(
    "commune_predictions",
    "run_id,commune_name,peak_level,peak_probability",
    filters=[("gte", ("peak_level", 4))],
)
print(f"\nLevel-4 commune events in history: {len(l4)}")
top10 = sorted(l4, key=lambda x: -x["peak_probability"])[:10]
for r in top10:
    date = r["run_id"][:10]
    name = r["commune_name"]
    prob = r["peak_probability"]
    print(f"  {date}  {name:18}  p={prob:.2f}")

# ---- Level-3+ commune-days per month (paginated) -----------------------
l3 = fetch_all(
    "commune_predictions",
    "run_id,peak_level",
    filters=[("gte", ("peak_level", 3))],
)
by_month: dict[str, int] = {}
for r in l3:
    ym = r["run_id"][:7]
    by_month[ym] = by_month.get(ym, 0) + 1
print(f"\nLevel-3+ commune-days per month (total rows: {len(l3)}):")
max_v = max(by_month.values()) if by_month else 1
for ym in sorted(by_month):
    bar_len = int(60 * by_month[ym] / max_v)
    bar = "#" * bar_len
    print(f"  {ym}: {by_month[ym]:4d} {bar}")

# ---- Any days with L4 in ALL 13 communes (basin-wide event)? -----------
l4_by_run: dict[str, int] = {}
for r in l4:
    l4_by_run[r["run_id"]] = l4_by_run.get(r["run_id"], 0) + 1
basin_wide = sorted([(k, v) for k, v in l4_by_run.items() if v >= 10], key=lambda x: -x[1])
print(f"\nDays with Level-4 in >=10 communes (potential basin-wide events): {len(basin_wide)}")
for rid, n in basin_wide[:15]:
    print(f"  {rid[:10]}: {n}/13 communes at L4")
