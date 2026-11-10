"""Rev-1 #5: B8, "keep every candidate" IVM for the stored-answer view (Q3), vs RateQ maintenance.

B8 materialises the |X| x n cost matrix C (int32, the smallest exact type; INF -> 2^31-1) and the
per-parcel minimum (c1, s1). After an edit it recomputes the affected cells of the affected
service column(s) and repairs the minimum: a decrease below c1 is applied in O(1); an increase of
the current minimum rescans the retained row (n candidates). We give B8 the same exact delta as
RateQ (the cell / band / fuel-week / service support U), i.e. the strongest generic IVM; the
variant B8_col recomputes the whole column(s) instead (no cell-level provenance).
RateQ = rateq.maintain.Maintainer (top-2 state, cell supports). E5 setup: 200k stored parcels
(100k olist seed 55 + 100k furniture seed 56), edits as in pilot.E5 (cell +-15%, band = one weight
band in every zone, fuel = +75 bp on one published week of one class, GRI = +5.9% whole service).

Outputs results/rev1/b8_ivm[_tag].json. Usage: python exp_rev1_b8_ivm.py 19,60,240,1000 [tag]
"""
import json, sys, time
import numpy as np
from exp_rev1_common import INSTS, REPS, tns, load, uptime, summ, save, concat
from rateq.instances import parcels_olist, parcels_furniture
from rateq.model import eval_service, INF, brute_vec
from rateq.index import RQIndex
from rateq.maintain import Maintainer, top2

I32 = np.int32(2 ** 31 - 1)
NAME = {19: "PUB2", 60: "PUB2x60", 240: "PUB2x240", 1000: "PUB2x1000"}
COUNTS = {"cell": 20, "band": 10, "fuel": 2, "gri": 2}   # per repetition


def to32(c):
    assert (c[c < INF] < I32).all()
    return np.where(c < INF, c, I32).astype(np.int32)


class B8:
    def __init__(self, inst, P, F0):
        self.inst, self.P, self.F0 = inst, P, F0
        n, N = len(inst.services), len(P)
        self.C = np.empty((N, n), dtype=np.int32)
        for s in inst.services:
            self.C[:, s.sid] = to32(eval_service(s, P, F0, inst.fuel))
        self.s1 = np.argmin(self.C, axis=1).astype(np.int64)      # first minimum = smallest sid
        self.c1 = self.C[np.arange(N), self.s1]
        self.s1[self.c1 == I32] = -1                              # no eligible service (as top2)
        self.rescans = 0

    def nbytes(self):
        return self.C.nbytes + self.c1.nbytes + self.s1.nbytes

    def apply(self, sids, U):
        """Recompute C[U, s] for s in sids, then repair (c1, s1) on U."""
        if len(U) == 0:
            return 0
        resc = np.zeros(len(U), bool)
        PU, FU = self.P.subset(U), self.F0[U]
        for sid in sids:
            s = self.inst.services[sid]
            old = self.C[U, sid]
            new = to32(eval_service(s, PU, FU, self.inst.fuel))
            self.C[U, sid] = new
            c1, s1 = self.c1[U], self.s1[U]
            better = (new < c1) | ((new == c1) & (sid < s1))
            self.c1[U] = np.where(better, new, c1); self.s1[U] = np.where(better, sid, s1)
            resc |= (s1 == sid) & (new > old) & ~better
        R = U[resc]
        if len(R):
            sub = self.C[R]
            a = np.argmin(sub, axis=1)
            cc = sub[np.arange(len(R)), a]
            self.s1[R] = np.where(cc == I32, -1, a); self.c1[R] = cc
        self.rescans += len(R)
        return len(R)


def run(n, seed0=6):
    t0 = time.time()
    iname = NAME[n]
    inst = INSTS[iname]()
    ix = RQIndex(inst)
    A, B = parcels_olist(100_000, seed=55), parcels_furniture(100_000, seed=56)
    P = concat(A, B); N = len(P)
    out = {"n": n, "N": N, "load_start": load(), "uptime_start": uptime()}
    t = tns(); mt = Maintainer(inst, ix, P); init_rq = (tns() - t) / 1e9
    F0 = mt.F0
    b8 = None
    t = tns()
    try:
        b8 = B8(inst, P, F0)
        init_b8 = (tns() - t) / 1e9
        out["B8_init_cpu_s"] = init_b8
        out["B8_state_MB"] = b8.nbytes() / 1e6
        out["B8_matrix_MB"] = b8.C.nbytes / 1e6
    except MemoryError as e:
        out["B8_failure"] = f"MemoryError while allocating/filling the {N}x{n} int32 matrix ({N * n * 4 / 1e6:.0f} MB): {e}"
    b8c = B8(inst, P, F0) if (b8 is not None and n <= 240) else None   # separate state for the column variant
    out["B8_matrix_MB_int32"] = N * n * 4 / 1e6
    out["B8_matrix_MB_int64"] = N * n * 8 / 1e6
    bucket_b = sum(b["order"].nbytes + 40 * len(b["pos"]) for b in mt.buckets)
    out["RQ_init_cpu_s"] = init_rq
    out["RQ_state_MB"] = (mt.c1.nbytes * 4 + mt.sg.nbytes + bucket_b) / 1e6
    out["RQ_state_breakdown_MB"] = {"top2": mt.c1.nbytes * 4 / 1e6, "signature": mt.sg.nbytes / 1e6, "buckets": bucket_b / 1e6,
                                    "index": ix.nbytes() / 1e6}
    out["shared_parcels_and_features_MB"] = (F0.nbytes + sum(getattr(P, f).nbytes for f in ("key", "a", "L", "M", "S", "res", "day"))) / 1e6
    # full recomputation (top-2 of every stored parcel), interleaved with the edit reps
    full = []
    per = {k: {m: [] for m in ("RQ", "B8", "B8_col")} for k in COUNTS}
    sizes = {k: {"support": [], "RQ_recomputed": [], "B8_rescans": []} for k in COUNTS}
    for rep in range(REPS):
        rng = np.random.default_rng(seed0 + 100 * rep)
        t = tns(); top2(inst, P, F0); full.append((tns() - t) / 1e9)
        log = {k: {m: [] for m in ("RQ", "B8", "B8_col")} for k in COUNTS}
        for kind in ("cell", "band", "gri", "fuel"):
            for e in range(COUNTS[kind]):
                if kind in ("cell", "band", "gri"):
                    s = inst.services[int(rng.integers(n))]
                    zs, ks = np.nonzero(s.price < INF)
                    if kind == "gri":
                        s.price[:] = np.where(s.price < INF, np.round(s.price * 1.059), INF).astype(np.int64)
                        cells = None
                    else:
                        j = int(rng.integers(len(zs))); z, k = int(zs[j]), int(ks[j])
                        zl = [z] if kind == "cell" else sorted(set(zs[ks == k].tolist()))
                        f = rng.uniform(0.85, 1.15)
                        for zz in zl:
                            s.price[zz, k] = int(s.price[zz, k] * f)
                        cells = [(zz, k) for zz in zl]
                    sids = [s.sid]
                else:
                    fc = list(inst.fuel.starts)[int(rng.integers(len(inst.fuel.starts)))]
                    w = int(rng.integers(1, len(inst.fuel.starts[fc]) - 1)) if len(inst.fuel.starts[fc]) > 2 else 0
                    inst.fuel.bps[fc][w] += 75
                    sids = [x.sid for x in inst.services if x.fuel_class == fc]
                order = ["RQ", "B8", "B8_col"] if e % 2 == 0 else ["B8_col", "B8", "RQ"]
                for m in order:
                    if (m == "B8" and b8 is None) or (m == "B8_col" and b8c is None):
                        continue
                    t = tns()
                    if kind in ("cell", "band"):
                        U = np.unique(np.concatenate([mt.support_cell(s.sid, zz, kk) for zz, kk in cells]))
                    elif kind == "gri":
                        U = mt.support_service(s.sid)
                    else:
                        U = mt.support_fuel(fc, w)
                    if m == "RQ":
                        # one service (cell/band/GRI) or the whole fuel class at once (merged update)
                        rec = mt.apply_many(sids, U)[1]
                        sizes[kind]["RQ_recomputed"].append(rec); sizes[kind]["support"].append(len(U))
                    elif m == "B8":
                        sizes[kind]["B8_rescans"].append(b8.apply(sids, U))
                    else:  # whole column(s): every parcel the service serves / every parcel for fuel
                        if kind == "fuel":
                            Uc = np.arange(N)
                        else:
                            Uc = mt.support_service(s.sid)
                        b8c.apply(sids, Uc)
                    log[kind][m].append((tns() - t) / 1e9)
        for kind in COUNTS:
            for m in log[kind]:
                if log[kind][m]:
                    per[kind][m].append(float(np.mean(log[kind][m])))
        print(n, "rep", rep, {k: {m: round(np.mean(v) * 1e3, 2) for m, v in log[k].items() if v} for k in log}, flush=True)
    # exactness: maintained states against a fresh full recomputation after all edits
    c1, s1, c2, s2 = top2(inst, P, F0)
    out["exact_RQ_top2"] = bool((c1 == mt.c1).all() and (s1 == mt.s1).all() and (c2 == mt.c2).all() and (s2 == mt.s2).all())
    if b8 is not None:
        out["exact_B8_min"] = bool((to32(c1) == b8.c1).all() and (s1 == b8.s1).all())
        if b8c is not None:
            out["exact_B8col_min"] = bool((to32(c1) == b8c.c1).all() and (s1 == b8c.s1).all())
        out["B8_matrix_spotcheck_cols_exact"] = bool(all((b8.C[:, s.sid] == to32(eval_service(s, P, F0, inst.fuel))).all()
                                                        for s in inst.services[:: max(1, n // 20)]))
    out["full_recompute_cpu_s"] = summ(full)
    out["edits_per_rep"] = COUNTS
    out["edit_cpu_s"] = {k: {m: summ(v) for m, v in per[k].items() if v} for k in per}
    out["speedup_B8_over_RQ"] = {k: summ(np.array(per[k]["B8"]) / np.array(per[k]["RQ"])) for k in per if per[k]["B8"]}
    out["speedup_B8col_over_RQ"] = {k: summ(np.array(per[k]["B8_col"]) / np.array(per[k]["RQ"])) for k in per if per[k]["B8_col"]}
    out["sizes_mean"] = {k: {m: float(np.mean(v)) if v else None for m, v in d.items()} for k, d in sizes.items()}
    out["load_end"] = load(); out["uptime_end"] = uptime(); out["wall_s"] = time.time() - t0
    return out


if __name__ == "__main__":
    ns = [int(x) for x in sys.argv[1].split(",")] if len(sys.argv) > 1 else [19, 60, 240, 1000]
    tag = sys.argv[2] if len(sys.argv) > 2 else ""
    res = {}
    for n in ns:
        res[NAME[n]] = run(n)
        r = res[NAME[n]]
        print(NAME[n], json.dumps({k: r.get(k) for k in ("B8_state_MB", "RQ_state_MB", "exact_RQ_top2", "exact_B8_min")}),
              {k: {m: round(v["median"] * 1e3, 3) for m, v in d.items()} for k, d in r["edit_cpu_s"].items()}, flush=True)
        save(f"b8_ivm{tag}", res)
