"""Rev-2 B2 (Python): what-if contract evaluation over 2M stored parcels.

K = 100 candidate contracts for one service (UPS Ground, sid 5): each is a rewrite of its rate table
with a per-zone discount d_z ~ U(0, 35%) and a per-ten-band adjustment N(0, 2%) (the PUB2x generator's
discount form). For each contract we need the cheapest-service answer of every stored parcel and the
total shipping cost under cheapest-service routing; the best contract minimises that total.
Methods (single-threaded Python, same evaluator, thread CPU time, methods rotated per contract):
  brute       full batch per contract: brute_vec over all n services and 2M parcels
  rateq       RateQ: the contract is a table rewrite of one service; support = the parcels the
              service serves; top-2 repair (rateq.maintain.Maintainer.apply) from the previous contract
  exclude_one the cheapest answer over the OTHER services is computed once; per contract the service
              is evaluated on its coverage and compared (a specialised baseline for this question)
One-time costs (RateQ state init, exclude-one precomputation) are reported separately and included in
the end-to-end totals. Exactness: per contract, every method's per-parcel (cost, service) and total
are compared with brute force. Stored parcels: half olist (seed 55), half furniture (seed 56).
Outputs results/rev2/whatif_<n>.json. Usage: python exp_rev2_whatif.py <n: 19|60> [K] [N]
"""
import json, os, sys, time, platform
import numpy as np
from exp_rev1_common import INSTS, concat, summ, load, uptime
from rateq.instances import parcels_olist, parcels_furniture
from rateq.model import brute_vec, eval_service, INF
from rateq.index import RQIndex
from rateq.maintain import Maintainer

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "results", "rev2")
os.makedirs(OUT, exist_ok=True)
NAME = {19: "PUB2", 60: "PUB2x60", 240: "PUB2x240"}
SID = 5  # UPS_Ground


def contracts(base, K, seed=11):
    rng = np.random.default_rng(seed)
    Z, Kb = base.shape
    out = []
    for _ in range(K):
        d = rng.uniform(0.0, 0.35, size=(Z, 1))
        tier = np.repeat(rng.normal(0, 0.02, size=(1, (Kb + 9) // 10)), 10, axis=1)[:, :Kb]
        mult = np.clip(1 - d - tier, 0.4, 1.1)
        out.append(np.where(base < INF, np.round(base * mult), INF).astype(np.int64))
    return out


def tns():
    return time.thread_time_ns()


def run(n, K, N):
    t00 = time.time()
    inst = INSTS[NAME[n]]()
    s = inst.services[SID]
    assert s.name == "UPS_Ground"
    base = s.price.copy()
    C = contracts(base, K)
    P = concat(parcels_olist(N // 2, seed=55), parcels_furniture(N - N // 2, seed=56))
    out = {"n": n, "N": N, "K": K, "service": s.name, "load_start": load(), "uptime_start": uptime()}
    ix = RQIndex(inst)
    t = tns(); mt = Maintainer(inst, ix, P); out["rateq_init_s"] = (tns() - t) / 1e9
    F0 = mt.F0
    # exclude-one precomputation: best over the other services
    t = tns()
    bo_c = np.full(N, INF, np.int64); bo_s = np.full(N, -1, np.int64)
    for x in inst.services:
        if x.sid == SID:
            continue
        c = eval_service(x, P, F0, inst.fuel)
        b = (c < bo_c) | ((c == bo_c) & (x.sid < bo_s))
        bo_c = np.where(b, c, bo_c); bo_s = np.where(b, x.sid, bo_s)
    cov = mt.support_service(SID)
    out["exclude_one_init_s"] = (tns() - t) / 1e9
    T = {"brute": [], "rateq": [], "exclude_one": []}
    totals, agree, recomputed = [], {"rateq": 0, "exclude_one": 0}, []
    for k, pk in enumerate(C):
        s.price[:] = pk
        res = {}
        meths = ["brute", "rateq", "exclude_one"]
        for m in meths[k % 3:] + meths[:k % 3]:
            t = tns()
            if m == "brute":
                b, a = brute_vec(inst, P)
            elif m == "rateq":
                nU, nR = mt.apply(SID, cov)
                b, a = mt.c1, mt.s1
            else:
                cs = eval_service(s, P.subset(cov), F0[cov], inst.fuel)
                b = bo_c.copy(); a = bo_s.copy()
                bc, ba = b[cov], a[cov]
                better = (cs < bc) | ((cs == bc) & (SID < ba))
                b[cov] = np.where(better, cs, bc); a[cov] = np.where(better, SID, ba)
            tot = int(b[b < INF].sum())
            T[m].append((tns() - t) / 1e9)
            res[m] = (b.copy() if m != "rateq" else b.copy(), a.copy(), tot)
            if m == "rateq":
                recomputed.append(nR)
        for m in ("rateq", "exclude_one"):
            ok = (res[m][0] == res["brute"][0]).all() and (res[m][1] == res["brute"][1]).all() and res[m][2] == res["brute"][2]
            agree[m] += int(ok)
        totals.append(res["brute"][2])
        del res
        if k % 10 == 9:
            print(n, "contract", k + 1, {m: round(float(np.sum(v)), 1) for m, v in T.items()}, agree, load(), flush=True)
    best = int(np.argmin(totals))
    out.update({"contract_cpu_s": {m: summ(v) for m, v in T.items()}, "total_cpu_s": {m: float(np.sum(v)) for m, v in T.items()},
                "end_to_end_cpu_s": {"brute": float(np.sum(T["brute"])), "rateq": out["rateq_init_s"] + float(np.sum(T["rateq"])),
                                     "exclude_one": out["exclude_one_init_s"] + float(np.sum(T["exclude_one"]))},
                "speedup_brute_over_rateq_per_contract": summ(np.array(T["brute"]) / np.array(T["rateq"])),
                "contracts_exact": {m: f"{v}/{K}" for m, v in agree.items()},
                "rateq_recomputed_mean": float(np.mean(recomputed)), "support_size": int(len(cov)),
                "best_contract": best, "best_total_usd": totals[best] / 100, "base_total_usd": None,
                "totals_usd_quantiles": {q: float(np.percentile(totals, q)) / 100 for q in (0, 50, 100)},
                "load_end": load(), "uptime_end": uptime(), "wall_s": time.time() - t00})
    s.price[:] = base
    b, a = brute_vec(inst, P)
    out["base_total_usd"] = int(b[b < INF].sum()) / 100
    return out


if __name__ == "__main__":
    n = int(sys.argv[1]); K = int(sys.argv[2]) if len(sys.argv) > 2 else 100; N = int(sys.argv[3]) if len(sys.argv) > 3 else 2_000_000
    r = run(n, K, N)
    r["_env"] = {"python": platform.python_version(), "numpy": np.__version__, "host": platform.node(),
                 "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
                 "timer": "time.thread_time_ns", "written": time.strftime("%Y-%m-%d %H:%M:%S")}
    json.dump(r, open(os.path.join(OUT, f"whatif_{n}{'' if N == 2_000_000 and K == 100 else f'_K{K}_N{N}'}.json"), "w"), indent=1)
    print(json.dumps({k: r[k] for k in ("total_cpu_s", "end_to_end_cpu_s", "contracts_exact", "best_contract", "best_total_usd")}), flush=True)
