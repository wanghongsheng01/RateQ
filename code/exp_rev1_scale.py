"""Rev-1 #13: Q3 maintenance with 2M and 20M stored parcels (n = 19, 60, 240).

Stored parcels: half olist, half furniture (as pilot.E5, scaled). Methods per edit:
  RateQ         cell / band / fuel-week support + top-2 repair (rateq.maintain.Maintainer)
  service-level support = every parcel the edited service serves (fuel: every parcel served by a
                service of the edited class) + re-evaluation of the service(s) on it, as in pilot.E5
                (repair not included, so this is a lower bound on a service-level strategy)
  full          recomputation of the top-2 of every stored parcel (chunks of 1M parcels)
Each repetition applies 20 cell, 10 band and 2 fuel edits (methods interleaved), then runs the full
recomputation and checks the maintained state against it (exactness). With REV1_FULLREPS=f, the
full recomputation is timed in repetitions < f and always in the last one (exactness check).
Outputs results/rev1/scale_q3_<N>_<n>.json. Usage: python exp_rev1_scale.py <N> <n>
"""
import json, os, sys, time
import numpy as np
from exp_rev1_common import INSTS, REPS, tns, load, uptime, summ, save, concat
from rateq.instances import parcels_olist, parcels_furniture
from rateq.model import eval_service, INF
from rateq.index import RQIndex
from rateq.maintain import Maintainer, top2

NAME = {19: "PUB2", 60: "PUB2x60", 240: "PUB2x240", 1000: "PUB2x1000"}
COUNTS = {"cell": 20, "band": 10, "fuel": 2}
CH = 1_000_000


def full_top2(inst, P):
    N = len(P)
    out = [np.empty(N, np.int64) for _ in range(4)]
    for i in range(0, N, CH):
        sl = slice(i, min(N, i + CH))
        r = top2(inst, P.subset(np.arange(sl.start, sl.stop)))
        for o, x in zip(out, r):
            o[sl] = x
    return out


def run(N, n):
    t0 = time.time()
    inst = INSTS[NAME[n]]()
    ix = RQIndex(inst)
    P = concat(parcels_olist(N // 2, seed=55), parcels_furniture(N - N // 2, seed=56))
    out = {"n": n, "N": N, "load_start": load(), "uptime_start": uptime()}
    t = tns(); mt = Maintainer(inst, ix, P); out["RQ_init_cpu_s"] = (tns() - t) / 1e9
    bucket_b = sum(b["order"].nbytes + 40 * len(b["pos"]) for b in mt.buckets)
    out["RQ_state_MB"] = (mt.c1.nbytes * 4 + mt.sg.nbytes + bucket_b) / 1e6
    out["features_MB"] = mt.F0.nbytes / 1e6
    fullreps = int(os.environ.get("REV1_FULLREPS", REPS))
    full = []; exact = []
    per = {k: {m: [] for m in ("RQ", "service")} for k in COUNTS}
    sizes = {k: {"support": [], "recomputed": [], "service_support": []} for k in COUNTS}
    for rep in range(REPS):
        rng = np.random.default_rng(6 + 100 * rep)
        log = {k: {m: [] for m in ("RQ", "service")} for k in COUNTS}
        for kind in ("cell", "band", "fuel"):
            for e in range(COUNTS[kind]):
                if kind == "fuel":
                    fc = list(inst.fuel.starts)[int(rng.integers(len(inst.fuel.starts)))]
                    w = int(rng.integers(1, len(inst.fuel.starts[fc]) - 1)) if len(inst.fuel.starts[fc]) > 2 else 0
                    inst.fuel.bps[fc][w] += 75
                    sids = [x.sid for x in inst.services if x.fuel_class == fc]
                else:
                    s = inst.services[int(rng.integers(n))]
                    zs, ks = np.nonzero(s.price < INF)
                    j = int(rng.integers(len(zs))); z, k = int(zs[j]), int(ks[j])
                    zl = [z] if kind == "cell" else sorted(set(zs[ks == k].tolist()))
                    f = rng.uniform(0.85, 1.15)
                    for zz in zl:
                        s.price[zz, k] = int(s.price[zz, k] * f)
                    sids = [s.sid]
                for m in (["RQ", "service"] if e % 2 == 0 else ["service", "RQ"]):
                    t = tns()
                    if m == "RQ":
                        if kind == "fuel":
                            U = mt.support_fuel(fc, w)
                        else:
                            U = np.unique(np.concatenate([mt.support_cell(s.sid, zz, k) for zz in zl]))
                        rec = sum(mt.apply(sid, U)[1] for sid in sids)
                        log[kind][m].append((tns() - t) / 1e9)
                        sizes[kind]["support"].append(len(U)); sizes[kind]["recomputed"].append(rec)
                    else:
                        tot = 0
                        for sid in sids:
                            Us = mt.support_service(sid)
                            for i in range(0, len(Us), CH):
                                u = Us[i:i + CH]
                                eval_service(inst.services[sid], P.subset(u), mt.F0[u], inst.fuel)
                            tot += len(Us)
                        log[kind][m].append((tns() - t) / 1e9)
                        sizes[kind]["service_support"].append(tot)
        for k in COUNTS:
            for m in log[k]:
                per[k][m].append(float(np.mean(log[k][m])))
        if rep < fullreps or rep == REPS - 1:
            t = tns(); c1, s1, c2, s2 = full_top2(inst, P); full.append((tns() - t) / 1e9)
            exact.append(bool((c1 == mt.c1).all() and (s1 == mt.s1).all() and (c2 == mt.c2).all() and (s2 == mt.s2).all()))
            del c1, s1, c2, s2
        print(N, n, "rep", rep, {k: {m: round(np.mean(v) * 1e3, 2) for m, v in log[k].items()} for k in log},
              "full", round(full[-1], 1) if full else None, "exact", exact[-1] if exact else None, load(), flush=True)
        out["full_recompute_cpu_s"] = summ(full); out["exact_each_checked_rep"] = exact
        out["edit_cpu_s"] = {k: {m: summ(v) for m, v in d.items()} for k, d in per.items()}
        out["sizes_mean"] = {k: {m: float(np.mean(v)) for m, v in d.items()} for k, d in sizes.items()}
        out["speedup_full_over_RQ"] = {k: summ(np.median(full) / np.array(per[k]["RQ"])) for k in per}
        out["speedup_service_over_RQ"] = {k: summ(np.array(per[k]["service"]) / np.array(per[k]["RQ"])) for k in per}
        out["edits_per_rep"] = COUNTS; out["full_reps_timed"] = len(full)
        out["load_end"] = load(); out["uptime_end"] = uptime(); out["wall_s"] = time.time() - t0
        save(f"scale_q3_{N}_{n}", {NAME[n]: out})
    return out


if __name__ == "__main__":
    run(int(sys.argv[1]), int(sys.argv[2]))
