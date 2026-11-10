"""Render pilot result JSONs into Markdown tables (results/tables.md). All report numbers come from here."""
import json, os

import sys
R = os.path.join(os.path.dirname(__file__), "..", "results")
TAG = sys.argv[1] if len(sys.argv) > 1 else ""


def load(e):
    p = os.path.join(R, f"pilot_{e}{TAG}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def f(x, d=1):
    if x is None:
        return "—"
    if isinstance(x, float):
        if abs(x) >= 1000:
            return f"{x:,.0f}"
        return f"{x:.{d}f}"
    return str(x)


out = []
e1 = load("E1")
if e1:
    out.append("## E1 single query (Q1), p50 latency in microseconds (agreement with brute force in brackets)\n")
    meths = ["B1_brute", "B4_precompute", "B3_rtree", "B6_mip", "RQ_base", "RQ_plus"]
    out.append("| instance/workload | n | " + " | ".join(meths) + " | evals RQ_base | evals RQ_plus | speedup RQ_plus vs B1 | speedup best-RQ vs B1 |")
    out.append("|" + "---|" * (len(meths) + 6))
    for k, v in e1.items():
        if k.startswith("_"):
            continue
        cells = []
        for m in meths:
            if m in v:
                cells.append(f"{f(v[m]['p50_us'])} ({v[m]['agree']:.2f})")
            else:
                cells.append("—")
        best = min(v["RQ_base"]["p50_us"], v["RQ_plus"]["p50_us"])
        out.append(f"| {k} | {v['n']} | " + " | ".join(cells) +
                   f" | {f(v['RQ_base']['evals'], 2)} | {f(v['RQ_plus']['evals'], 2)} | "
                   f"{v['B1_brute']['p50_us'] / v['RQ_plus']['p50_us']:.1f}× | {v['B1_brute']['p50_us'] / best:.1f}× |")
    out.append("\np95 (microseconds):\n")
    out.append("| instance/workload | " + " | ".join(meths) + " |")
    out.append("|" + "---|" * (len(meths) + 1))
    for k, v in e1.items():
        if k.startswith("_"):
            continue
        out.append(f"| {k} | " + " | ".join(f(v[m]["p95_us"]) if m in v else "—" for m in meths) + " |")
    rt = {k: v["B3_rtree"] for k, v in e1.items() if not k.startswith("_") and "B3_rtree" in v}
    if rt:
        out.append("\nR-tree (one origin only): " + "; ".join(f"{k}: {v['boxes']:,} boxes, build {v['build_s']:.1f}s" for k, v in rt.items()))

e2 = load("E2")
if e2:
    out.append("\n## E2 batch (Q2): seconds for N parcels (agreement with brute force)\n")
    meths = ["B1_brute_vec", "B2_duckdb_t1", "B2_duckdb_t8", "B5_kinetic", "RQ_base", "RQ_plus_cold", "RQ_plus_warm"]
    out.append("| instance/workload | n | N | " + " | ".join(meths) + " | evals/parcel RQ_plus_warm | RQ_plus_warm vs B1 | vs DuckDB-t1 | vs DuckDB-t8 |")
    out.append("|" + "---|" * (len(meths) + 7))
    for k, v in e2.items():
        if k.startswith("_"):
            continue
        cells = [f"{f(v[m]['s'], 2)} ({v[m]['agree']:.3f})" for m in meths]
        w = v["RQ_plus_warm"]["s"]
        out.append(f"| {k} | {v['n']} | {v['N']:,} | " + " | ".join(cells) +
                   f" | {f(v['RQ_plus_warm']['evals_per_parcel'], 2)} | {v['B1_brute_vec']['s'] / w:.1f}× | "
                   f"{v['B2_duckdb_t1']['s'] / w:.1f}× | {v['B2_duckdb_t8']['s'] / w:.1f}× |")
    out.append("\nB2 = DuckDB with ASOF joins for every interval lookup; at n=1000 measured on 20,000 parcels and scaled linearly to N.")
    nv = {k: v["B2_naive_t1"]["s"] for k, v in e2.items() if not k.startswith("_") and "B2_naive_t1" in v}
    if nv:
        out.append("B2_naive (BETWEEN range joins, 1 thread, measured on 5,000 parcels, scaled): " + "; ".join(f"{k}: {f(x, 1)} s" for k, x in nv.items()))

e3 = load("E3")
if e3:
    out.append("\n## E3 memory and build\n")
    cols = ["n", "signatures", "local_signatures", "groups", "elem_intervals", "raw_rules_MB", "RQ_base_MB", "RQ_base_build_s",
            "RQ_plus_MB_after_200k", "RQ_plus_rows_after_200k", "RQ_plus_200k_incl_lazy_s", "precompute_MB", "precompute_build_s"]
    out.append("| instance | " + " | ".join(cols) + " |")
    out.append("|" + "---|" * (len(cols) + 1))
    for k, v in e3.items():
        if k.startswith("_"):
            continue
        out.append(f"| {k} | " + " | ".join(f(v[c], 2) for c in cols) + " |")

e4 = load("E4")
if e4:
    out.append("\n## E4 updates\n")
    out.append("| instance | n | price edit RQ_base mean/p95 µs | RQ_plus invalidate mean µs | DuckDB UPDATE mean µs | rows touched | RQ_base rebuild s | precompute rebuild s | exact after price edits (base/plus) | fuel edit µs | zone edit mean µs | new signatures (50 edits) | exact after zone edits |")
    out.append("|" + "---|" * 13)
    for k, v in e4.items():
        if k.startswith("_"):
            continue
        pe = v["price_edit"]
        out.append(f"| {k} | {v['n']} | {f(pe['RQ_base']['mean_us'])}/{f(pe['RQ_base']['p95_us'])} | {f(pe['RQ_plus_invalidate']['mean_us'])} | "
                   f"{f(pe['duckdb_update']['mean_us'])} | {f(pe['rows_touched_mean'])} | {f(v['RQ_base_rebuild_s'], 3)} | "
                   f"{f(v['precompute_rebuild_s'], 3)} | {v['exact_after_price_edits']['RQ_base']:.3f}/{v['exact_after_price_edits']['RQ_plus']:.3f} | "
                   f"{f(v['fuel_edit_us'])} | {f(v['zone_edit']['mean_us'])} | {v['zone_new_signatures']} | {v['exact_after_zone_edits']:.3f} |")

e5 = load("E5")
if e5:
    out.append("\n## E5 historical maintenance (Q3)\n")
    out.append("| instance | n | N | full recompute s | edit type | count | ours mean s | support | recomputed | service-level mean s | service-level support | speedup vs full | exact final state |")
    out.append("|" + "---|" * 13)
    for k, v in e5.items():
        if k.startswith("_"):
            continue
        for et, d in v["edits"].items():
            out.append(f"| {k} | {v['n']} | {v['N']:,} | {f(v['full_recompute_s'], 2)} | {et} | {d['count']} | {f(d['ours_s'], 4)} | "
                       f"{f(d['support'])} | {f(d['recomputed'], 1)} | {f(d.get('service_level_s'), 3)} | {f(d.get('service_support'))} | "
                       f"{v['full_recompute_s'] / d['ours_s']:.0f}× | {v['exact_final_state']} |")

open(os.path.join(R, f"tables{TAG}.md"), "w").write("\n".join(out) + "\n")
print("\n".join(out))
