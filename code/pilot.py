"""RateQ pilot: E1 single-query latency, E2 batch throughput, E3 memory/build,
E4 update cost, E5 incremental historical recomputation (Q3). Writes results/pilot_<exp>.json.
Usage: python pilot.py E1 E2 ...   (all if none given)
"""
import json, sys, time, platform, os
import numpy as np
from rateq.instances import (build_pub, build_pub_scaled, build_syn, build_erp_style, build_pub2, build_pub2_scaled,
                             parcels_olist, parcels_furniture, KEYSPAN)
from rateq.model import brute_vec, INF
from rateq.index import RQIndex, RQIndexPlus, brute1, Compiled, fuel_fn
from rateq.baselines import Precompute, DuckSQL, DuckSQLTuned, RTreeIdx, kinetic_batch, mip_query1
from rateq.maintain import Maintainer, top2

OUT = os.path.join(os.path.dirname(__file__), "..", "results")
os.makedirs(OUT, exist_ok=True)
INSTS = {"PUB": build_pub, "PUBx60": lambda: build_pub_scaled(60), "PUBx240": lambda: build_pub_scaled(240),
         "PUBx1000": lambda: build_pub_scaled(1000), "SYN200": lambda: build_syn(200), "ERPSTYLE20": lambda: build_erp_style(20),
         "PUB2": build_pub2, "PUB2x60": lambda: build_pub2_scaled(60), "PUB2x240": lambda: build_pub2_scaled(240),
         "PUB2x1000": lambda: build_pub2_scaled(1000)}
SEL = os.environ.get("PILOT_INSTS")
TAG = os.environ.get("PILOT_TAG", "")


def names(default):
    return SEL.split(",") if SEL else default
WLS = {"olist": parcels_olist, "furn": parcels_furniture}


def pct(x):
    x = np.asarray(x) * 1e6
    return {"mean_us": float(x.mean()), "p50_us": float(np.percentile(x, 50)), "p95_us": float(np.percentile(x, 95))}


def args(P, i):
    return (int(P.key[i]), float(P.a[i]), float(P.L[i]), float(P.M[i]), float(P.S[i]), float(P.res[i]), int(P.day[i]))


def _cpu_model():
    try:
        if sys.platform == "darwin":
            import subprocess
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "unknown"


def save(name, obj):
    obj["_env"] = {"python": platform.python_version(), "numpy": np.__version__, "machine": platform.machine(),
                   "host": platform.node(), "cpu": _cpu_model(), "threads": 1,
                   "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
                   "loadavg_at_save": [round(x, 2) for x in os.getloadavg()]}
    json.dump(obj, open(os.path.join(OUT, f"pilot_{name}{TAG}.json"), "w"), indent=1)


def run_single(fn, P, q, truth):
    lat, agree = [], 0
    for i in range(q):
        a = args(P, i)
        t = time.perf_counter(); r = fn(*a); lat.append(time.perf_counter() - t)
        agree += (r[0] == truth[0][i] and r[1] == truth[1][i])
    d = pct(lat); d["agree"] = agree / q; d["q"] = q
    return d


def E1(q=1000):
    res = {}
    for iname in names(["PUB", "PUBx60", "PUBx240", "PUBx1000", "SYN200", "ERPSTYLE20"]):
        inst = INSTS[iname]()
        for wl in ["olist", "furn"]:
            P = WLS[wl](q, seed=11)
            truth = brute_vec(inst, P)
            comp, fuel = [Compiled(s) for s in inst.services], fuel_fn(inst)
            r = {}
            r["B1_brute"] = run_single(lambda *a: brute1(comp, fuel, *a), P, q, truth)
            pre = Precompute(inst)
            r["B4_precompute"] = run_single(pre.query1, P, q, truth)
            ix = RQIndex(inst); st = []
            r["RQ_base"] = run_single(lambda *a: ix.query1(*a, stats=st), P, q, truth); r["RQ_base"]["evals"] = float(np.mean(st))
            ixp = RQIndexPlus(inst)
            for i in range(q):  # warm the lazy rows once (reported separately in E3)
                ixp.query1(*args(P, i))
            st = []
            r["RQ_plus"] = run_single(lambda *a: ixp.query1(*a, stats=st), P, q, truth); r["RQ_plus"]["evals"] = float(np.mean(st))
            if iname in ("PUB", "PUBx60", "PUB2", "PUB2x60"):
                o = 4  # R-tree restricted to one origin (box count), queries re-keyed to that origin
                P2 = P.subset(np.arange(q)); P2.key = o * KEYSPAN + (P2.key % KEYSPAN)
                truth2 = brute_vec(inst, P2)
                t = time.perf_counter(); rt = RTreeIdx(inst, o * KEYSPAN, (o + 1) * KEYSPAN - 1); bt = time.perf_counter() - t
                r["B3_rtree"] = run_single(rt.query1, P2, min(q, 300), truth2)
                r["B3_rtree"].update({"boxes": rt.nboxes, "build_s": bt})
                qm = 100
                lat, agree = [], 0
                for i in range(qm):
                    t = time.perf_counter(); c, sid = mip_query1(inst, P, i, fuel); lat.append(time.perf_counter() - t)
                    agree += (c == truth[0][i] and sid == truth[1][i])
                r["B6_mip"] = pct(lat); r["B6_mip"].update({"agree": agree / qm, "q": qm})
            res[f"{iname}/{wl}"] = {"n": len(inst.services), **r}
            print(iname, wl, {k: round(v["p50_us"], 1) for k, v in r.items()},
                  {k: v["agree"] for k, v in r.items()}, flush=True)
    save("E1", res)


def E2(N=100000):
    res = {}
    for iname in names(["PUB", "PUBx60", "PUBx240", "PUBx1000", "SYN200", "ERPSTYLE20"]):
        inst = INSTS[iname]()
        for wl in ["olist", "furn"]:
            P = WLS[wl](N, seed=22)
            r = {}
            t = time.perf_counter(); truth = brute_vec(inst, P); r["B1_brute_vec"] = {"s": time.perf_counter() - t, "agree": 1.0}
            Nd = N if len(inst.services) <= 240 else 20000
            for th in (1, 8):  # B2: ASOF-join SQL (DuckSQLTuned); the BETWEEN-join version is kept as B2_naive
                db = DuckSQLTuned(inst, threads=th); Pd = P.subset(np.arange(Nd))
                t = time.perf_counter(); b, a = db.query_batch(Pd); s = time.perf_counter() - t
                r[f"B2_duckdb_t{th}"] = {"s": s * N / Nd, "measured_on": Nd, "variant": "asof",
                                         "agree": float(((b == truth[0][:Nd]) & (a == truth[1][:Nd])).mean())}
            if len(inst.services) <= 240:
                Nn = min(N, 5000); db = DuckSQL(inst, threads=1); Pd = P.subset(np.arange(Nn))
                t = time.perf_counter(); b, a = db.query_batch(Pd); s = time.perf_counter() - t
                r["B2_naive_t1"] = {"s": s * N / Nn, "measured_on": Nn,
                                    "agree": float(((b == truth[0][:Nn]) & (a == truth[1][:Nn])).mean())}
            ix = RQIndex(inst)
            st = []; t = time.perf_counter(); b, a = kinetic_batch(ix, P, st); s = time.perf_counter() - t
            r["B5_kinetic"] = {"s": s, "agree": float(((b == truth[0]) & (a == truth[1])).mean()),
                               "evals_per_parcel": st[0][0] / N, "events": st[0][1]}
            st = []; t = time.perf_counter(); b, a = ix.query_batch(P, st); s = time.perf_counter() - t
            r["RQ_base"] = {"s": s, "agree": float(((b == truth[0]) & (a == truth[1])).mean()), "evals_per_parcel": st[0] / N}
            ixp = RQIndexPlus(inst)
            st = []; t = time.perf_counter(); b, a = ixp.query_batch(P, st); s = time.perf_counter() - t
            r["RQ_plus_cold"] = {"s": s, "agree": float(((b == truth[0]) & (a == truth[1])).mean()), "evals_per_parcel": st[0] / N}
            P3 = WLS[wl](N, seed=23); truth3 = brute_vec(inst, P3)
            st = []; t = time.perf_counter(); b, a = ixp.query_batch(P3, st); s = time.perf_counter() - t
            r["RQ_plus_warm"] = {"s": s, "agree": float(((b == truth3[0]) & (a == truth3[1])).mean()), "evals_per_parcel": st[0] / N}
            res[f"{iname}/{wl}"] = {"n": len(inst.services), "N": N, **r}
            print(iname, wl, {k: round(v["s"], 3) for k, v in r.items()}, {k: v["agree"] for k, v in r.items()}, flush=True)
    save("E2", res)


def E3():
    res = {}
    for iname in names(["PUB", "PUBx60", "PUBx240", "PUBx1000", "SYN200", "ERPSTYLE20"]):
        inst = INSTS[iname]()
        t = time.perf_counter(); ix = RQIndex(inst); bt = time.perf_counter() - t
        t = time.perf_counter(); pre = Precompute(inst); pt = time.perf_counter() - t
        ixp = RQIndexPlus(inst)
        P = parcels_furniture(100000, seed=33); Q = parcels_olist(100000, seed=33)
        t = time.perf_counter(); ixp.query_batch(P); ixp.query_batch(Q); lt = time.perf_counter() - t
        rows = sum(len(g["rows"]) for g in ixp.groups)
        res[iname] = {"n": len(inst.services), "signatures": ix.n_sig,
                      "local_signatures": int(sum(len(g["lz"]) for g in ix.groups)), "groups": len(ix.groups),
                      "elem_intervals": int(sum(len(g["E"]) + 1 for g in ix.groups)),
                      "RQ_base_MB": ix.nbytes() / 1e6, "RQ_base_build_s": bt,
                      "RQ_plus_MB_after_200k": ixp.nbytes() / 1e6, "RQ_plus_rows_after_200k": rows,
                      "RQ_plus_200k_incl_lazy_s": lt,
                      "precompute_MB": pre.nbytes() / 1e6, "precompute_build_s": pt,
                      "raw_rules_MB": sum(s.price.nbytes + s.zstart.nbytes + s.zzone.nbytes for s in inst.services) / 1e6}
        print(iname, res[iname], flush=True)
    save("E3", res)


def E4(n_edits=200, seed=5):
    rng = np.random.default_rng(seed)
    res = {}
    for iname in names(["PUBx240", "SYN200", "ERPSTYLE20"]):
        inst = INSTS[iname]()
        ix = RQIndex(inst); ixp = RQIndexPlus(inst)
        P = parcels_furniture(20000, seed=44)
        ixp.query_batch(P)
        db = DuckSQL(inst, threads=1)
        t_ix, t_ixp, t_db, touched = [], [], [], []
        for _ in range(n_edits):
            s = inst.services[int(rng.integers(len(inst.services)))]
            zs, ks = np.nonzero(s.price < INF)
            j = int(rng.integers(len(zs))); z, k = int(zs[j]), int(ks[j])
            newp = int(s.price[z, k] * rng.uniform(0.85, 1.15))
            t = time.perf_counter(); touched.append(ix.update_price(s.sid, z, k, newp)); t_ix.append(time.perf_counter() - t)
            t = time.perf_counter(); ixp.update_price(s.sid, z, k, newp); t_ixp.append(time.perf_counter() - t)
            t = time.perf_counter(); db.update_price(s.sid, z, k, newp); t_db.append(time.perf_counter() - t)
        t = time.perf_counter(); Precompute(inst); pre_rebuild = time.perf_counter() - t
        t = time.perf_counter(); RQIndex(inst); rebuild = time.perf_counter() - t
        truth = brute_vec(inst, P)
        b, a = ix.query_batch(P); ok_ix = float(((b == truth[0]) & (a == truth[1])).mean())
        t = time.perf_counter(); b, a = ixp.query_batch(P); requery = time.perf_counter() - t
        ok_ixp = float(((b == truth[0]) & (a == truth[1])).mean())
        # fuel and zone edits
        fc = next(iter(inst.fuel.starts))
        t = time.perf_counter(); ix.update_fuel(fc, 100, 2300); tf = time.perf_counter() - t
        tz, created = [], []
        for _ in range(50):
            s = inst.services[int(rng.integers(len(inst.services)))]
            o = int(rng.integers(5)); z3 = int(rng.integers(10, 990))
            lo = o * KEYSPAN + z3 * 100; hi = lo + int(rng.integers(1, 5)) * 100 - 1
            nz = int(rng.integers(0, s.price.shape[0]))
            t = time.perf_counter(); created.append(ix.update_zone(s.sid, lo, hi, nz)); tz.append(time.perf_counter() - t)
        truth = brute_vec(inst, P)
        b, a = ix.query_batch(P); ok_zone = float(((b == truth[0]) & (a == truth[1])).mean())
        res[iname] = {"n": len(inst.services), "price_edit": {"RQ_base": pct(t_ix), "RQ_plus_invalidate": pct(t_ixp),
                      "duckdb_update": pct(t_db), "rows_touched_mean": float(np.mean(touched))},
                      "RQ_base_rebuild_s": rebuild, "precompute_rebuild_s": pre_rebuild,
                      "RQ_plus_requery_20k_after_edits_s": requery,
                      "exact_after_price_edits": {"RQ_base": ok_ix, "RQ_plus": ok_ixp},
                      "fuel_edit_us": tf * 1e6, "zone_edit": pct(tz), "zone_new_signatures": int(np.sum(created)),
                      "exact_after_zone_edits": ok_zone}
        print(iname, json.dumps(res[iname])[:600], flush=True)
    save("E4", res)


def E5(N=200000, seed=6):
    rng = np.random.default_rng(seed)
    res = {}
    for iname in names(["PUBx240", "ERPSTYLE20"]):
        inst = INSTS[iname]()
        ix = RQIndex(inst)
        A, B = parcels_olist(N // 2, seed=55), parcels_furniture(N // 2, seed=56)
        from rateq.model import Parcels
        P = Parcels(*(np.r_[getattr(A, f), getattr(B, f)] for f in ("key", "a", "L", "M", "S", "res", "day")))
        t = time.perf_counter(); mt = Maintainer(inst, ix, P); init = time.perf_counter() - t
        t = time.perf_counter(); top2(inst, P, mt.F0); full = time.perf_counter() - t
        log = {"cell": [], "band": [], "gri": [], "fuel": []}
        def edit_cell(scope):
            s = inst.services[int(rng.integers(len(inst.services)))]
            zs, ks = np.nonzero(s.price < INF)
            j = int(rng.integers(len(zs))); z, k = int(zs[j]), int(ks[j])
            zl = [z] if scope == "cell" else sorted(set(zs[ks == k].tolist()))
            f = rng.uniform(0.85, 1.15)
            t = time.perf_counter()
            U = []
            for zz in zl:
                s.price[zz, k] = int(s.price[zz, k] * f)
                U.append(mt.support_cell(s.sid, zz, k))
            U = np.unique(np.concatenate(U)) if U else np.zeros(0, np.int64)
            nU, nR = mt.apply(s.sid, U)
            dt = time.perf_counter() - t
            t = time.perf_counter(); Us = mt.support_service(s.sid); svc_t = time.perf_counter() - t
            from rateq.model import eval_service
            t = time.perf_counter(); eval_service(s, P.subset(Us), mt.F0[Us], inst.fuel); svc_t += time.perf_counter() - t
            return {"ours_s": dt, "support": nU, "recomputed": nR, "service_level_s": svc_t, "service_support": len(Us)}
        for _ in range(100):
            log["cell"].append(edit_cell("cell"))
        for _ in range(20):
            log["band"].append(edit_cell("band"))
        for _ in range(3):  # general rate increase: whole service
            s = inst.services[int(rng.integers(len(inst.services)))]
            t = time.perf_counter()
            s.price[:] = np.where(s.price < INF, np.round(s.price * 1.059), INF).astype(np.int64)
            U = mt.support_service(s.sid); nU, nR = mt.apply(s.sid, U)
            log["gri"].append({"ours_s": time.perf_counter() - t, "support": nU, "recomputed": nR})
        for _ in range(3):  # fuel change for one week of one class: support = parcels shipped that week
            fc = list(inst.fuel.starts)[int(rng.integers(len(inst.fuel.starts)))]
            # a published week (index >= 1): week 0 is extended back to the start of the year in PUB2
            w = int(rng.integers(1, len(inst.fuel.starts[fc]) - 1)) if len(inst.fuel.starts[fc]) > 2 else 0
            inst.fuel.bps[fc][w] += 75
            # support = theorem's fuel support D_w (segment 0 also covers days before its start,
            # the last segment is open to the right); see Maintainer.support_fuel
            # merged fuel-class update: every service of the class is repaired on the common support,
            # and each parcel that needs it is recomputed once (Maintainer.apply_many)
            t = time.perf_counter(); U = mt.support_fuel(fc, w)
            _, rec = mt.apply_many([s.sid for s in inst.services if s.fuel_class == fc], U)
            log["fuel"].append({"ours_s": time.perf_counter() - t, "support": len(U), "recomputed": rec})
        c1, s1, c2, s2 = top2(inst, P, mt.F0)
        exact = bool((c1 == mt.c1).all() and (s1 == mt.s1).all() and (c2 == mt.c2).all() and (s2 == mt.s2).all())
        summ = {}
        for k, v in log.items():
            summ[k] = {key: float(np.mean([x[key] for x in v])) for key in v[0]}
            summ[k]["count"] = len(v)
        res[iname] = {"n": len(inst.services), "N": N, "full_recompute_s": full, "init_s": init,
                      "exact_final_state": exact, "edits": summ}
        print(iname, json.dumps(res[iname])[:900], flush=True)
    save("E5", res)


if __name__ == "__main__":
    exps = sys.argv[1:] or ["E1", "E2", "E3", "E4", "E5"]
    for e in exps:
        t = time.time(); globals()[e](); print(e, "done in", round(time.time() - t, 1), "s", flush=True)
