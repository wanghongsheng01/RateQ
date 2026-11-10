"""Rev-1 #15: sensitivity of the contract-variant generator (PUB2xn) at n = 240 and 1000.

Generator (rateq.instances._variant with parameters): each added service copies a random official
PUB2 service, scales its prices by clip(1 - d - dz - tier, 0.4, 1.1) with d ~ U(0, dmax),
dz ~ N(0, 0.03) per zone, tier ~ N(0, 0.02) per block of ten bands, scales surcharges by U(0.6, 1),
and with probability rho serves only zones <= U{2..5} (regional). dmax = 0.35, rho = 0.3 reproduces
PUB2x240 / PUB2x1000 exactly (asserted). Sweep: dmax in {0.05, 0.15, 0.35, 0.60} x rho in
{0, 0.3, 0.6}; worst cases: near-identical (d ~ U(0, 0.01), no zone/tier noise, surcharges
unscaled, no regional services) and identical copies (d = 0: every copy ties its base service).
Measured: evaluations per parcel and Q1 thread-CPU latency (300 queries, 5 interleaved reps) for
B1 brute force, RQ-Index and RQ+ (warm), batch evaluations per parcel on 20k parcels; agreement with
brute force. Outputs results/rev1/sensitivity[_tag].json.
Usage: python exp_rev1_sensitivity.py <n> <configs: all | grid | worst | dmax:rho,...> [tag]
"""
import json, sys, time
import numpy as np
from exp_rev1_common import WLS, REPS, tns, load, uptime, summ, args, save
from rateq.instances import build_pub2, build_pub2_scaled
from rateq.model import Service, Surcharge, Instance, INF, brute_vec
from rateq.index import RQIndex, RQIndexPlus, Compiled, fuel_fn, brute1


def variant(s, sid, rng, regional, dmax, mode):
    Z, K = s.price.shape
    if mode == "identical":
        d, dz, tier, sm = 0.0, 0.0, 0.0, 1.0
    elif mode == "near":
        d = rng.uniform(0.0, 0.01); dz = np.zeros((Z, 1)); tier = np.zeros((1, K)); sm = 1.0
    else:
        d = rng.uniform(0.0, dmax)
        dz = rng.normal(0, 0.03, size=(Z, 1))
        tier = np.repeat(rng.normal(0, 0.02, size=(1, (K + 9) // 10)), 10, axis=1)[:, :K]
    mult = np.clip(1 - d - dz - tier, 0.4, 1.1) if mode != "identical" else np.ones((Z, K))
    price = np.where(s.price < INF, np.round(s.price * mult), INF).astype(np.int64)
    zzone = s.zzone.copy()
    if regional:
        cap = int(rng.integers(2, 6))
        zzone = np.where(zzone > cap, -1, zzone)
    if mode == "normal":
        sm = rng.uniform(0.6, 1.0)
    sur = [Surcharge(r.name, r.pred, np.round(r.price * sm).astype(np.int64), r.group, r.fuel) for r in s.surcharges]
    return Service(sid, f"{s.name}~v{sid}", s.carrier, s.fuel_class, s.zstart, zzone, s.breaks, price,
                   s.divisor, s.dim_thr, rounding=s.rounding, extrapolate=s.extrapolate, limit_weight=s.limit_weight,
                   oversize=s.oversize, oversize_min=s.oversize_min, oversize2=s.oversize2, oversize2_min=s.oversize2_min,
                   inelig=s.inelig, surcharges=sur, das_start=s.das_start, das_level=s.das_level)


def build(n, dmax=0.35, rho=0.3, mode="normal", seed=0):
    inst = build_pub2(seed)
    rng = np.random.default_rng(seed + 1000)
    base = list(inst.services); svcs = list(base)
    while len(svcs) < n:
        s = base[int(rng.integers(len(base)))]
        reg = rng.random() < rho if mode == "normal" else False
        svcs.append(variant(s, len(svcs), rng, reg, dmax, mode))
    return Instance(f"PUB2x{n}[{mode},d{dmax},r{rho}]", svcs, inst.fuel, inst.n_zones)


def measure(inst, wl, q=300, Nb=20000):
    comp, fuel = [Compiled(s) for s in inst.services], fuel_fn(inst)
    ix = RQIndex(inst); ixp = RQIndexPlus(inst)
    P = WLS[wl](q, seed=11); truth = brute_vec(inst, P)
    for i in range(q):
        ixp.query1(*args(P, i))
    meth = ["B1_brute", "RQ_index", "RQ_plus"]
    mean_us = {m: [] for m in meth}; p50 = {m: [] for m in meth}; ev = {}; ag = {m: 1.0 for m in meth}
    for rep in range(REPS):
        for m in meth[rep % 3:] + meth[:rep % 3]:
            lat = np.empty(q); ok = 0; st = []
            for i in range(q):
                x = args(P, i)
                t = tns()
                r = brute1(comp, fuel, *x) if m == "B1_brute" else (ix.query1(*x, stats=st) if m == "RQ_index" else ixp.query1(*x, stats=st))
                lat[i] = tns() - t
                ok += (r[0] == truth[0][i] and r[1] == truth[1][i])
            mean_us[m].append(lat.mean() / 1e3); p50[m].append(np.median(lat) / 1e3)
            ag[m] = min(ag[m], ok / q); ev[m] = float(np.mean(st)) if st else float(len(inst.services))
    PB = WLS[wl](Nb, seed=22); tb = brute_vec(inst, PB)
    bev, bag = {}, {}
    for m, o in (("RQ_index", ix), ("RQ_plus", ixp)):
        st = []; b, a = o.query_batch(PB, st)
        bev[m] = st[0] / Nb; bag[m] = float(((b == tb[0]) & (a == tb[1])).mean())
    B1m = np.array(mean_us["B1_brute"]); B1p = np.array(p50["B1_brute"])
    return {"Q1_evals_per_parcel": ev, "Q1_mean_us": {m: summ(v) for m, v in mean_us.items()},
            "Q1_p50_us": {m: summ(v) for m, v in p50.items()},
            "speedup_mean_vs_B1": {m: summ(B1m / np.array(mean_us[m])) for m in meth[1:]},
            "speedup_p50_vs_B1": {m: summ(B1p / np.array(p50[m])) for m in meth[1:]},
            "Q1_agree": ag, "batch_evals_per_parcel": bev, "batch_agree": bag, "load": load()}


if __name__ == "__main__":
    n = int(sys.argv[1]); which = sys.argv[2]; tag = sys.argv[3] if len(sys.argv) > 3 else ""
    grid = [(d, r, "normal") for d in (0.05, 0.15, 0.35, 0.60) for r in (0.0, 0.3, 0.6)]
    worst = [(0.01, 0.0, "near"), (0.0, 0.0, "identical")]
    cfgs = grid + worst if which == "all" else grid if which == "grid" else worst if which == "worst" else \
        [(float(x.split(":")[0]), float(x.split(":")[1]), "normal") for x in which.split(",")]
    # the default generator setting must reproduce the paper instance exactly
    ref = build_pub2_scaled(n); mine = build(n)
    assert all((a.price == b.price).all() and (a.zzone == b.zzone).all() for a, b in zip(ref.services, mine.services))
    del ref, mine
    res = {"_uptime_start": uptime(), "_generator_default_reproduces_PUB2xn": True}
    for d, r, mode in cfgs:
        t0 = time.time()
        inst = build(n, d, r, mode)
        for wl in ("olist", "furn"):
            out = measure(inst, wl)
            out.update({"n": n, "dmax": d, "rho": r, "mode": mode, "wall_s": time.time() - t0})
            res[f"n{n}/{mode}/d{d}/r{r}/{wl}"] = out
            print(n, mode, d, r, wl, "evals", {m: round(v, 2) for m, v in out["Q1_evals_per_parcel"].items()},
                  "speedup", {m: round(v["median"], 1) for m, v in out["speedup_mean_vs_B1"].items()}, out["Q1_agree"], flush=True)
            res["_uptime_end"] = uptime()
            save(f"sensitivity{tag}", res)
