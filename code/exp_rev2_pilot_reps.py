"""Rev-2 B3: the method-level comparison of pilot.E1 / pilot.E2 (Table III) with 5 repetitions.

This wrapper re-runs pilot.py's E1 and E2 configurations (same instances, workloads, seeds, query
counts and method objects; pilot.py itself is imported, not edited) with a repetition loop in which
the methods are interleaved (rotated order per repetition). Timer: time.thread_time_ns for the
single-threaded Python methods; DuckDB (multi-threaded engine) is timed by wall clock
(time.perf_counter) with process CPU time recorded as well, and runs in a separate process pinned
to its own cores. DuckDB (tuned ASOF variant, 1 and 8 threads) runs on ALL 100,000 parcels at every
n (no scaling). The naive BETWEEN-join DuckDB variant (B2_naive, not in Table III) is not re-run.

Schema: as pilot_E1*/pilot_E2* (per method: E1 mean_us/p50_us/p95_us/agree/q[/evals],
E2 s/agree[/evals_per_parcel/...]); every value is the MEDIAN over repetitions, and each method also
carries "reps" (per-repetition values), "ci95" (t-interval of the mean over repetitions) and
"n_reps". Parts: results/rev2/parts/*.json; merged: results/pilot_E{1,2}_{pub2,syn}_server.json.

Usage:  python exp_rev2_pilot_reps.py py <INST> [reps]          E1 + E2 Python methods for one instance
        python exp_rev2_pilot_reps.py duck <INST> <wl> <threads> [reps]
        python exp_rev2_pilot_reps.py merge
"""
import json, os, sys, time, platform, glob
import numpy as np
import pilot
from pilot import INSTS, WLS, args, KEYSPAN
from rateq.model import brute_vec
from rateq.index import RQIndex, RQIndexPlus, brute1, Compiled, fuel_fn
from rateq.baselines import Precompute, DuckSQLTuned, RTreeIdx, kinetic_batch, mip_query1

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PARTS = os.path.join(ROOT, "results", "rev2", "parts")
os.makedirs(PARTS, exist_ok=True)
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447}


def ci(x):
    x = np.asarray(x, float)
    if len(x) < 2:
        return [float("nan"), float("nan")]
    h = T975.get(len(x) - 1, 1.96) * x.std(ddof=1) / np.sqrt(len(x))
    return [float(x.mean() - h), float(x.mean() + h)]


def env():
    return {"python": platform.python_version(), "numpy": np.__version__, "host": platform.node(), "cpu": pilot._cpu_model(),
            "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
            "loadavg": [round(x, 2) for x in os.getloadavg()], "time": time.strftime("%Y-%m-%d %H:%M:%S")}


def fold(reps, extra=None):
    """reps: list of dicts of numbers -> median per key + reps + ci95."""
    keys = [k for k in reps[0] if isinstance(reps[0][k], (int, float)) and not isinstance(reps[0][k], bool)]
    out = {k: float(np.median([r[k] for r in reps])) for k in keys}
    out["reps"] = {k: [r[k] for r in reps] for k in keys}
    out["ci95"] = {k: ci([r[k] for r in reps]) for k in keys}
    out["n_reps"] = len(reps)
    out.update(extra or {})
    return out


def lat_stats(lat_ns):
    x = np.asarray(lat_ns) / 1e3
    return {"mean_us": float(x.mean()), "p50_us": float(np.percentile(x, 50)), "p95_us": float(np.percentile(x, 95)),
            "p99_us": float(np.percentile(x, 99))}


def e1(iname, reps, q=1000):
    inst = INSTS[iname]()
    res = {}
    for wl in ["olist", "furn"]:
        P = WLS[wl](q, seed=11)
        truth = brute_vec(inst, P)
        comp, fuel = [Compiled(s) for s in inst.services], fuel_fn(inst)
        pre = Precompute(inst)
        ix = RQIndex(inst)
        ixp = RQIndexPlus(inst)
        for i in range(q):  # warm the lazy rows once (as pilot.E1)
            ixp.query1(*args(P, i))
        meths = {"B1_brute": (lambda *a: brute1(comp, fuel, *a), P, q, truth, False),
                 "B4_precompute": (pre.query1, P, q, truth, False),
                 "RQ_base": (ix.query1, P, q, truth, True),
                 "RQ_plus": (ixp.query1, P, q, truth, True)}
        extra = {}
        if iname in ("PUB", "PUBx60", "PUB2", "PUB2x60"):
            o = 4
            P2 = P.subset(np.arange(q)); P2.key = o * KEYSPAN + (P2.key % KEYSPAN)
            truth2 = brute_vec(inst, P2)
            t = time.thread_time_ns(); rt = RTreeIdx(inst, o * KEYSPAN, (o + 1) * KEYSPAN - 1); bt = (time.thread_time_ns() - t) / 1e9
            meths["B3_rtree"] = (rt.query1, P2, min(q, 300), truth2, False)
            extra["B3_rtree"] = {"boxes": rt.nboxes, "build_s": bt}
            meths["B6_mip"] = (lambda *a: None, P, 100, truth, "mip")
        names = list(meths)
        per = {m: [] for m in names}; agree = {m: 1.0 for m in names}; evals = {}
        for rep in range(reps):
            for m in names[rep % len(names):] + names[:rep % len(names)]:
                fn, PP, qq, tr, has_stats = meths[m]
                lat = np.empty(qq, dtype=np.int64); ok = 0; st = []
                for i in range(qq):
                    if has_stats == "mip":
                        t = time.thread_time_ns(); c, sid = mip_query1(inst, PP, i, fuel); lat[i] = time.thread_time_ns() - t
                        ok += (c == tr[0][i] and sid == tr[1][i]); continue
                    a = args(PP, i)
                    t = time.thread_time_ns()
                    r = fn(*a, stats=st) if has_stats is True else fn(*a)
                    lat[i] = time.thread_time_ns() - t
                    ok += (r[0] == tr[0][i] and r[1] == tr[1][i])
                per[m].append(lat_stats(lat)); agree[m] = min(agree[m], ok / qq)
                if st:
                    evals[m] = float(np.mean(st))
        r = {}
        for m in names:
            r[m] = fold(per[m], {"agree": agree[m], "q": meths[m][2], **({"evals": evals[m]} if m in evals else {}), **extra.get(m, {})})
        r["speedup_p50_best_RQ_over_B1"] = fold([{"x": per["B1_brute"][k]["p50_us"] / min(per["RQ_base"][k]["p50_us"], per["RQ_plus"][k]["p50_us"])}
                                                 for k in range(reps)])
        res[f"{iname}/{wl}"] = {"n": len(inst.services), **r}
        print("E1", iname, wl, {m: round(r[m]["p50_us"], 1) for m in names}, {m: r[m]["agree"] for m in names}, flush=True)
    return res


def e2(iname, reps, N=100000):
    inst = INSTS[iname]()
    res = {}
    for wl in ["olist", "furn"]:
        P = WLS[wl](N, seed=22)
        P3 = WLS[wl](N, seed=23)
        truth = brute_vec(inst, P); truth3 = brute_vec(inst, P3)
        ix = RQIndex(inst)
        names = ["B1_brute_vec", "B5_kinetic", "RQ_base", "RQ_plus"]   # RQ_plus = cold then warm on a fresh index
        per = {m: [] for m in ["B1_brute_vec", "B5_kinetic", "RQ_base", "RQ_plus_cold", "RQ_plus_warm"]}
        agree = {m: 1.0 for m in per}; info = {}
        for rep in range(reps):
            for m in names[rep % 4:] + names[:rep % 4]:
                st = []
                if m == "B1_brute_vec":
                    t = time.thread_time_ns(); b, a = brute_vec(inst, P); s = (time.thread_time_ns() - t) / 1e9
                    per[m].append({"s": s}); agree[m] = min(agree[m], float(((b == truth[0]) & (a == truth[1])).mean()))
                elif m == "B5_kinetic":
                    t = time.thread_time_ns(); b, a = kinetic_batch(ix, P, st); s = (time.thread_time_ns() - t) / 1e9
                    per[m].append({"s": s, "evals_per_parcel": st[0][0] / N, "events": st[0][1]})
                    agree[m] = min(agree[m], float(((b == truth[0]) & (a == truth[1])).mean()))
                elif m == "RQ_base":
                    t = time.thread_time_ns(); b, a = ix.query_batch(P, st); s = (time.thread_time_ns() - t) / 1e9
                    per[m].append({"s": s, "evals_per_parcel": st[0] / N})
                    agree[m] = min(agree[m], float(((b == truth[0]) & (a == truth[1])).mean()))
                else:
                    ixp = RQIndexPlus(inst)
                    t = time.thread_time_ns(); b, a = ixp.query_batch(P, st); s = (time.thread_time_ns() - t) / 1e9
                    per["RQ_plus_cold"].append({"s": s, "evals_per_parcel": st[0] / N})
                    agree["RQ_plus_cold"] = min(agree["RQ_plus_cold"], float(((b == truth[0]) & (a == truth[1])).mean()))
                    st = []
                    t = time.thread_time_ns(); b, a = ixp.query_batch(P3, st); s = (time.thread_time_ns() - t) / 1e9
                    per["RQ_plus_warm"].append({"s": s, "evals_per_parcel": st[0] / N})
                    agree["RQ_plus_warm"] = min(agree["RQ_plus_warm"], float(((b == truth3[0]) & (a == truth3[1])).mean()))
        r = {m: fold(per[m], {"agree": agree[m]}) for m in per}
        res[f"{iname}/{wl}"] = {"n": len(inst.services), "N": N, **r}
        print("E2", iname, wl, {m: round(r[m]["s"], 3) for m in r}, {m: r[m]["agree"] for m in r}, flush=True)
    return res


def duck(iname, wl, threads, reps, N=100000):
    inst = INSTS[iname]()
    P = WLS[wl](N, seed=22)
    truth = brute_vec(inst, P)
    db = DuckSQLTuned(inst, threads=threads)
    mem = os.environ.get("REV2_DUCK_MEM")          # e.g. "24GB": cap DuckDB memory, spill to disk beyond it
    if mem:
        db.con.execute(f"SET memory_limit='{mem}'")
        db.con.execute(f"SET temp_directory='{os.path.join(ROOT, 'results', 'rev2', 'duck_tmp', f'{iname}_{wl}_t{threads}_{os.getpid()}')}'")
    per, agree = [], 1.0
    for rep in range(reps):
        w0, c0 = time.perf_counter(), time.process_time()
        b, a = db.query_batch(P)
        w, c = time.perf_counter() - w0, time.process_time() - c0
        per.append({"s": w, "process_cpu_s": c})
        agree = min(agree, float(((b == truth[0]) & (a == truth[1])).mean()))
        print("duck", iname, wl, threads, rep, round(w, 2), round(c, 2), agree, flush=True)
    return fold(per, {"measured_on": N, "variant": "asof", "agree": agree, "timer": "wall (s), process CPU (process_cpu_s)",
                      "threads": threads, "memory_limit": mem or "duckdb default (80% of RAM)"})


def merge():
    groups = {"pub2": ["PUB2", "PUB2x60", "PUB2x240", "PUB2x1000"], "syn": ["SYN200"]}
    for tag, insts in groups.items():
        for E in ("E1", "E2"):
            out = {}
            for iname in insts:
                f = os.path.join(PARTS, f"{E}_{iname}.json")
                if os.path.exists(f):
                    out.update({k: v for k, v in json.load(open(f)).items() if not k.startswith("_")})
            if E == "E2":
                for f in glob.glob(os.path.join(PARTS, "duck_*.json")):
                    d = json.load(open(f))
                    k = f"{d['inst']}/{d['wl']}"
                    if k in out:
                        out[k][f"B2_duckdb_t{d['threads']}"] = d["result"]
                for k, v in out.items():
                    if "B2_duckdb_t1" in v and "RQ_base" in v:
                        v["speedup_duckdb_t1_over_RQ"] = v["B2_duckdb_t1"]["s"] / v["RQ_base"]["s"]
            out["_env"] = {**env(), "note": "median over repetitions; per-repetition values in reps; see code/exp_rev2_pilot_reps.py"}
            for f in glob.glob(os.path.join(PARTS, f"*{E}_*.json")):
                pass
            json.dump(out, open(os.path.join(ROOT, "results", f"pilot_{E}_{tag}_server.json"), "w"), indent=1)
            print("wrote", f"pilot_{E}_{tag}_server.json", len(out) - 1, "configs")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "py":
        iname = sys.argv[2]; reps = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        r1 = e1(iname, reps); r1["_env"] = env()
        json.dump(r1, open(os.path.join(PARTS, f"E1_{iname}.json"), "w"), indent=1)
        r2 = e2(iname, reps); r2["_env"] = env()
        json.dump(r2, open(os.path.join(PARTS, f"E2_{iname}.json"), "w"), indent=1)
    elif cmd == "duck":
        iname, wl, th = sys.argv[2], sys.argv[3], int(sys.argv[4]); reps = int(sys.argv[5]) if len(sys.argv) > 5 else 5
        r = duck(iname, wl, th, reps)
        json.dump({"inst": iname, "wl": wl, "threads": th, "result": r, "_env": env()},
                  open(os.path.join(PARTS, f"duck_{iname}_{wl}_t{th}.json"), "w"), indent=1)
    else:
        merge()
