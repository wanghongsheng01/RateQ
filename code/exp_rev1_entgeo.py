"""Rev-1 #17: E1 (single query) and E2 (batch) on the ent-geo workload, with olist and furniture
run in the same harness for comparison. ent-geo = weights/dimensions/residential flags drawn from
the enterprise's real US parcels (_private/rev1_ent_shapes.npz, written by exp_rev1_entgeo_prep.py),
destinations weighted by ZCTA population (ACS 2023 5-year B01003). Output is aggregate only.

E1: 1000 queries, B1 scalar brute force, RQ-Index, RQ+ (lazy rows warmed on the queries, as in
pilot.E1); 5 interleaved repetitions; thread CPU time. E2: 100k parcels, vectorised brute force,
RQ-Index batch, RQ+ batch cold (fresh index per repetition) and warm.
Outputs results/rev1/entgeo_E1E2[_tag].json. Usage: python exp_rev1_entgeo.py PUB2,PUB2x60 entgeo,olist,furn [tag]
"""
import json, sys, time
import numpy as np
from exp_rev1_common import INSTS, WLS, REPS, tns, load, uptime, summ, args, save
from rateq.model import brute_vec
from rateq.index import RQIndex, RQIndexPlus, Compiled, fuel_fn, brute1


def e1(inst, ix, ixp, wl, q=1000):
    comp, fuel = [Compiled(s) for s in inst.services], fuel_fn(inst)
    P = WLS[wl](q, seed=11)
    truth = brute_vec(inst, P)
    for i in range(q):
        ixp.query1(*args(P, i))
    meth = ["B1_brute", "RQ_index", "RQ_plus"]
    mean_us = {m: [] for m in meth}; p50 = {m: [] for m in meth}; p95 = {m: [] for m in meth}
    ev = {}; ag = {m: 1.0 for m in meth}
    for rep in range(REPS):
        for m in meth[rep % 3:] + meth[:rep % 3]:
            lat = np.empty(q); ok = 0; st = []
            for i in range(q):
                x = args(P, i)
                t = tns()
                if m == "B1_brute":
                    r = brute1(comp, fuel, *x)
                elif m == "RQ_index":
                    r = ix.query1(*x, stats=st)
                else:
                    r = ixp.query1(*x, stats=st)
                lat[i] = tns() - t
                ok += (r[0] == truth[0][i] and r[1] == truth[1][i])
            mean_us[m].append(lat.mean() / 1e3); p50[m].append(np.median(lat) / 1e3); p95[m].append(np.percentile(lat, 95) / 1e3)
            ag[m] = min(ag[m], ok / q); ev[m] = float(np.mean(st)) if st else float(len(inst.services))
    sp = {m: summ(np.array(mean_us["B1_brute"]) / np.array(mean_us[m])) for m in meth[1:]}
    sp50 = {m: summ(np.array(p50["B1_brute"]) / np.array(p50[m])) for m in meth[1:]}
    return {"q": q, "evals_per_parcel": ev, "mean_us": {m: summ(v) for m, v in mean_us.items()},
            "p50_us": {m: summ(v) for m, v in p50.items()}, "p95_us": {m: summ(v) for m, v in p95.items()},
            "speedup_mean_vs_B1": sp, "speedup_p50_vs_B1": sp50, "agree": ag}


def e2(inst, ix, wl, N=100_000):
    P = WLS[wl](N, seed=22)
    truth = brute_vec(inst, P)
    ixw = RQIndexPlus(inst); ixw.query_batch(WLS[wl](N, seed=23))   # warm on another stream (as pilot.E2)
    meth = ["B1_brute_vec", "RQ_batch", "RQ_plus_cold", "RQ_plus_warm"]
    ts = {m: [] for m in meth}; ev = {}; ag = {m: 1.0 for m in meth}
    for rep in range(REPS):
        cold = RQIndexPlus(inst)
        for m in meth[rep % 4:] + meth[:rep % 4]:
            st = []
            t = tns()
            if m == "B1_brute_vec":
                b, a = brute_vec(inst, P)
            elif m == "RQ_batch":
                b, a = ix.query_batch(P, st)
            elif m == "RQ_plus_cold":
                b, a = cold.query_batch(P, st)
            else:
                b, a = ixw.query_batch(P, st)
            ts[m].append((tns() - t) / 1e9)
            ag[m] = min(ag[m], float(((b == truth[0]) & (a == truth[1])).mean()))
            ev[m] = st[0] / N if st else float(len(inst.services))
    sp = {m: summ(np.array(ts["B1_brute_vec"]) / np.array(ts[m])) for m in meth[1:]}
    return {"N": N, "cpu_s": {m: summ(v) for m, v in ts.items()}, "evals_per_parcel": ev, "speedup_vs_B1": sp, "agree": ag}


if __name__ == "__main__":
    insts = sys.argv[1].split(",")
    wls = sys.argv[2].split(",") if len(sys.argv) > 2 else ["entgeo", "olist", "furn"]
    tag = sys.argv[3] if len(sys.argv) > 3 else ""
    res = {"_uptime_start": uptime()}
    for iname in insts:
        inst = INSTS[iname]()
        ix = RQIndex(inst); ixp = RQIndexPlus(inst)
        for wl in wls:
            t0 = time.time()
            r = {"n": len(inst.services), "load_start": load(), "E1": e1(inst, ix, ixp, wl), "E2": e2(inst, ix, wl)}
            r["load_end"] = load(); r["uptime_end"] = uptime(); r["wall_s"] = time.time() - t0
            res[f"{iname}/{wl}"] = r
            print(iname, wl, "E1 mean_us", {m: round(v["median"], 1) for m, v in r["E1"]["mean_us"].items()},
                  "evals", r["E1"]["evals_per_parcel"], "E2 s", {m: round(v["median"], 3) for m, v in r["E2"]["cpu_s"].items()},
                  "agree", r["E1"]["agree"], r["E2"]["agree"], flush=True)
            save(f"entgeo_E1E2{tag}", res)
