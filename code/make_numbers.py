"""Generate paper/numbers.tex (LaTeX macros) and paper/tables/*.tex from the result files.

Every number printed in the manuscript comes from this script. Sources (results/):
  pilot_E1_pub2.json .. pilot_E5_pub2.json   official 2026 UPS/FedEx/USPS instances (PUB2, PUB2x60/x240/x1000)
  pilot_E1.json, pilot_E2_tuned.json, pilot_E3.json   synthetic stress instance SYN200
  erp_pilot_USA.json, erp_replay_USA_after.json       enterprise rate cards (10 US services)
  cpp_E1E2_pub2.json (optional)                        same-language compiled implementation
Usage: cd code && PYTHONPATH=. ../.venv/bin/python make_numbers.py
"""
import json
import os
import re

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "results")
PAPER = os.path.join(HERE, "..", "paper")
TAB = os.path.join(PAPER, "tables")
os.makedirs(TAB, exist_ok=True)

L = lambda f: json.load(open(os.path.join(RES, f)))
def _prefer(*names):  # first existing result file (server reruns before the original runs)
    for nm in names:
        if os.path.exists(os.path.join(RES, nm)):
            return L(nm)
    raise FileNotFoundError(names)
E1 = _prefer("pilot_E1_pub2_server.json", "pilot_E1_pub2.json")
E2 = _prefer("pilot_E2_pub2_server.json", "pilot_E2_pub2.json")
E3, E4, E5 = (L(f"pilot_E{i}_pub2.json") for i in range(3, 6))
S1 = _prefer("pilot_E1_syn_server.json", "pilot_E1.json")
S2 = _prefer("pilot_E2_syn_server.json", "pilot_E2_tuned.json")
S3 = L("pilot_E3.json")
E2N = L("pilot_E2_pub2.json")  # ASOF-vs-BETWEEN plan ratio (both DuckDB plans in one M4 run; the server rerun omits the BETWEEN plan)
def _erp_file():
    """Enterprise pilot: use results/erp_pilot_USA.json only if it records an acceptable machine load
    (1-minute load <= 4); otherwise the last accepted run in results/archive_1109_prefix/."""
    d = L("erp_pilot_USA.json")
    env = d.get("_env") or {}
    timing = str(env.get("timing", "")).lower()
    if "thread" in timing and "cpu" in timing:  # thread CPU time: accepted at any machine load
        return d
    load = env.get("loadavg_at_save") or env.get("load") or env.get("loadavg_at_start")
    if load is not None and float(load[0] if isinstance(load, (list, tuple)) else load) <= 4.0:
        return d
    return L(os.path.join("archive_1109_prefix", "erp_pilot_USA.json"))


def _loads(env):
    """All 1-minute load values recorded in an _env block (lists are 1/5/15-minute triples)."""
    out = []
    for k, v in env.items():
        if "load" in k.lower():
            if isinstance(v, (list, tuple)) and v and isinstance(v[0], (int, float)):
                out.append(float(v[0]))
            elif isinstance(v, (int, float)):
                out.append(float(v))
    return out
ERP, REP = _erp_file(), L("erp_replay_USA_after.json")
_ee = ERP.get("_env") or {}
_ld = _loads(_ee)
if "thread" in str(_ee.get("timing", "")).lower() and _ld:
    ERP_TIMING_NOTE = (f" The enterprise Python runs measure thread CPU time on the M4, whose 1-minute load ranged from "
                          f"{min(_ld):.0f} to {max(_ld):.0f}.")
else:
    ERP_TIMING_NOTE = ""

# Aggregate warehouse statistics (read-only aggregate SQL over the company's 2026 shipment table;
# no per-parcel data leaves the warehouse). Only these aggregates are released.
WAREHOUSE = {"parcels_2026_M": 1.895, "warehouses": 79, "warehouses_1k": 40, "median": 11, "max": 31, "weighted": 23.2}

M = {}
M["ErpTimingNote"] = ERP_TIMING_NOTE


def num(x, d=None):
    """Consistent rounding: integers with thousands separators from 100 up, else one decimal (two below 1)."""
    x = float(x)
    if d is not None:
        s = f"{x:,.{d}f}"
    elif abs(x) >= 100:
        s = f"{x:,.0f}"
    elif abs(x) >= 1:
        s = f"{x:.1f}"
    else:
        s = f"{x:.2f}"
    return s.replace(",", "{,}")


def put(name, val, d=None):
    assert re.fullmatch(r"[A-Za-z]+", name), name
    M[name] = val if isinstance(val, str) else num(val, d)


def sp(x):  # speedup
    return num(x, 0) if x >= 100 else num(x, 1)


# ------------------------------------------------------------------ best / second-best markup for results tables
STAND = {}  # table -> {"comparisons", "best", "second", "worse", "losses"}


def _key(s_):
    return float(re.sub(r"[^0-9.\-]", "", s_.replace("{,}", "")))


def mark(vals, fmt, ours, lower=True, units=None, second_min=3, table=None, label=None, loss_cause=None):
    """Bold the best and underline the second-best printed value of one method comparison.
    vals: numbers (None = not run); fmt: one formatter or one per value; ours: indexes of RateQ methods;
    units: factors that bring printed values to a common unit; ranking uses the printed (rounded) values,
    so ties are all bold. The second best is underlined only when at least `second_min` methods compete.
    Returns the cells and records the standing of RateQ in STAND[table]."""
    fm = fmt if isinstance(fmt, (list, tuple)) else [fmt] * len(vals)
    un = units or [1.0] * len(vals)
    printed = [None if v is None else fm[i](v) for i, v in enumerate(vals)]
    keys = [None if p_ is None else _key(p_) * un[i] for i, p_ in enumerate(printed)]
    present = sorted({round(k, 12) for k in keys if k is not None}, reverse=not lower)
    best = present[0]
    second = present[1] if len(present) > 1 else None
    use2 = sum(k is not None for k in keys) >= second_min
    cells = []
    for p_, k in zip(printed, keys):
        if p_ is None:
            cells.append("---")
        elif round(k, 12) == best:
            cells.append(f"\\textbf{{{p_}}}")
        elif use2 and round(k, 12) == second:
            cells.append(f"\\underline{{{p_}}}")
        else:
            cells.append(p_)
    ok = {round(keys[i], 12) for i in ours if keys[i] is not None}
    st = "best" if best in ok else ("second" if second is not None and second in ok else "worse")
    if table is not None:
        t = STAND.setdefault(table, {"comparisons": 0, "best": 0, "second": 0, "worse": 0, "losses": []})
        t["comparisons"] += 1
        t[st] += 1
        if st != "best":
            t["losses"].append({"comparison": label, "standing": st, "cause": loss_cause})
    return cells


INST = [("PUB2", 19, "A"), ("PUB2x60", 60, "B"), ("PUB2x240", 240, "C"), ("PUB2x1000", 1000, "D")]
WL = [("olist", "Ol"), ("furn", "Fu")]

# ------------------------------------------------------------------ Q1 single query (E1)
q1_best = {}
for inst, n, tag in INST:
    for wl, wt in WL:
        r = E1[f"{inst}/{wl}"]
        b1 = r["B1_brute"]["p50_us"]
        best = min(r["RQ_base"]["p50_us"], r["RQ_plus"]["p50_us"])
        q1_best[(inst, wl)] = b1 / best
        put(f"QOne{tag}{wt}Speed", sp(b1 / best))
        put(f"QOne{tag}{wt}Brute", b1)
        put(f"QOne{tag}{wt}RQ", r["RQ_base"]["p50_us"])
        put(f"QOne{tag}{wt}RQP", r["RQ_plus"]["p50_us"])
        put(f"QOne{tag}{wt}EvalsRQ", r["RQ_base"]["evals"], 1)
        put(f"QOne{tag}{wt}EvalsRQP", r["RQ_plus"]["evals"], 2)
small = [q1_best[(i, w)] for i in ("PUB2", "PUB2x60") for w in ("olist", "furn")]
put("QOneSmallMin", sp(min(small))); put("QOneSmallMax", sp(max(small)))
large = [q1_best[(i, w)] for i in ("PUB2x240", "PUB2x1000") for w in ("olist", "furn")]
put("QOneLargeMin", sp(min(large))); put("QOneLargeMax", sp(max(large)))
put("QOneMaxSpeed", sp(max(q1_best.values())))
# RQ+ p95 stays flat
p95p = [E1[f"{i}/{w}"]["RQ_plus"]["p95_us"] for i, _, _ in INST for w, _ in WL]
put("QOnePlusPNinetyFiveMax", max(p95p))
p95b = [E1[f"{i}/{w}"]["B1_brute"]["p95_us"] for i, _, _ in INST for w, _ in WL]
put("QOneBrutePNinetyFiveMax", max(p95b))
mip = [E1[f"{i}/{w}"]["B6_mip"]["p50_us"] / E1[f"{i}/{w}"]["B1_brute"]["p50_us"] for i in ("PUB2", "PUB2x60") for w in ("olist", "furn")]
put("MipSlowMin", sp(min(mip)))
pre = [E1[f"{i}/{w}"]["B4_precompute"]["p50_us"] / min(E1[f"{i}/{w}"]["RQ_base"]["p50_us"], E1[f"{i}/{w}"]["RQ_plus"]["p50_us"])
       for i, _, _ in INST for w, _ in WL]
put("PreOverRQMin", sp(min(pre))); put("PreOverRQMax", sp(max(pre)))
put("QOneQueries", E1["PUB2/olist"]["B1_brute"]["q"], 0)
put("QOneMipQueries", E1["PUB2/olist"]["B6_mip"]["q"], 0)
put("QOneRtreeQueries", E1["PUB2/olist"]["B3_rtree"]["q"], 0)
put("RtreeBoxesA", E1["PUB2/olist"]["B3_rtree"]["boxes"], 0)
put("RtreeBoxesB", E1["PUB2x60/olist"]["B3_rtree"]["boxes"], 0)
# evaluations at n=1000
put("EvalsMaxBase", max(E1[f"{i}/{w}"]["RQ_base"]["evals"] for i, _, _ in INST for w, _ in WL), 1)
put("EvalsMaxPlus", max(E1[f"{i}/{w}"]["RQ_plus"]["evals"] for i, _, _ in INST for w, _ in WL), 2)
# SYN200
for wl, wt in WL:
    r = S1[f"SYN200/{wl}"]
    best = min(r["RQ_base"]["p50_us"], r["RQ_plus"]["p50_us"])
    put(f"SynQOne{wt}Speed", sp(r["B1_brute"]["p50_us"] / best))

# ------------------------------------------------------------------ Q2 batch (E2)
q2_best, duck_slow = {}, []
for inst, n, tag in INST:
    for wl, wt in WL:
        r = E2[f"{inst}/{wl}"]
        b1 = r["B1_brute_vec"]["s"]
        best = min(r["RQ_base"]["s"], r["RQ_plus_warm"]["s"])
        q2_best[(inst, wl)] = b1 / best
        put(f"QTwo{tag}{wt}Speed", sp(b1 / best))
        put(f"QTwo{tag}{wt}Duck", sp(r["B2_duckdb_t1"]["s"] / best))
        duck_slow.append(r["B2_duckdb_t1"]["s"] / b1)
put("QTwoN", E2["PUB2/olist"]["N"], 0)
put("QTwoSmallMin", sp(min(q2_best[(i, w)] for i in ("PUB2", "PUB2x60") for w in ("olist", "furn"))))
put("QTwoSmallMax", sp(max(q2_best[(i, w)] for i in ("PUB2", "PUB2x60") for w in ("olist", "furn"))))
put("QTwoLargeMin", sp(min(q2_best[(i, w)] for i in ("PUB2x240", "PUB2x1000") for w in ("olist", "furn"))))
put("QTwoLargeMax", sp(max(q2_best[(i, w)] for i in ("PUB2x240", "PUB2x1000") for w in ("olist", "furn"))))
put("DuckSlowMin", sp(min(duck_slow))); put("DuckSlowMax", sp(max(duck_slow)))
duck8 = [E2[f"{i}/{w}"]["B2_duckdb_t8"]["s"] / E2[f"{i}/{w}"]["B1_brute_vec"]["s"] for i, _, _ in INST for w, _ in WL]
put("DuckEightSlowMin", sp(min(duck8)))
naive = [E2N[f"{i}/{w}"]["B2_naive_t1"]["s"] / E2N[f"{i}/{w}"]["B2_duckdb_t1"]["s"] for i in ("PUB2", "PUB2x60", "PUB2x240") for w in ("olist", "furn")]
put("AsofGainMin", sp(min(naive))); put("AsofGainMax", sp(max(naive)))
duck_vs_rq = [E2[f"{i}/{w}"]["B2_duckdb_t1"]["s"] / min(E2[f"{i}/{w}"]["RQ_base"]["s"], E2[f"{i}/{w}"]["RQ_plus_warm"]["s"])
              for i, _, _ in INST for w, _ in WL]
put("DuckOverRQMin", sp(min(duck_vs_rq))); put("DuckOverRQMax", sp(max(duck_vs_rq)))
t8gain = [E2[f"{i}/{w}"]["B2_duckdb_t1"]["s"] / E2[f"{i}/{w}"]["B2_duckdb_t8"]["s"] for i, _, _ in INST for w, _ in WL]
put("DuckEightGainMin", sp(min(t8gain))); put("DuckEightGainMax", sp(max(t8gain)))
put("QTwoFuEvalsBaseC", E2["PUB2x240/furn"]["RQ_base"]["evals_per_parcel"], 1)
put("QTwoFuEvalsPlusC", E2["PUB2x240/furn"]["RQ_plus_warm"]["evals_per_parcel"], 2)
for wl, wt in WL:
    r = S2[f"SYN200/{wl}"]
    best = min(r["RQ_base"]["s"], r["RQ_plus_warm"]["s"])
    put(f"SynQTwo{wt}Speed", sp(r["B1_brute_vec"]["s"] / best))
put("DuckMeasuredOn", E2["PUB2x1000/olist"]["B2_duckdb_t1"]["measured_on"], 0)
_mo = E2["PUB2x1000/olist"]["B2_duckdb_t1"]["measured_on"]
M["DuckScaleNote"] = (f" (DuckDB at $n=1000$ on {num(_mo, 0)} parcels, scaled linearly)" if _mo < E2["PUB2x1000/olist"]["N"] else "")
_srv = os.path.exists(os.path.join(RES, "pilot_E1_pub2_server.json"))
M["PyMethodPlatform"] = ("on the Xeon server, one pinned core, with five repetitions in which the methods are interleaved" if _srv else "on the M4")
put("NaiveMeasuredOn", E2N["PUB2/olist"]["B2_naive_t1"]["measured_on"], 0)

# ------------------------------------------------------------------ memory / build (E3) and updates (E4)
put("PubSig", E3["PUB2"]["signatures"], 0); put("PubLocalSig", E3["PUB2"]["local_signatures"], 0)
put("PubGroups", E3["PUB2"]["groups"], 0); put("PubElem", E3["PUB2"]["elem_intervals"], 0)
ratios = [E3[i]["RQ_base_MB"] / E3[i]["raw_rules_MB"] for i, _, _ in INST]
put("MemRatioMin", min(ratios), 2); put("MemRatioMax", max(ratios), 2)
put("PlusRows", E3["PUB2"]["RQ_plus_rows_after_200k"], 0)
put("BuildMaxS", max(E3[i]["RQ_base_build_s"] for i, _, _ in INST), 1)
put("BaseMBD", E3["PUB2x1000"]["RQ_base_MB"], 1)
put("PlusMBD", E3["PUB2x1000"]["RQ_plus_MB_after_200k"], 0)
put("SynSig", S3["SYN200"]["signatures"], 0); put("SynLocalSig", S3["SYN200"]["local_signatures"], 0)
put("SynGroups", S3["SYN200"]["groups"], 0); put("SynElem", S3["SYN200"]["elem_intervals"], 0)
put("SynBaseMB", S3["SYN200"]["RQ_base_MB"], 0); put("SynRawMB", S3["SYN200"]["raw_rules_MB"], 1)
put("SynBuildS", S3["SYN200"]["RQ_base_build_s"], 2)
put("SynPlusRowsM", S3["SYN200"]["RQ_plus_rows_after_200k"] / 1e6, 1)
put("SynPlusRowsParcels", 200000, 0)
pe = [E4[i]["price_edit"]["RQ_base"]["mean_us"] for i, _, _ in INST]
put("PriceEditMin", min(pe)); put("PriceEditMax", max(pe))
rb = [E4[i]["RQ_base_rebuild_s"] * 1e6 / E4[i]["price_edit"]["RQ_base"]["mean_us"] for i, _, _ in INST]
put("RebuildOverEditMin", sp(min(rb))); put("RebuildOverEditMax", sp(max(rb)))
fe = [E4[i]["fuel_edit_us"] for i, _, _ in INST]
put("FuelEditMin", min(fe)); put("FuelEditMax", max(fe))
ze = [E4[i]["zone_edit"]["mean_us"] / 1000 for i, _, _ in INST]
put("ZoneEditMin", min(ze)); put("ZoneEditMax", max(ze))
put("RowsTouchedMax", max(E4[i]["price_edit"]["rows_touched_mean"] for i, _, _ in INST), 1)

# ------------------------------------------------------------------ Q3 maintenance (E5)
svc_ratio, full_ratio, band_svc, band_full, gri, fuel = [], [], [], [], [], []
for inst, n, tag in INST:
    r = E5[inst]; ed = r["edits"]; full = r["full_recompute_s"]
    c, b = ed["cell"], ed["band"]
    svc_ratio.append(c["service_level_s"] / c["ours_s"]); full_ratio.append(full / c["ours_s"])
    band_svc.append(b["service_level_s"] / b["ours_s"]); band_full.append(full / b["ours_s"])
    gri.append(full / ed["gri"]["ours_s"]); fuel.append(full / ed["fuel"]["ours_s"])
    put(f"QThree{tag}CellSvc", sp(svc_ratio[-1])); put(f"QThree{tag}CellFull", sp(full_ratio[-1]))
    put(f"QThree{tag}CellMs", c["ours_s"] * 1e3, 2); put(f"QThree{tag}Full", full, 1)
    put(f"QThree{tag}CellSupport", c["support"], 0)
put("QThreeN", E5["PUB2"]["N"], 0)
put("CellSvcMin", sp(min(svc_ratio))); put("CellSvcMax", sp(max(svc_ratio)))
put("CellFullMin", sp(min(full_ratio))); put("CellFullMax", sp(max(full_ratio)))
put("BandSvcMin", sp(min(band_svc))); put("BandSvcMax", sp(max(band_svc)))
put("BandFullMin", sp(min(band_full))); put("BandFullMax", sp(max(band_full)))
put("GriFullMin", sp(min(gri))); put("GriFullMax", sp(max(gri)))
put("FuelFullMin", sp(min(fuel))); put("FuelFullMax", sp(max(fuel)))
csup = [E5[i]["edits"]["cell"]["support"] for i, _, _ in INST]
ssup = [E5[i]["edits"]["cell"]["service_support"] for i, _, _ in INST]
cms = [E5[i]["edits"]["cell"]["ours_s"] * 1e3 for i, _, _ in INST]
put("CellMsMin", min(cms), 2); put("CellMsMax", max(cms), 2)
put("CellSupMin", min(csup), 0); put("CellSupMax", max(csup), 0)
put("SvcSupMin", min(ssup), 0); put("SvcSupMax", max(ssup), 0)
sup_ratio = [E5[i]["edits"]["cell"]["service_support"] / E5[i]["edits"]["cell"]["support"] for i, _, _ in INST]
put("SupRatioMin", min(sup_ratio), 0); put("SupRatioMax", max(sup_ratio), 0)
put("CellCount", E5["PUB2"]["edits"]["cell"]["count"], 0); put("BandCount", E5["PUB2"]["edits"]["band"]["count"], 0)
put("CellRecompA", E5["PUB2"]["edits"]["cell"]["recomputed"], 1)
zero_from = [n for i, n, _ in INST if E5[i]["edits"]["cell"]["recomputed"] == 0]
put("CellRecompZeroFrom", num(min(zero_from), 0) if zero_from else "---")
put("FuelSupport", np.mean([E5[i]["edits"]["fuel"]["support"] for i, _, _ in INST]), 0)

# ------------------------------------------------------------------ enterprise (ERP)
rp = REP["replay"]
put("ErpN", ERP["N"], 0); put("ErpServices", ERP["n_services"], 0)
put("ErpBw", 100 * rp["bw"], 1); put("ErpBase", 100 * rp["base"], 1)
put("ErpTotalNozip", 100 * rp["total_nozip"], 1); put("ErpNozipN", rp["n_nozip"], 0)
put("ErpTotalAll", 100 * rp["total"], 1)
put("ErpRuleVersions", REP["rule_versions_used"], 0)
put("ErpSig", ERP["signatures"], 0); put("ErpGroups", ERP["groups"], 0)
put("ErpElemKey", ERP["elem_key_intervals"], 0); put("ErpElemW", ERP["elem_weight_intervals"], 0)
put("ErpRawMB", ERP["raw_rules_MB"], 1); put("ErpBaseMB", ERP["RQ_base_MB"], 1)
put("ErpMemRatio", ERP["RQ_base_MB"] / ERP["raw_rules_MB"], 2)
put("ErpBuildS", ERP["RQ_base_build_s"], 2)
put("ErpEligMean", ERP["eligible_services_per_parcel"]["mean"], 1)
put("ErpEligMedian", ERP["eligible_services_per_parcel"]["median"], 0)
put("ErpEligMax", ERP["eligible_services_per_parcel"]["max"], 0)
put("ErpROneBrute", ERP["R1_brute"]["p50_us"], 0); put("ErpROneRQ", ERP["R1_RQ_base"]["p50_us"], 0)
put("ErpROnePlus", ERP["R1_RQ_plus"]["p50_us"], 0); put("ErpROnePre", ERP["R1_precompute"]["p50_us"], 0)
put("ErpROneDiffPct", 100 * abs(ERP["R1_RQ_base"]["p50_us"] - ERP["R1_brute"]["p50_us"]) / ERP["R1_brute"]["p50_us"], 1)
put("ErpRTwoBrute", ERP["R2_brute_vec_s"], 2); put("ErpRTwoRQ", ERP["R2_RQ_base_s"], 2)
put("ErpRTwoKin", ERP["R2_kinetic_s"], 1)
put("ErpRTwoSpeed", ERP["R2_brute_vec_s"] / ERP["R2_RQ_base_s"], 2)
put("ErpEvalsPlus", ERP["R2_RQ_plus_evals_per_parcel"], 1)
put("ErpEditMean", ERP["R4_RQ_base_edit"]["mean_us"], 0); put("ErpEditPfifty", ERP["R4_RQ_base_edit"]["p50_us"], 0)
put("ErpRebuild", ERP["R4_rebuild_s"], 2)
cell, band = ERP["R5_price_edits"]["cell"], ERP["R5_price_edits"]["band"]
full = ERP["R5_full_recompute_s"]
put("ErpFull", full, 2)
put("ErpCellMs", cell["s"] * 1e3, 2); put("ErpCellSupport", cell["support"], 0)
put("ErpCellSpeed", sp(full / cell["s"])); put("ErpBandMs", band["s"] * 1e3, 1)
put("ErpBandSpeed", sp(full / band["s"])); put("ErpBandSupport", band["support"], 0)
rr = ERP["R5_real_rule_replay"]
ev = ERP["R5_real_events"]
assert rr["exact_final_state"] and rr["pred_equals_service_all"]
put("ErpEvents", rr["events"], 0); put("ErpTemplatesChanged", len({e["template"] for e in ev}), 0)
put("ErpEvSurch", sum(1 for e in ev if e["kind"] == "3"), 0)
put("ErpEvElig", sum(1 for e in ev if e["kind"] == "2"), 0)
put("ErpEvZero", sum(1 for e in ev if e["support"] == 0), 0)
put("ErpEvLocal", sum(1 for e in ev if 0 < e["support"] < 10000), 0)
put("ErpEvWide", sum(1 for e in ev if e["support"] >= 10000), 0)
loc = [e for e in ev if 0 < e["support"] < 10000]
put("ErpLocalPred", sum(e["pred_s"] for e in loc), 2); put("ErpLocalSvc", sum(e["service_level_s"] for e in loc), 2)
put("ErpLocalSpeed", sum(e["service_level_s"] for e in loc) / sum(e["pred_s"] for e in loc), 1)
put("ErpMedianSupport", rr["median_support"], 0)
put("ErpTotFull", rr["total_full_s"], 1); put("ErpTotSvc", rr["total_service_level_s"], 2)
put("ErpTotPred", rr["total_pred_s"], 2); put("ErpTotAdapt", rr["total_adaptive_s"], 2)
put("ErpTotOracle", rr["oracle_s"], 2)
put("ErpAdaptSpeed", rr["total_full_s"] / rr["total_adaptive_s"], 1)
put("ErpPredSpeed", rr["total_full_s"] / rr["total_pred_s"], 1)
put("ErpSvcSpeed", rr["total_full_s"] / rr["total_service_level_s"], 1)
put("ErpAdaptOraclePct", 100 * (rr["total_adaptive_s"] / rr["oracle_s"] - 1), 1)
put("ErpAdaptPredChoices", rr["adaptive_pred_choices"], 0)
put("ErpAdaptRight", sum(1 for e in ev if (e["adaptive_choice"] == "pred") == (e["pred_s"] <= e["service_level_s"])), 0)
put("ErpFirstDay", min(e["when"] for e in ev)); put("ErpLastDay", max(e["when"] for e in ev))
put("ErpChosenDiff", 100 * ERP["chosen_differs_from_recorded"], 1)
# sensitivity of the adaptive rule to theta (offline over the logged per-event times; same rule as erp_pilot.py)
def adaptive_total(th):
    return sum(e["pred_s"] if (not e["inelig_changed"] and e["changed_frac"] <= th) else e["service_level_s"] for e in ev)
THETAS = [0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0]
tot = {th: adaptive_total(th) for th in THETAS}
band_t = [th for th in THETAS if 0.05 <= th <= 0.5]
put("ThetaLo", "0.05"); put("ThetaHi", "0.5")
put("ThetaDevPct", 100 * max(abs(tot[th] - tot[0.3]) for th in band_t) / tot[0.3], 1)
put("ThetaBandMin", min(tot[th] for th in band_t), 2); put("ThetaBandMax", max(tot[th] for th in band_t), 2)
put("ErpPerEventDiffPct", 100 * float(np.median([abs(e["pred_s"] / e["service_level_s"] - 1) for e in ev])), 1)
with open(os.path.join(RES, "theta_sweep_erp.json"), "w") as f:
    json.dump({"theta_total_s": {str(k): v for k, v in tot.items()}, "oracle_s": rr["oracle_s"]}, f, indent=1)
WHF = os.path.join(RES, "warehouse_us_cards.json")  # aggregate of the company's US warehouses (four numbers)
if os.path.exists(WHF):
    wf = json.load(open(WHF))
    M["WhUSCardShare"] = num(100 * wf["share_2026_us_parcels_on_channels_with_US_card"], 1)
    M["WhUSOnePctMedian"] = num(wf["channels_ge1pct_median"], 0); M["WhUSOnePctMax"] = num(wf["channels_ge1pct_max"], 0)
    M["WhUSCount"] = num(wf["us_warehouses"], 0)
    M["WhUSSentenceRQFive"] = (f"These cards price the channels that carry {M['WhUSCardShare']}\\% of the 2026 parcels of the {M['WhUSCount']} US warehouses; "
                               f"counting only channels with at least 1\\% of a warehouse's parcels, a US warehouse uses a median of {M['WhUSOnePctMedian']} "
                               f"and at most {M['WhUSOnePctMax']} channels, the scale of ENT.")
else:
    M["WhUSSentenceRQFive"] = ""
WHUS = os.path.join(RES, "warehouse_us_channels.json")  # optional: read-only aggregate of US-destination channels
if os.path.exists(WHUS):
    wu = json.load(open(WHUS))
    M["WhUSSentence"] = (f"Counted over US destinations only, the median warehouse uses {num(wu['median'], 0)} and the largest "
                         f"{num(wu['max'], 0)} carrier channels.")
else:
    M["WhUSSentence"] = ""
for k, name in (("parcels_2026_M", "WhParcelsM"), ("warehouses", "WhWarehouses"), ("warehouses_1k", "WhBigWarehouses"),
                ("median", "WhMedian"), ("max", "WhMax"), ("weighted", "WhWeighted")):
    v = WAREHOUSE[k]
    put(name, v, 1 if isinstance(v, float) else 0)

# environment
env = E3["_env"]
put("EnvPython", env["python"]); put("EnvCpu", env["cpu"])
put("SrvPython", E5["_env"].get("python", "")); put("SrvNumpy", E5["_env"].get("numpy", ""))

# ------------------------------------------------------------------ optional: compiled same-language runs
# ------------------------------------------------------------------ compiled same-language implementation (primary Q1/Q2 evidence)
# Public data: results/cpp_E1E2_pub2_server.json (PUB2, x60, x240, x1000) and cpp_E1E2_pub2r_server.json (PUB2n11, PUB2n31),
# Intel Xeon server, one pinned core; enterprise data: cpp_E1E2_erp.json (Apple M4). Exactness: cpp_verify_*.json.
# A configuration enters the paper only after its exactness check is present in a verify file.
def _load(f):
    q = os.path.join(RES, f)
    return json.load(open(q)) if os.path.exists(q) else {}
CS = {**{k: v for k, v in _load("cpp_E1E2_pub2r_server.json").items() if k != "_env"},
      **{k: v for k, v in _load("cpp_E1E2_pub2_server.json").items() if k != "_env"}}
CERP = _load("cpp_E1E2_erp.json")
SENV = _load("cpp_E1E2_pub2_server.json").get("_env", {})
VER = {**_load("cpp_verify_pub2r.json"), **_load("cpp_verify_pub2.json"), **_load("cpp_verify_erp.json")}
CINST = [("PUB2n11", 11), ("PUB2", 19), ("PUB2n31", 31), ("PUB2x60", 60), ("PUB2x240", 240), ("PUB2x1000", 1000)]
verified = lambda inst: inst in VER and VER[inst]["index"]["differs_from_python"] == 0 and \
    all(sum(r["mismatch_vs_py_brute_vec"].values()) == 0 for r in VER[inst]["runs"].values())
CROWS = [(inst, n, wl) for inst, n in CINST for wl, _ in WL if f"{inst}/{wl}" in CS and verified(inst)]
put("HaveCppDone", "1" if CROWS else "0")
if CROWS:
    g1 = lambda inst, wl, k: CS[f"{inst}/{wl}"]["agg"]["E1"][k]["mean"]
    g2 = lambda inst, wl, k: CS[f"{inst}/{wl}"]["agg"]["E2"][k]["mean"]
    def ev1(inst, wl):  # exact evaluations per query over the timed Q1 queries (E1 seeds of the verify run)
        e = [r["evals_q1"] for k, r in VER[inst]["runs"].items() if k.startswith(f"{wl}_e1_")]
        return float(np.mean(e)) if e else CS[f"{inst}/{wl}"]["agg"]["E2"]["evals_per_parcel_RQ_q1"]
    ev2 = lambda inst, wl: CS[f"{inst}/{wl}"]["agg"]["E2"]["evals_per_parcel_RQ_batch"]
    q1 = {(i, w): g1(i, w, "speedup_cpu_p50_ns_B1s") for i, n, w in CROWS}
    q2 = {(i, w): g2(i, w, "speedup_B1v_vs_RQ_batch") for i, n, w in CROWS}
    nmap = dict(CINST)
    sel = lambda d, ns: [v for (i, w), v in d.items() if nmap[i] in ns]
    put("CppQOneWhMin", sp(min(sel(q1, (11, 19, 31))))); put("CppQOneWhMax", sp(max(sel(q1, (11, 19, 31)))))
    put("CppQTwoWhMin", sp(min(sel(q2, (11, 19, 31))))); put("CppQTwoWhMax", sp(max(sel(q2, (11, 19, 31)))))
    for nn, tag in ((60, "Sixty"), (240, "TwoForty"), (1000, "Thousand")):
        if sel(q1, (nn,)):
            put(f"CppQOne{tag}Min", sp(min(sel(q1, (nn,))))); put(f"CppQOne{tag}Max", sp(max(sel(q1, (nn,)))))
            put(f"CppQTwo{tag}Min", sp(min(sel(q2, (nn,))))); put(f"CppQTwo{tag}Max", sp(max(sel(q2, (nn,)))))
    nmax = max(n for _, n, _ in CROWS)
    put("CppMaxN", num(nmax, 0)); put("CppQOneTop", sp(max(sel(q1, (nmax,))))); put("CppQTwoTop", sp(max(sel(q2, (nmax,)))))
    put("CppQOneAll", sp(max(q1.values()))); put("CppQTwoAll", sp(max(q2.values())))
    ewh = [ev1(i, w) for i, n, w in CROWS if n <= 31]
    put("CppEvalsWhMin", min(ewh), 2); put("CppEvalsWhMax", max(ewh), 2)
    p95 = [g1(i, w, "speedup_cpu_p95_ns_B1s") for i, n, w in CROWS]
    put("CppPNinetyFiveMin", sp(min(p95))); put("CppPNinetyFiveMax", sp(max(p95)))
    mn = [g1(i, w, "speedup_cpu_mean_ns_B1s") for i, n, w in CROWS]
    put("CppMeanMin", sp(min(mn))); put("CppMeanMax", sp(max(mn)))
    rqwh = [g1(i, w, "RQ_q1_cpu_p50_ns") / 1000 for i, n, w in CROWS if n <= 31]
    put("CppRQWhMin", min(rqwh), 2); put("CppRQWhMax", max(rqwh), 2)
    put("CppSeeds", CS[f"{CROWS[0][0]}/{CROWS[0][2]}"]["agg"]["E1"]["speedup_cpu_p50_ns_B1s"]["k"], 0)
    put("CppQueries", CS[f"{CROWS[0][0]}/{CROWS[0][2]}"]["E1"]["11"]["q"] if "11" in CS[f"{CROWS[0][0]}/{CROWS[0][2]}"]["E1"] else 1000, 0)
    if CERP:
        ea = CERP["ERP/erp"]["agg"]
        put("CppErpQOne", ea["E1"]["speedup_cpu_p50_ns_B1s"]["mean"], 1); put("CppErpQTwo", ea["E2"]["speedup_B1v_vs_RQ_batch"]["mean"], 1)
        ee = [r["evals_q1"] for k, r in VER.get("ERP", {}).get("runs", {}).items() if "_e1_" in k and r.get("evals_q1") is not None]
        ERP_EV1 = float(np.mean(ee)) if ee else ea["E2"]["evals_per_parcel_RQ_q1"]
        put("CppErpEvals", ERP_EV1, 1)
        # time overhead over compiled brute force, computed from the same seed-mean times that Table II prints
        # (RQ p50 / B1s p50 and RQ batch / B1v batch), so a reader dividing the table columns gets the same percentage
        _ov = max(ea["E1"]["RQ_q1_cpu_p50_ns"]["mean"] / ea["E1"]["B1s_cpu_p50_ns"]["mean"],
                  ea["E2"]["RQ_batch_s_per_100k"]["mean"] / ea["E2"]["B1v_s_per_100k"]["mean"])
        put("CppErpWithinPct", 100 * (_ov - 1) if _ov > 1 else 0, 0)
        put("CppErpRQ", ea["E1"]["RQ_q1_cpu_p50_ns"]["mean"] / 1000, 2); put("CppErpBrute", ea["E1"]["B1s_cpu_p50_ns"]["mean"] / 1000, 2)
    # table: primary compiled comparison
    us_ = lambda x: num(x / 1000, 2) if x < 10000 else num(x / 1000)
    with open(os.path.join(TAB, "cpp.tex"), "w") as f:
        f.write("% generated by code/make_numbers.py\n")
        f.write("\\begin{table*}[t]\n\\centering\\footnotesize\n\\setlength{\\tabcolsep}{3.4pt}\n")
        f.write("\\caption{Compiled same-language comparison (C++, one core, thread CPU time; mean over " + M["CppSeeds"].replace("\\xspace", "")
                + " seeds of the per-seed median). Q1: per-query time ($\\mu$s) of the strongest scalar brute force B1s and the RQ-Index, and speedup at p50, p95, p99 and mean. "
                "Q2: time (s) per 100{,}000 parcels of the column-wise brute force B1v and the batch RQ-Index. Evals: exact evaluations per parcel of the RQ-Index over the timed queries and batches (brute force: $n$). "
                "Public data on an Intel Xeon server, enterprise data on an Apple M4. Every answer equals the brute-force answer. "
                + "Faster of the two methods in bold: RQ-Index in @CPPQ1@ (Q1) and @CPPQ2@ (Q2) rows.}\n\\label{tab:cpp}\n")
        f.write("\\begin{tabular}{@{}lrlrrrrrrr|rrrr@{}}\n\\toprule\n")
        f.write("& & & \\multicolumn{7}{c|}{Q1 single query} & \\multicolumn{4}{c}{Q2 batch}\\\\\n")
        f.write("Instance & $n$ & Load & B1s p50 & RQ p50 & $\\times$p50 & $\\times$p95 & $\\times$p99 & $\\times$mean & Evals & B1v & RQ & $\\times$ & Evals\\\\\n\\midrule\n")
        rows = []
        for inst, n in CINST:
            sub = [r for r in CROWS if r[0] == inst]
            for (i, nn, w) in sub:
                c1 = mark([g1(i, w, 'B1s_cpu_p50_ns'), g1(i, w, 'RQ_q1_cpu_p50_ns')], us_, [1], table="cpp_Q1", label=f"{i}/{w}")
                c2 = mark([g2(i, w, 'B1v_s_per_100k'), g2(i, w, 'RQ_batch_s_per_100k')], lambda v: num(v, 3), [1], table="cpp_Q2", label=f"{i}/{w}")
                rows.append(f"{i if w == 'olist' else ''} & {nn if w == 'olist' else ''} & {w} & {c1[0]} & {c1[1]} & "
                            f"{sp(g1(i, w, 'speedup_cpu_p50_ns_B1s'))} & {sp(g1(i, w, 'speedup_cpu_p95_ns_B1s'))} & {sp(g1(i, w, 'speedup_cpu_p99_ns_B1s'))} & "
                            f"{sp(g1(i, w, 'speedup_cpu_mean_ns_B1s'))} & {num(ev1(i, w), 2)} & {c2[0]} & {c2[1]} & "
                            f"{sp(g2(i, w, 'speedup_B1v_vs_RQ_batch'))} & {num(ev2(i, w), 2)}\\\\")
            if sub:
                rows.append("\\midrule")
        if CERP:
            ea = CERP["ERP/erp"]["agg"]
            ce1 = mark([ea['E1']['B1s_cpu_p50_ns']['mean'], ea['E1']['RQ_q1_cpu_p50_ns']['mean']], us_, [1], table="cpp_Q1", label="ENT",
                       loss_cause=f"n=10 with {num(ERP_EV1, 1)} of {num(ERP['eligible_services_per_parcel']['mean'], 1)} eligible services evaluated per parcel: nothing to prune")
            ce2 = mark([ea['E2']['B1v_s_per_100k']['mean'], ea['E2']['RQ_batch_s_per_100k']['mean']], lambda v: num(v, 3), [1], table="cpp_Q2", label="ENT",
                       loss_cause="n=10, few services to prune")
            rows.append(f"ENT & 10 & ent & {ce1[0]} & {ce1[1]} & "
                        f"{num(ea['E1']['speedup_cpu_p50_ns_B1s']['mean'], 1)} & {num(ea['E1']['speedup_cpu_p95_ns_B1s']['mean'], 1)} & {num(ea['E1']['speedup_cpu_p99_ns_B1s']['mean'], 1)} & "
                        f"{num(ea['E1']['speedup_cpu_mean_ns_B1s']['mean'], 1)} & {num(ERP_EV1, 2)} & {ce2[0]} & "
                        f"{ce2[1]} & {num(ea['E2']['speedup_B1v_vs_RQ_batch']['mean'], 1)} & {num(ea['E2']['evals_per_parcel_RQ_batch'], 2)}\\\\")
        elif rows and rows[-1] == "\\midrule":
            rows.pop()
        f.write("\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n\\end{table*}\n")
# exactness of the compiled implementation
tot = sum(r["N"] for v in VER.values() for r in v["runs"].values())
mis = sum(sum(r["mismatch_vs_py_brute_vec"].values()) for v in VER.values() for r in v["runs"].values()) + \
    sum(v["index"]["differs_from_python"] for v in VER.values())
put("CppVerifyParcels", tot, 0); put("CppVerifyMismatch", mis, 0); put("CppVerifyInstances", len(VER), 0)
put("CppVerifyRuns", sum(len(v["runs"]) for v in VER.values()), 0)
put("CppServerCpu", "Intel Xeon Platinum 8336C")

# ------------------------------------------------------------------ round-1 revision experiments (results/rev1)
# Public-data runs: results/rev1/server/*.json (Xeon server); enterprise-shape runs: results/rev1/*entgeo*.json (Apple M4).
# Only ratios within one file are printed (README: absolute times are not comparable across hosts).
RV = os.path.join(RES, "rev1")
def _rv(f):
    q = os.path.join(RV, f)
    return json.load(open(q)) if os.path.exists(q) else None
def _pref(*names):  # first existing file, e.g. a *_fuelfix.json rerun before the original
    for nm in names:
        d = _rv(nm)
        if d is not None:
            return d
    return None
med = lambda x: x["median"] if isinstance(x, dict) and "median" in x else x
REV_TAB = []
# --- B7 result cache
b7 = []
for f in ("server/b7_cache_PUB2.json", "server/b7_cache_PUB2x60.json", "server/b7_cache_PUB2x240.json",
          "b7_cache_entgeo_PUB2.json", "b7_cache_entgeo_PUB2x60.json", "b7_cache_entgeo_PUB2x240.json"):
    d = _rv(f)
    if d:
        b7 += [v for k, v in d.items() if "/" in k and "Q1_us" in v]
if b7:
    hit = [v["steady_state_100k"]["hit_rate"] for v in b7]
    put("BSevenHitMin", 100 * min(hit), 0); put("BSevenHitMax", 100 * max(hit), 0)
    g = [med(v["Q1_us"]["RQ_index"]["mean_us"]) / med(v["Q1_us"]["B7_cache_steady"]["mean_us"]) for v in b7]
    put("BSevenQOneMin", sp(min(g))); put("BSevenQOneMax", sp(max(g)))
    g2 = [med(v["Q2_100k_s"]["RQ_batch"]["cpu_s"]) / med(v["Q2_100k_s"]["B7_batch_steady"]["cpu_s"]) for v in b7]
    put("BSevenQTwoMin", num(min(g2), 2)); put("BSevenQTwoMax", num(max(g2), 1))
    fd = [1e3 * med(v["cell_edit_invalidation"]["find_drop_s"]) for v in b7]
    ru = [1e3 * med(v["cell_edit_invalidation"]["rq_index_update_s"]) for v in b7]
    put("BSevenInvMin", min(fd), 0); put("BSevenInvMax", max(fd), 0)
    put("BSevenRepairMin", min(ru), 2); put("BSevenRepairMax", max(ru), 2)
    mb = [v["table_after_1M"]["python_dict_MB"] for v in b7]
    put("BSevenMBMin", min(mb), 0); put("BSevenMBMax", max(mb), 0)
    pk = [v["table_after_1M"]["packed_MB"] for v in b7]
    put("BSevenPackedMin", min(pk), 0); put("BSevenPackedMax", max(pk), 0)
    rmb = [v["RQ_index_MB"] for v in b7]
    put("BSevenRQMBMin", min(rmb), 1); put("BSevenRQMBMax", max(rmb), 1)
    put("BSevenKeyCols", b7[0]["key_columns"], 0)
    put("BSevenFuelPeriods", b7[0]["fuel_periods"], 0)
# --- B8 cost-matrix IVM (prefer reruns with merged fuel-class recomputation)
b8 = {}
for base in ("server/b8_ivm_n19_240", "server/b8_ivm_n1000"):
    d = _pref(base + "_fuelfix.json", base + ".json")
    if d:
        b8.update({k: v for k, v in d.items() if not k.startswith("_")})
B8I = [i for i in ("PUB2", "PUB2x60", "PUB2x240", "PUB2x1000") if i in b8]
if B8I:
    for i, tag in zip(("PUB2", "PUB2x60", "PUB2x240", "PUB2x1000"), ("A", "B", "C", "D")):
        if i in b8:
            put(f"BEight{tag}MB", b8[i]["B8_state_MB"], 1 if b8[i]["B8_state_MB"] < 100 else 0)
    put("BEightRQMB", b8[B8I[0]]["RQ_state_MB"], 1)
    ec = lambda i, m, k="cell": med(b8[i]["edit_cpu_s"][k][m])
    col = [ec(i, "B8_col") / ec(i, "RQ") for i in B8I if "B8_col" in b8[i]["edit_cpu_s"]["cell"] and med(b8[i]["edit_cpu_s"]["cell"]["B8_col"]) is not None]
    put("BEightColMin", sp(min(col))); put("BEightColMax", sp(max(col)))
    same = [ec(i, "B8") / ec(i, "RQ") for i in B8I]
    put("BEightSameMin", num(min(same), 2)); put("BEightSameMax", num(max(same), 2))
    put("BEightMBRatio", sp(b8[B8I[-1]]["B8_state_MB"] / b8[B8I[-1]]["RQ_state_MB"]))
    fr = [ec(i, "RQ", "fuel") / ec(i, "B8", "fuel") for i in B8I]  # > 1: B8 faster
    if all(r > 1.05 for r in fr):
        sent = (f"On fuel edits, which change every service of a fuel class at once, B8 is {sp(min(fr))}--{sp(max(fr))}\\xx faster, because it reads the "
                f"stored costs of the few hundred parcels whose top-2 state changes, whereas \\sys re-evaluates them; this is what the "
                f"{num(b8[B8I[-1]]['B8_state_MB'], 0)}\\,MB of B8 at $n={B8I[-1].replace('PUB2x', '') if 'x' in B8I[-1] else 19}$ buy against {num(b8[B8I[-1]]['RQ_state_MB'], 1)}\\,MB.")
    elif all(r < 0.95 for r in fr):
        sent = f"RateQ is also {sp(min(1 / r for r in fr))}--{sp(max(1 / r for r in fr))}\\xx faster on fuel edits."
    else:
        sent = f"On fuel edits the two are within a factor of {sp(max(max(fr), max(1 / r for r in fr)))} of each other."
    M["BEightFuelSentence"] = sent
# --- lower-bound ablation
ab = {}
for f in ("server/ablation_bound_pub2_x240.json", "server/ablation_bound_x1000.json"):
    d = _rv(f)
    if d:
        ab.update({k: v for k, v in d.items() if "/" in k})
if ab:
    r1 = [v["Q1_evals_per_parcel"]["RowMin"] / v["Q1_evals_per_parcel"]["RQ_index"] for v in ab.values()]
    put("AblRowMinMin", sp(min(r1))); put("AblRowMinMax", sp(max(r1)))
    if "PUB2x1000/furn" in ab:
        v = ab["PUB2x1000/furn"]
        for m, nm in (("B1_brute", "B"), ("RowMin", "Row"), ("RQ_index", "RQ"), ("RQ_plus", "Plus")):
            put(f"AblD{nm}Us", med(v["Q1_mean_us"][m]), 0)
            put(f"AblD{nm}Ev", v["Q1_evals_per_parcel"][m], 1 if v["Q1_evals_per_parcel"][m] < 100 else 0)
        put("AblDPlusGain", sp(v["Q1_evals_per_parcel"]["RQ_index"] / v["Q1_evals_per_parcel"]["RQ_plus"]))
# --- top-k ablation
tk = _rv("server/ablation_topk_v2.json")
if tk and "PUB2" in tk:
    put("TopkGriOne", tk["PUB2"]["recomputed_R_mean"]["gri"]["top1"], 0)
    put("TopkGriTwo", tk["PUB2"]["recomputed_R_mean"]["gri"]["top2"], 0)
    rat = []
    for i in ("PUB2", "PUB2x60", "PUB2x240"):
        for kind, kk in tk[i]["edit_cpu_s"].items():
            t = [med(kk[x]) for x in ("top1", "top2", "top3")]
            rat.append(max(t) / min(t))
    put("TopkTimeSpread", num(max(rat), 1))
# --- stored-parcel scale (a configuration is used only when all five edit repetitions are present)
SC = []
for N_, n_ in ((2000000, 19), (2000000, 60), (2000000, 240), (20000000, 19), (20000000, 60)):
    d = _rv(f"server/scale_q3_{N_}_{n_}.json")
    if not d:
        continue
    v = [x for k, x in d.items() if not k.startswith("_")][0]
    if v["edit_cpu_s"]["cell"]["RQ"]["reps"] >= 5 and all(v["exact_each_checked_rep"]):
        SC.append((N_, n_, v))
if SC:
    full_ = [med(v["full_recompute_cpu_s"]) for _, _, v in SC]
    rqc = [1e3 * med(v["edit_cpu_s"]["cell"]["RQ"]) for _, _, v in SC]
    svc = [med(v["edit_cpu_s"]["cell"]["service"]) for _, _, v in SC]
    put("ScaleFullMin", min(full_), 1); put("ScaleFullMax", max(full_), 0)
    put("ScaleCellMin", min(rqc), 1); put("ScaleCellMax", max(rqc), 1 if max(rqc) < 100 else 0)
    put("ScaleSvcMin", min(svc), 2); put("ScaleSvcMax", max(svc), 1)
    put("ScaleMaxN", num(max(N for N, _, _ in SC) / 1e6, 0)); put("ScaleRQStateGB", max(v["RQ_state_MB"] for _, _, v in SC) / 1000, 1)
    big = [v for N, _, v in SC if N == max(x[0] for x in SC)]
    put("ScaleBigFullMax", max(med(v["full_recompute_cpu_s"]) for v in big), 0)
    put("ScaleBigCellMax", max(1e3 * med(v["edit_cpu_s"]["cell"]["RQ"]) for v in big), 1)
    with open(os.path.join(TAB, "scale.tex"), "w") as f:
        f.write("% generated by code/make_numbers.py\n")
        f.write("\\begin{table}[t]\n\\centering\\footnotesize\n\\setlength{\\tabcolsep}{1.9pt}\n")
        f.write("\\caption{Q3 on 2M and 20M stored parcels (server, CPU time, median over repetitions): full recomputation, and time per edit of service-level maintenance "
                "(Svc; re-evaluation only, a lower bound on its cost) and of \\sys. Every maintained state equals a full recomputation. "
                "Best per edit type in bold, second underlined: \\sys is best in @SCB@ comparisons.}\n\\label{tab:scale}\n")
        f.write("\\begin{tabular}{@{}rrrrrrrrr@{}}\n\\toprule\n")
        f.write("& & Full & \\multicolumn{2}{c}{Cell edit} & \\multicolumn{2}{c}{Band edit} & \\multicolumn{2}{c}{Fuel edit}\\\\\n")
        f.write("$|X|$ & $n$ & (s) & Svc (s) & \\sys (ms) & Svc (s) & \\sys (ms) & Svc (s) & \\sys (s)\\\\\n\\midrule\n")
        for N_, n_, v in SC:
            e = v["edit_cpu_s"]
            fu = med(v['full_recompute_cpu_s']); lbl = f"{num(N_ / 1e6, 0)}M/n={n_}"
            cc = mark([fu, med(e['cell']['service']), med(e['cell']['RQ'])], [lambda x: num(x), lambda x: num(x, 2), lambda x: num(1e3 * x, 1)], [2],
                      units=[1, 1, 1e-3], table="scale", label=lbl + "/cell")
            bb = mark([fu, med(e['band']['service']), med(e['band']['RQ'])], [lambda x: num(x), lambda x: num(x, 2), lambda x: num(1e3 * x)], [2],
                      units=[1, 1, 1e-3], table="scale", label=lbl + "/band")
            ff = mark([fu, med(e['fuel']['service']), med(e['fuel']['RQ'])], [lambda x: num(x), lambda x: num(x, 1), lambda x: num(x, 2)], [2],
                      table="scale", label=lbl + "/fuel")
            f.write(f"{num(N_ / 1e6, 0)}M & {n_} & {num(fu)} & {cc[1]} & {cc[2]} & "
                    f"{bb[1]} & {bb[2]} & {ff[1]} & {ff[2]}\\\\\n")
        f.write("\\bottomrule\n\\end{tabular}\n\\end{table}\n")
# --- ent-geo workload (Apple M4, Python)
EG = []
for inst in ("PUB2", "PUB2x60", "PUB2x240"):
    d = _rv(f"entgeo_E1E2_{inst}.json")
    if d and f"{inst}/entgeo" in d:
        EG.append((inst, d[f"{inst}/entgeo"]))
if EG:
    ev = [v["E1"]["evals_per_parcel"]["RQ_index"] for _, v in EG]
    put("EgEvalsMin", min(ev), 1); put("EgEvalsMax", max(ev), 1)
    evp = [v["E1"]["evals_per_parcel"]["RQ_plus"] for _, v in EG]
    put("EgPlusEvalsMax", max(evp), 2)
    q1b = [max(med(v["E1"]["speedup_mean_vs_B1"]["RQ_index"]), med(v["E1"]["speedup_mean_vs_B1"]["RQ_plus"])) for _, v in EG]
    put("EgQOneMin", sp(min(q1b))); put("EgQOneMax", sp(max(q1b)))
    q2b = [max(med(v["E2"]["speedup_vs_B1"]["RQ_batch"]), med(v["E2"]["speedup_vs_B1"]["RQ_plus_warm"])) for _, v in EG]
    put("EgQTwoMin", sp(min(q2b))); put("EgQTwoMax", sp(max(q2b)))
    st = _rv("entgeo_workload_stats.json")
    if st:
        put("EgParcels", st["shapes"]["us_parcels_used"], 0); put("EgShapes", st["shapes"]["distinct_shapes"], 0)
        put("EgWeightMedian", st["shapes"]["weight_lb"]["p50"], 0); put("EgZcta", st["zcta"]["zcta_with_population"], 0)
# --- generator sensitivity
SE = {}
for f in ("server/sensitivity_n240.json", "server/sensitivity_n1000a.json", "server/sensitivity_n1000b.json", "server/sensitivity_n1000w.json"):
    d = _rv(f)
    if d:
        SE.update({k: v for k, v in d.items() if k.startswith("n")})
if SE:
    nm_ = [k for k in SE if "/normal/" in k]
    put("SensSettings", len({k.split("/")[2] + k.split("/")[3] for k in nm_}), 0)
    best = lambda v: max(med(v["speedup_mean_vs_B1"]["RQ_index"]), med(v["speedup_mean_vs_B1"]["RQ_plus"]))
    sn = [best(SE[k]) for k in nm_]
    put("SensNormMin", sp(min(sn))); put("SensNormMax", sp(max(sn)))
    nr = [best(SE[k]) for k in SE if "/near/" in k]
    if nr:
        put("SensNearMin", sp(min(nr))); put("SensNearMax", sp(max(nr)))
    idn = [med(SE[k]["speedup_mean_vs_B1"]["RQ_index"]) for k in SE if "/identical/" in k]
    if idn:
        put("SensIdentMin", sp(min(idn))); put("SensIdentMax", sp(max(idn)))

# ------------------------------------------------------------------ public local rule-edit stream (results/rev2/rule_stream_*.json)
RS = []
for inst, n_ in (("PUB2", 19), ("PUB2x60", 60), ("PUB2x240", 240)):
    q = os.path.join(RES, "rev2", f"rule_stream_{inst}.json")
    if os.path.exists(q):
        d = json.load(open(q))
        if d.get("exact_final_state_all_reps") and d.get("pred_equals_service_all_events_all_reps"):
            RS.append((inst, n_, d))
if RS:
    ssv = [d["speedup_service_over_pred_fast"]["median"] for _, _, d in RS]
    sfu = [d["speedup_full_over_pred_fast"]["median"] for _, _, d in RS]
    put("RsSvcMin", sp(min(ssv))); put("RsSvcMax", sp(max(ssv)))
    put("RsFullMin", num(min(sfu), 0)); put("RsFullMax", num(max(sfu), 0))
    put("RsSupportPct", 100 * float(np.median([d["support_share_median"] for _, _, d in RS])), 0)
    d0 = RS[0][2]
    put("RsEvents", d0["events"], 0); put("RsReps", d0["reps"], 0); put("RsParcels", d0["N"], 0)
    for k, nm in (("amount", "RsAmount"), ("resi", "RsResi"), ("threshold", "RsThreshold"), ("das_list", "RsDas")):
        put(nm, d0["by_kind"][k]["events"], 0)
    put("RsNMin", num(min(n for _, n, _ in RS), 0)); put("RsNMax", num(max(n for _, n, _ in RS), 0))
    host = str(d0.get("_env", {}).get("host", ""))
    M["RsPlatform"] = "the M4" if "Mac" in host or "mac" in host else "the Xeon server"

# ------------------------------------------------------------------ write numbers.tex
with open(os.path.join(PAPER, "numbers.tex"), "w") as f:
    f.write("% generated by code/make_numbers.py -- do not edit\n")
    for k in sorted(M):
        f.write(f"\\newcommand{{\\{k}}}{{{M[k]}\\xspace}}\n")

# ------------------------------------------------------------------ tables


def lat(x):
    return num(x)


def bold_min(vals, fmt):
    m = min(v for v in vals if v is not None)
    return ["---" if v is None else (f"\\textbf{{{fmt(v)}}}" if v == m else fmt(v)) for v in vals]


# Table: Q1 single-query p50 latency
rows = []
for inst, n, tag in INST + [("SYN200", 200, "S")]:
    for wl, wt in WL:
        r = (E1 if inst != "SYN200" else S1)[f"{inst}/{wl}"]
        g = lambda k: r[k]["p50_us"] if k in r else None
        vals = [g("B1_brute"), g("B4_precompute"), g("B3_rtree"), g("B6_mip"), g("RQ_base"), g("RQ_plus")]
        cells = bold_min(vals, lat)
        best = min(r["RQ_base"]["p50_us"], r["RQ_plus"]["p50_us"])
        name = {"PUB2": "PUB2", "PUB2x60": "PUB2x60", "PUB2x240": "PUB2x240", "PUB2x1000": "PUB2x1000", "SYN200": "SYN200"}[inst]
        rows.append(f"{name if wl == 'olist' else ''} & {n if wl == 'olist' else ''} & {wl} & " + " & ".join(cells)
                    + f" & {num(r['RQ_base']['evals'], 1)}/{num(r['RQ_plus']['evals'], 1)} & {sp(r['B1_brute']['p50_us'] / best)}\\\\")
    if inst != "SYN200":
        pass
    rows.append("\\midrule" if inst != "SYN200" else "")
rows = [r for r in rows if r]
if rows[-1] == "\\midrule":
    rows.pop()
with open(os.path.join(TAB, "q1.tex"), "w") as f:
    f.write("% generated by code/make_numbers.py\n")
    f.write("\\begin{table}[t]\n\\centering\\small\n\\setlength{\\tabcolsep}{2.6pt}\n")
    f.write("\\caption{Q1 single-query latency (p50, $\\mu$s). All methods return the brute-force answer on every query. "
            "Evals: exact service evaluations per query (RQ/RQ+). Speedup: best RQ variant over B1. "
            "--- : not run (B3/B6 build or query cost).}\n\\label{tab:q1}\n")
    f.write("\\begin{tabular}{@{}llrrrrrrrrr@{}}\n\\toprule\n")
    f.write("Instance & $n$ & Load & B1 & B4 & B3 & B6 & RQ & RQ+ & Evals & Speedup\\\\\n\\midrule\n")
    f.write("\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n")

# Table: Q2 batch seconds
rows = []
for inst, n, tag in INST + [("SYN200", 200, "S")]:
    for wl, wt in WL:
        r = (E2 if inst != "SYN200" else S2)[f"{inst}/{wl}"]
        vals = [r["B1_brute_vec"]["s"], r["B2_duckdb_t1"]["s"], r["B2_duckdb_t8"]["s"], r["B5_kinetic"]["s"],
                r["RQ_base"]["s"], r["RQ_plus_cold"]["s"], r["RQ_plus_warm"]["s"]]
        fm = lambda v: num(v, 2) if v < 10 else num(v)
        cells = bold_min(vals, fm)
        best = min(r["RQ_base"]["s"], r["RQ_plus_warm"]["s"])
        rows.append(f"{inst if wl == 'olist' else ''} & {n if wl == 'olist' else ''} & {wl} & " + " & ".join(cells)
                    + f" & {sp(r['B1_brute_vec']['s'] / best)}\\\\")
    if inst != "SYN200":
        rows.append("\\midrule")
with open(os.path.join(TAB, "q2.tex"), "w") as f:
    f.write("% generated by code/make_numbers.py\n")
    f.write("\\begin{table}[t]\n\\centering\\small\n\\setlength{\\tabcolsep}{2.4pt}\n")
    f.write(f"\\caption{{Q2 batch time (s) for {num(E2['PUB2/olist']['N'], 0)} parcels; every method returns the brute-force answers. "
            "DuckDB with 1/8 threads; RQ+ cold (rows built during the batch) and warm. Speedup: best of RQ and RQ+ warm over B1-vec.}\n\\label{tab:q2}\n")
    f.write("\\begin{tabular}{@{}llrrrrrrrrr@{}}\n\\toprule\n")
    f.write("Instance & $n$ & Load & B1-vec & DB-1 & DB-8 & B5 & RQ & RQ+c & RQ+w & Speedup\\\\\n\\midrule\n")
    f.write("\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n")


# Table: Q1 and Q2 side by side (two-column float)
rows = []
for inst, n, tag in INST + [("SYN200", 200, "S")]:
    for wl, wt in WL:
        r1 = (E1 if inst != "SYN200" else S1)[f"{inst}/{wl}"]
        r2 = (E2 if inst != "SYN200" else S2)[f"{inst}/{wl}"]
        g = lambda k: r1[k]["p50_us"] if k in r1 else None
        v1 = [g("B1_brute"), g("B4_precompute"), g("B3_rtree"), g("RQ_base"), g("RQ_plus")]
        b1 = min(r1["RQ_base"]["p50_us"], r1["RQ_plus"]["p50_us"])
        v2 = [r2["B1_brute_vec"]["s"], r2["B2_duckdb_t1"]["s"], r2["B2_duckdb_t8"]["s"], r2["B5_kinetic"]["s"],
              r2["RQ_base"]["s"], r2["RQ_plus_cold"]["s"], r2["RQ_plus_warm"]["s"]]
        b2 = min(r2["RQ_base"]["s"], r2["RQ_plus_warm"]["s"])
        fm = lambda v: num(v, 2) if v < 10 else num(v)
        m1 = mark(v1, lat, [3, 4], table="py_Q1", label=f"{inst}/{wl}")
        m2 = mark(v2, fm, [4, 5, 6], table="py_Q2", label=f"{inst}/{wl}")
        rows.append(f"{inst if wl == 'olist' else ''} & {n if wl == 'olist' else ''} & {wl} & " + " & ".join(m1)
                    + f" & {num(r1['RQ_base']['evals'], 1)}/{num(r1['RQ_plus']['evals'], 1)} & {sp(r1['B1_brute']['p50_us'] / b1)}"
                    + " & " + " & ".join(m2) + f" & {sp(r2['B1_brute_vec']['s'] / b2)}\\\\")
    rows.append("\\midrule")
rows.pop()
with open(os.path.join(TAB, "q12.tex"), "w") as f:
    f.write("% generated by code/make_numbers.py\n")
    f.write("\\begin{table*}[t]\n\\centering\\footnotesize\n\\setlength{\\tabcolsep}{3.1pt}\n")
    f.write("\\caption{Q1 single-query latency (p50 over " + num(E1['PUB2/olist']['B1_brute']['q'], 0) + " parcels, $\\mu$s) and Q2 batch time (s, "
            + num(E2['PUB2/olist']['N'], 0) + " parcels). Every method returns the brute-force answer for every parcel. "
            "Evals: exact service evaluations per parcel (RQ-Index/\\rqp). $\\times$: best RQ variant over B1 (Q1) and over B1-vec (Q2). "
            "DB-1/DB-8: DuckDB with 1/8 threads; \\rqp-c/-w: cold/warm row cache; --- : not run. "
            "Best in bold, second best underlined: a RateQ variant is best in @PYQ1B@ (Q1) and @PYQ2B@ (Q2) rows.}\n\\label{tab:q12}\n")
    f.write("\\begin{tabular}{@{}llrrrrrrrr|rrrrrrrr@{}}\n\\toprule\n")
    f.write("& & & \\multicolumn{7}{c|}{Q1 single query ($\\mu$s)} & \\multicolumn{8}{c}{Q2 batch (s)}\\\\\n")
    f.write("Instance & $n$ & Load & B1 & B4 & B3 & RQ & \\rqp & Evals & $\\times$ & B1-vec & DB-1 & DB-8 & B5 & RQ & \\rqp-c & \\rqp-w & $\\times$\\\\\n\\midrule\n")
    f.write("\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n\\end{table*}\n")

# Table: datasets / structure
with open(os.path.join(TAB, "datasets.tex"), "w") as f:
    f.write("% generated by code/make_numbers.py\n")
    f.write("\\begin{table}[t]\n\\centering\\footnotesize\n\\setlength{\\tabcolsep}{2.5pt}\n")
    f.write("\\caption{Instances and RQ-Index structure: services $n$, signatures $|\\Sigma|$ (group-local, summed over groups, in brackets), "
            "groups, elementary weight intervals, raw rule size and RQ-Index size (MB), build time (s; Python on the M4; $^*$C++ build of the same index).}\n\\label{tab:data}\n")
    f.write("\\begin{tabular}{@{}lrrrrrrr@{}}\n\\toprule\n")
    f.write("Instance & $n$ & $|\\Sigma|$ & Groups & $|E|$ & Rules & RQ & Build\\\\\n\\midrule\n")
    for inst, n, tag in INST:
        r = E3[inst]
        f.write(f"{inst} & {n} & {r['signatures']} ({r['local_signatures']}) & {r['groups']} & {r['elem_intervals']} & "
                f"{num(r['raw_rules_MB'], 2)} & {num(r['RQ_base_MB'], 2)} & {num(r['RQ_base_build_s'], 2)}\\\\\n")
    r = S3["SYN200"]
    f.write(f"SYN200 & 200 & {r['signatures']} ({num(r['local_signatures'], 0)}) & {r['groups']} & {num(r['elem_intervals'], 0)} & "
            f"{num(r['raw_rules_MB'], 2)} & {num(r['RQ_base_MB'], 1)} & {num(r['RQ_base_build_s'], 2)}\\\\\n")
    f.write(f"ENT & {ERP['n_services']} & {ERP['signatures']} ({ERP['local_signatures']}) & {ERP['groups']} & {num(ERP['elem_weight_intervals'], 0)} & "
            f"{num(ERP['raw_rules_MB'], 1)} & {num(ERP['RQ_base_MB'], 1)} & {(num(VER['ERP']['index']['build_s'], 2) + '$^*$') if 'ERP' in VER else '---'}\\\\\n")
    f.write("\\bottomrule\n\\end{tabular}\n\\end{table}\n")

# Table: Q3 maintenance
with open(os.path.join(TAB, "q3.tex"), "w") as f:
    f.write("% generated by code/make_numbers.py\n")
    f.write("\\begin{table}[t]\n\\centering\\small\n\\setlength{\\tabcolsep}{2.5pt}\n")
    f.write(f"\\caption{{Q3 maintenance of the stored (best, runner-up) state of {num(E5['PUB2']['N'], 0)} historical parcels (Python, one server core): "
            "$n$ (full-recomputation time), mean time per edit (ms), support size $|U|$, and speedups over service-level maintenance (Svc) and full recomputation (Full). "
            "The final state equals a full recomputation in every run. Best (among \\sys, Svc and Full) in bold, second underlined: \\sys is best in @Q3B@ rows.}\n\\label{tab:q3}\n")
    f.write("\\begin{tabular}{@{}rlrrrrr@{}}\n\\toprule\n")
    f.write("$n$ & Edit & Ours (ms) & $|U|$ & Svc (ms) & $\\times$Svc & $\\times$Full\\\\\n\\midrule\n")
    for inst, n, tag in INST:
        r = E5[inst]; full = r["full_recompute_s"]
        for et, lab in (("cell", "cell"), ("band", "band"), ("gri", "GRI"), ("fuel", "fuel")):
            e = r["edits"][et]
            svc = e.get("service_level_s")
            fo = (lambda v: num(v, 2)) if e['ours_s'] < 0.01 else (lambda v: num(v))
            mq = mark([e['ours_s'] * 1e3, svc * 1e3 if svc else None, full * 1e3], [fo, lambda v: num(v, 1), lambda v: num(v)], [0],
                      table="q3", label=f"n={n}/{et}")
            f.write(f"{(str(n) + ' (' + num(full, 1) + ' s)') if et == 'cell' else ''} & {lab} & {mq[0]} & {num(e['support'], 0)} & "
                    f"{mq[1]} & {sp(svc / e['ours_s']) if svc else '---'} & {sp(full / e['ours_s'])}\\\\\n")
        if inst != "PUB2x1000":
            f.write("\\midrule\n")
    f.write("\\bottomrule\n\\end{tabular}\n\\end{table}\n")

# Table: updates (E4)
with open(os.path.join(TAB, "updates.tex"), "w") as f:
    f.write("% generated by code/make_numbers.py\n")
    f.write("\\begin{table}[t]\n\\centering\\small\n\\setlength{\\tabcolsep}{3pt}\n")
    f.write("\\caption{Index updates on PUB2 instances (mean): price-cell edit ($\\mu$s) in RQ and invalidation in RQ+, DuckDB \\texttt{UPDATE} of the same cell, "
            "full RQ rebuild (s), fuel-ratio edit ($\\mu$s) and zone-rule edit (ms). Answers after all edits equal brute force. "
            "Price edit: best in bold, second underlined.}\n\\label{tab:upd}\n")
    f.write("\\begin{tabular}{@{}rrrrrrr@{}}\n\\toprule\n")
    f.write("$n$ & RQ & RQ+ & DuckDB & Rebuild & Fuel & Zone\\\\\n\\midrule\n")
    for inst, n, tag in INST:
        r = E4[inst]
        mu = mark([r['price_edit']['RQ_base']['mean_us'], r['price_edit']['RQ_plus_invalidate']['mean_us'], r['price_edit']['duckdb_update']['mean_us']],
                  lambda v: num(v), [0, 1], table="updates", label=f"n={n}")
        f.write(f"{n} & {mu[0]} & {mu[1]} & "
                f"{mu[2]} & {num(r['RQ_base_rebuild_s'], 2)} & {num(r['fuel_edit_us'])} & {num(r['zone_edit']['mean_us'] / 1000, 1)}\\\\\n")
    f.write("\\bottomrule\n\\end{tabular}\n\\end{table}\n")

# Table: enterprise
with open(os.path.join(TAB, "erp.tex"), "w") as f:
    f.write("% generated by code/make_numbers.py\n")
    f.write("\\begin{table}[t]\n\\centering\\small\n\\setlength{\\tabcolsep}{3pt}\n")
    f.write(f"\\caption{{Enterprise deployment: {ERP['n_services']} production services, {num(ERP['N'], 0)} shipped parcels, "
            f"{rr['events']} logged rule changes. All answers and all maintained states equal brute force. Best in bold, second underlined.}}\n\\label{{tab:erp}}\n")
    f.write("\\begin{tabular*}{\\columnwidth}{@{\\extracolsep{\\fill}}lr@{}}\n\\toprule\n")
    ea = CERP["ERP/erp"]["agg"] if CERP else None
    lines = [
        ("\\multicolumn{2}{@{}l}{\\emph{Replay of production charges (share of parcels matched)}}", None),
        ("Billable weight / base charge", f"{num(100 * rp['bw'], 1)}\\% / {num(100 * rp['base'], 1)}\\%"),
        (f"Total charge, {num(rp['n_nozip'], 0)} parcels w/o ZIP-list rules", f"{num(100 * rp['total_nozip'], 1)}\\%"),
        ("Total charge, all parcels", f"{num(100 * rp['total'], 1)}\\%"),
        ("\\multicolumn{2}{@{}l}{\\emph{Queries (C++, thread CPU time)}}", None),
    ]
    if ea:
        e1 = mark([ea['E1']['B1s_cpu_p50_ns']['mean'] / 1000, ea['E1']['RQ_q1_cpu_p50_ns']['mean'] / 1000], lambda v: num(v, 2), [1], table="erp", label="ENT Q1",
                  loss_cause=f"n=10 with {num(ERP_EV1, 1)} evaluations per parcel: the bound lists have nothing to prune")
        e2 = mark([ea['E2']['B1v_s_per_100k']['mean'], ea['E2']['RQ_batch_s_per_100k']['mean']], lambda v: num(v, 3), [1], table="erp", label="ENT Q2")
        lines += [("Q1 p50: B1s / RQ-Index ($\\mu$s)", f"{e1[0]} / {e1[1]}"),
                  ("Q2 per 100k parcels: B1v / RQ-Index (s)", f"{e2[0]} / {e2[1]}")]
    rm = mark([rr['total_full_s'] / rr['total_service_level_s'], rr['total_full_s'] / rr['total_pred_s'], rr['total_full_s'] / rr['total_adaptive_s']],
              lambda v: num(v, 1), [1, 2], lower=False, table="erp", label="76 real changes",
              loss_cause="most real changes rewrite whole rule sets, so predicate-level supports approach the coverage")
    lines += [
        ("Eligible services per parcel (mean / max)", f"{num(ERP['eligible_services_per_parcel']['mean'], 1)} / {ERP['eligible_services_per_parcel']['max']}"),
        ("\\multicolumn{2}{@{}l}{\\emph{Q3 maintenance: speedup over full recomputation}}", None),
        ("Synthetic cell edit / band edit", f"{sp(ERP['R5_full_recompute_s'] / cell['s'])}$\\times$ / {sp(ERP['R5_full_recompute_s'] / band['s'])}$\\times$"),
        (f"{rr['events']} real changes: service / predicate level", f"{rm[0]}$\\times$ / {rm[1]}$\\times$"),
        ("\\quad adaptive / per-event oracle", f"{rm[2]}$\\times$ / {num(rr['total_full_s'] / rr['oracle_s'], 1)}$\\times$"),
    ]
    for a, b in lines:
        f.write((a if b is None else f"{a} & {b}") + "\\\\\n")
    f.write("\\bottomrule\n\\end{tabular*}\n\\end{table}\n")

def _cnt(t):
    d = STAND.get(t, {"comparisons": 0, "best": 0, "second": 0})
    return d
PH = {}
for t, key in (("cpp_Q1", "CPPQ1"), ("cpp_Q2", "CPPQ2"), ("py_Q1", "PYQ1B"), ("py_Q2", "PYQ2B"), ("q3", "Q3B"), ("scale", "SCB")):
    d = _cnt(t)
    PH[key] = f"{d['best']} of {d['comparisons']}" + (f" and second in {d['second']}" if t.startswith("py") and d["second"] else "")
for fn in os.listdir(TAB):
    q = os.path.join(TAB, fn)
    txt = open(q).read()
    if "@" in txt:
        for k, v in PH.items():
            txt = txt.replace(f"@{k}@", v)
        open(q, "w").write(txt)
macros = {}
for t, tag in (("cpp_Q1", "CppQOne"), ("cpp_Q2", "CppQTwo"), ("py_Q1", "PyQOne"), ("py_Q2", "PyQTwo"), ("q3", "QThreeTab"),
               ("scale", "ScaleTab"), ("updates", "UpdTab"), ("erp", "ErpTab")):
    d = _cnt(t)
    macros[f"Stand{tag}Best"] = d["best"]; macros[f"Stand{tag}Second"] = d["second"]; macros[f"Stand{tag}Rows"] = d["comparisons"]
with open(os.path.join(PAPER, "numbers.tex"), "a") as f:
    for k, v in sorted(macros.items()):
        f.write(f"\\newcommand{{\\{k}}}{{{v}\\xspace}}\n")
names = {"cpp_Q1": "Table II (C++ Q1)", "cpp_Q2": "Table II (C++ Q2)", "py_Q1": "Table III (Python Q1)", "py_Q2": "Table III (Python Q2)",
         "q3": "Table IV (Q3)", "scale": "Table V (Q3 scale)", "updates": "Table VI (index updates)", "erp": "Table VII (enterprise)"}
text_losses = [
    {"where": "RQ1, B7 result cache", "what": "an exact answer cache in front of the RQ-Index lowers mean Q1 time a further 1.7-5.9x",
     "cause": "real parcels repeat (79-91% steady hit rate); the cache needs the RQ-Index for misses and costs 46-85 MB and 7-30 ms per invalidation"},
    {"where": "RQ3, B8 cost-matrix IVM", "what": "B8 is faster on fuel edits",
     "cause": "B8 stores all |X|*n costs (802 MB at n=1000 vs 16.9 MB) and reads the stored costs of the parcels whose top-2 changes"},
    {"where": "RQ3, enterprise rule-change stream", "what": "predicate-level supports are about as fast as service-level ones",
     "cause": "most real changes rewrite whole rule sets, so the predicate-level support approaches the coverage; on the public local-edit stream predicate-level is 3.8-17.6x faster"},
    {"where": "RQ1/RQ5, ENT single queries", "what": "compiled RQ-Index at 0.9x of B1s",
     "cause": "n=10 and 4.6 evaluations per parcel: nothing to prune"},
]
json.dump({"tables": [{"table": names[t], **{k: v for k, v in d.items()}, "worse": d["worse"]} for t, d in STAND.items()],
           "losses_in_text": text_losses,
           "rule": "ranking by printed (rounded) values; ties all bold; second best underlined when at least three methods compete"},
          open(os.path.join(RES, "standing_counts.json"), "w"), indent=1)
print(f"{len(M)} macros written; compiled rows: {len(CROWS)}")
