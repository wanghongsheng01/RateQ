"""Rev-1 #16 ablations.

(a) bound  : lower bound per (signature, group) list
       RowMin   = minimum of the service's whole zone row (no weight structure; one list per
                  signature and group)                                   [ablation (i)]
       RQ-Index = suffix minimum over the bands at or above the parcel's elementary interval [(ii)]
       RQ+      = RQ-Index key plus surcharges/tier raises implied by the geometric class   [(iii)]
     reported: evaluations per parcel (Q1 and batch), Q1 thread-CPU latency (5 interleaved reps of
     1000 queries), batch CPU time on 20k parcels; PUB2, PUB2x240, PUB2x1000 x olist/furn.
(b) state  : stored top-1 / top-2 / top-3 per parcel for Q3; |R| (parcels recomputed in full) and
     (kinds: cell +-15%, cell_up = cell +1..15%, band, gri = +5.9% whole service, fuel week +75 bp)
     CPU time for cell / band / fuel edits on the E5 setup (200k parcels), n = 19, 60, 240.
     Repair rule for top-k (generalises maintain.Maintainer): for an edit of s with new cost c',
     if s is not stored, insert (c', s) if it beats the k-th entry; if s is stored and (c', s) is
     not behind the stored k-th entry, re-sort; otherwise recompute the parcel (set R).
Outputs results/rev1/ablation_bound[_tag].json, results/rev1/ablation_topk[_tag].json.
Usage: python exp_rev1_ablations.py bound PUB2,PUB2x240 olist,furn [tag] | topk 19,60,240 [tag]
"""
import json, sys, time
import numpy as np
from exp_rev1_common import INSTS, WLS, REPS, tns, load, uptime, summ, args, save, concat
from rateq.instances import parcels_olist, parcels_furniture
from rateq.model import brute_vec, eval_service, INF
from rateq.index import RQIndex, RQIndexPlus, Compiled, fuel_fn, brute1
from rateq.maintain import Maintainer, top2


class RowMinIndex(RQIndex):
    """Ablation (i): bound = min over the zone row, independent of the billable weight."""

    def _hull(self, s, z, E):
        if z < 0:
            return np.full(len(E) + 1, INF, dtype=np.int64)
        return np.full(len(E) + 1, s.price[z].min(), dtype=np.int64)


def bound(insts, wls, q=1000, Nb=20000):
    res = {"_uptime_start": uptime()}
    for iname in insts:
        inst = INSTS[iname]()
        comp, fuel = [Compiled(s) for s in inst.services], fuel_fn(inst)
        t = tns(); ixr = RowMinIndex(inst); br = (tns() - t) / 1e9
        t = tns(); ix = RQIndex(inst); bq = (tns() - t) / 1e9
        t = tns(); ixp = RQIndexPlus(inst); bp = (tns() - t) / 1e9
        for wl in wls:
            P = WLS[wl](q, seed=11)
            truth = brute_vec(inst, P)
            for i in range(q):          # warm RQ+ lazy rows (as in pilot.E1)
                ixp.query1(*args(P, i))
            meth = {"B1_brute": lambda *a, st=None: brute1(comp, fuel, *a), "RowMin": ixr.query1, "RQ_index": ix.query1, "RQ_plus": ixp.query1}
            mean_us = {m: [] for m in meth}; p50 = {m: [] for m in meth}; p95 = {m: [] for m in meth}
            evals = {}; agree = {m: 1.0 for m in meth}
            names = list(meth)
            for rep in range(REPS):
                for m in names[rep % 4:] + names[:rep % 4]:
                    fn = meth[m]; lat = np.empty(q); ok = 0; st = []
                    for i in range(q):
                        x = args(P, i)
                        t = tns()
                        r = fn(*x) if m == "B1_brute" else fn(*x, stats=st)
                        lat[i] = tns() - t
                        ok += (r[0] == truth[0][i] and r[1] == truth[1][i])
                    mean_us[m].append(lat.mean() / 1e3); p50[m].append(np.median(lat) / 1e3); p95[m].append(np.percentile(lat, 95) / 1e3)
                    agree[m] = min(agree[m], ok / q)
                    evals[m] = float(np.mean(st)) if st else float(len(inst.services))
            # batch
            PB = WLS[wl](Nb, seed=22); tb = brute_vec(inst, PB)
            bt = {m: [] for m in ("RowMin", "RQ_index", "RQ_plus")}; bev = {}; bag = {m: 1.0 for m in bt}
            bobj = {"RowMin": ixr, "RQ_index": ix, "RQ_plus": ixp}
            ixp.query_batch(PB)  # warm lazy rows for this batch
            for rep in range(REPS):
                for m in list(bt)[rep % 3:] + list(bt)[:rep % 3]:
                    st = []
                    t = tns(); b, a = bobj[m].query_batch(PB, st); bt[m].append((tns() - t) / 1e9)
                    bev[m] = st[0] / Nb; bag[m] = min(bag[m], float(((b == tb[0]) & (a == tb[1])).mean()))
            res[f"{iname}/{wl}"] = {"n": len(inst.services), "build_cpu_s": {"RowMin": br, "RQ_index": bq, "RQ_plus": bp},
                                    "Q1_evals_per_parcel": evals, "Q1_mean_us": {m: summ(v) for m, v in mean_us.items()},
                                    "Q1_p50_us": {m: summ(v) for m, v in p50.items()}, "Q1_p95_us": {m: summ(v) for m, v in p95.items()},
                                    "Q1_agree": agree, "batch_N": Nb, "batch_evals_per_parcel": bev,
                                    "batch_cpu_s": {m: summ(v) for m, v in bt.items()}, "batch_agree": bag,
                                    "load": load()}
            print(iname, wl, "evals", {m: round(v, 2) for m, v in evals.items()}, "mean_us",
                  {m: round(np.median(v), 1) for m, v in mean_us.items()}, "agree", agree, flush=True)
            res["_uptime_end"] = uptime()
            save(f"ablation_bound{TAG}", res)


# ------------------------------------------------------------------ (b) top-k state
def _lexsort_rows(C, S):
    o = np.lexsort((S, C), axis=1)
    return np.take_along_axis(C, o, 1), np.take_along_axis(S, o, 1)


def topk_full(inst, P, F0, k):
    N = len(P)
    C = np.full((N, k), INF, np.int64); S = np.full((N, k), -1, np.int64)
    for s in inst.services:
        c = eval_service(s, P, F0, inst.fuel)
        C2, S2 = _lexsort_rows(np.c_[C, c], np.c_[S, np.full(N, s.sid)])
        C, S = C2[:, :k], S2[:, :k]
    return C, S


class TopK:
    def __init__(self, inst, P, F0, k):
        self.inst, self.P, self.F0, self.k = inst, P, F0, k
        self.C, self.S = topk_full(inst, P, F0, k)

    def apply(self, sid, U):
        if len(U) == 0:
            return 0
        s = self.inst.services[sid]
        cn = eval_service(s, self.P.subset(U), self.F0[U], self.inst.fuel)
        C, S = self.C[U], self.S[U]
        lc, ls = C[:, -1], S[:, -1]
        inL = (S == sid).any(axis=1)
        rec = inL & ((cn > lc) | ((cn == lc) & (sid > ls)))
        Cx = np.where(S == sid, INF + 1, C); Sx = np.where(S == sid, 1 << 40, S)
        C2, S2 = _lexsort_rows(np.c_[Cx, cn], np.c_[Sx, np.full(len(U), sid)])
        self.C[U], self.S[U] = C2[:, :self.k], S2[:, :self.k]
        R = U[rec]
        if len(R):
            self.C[R], self.S[R] = topk_full(self.inst, self.P.subset(R), self.F0[R], self.k)
        return len(R)


def topk(ns, counts={"cell": 20, "cell_up": 20, "band": 10, "fuel": 2, "gri": 2}):
    NAME = {19: "PUB2", 60: "PUB2x60", 240: "PUB2x240", 1000: "PUB2x1000"}
    res = {"_uptime_start": uptime()}
    for n in ns:
        inst = INSTS[NAME[n]]()
        ix = RQIndex(inst)
        P = concat(parcels_olist(100_000, seed=55), parcels_furniture(100_000, seed=56))
        mt = Maintainer(inst, ix, P)   # supports + reference top-2 repair
        F0 = mt.F0
        ks = (1, 2, 3)
        st = {}
        init = {}
        for k in ks:
            t = tns(); st[k] = TopK(inst, P, F0, k); init[k] = (tns() - t) / 1e9
        assert (st[2].C[:, 0] == mt.c1).all() and (st[2].S[:, 0] == mt.s1).all() and (st[2].C[:, 1] == mt.c2).all() and (st[2].S[:, 1] == mt.s2).all()
        per = {kind: {k: [] for k in ks} for kind in counts}
        Rs = {kind: {k: [] for k in ks} for kind in counts}
        sup = {kind: [] for kind in counts}
        ref_mismatch = 0
        for rep in range(REPS):
            rng = np.random.default_rng(6 + 100 * rep)
            log = {kind: {k: [] for k in ks} for kind in counts}
            for kind in ("cell", "cell_up", "band", "gri", "fuel"):
                for e in range(counts[kind]):
                    if kind == "fuel":
                        fc = list(inst.fuel.starts)[int(rng.integers(len(inst.fuel.starts)))]
                        w = int(rng.integers(1, len(inst.fuel.starts[fc]) - 1)) if len(inst.fuel.starts[fc]) > 2 else 0
                        inst.fuel.bps[fc][w] += 75
                        sids = [x.sid for x in inst.services if x.fuel_class == fc]
                        t = tns(); U = mt.support_fuel(fc, w); ts = (tns() - t) / 1e9
                    elif kind == "gri":   # general rate increase +5.9% of one whole service
                        s = inst.services[int(rng.integers(n))]
                        s.price[:] = np.where(s.price < INF, np.round(s.price * 1.059), INF).astype(np.int64)
                        sids = [s.sid]
                        t = tns(); U = mt.support_service(s.sid); ts = (tns() - t) / 1e9
                    else:
                        s = inst.services[int(rng.integers(n))]
                        zs, kk = np.nonzero(s.price < INF)
                        j = int(rng.integers(len(zs))); z, kb = int(zs[j]), int(kk[j])
                        zl = [z] if kind in ("cell", "cell_up") else sorted(set(zs[kk == kb].tolist()))
                        f = rng.uniform(1.01, 1.15) if kind == "cell_up" else rng.uniform(0.85, 1.15)
                        for zz in zl:
                            s.price[zz, kb] = int(s.price[zz, kb] * f)
                        sids = [s.sid]
                        t = tns(); U = np.unique(np.concatenate([mt.support_cell(s.sid, zz, kb) for zz in zl])); ts = (tns() - t) / 1e9
                    sup[kind].append(len(U))
                    order = list(ks) if e % 2 == 0 else list(ks)[::-1]
                    for k in order:
                        t = tns(); r = sum(st[k].apply(sid, U) for sid in sids); dt = (tns() - t) / 1e9
                        log[kind][k].append(ts + dt); Rs[kind][k].append(r)
                    rr = sum(mt.apply(sid, U)[1] for sid in sids)   # reference (not timed)
                    ref_mismatch += int(rr != Rs[kind][2][-1])
            for kind in counts:
                for k in ks:
                    per[kind][k].append(float(np.mean(log[kind][k])))
            print(n, "rep", rep, {kind: {k: (round(np.mean(log[kind][k]) * 1e3, 2), int(np.sum(Rs[kind][k][-counts[kind]:]))) for k in ks} for kind in counts}, flush=True)
        exact = {}
        for k in ks:
            C, S = topk_full(inst, P, F0, k)
            exact[k] = bool((C == st[k].C).all() and (S == st[k].S).all())
        res[NAME[n]] = {"n": n, "N": len(P), "init_cpu_s": {f"top{k}": init[k] for k in ks},
                        "state_MB": {f"top{k}": (st[k].C.nbytes + st[k].S.nbytes) / 1e6 for k in ks},
                        "edits_per_rep": counts, "support_mean": {kind: float(np.mean(v)) for kind, v in sup.items()},
                        "recomputed_R_mean": {kind: {f"top{k}": float(np.mean(Rs[kind][k])) for k in ks} for kind in counts},
                        "edit_cpu_s": {kind: {f"top{k}": summ(per[kind][k]) for k in ks} for kind in counts},
                        "exact_final_state": {f"top{k}": exact[k] for k in ks},
                        "top2_matches_Maintainer_R": ref_mismatch == 0, "load": load(), "uptime": uptime()}
        print(NAME[n], json.dumps(res[NAME[n]]["recomputed_R_mean"]), exact, flush=True)
        save(f"ablation_topk{TAG}", res)


TAG = ""
if __name__ == "__main__":
    if sys.argv[1] == "bound":
        TAG = sys.argv[4] if len(sys.argv) > 4 else ""
        bound(sys.argv[2].split(","), sys.argv[3].split(","))
    else:
        TAG = sys.argv[3] if len(sys.argv) > 3 else ""
        topk([int(x) for x in sys.argv[2].split(",")])
