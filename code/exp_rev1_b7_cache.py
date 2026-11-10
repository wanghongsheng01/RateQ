"""Rev-1 #4: B7, an exact result cache / precomputed answer table for the CFS query.

Cache key = every input that can change the cheapest-service answer on the PUB2 family:
  sigma      zone signature (zones of all services), RQIndex.sig_of
  das        delivery-area level under each distinct DAS map (3 maps in PUB2)
  e_g        per service group, the elementary interval of the group's billable weight t
             (inside one interval every service's band, tier-raised band and eligibility are constant)
  mask       bitmask of every distinct geometric predicate (surcharges, oversize tiers, ineligibility)
             that does not read DAS (DAS predicates read only DAS and RES -> covered by das + res)
  res        residential flag
  fuel       fuel period: index of the ship day among the union of all fuel-class start days
The preconditions that make this key exact are asserted (no predicate reads T, ZONE, ZIP, DAY or
MONTH; DAS predicates read only DAS/RES; fixed surcharges only; no extrapolation). Every cached
answer is additionally checked against brute force (agreement fields).

Outputs results/rev1/b7_cache.json. Usage: python exp_rev1_b7_cache.py [INST,...] [WL,...]
"""
import bisect, json, os, sys, time, tracemalloc
import numpy as np
from exp_rev1_common import INSTS, WLS, REPS, tns, load, uptime, summ, args, save, concat
from rateq.model import brute_vec, eval_service, billable, FI, INF, GPred, Pred
from rateq.index import RQIndex, Compiled, fuel_fn, brute1, billable1

INF_I = int(INF)
ALLOWED = {"A", "G", "L", "V", "M", "S", "SUMD", "RES"}


def pfeats(p):
    return p.feats() if isinstance(p, GPred) else {f for cl in p.clauses for f in cl}


def pkey(p):
    return repr(sorted(sorted(cl.items()) for cl in p.clauses)) if isinstance(p, Pred) else repr(p.clauses)


class ResultCache:
    def __init__(self, inst, ix):
        self.inst, self.ix = inst, ix
        svcs = inst.services
        preds, das_maps = {}, {}
        for s in svcs:
            assert s.rounding == "carrier" and not s.extrapolate, "B7 key exactness: carrier rounding, no extrapolation"
            for r in s.surcharges:
                assert r.kind == "fixed", "B7 key exactness: fixed surcharges only"
            for p in [s.oversize, s.oversize2, s.inelig] + [r.pred for r in s.surcharges]:
                if p is None:
                    continue
                fs = pfeats(p)
                if "DAS" in fs:
                    assert fs <= {"DAS", "RES"}, f"DAS predicate reads {fs}"
                    continue
                assert fs <= ALLOWED, f"predicate reads {fs - ALLOWED}"
                preds.setdefault(pkey(p), p)
            if s.das_start is not None:
                das_maps.setdefault((id(s.das_start), id(s.das_level)), s)
        self.preds = list(preds.values())
        self.das = [(s.das_start, s.das_level) for s in das_maps.values()]
        self.das_l = [(a.tolist(), b.tolist()) for a, b in self.das]
        self.gsvc = [svcs[int(g["sids"][0])] for g in ix.groups]
        self.gcomp = [Compiled(s) for s in self.gsvc]
        st = np.unique(np.concatenate([v for v in inst.fuel.starts.values()]))
        self.fuel_starts = st; self.fuel_l = st.tolist()
        self.nwords = (len(self.preds) + 62) // 63
        self.ncols = 1 + len(self.das) + len(ix.groups) + self.nwords + 2
        self.table = {}

    # ---------------- vectorised keys (batch)
    def keys(self, P, F0=None):
        ix = self.ix
        F0 = P.base_feats() if F0 is None else F0
        N = len(P)
        K = np.zeros((N, self.ncols), dtype=np.int64)
        K[:, 0] = ix.sig_of[np.searchsorted(ix.U, P.key, side="right") - 1]
        c = 1
        for st, lv in self.das:
            i = np.searchsorted(st, P.key, side="right") - 1
            K[:, c] = np.where(i < 0, 0, lv[np.clip(i, 0, None)]); c += 1
        for g, s0 in zip(ix.groups, self.gsvc):
            t = billable(s0, P.a, F0[:, FI["V"]])
            K[:, c] = np.searchsorted(g["E"], t - 1e-9, side="left"); c += 1
        for w in range(self.nwords):
            m = np.zeros(N, dtype=np.int64)
            for b, p in enumerate(self.preds[63 * w: 63 * (w + 1)]):
                m |= p.eval(F0).astype(np.int64) << b
            K[:, c] = m; c += 1
        K[:, c] = P.res.astype(np.int64); c += 1
        K[:, c] = np.searchsorted(self.fuel_starts, P.day, side="right")
        return K

    @staticmethod
    def as_void(K):
        K = np.ascontiguousarray(K)
        return K.view(np.dtype((np.void, K.dtype.itemsize * K.shape[1]))).ravel()

    # ---------------- scalar key (Q1)
    def key1(self, key, a, L, M, S, res, day):
        ix = self.ix
        out = [ix.sig_of_l[bisect.bisect_right(ix.Ul, key) - 1]]
        for st, lv in self.das_l:
            j = bisect.bisect_right(st, key) - 1
            out.append(lv[j] if j >= 0 else 0)
        V = L * M * S
        for g, c0 in zip(ix.groups, self.gcomp):
            out.append(bisect.bisect_left(g["El"], billable1(c0, a, V) - 1e-9))
        f = [L, M, S, L + 2 * M + 2 * S, V, a, 0.0, 0, res, 0, 0, day, 0, L + M + S]
        m = 0
        for b, p in enumerate(self.preds):
            if p.eval1(f):
                m |= 1 << b
        out.append(m); out.append(int(res)); out.append(bisect.bisect_right(self.fuel_l, day))
        return tuple(out)

    def query1(self, overlay, key, a, L, M, S, res, day, stats=None):
        k = self.key1(key, a, L, M, S, res, day)
        r = self.table.get(k)
        if r is None:
            r = overlay.get(k)
            if r is None:
                r = self.ix.query1(key, a, L, M, S, res, day)
                overlay[k] = r
                if stats is not None:
                    stats[1] += 1
                return r
        if stats is not None:
            stats[0] += 1
        return r


def batch_lookup(rc, P, table_v, table_ans, F0=None):
    """Answer a batch: keys -> lookup in a sorted void-key table -> RQ batch on one representative
    per missing key. Returns best, arg, #distinct misses, #hit parcels."""
    K = rc.keys(P, F0)
    Kv = rc.as_void(K)
    u, first, inv = np.unique(Kv, return_index=True, return_inverse=True)
    inv = inv.ravel()
    ans_c = np.empty(len(u), np.int64); ans_s = np.empty(len(u), np.int64)
    hit = np.zeros(len(u), bool)
    if table_v is not None and len(table_v):
        pos = np.searchsorted(table_v, u)
        pos_c = np.clip(pos, 0, len(table_v) - 1)
        hit = (pos < len(table_v)) & (table_v[pos_c] == u)
        ans_c[hit] = table_ans[0][pos_c[hit]]; ans_s[hit] = table_ans[1][pos_c[hit]]
    miss = np.nonzero(~hit)[0]
    if len(miss):
        b, a = rc.ix.query_batch(P.subset(first[miss]))
        ans_c[miss] = b; ans_s[miss] = a
    return ans_c[inv], ans_s[inv], len(miss), int(hit[inv].sum()), (u, ans_c, ans_s)


def run(iname, wl, N_big=1_000_000, N_ss=100_000, q=1000, n_edits=20, seed=7):
    t0 = time.time()
    inst = INSTS[iname]()
    ix = RQIndex(inst)
    rc = ResultCache(inst, ix)
    comp, fuel = [Compiled(s) for s in inst.services], fuel_fn(inst)
    gen = WLS[wl]
    out = {"n": len(inst.services), "workload": wl, "key_columns": rc.ncols, "distinct_geometric_predicates": len(rc.preds),
           "das_maps": len(rc.das), "groups": len(ix.groups), "signatures": int(ix.n_sig), "fuel_periods": int(len(rc.fuel_starts) + 1),
           "load_start": load()}
    # ---- cold start on a 1M stream: distinct keys in prefixes
    A = gen(N_big, seed=101)
    chunks, Ks = 200_000, []
    for i in range(0, N_big, chunks):
        Ks.append(rc.keys(A.subset(np.arange(i, min(N_big, i + chunks)))))
    K = np.concatenate(Ks); del Ks
    Kv = rc.as_void(K)
    cold = {}
    for n_ in (10_000, 100_000, 1_000_000):
        if n_ <= N_big:
            d = len(np.unique(Kv[:n_]))
            cold[str(n_)] = {"distinct_keys": int(d), "hit_rate": 1 - d / n_}
    out["cold_start"] = cold
    # time-ordered stream (realistic: parcels arrive by ship day; a fuel period never recurs)
    o = np.argsort(A.day, kind="stable")
    n100 = min(100_000, N_big); d100 = len(np.unique(Kv[o][:n100]))
    out["cold_start_time_ordered_100k"] = {"parcels": n100, "distinct_keys": int(d100), "hit_rate": 1 - d100 / n100}
    # ---- fill the table with the 1M stream (answers by RQ batch on one representative per key)
    u, first = np.unique(Kv, return_index=True)
    tr = tns()
    b, a = ix.query_batch(A.subset(first))
    fill_s = (tns() - tr) / 1e9
    # exactness of the filled table: every parcel of the 1M stream vs brute force (chunked)
    inv = np.searchsorted(u, Kv)
    agree = 0
    for i in range(0, N_big, chunks):
        sl = np.arange(i, min(N_big, i + chunks))
        tb, ta = brute_vec(inst, A.subset(sl))
        agree += int(((b[inv[sl]] == tb) & (a[inv[sl]] == ta)).sum())
    out["table_after_1M"] = {"entries": int(len(u)), "fill_cpu_s_rq_batch": fill_s, "agree_all_1M_parcels": agree / N_big}
    table_v, table_ans = u, (b, a)
    # memory: Python dict {key tuple: (cost, sid)} measured with tracemalloc on the actual entries; packed estimate
    tracemalloc.start()
    keys_t = [tuple(r) for r in K[first].tolist()]
    dct = {k: (int(c), int(s)) for k, c, s in zip(keys_t, b.tolist(), a.tolist())}
    cur, _ = tracemalloc.get_traced_memory(); tracemalloc.stop()
    rc.table = dct
    out["table_after_1M"]["python_dict_MB"] = cur / 1e6
    out["table_after_1M"]["packed_MB"] = len(u) * (rc.ncols * 8 + 12) / 1e6
    out["table_after_1M"]["packed_bytes_per_entry"] = rc.ncols * 8 + 12
    out["RQ_index_MB"] = ix.nbytes() / 1e6
    del keys_t
    # ---- steady state: a fresh 100k stream against the warm table
    B = gen(N_ss, seed=202)
    KB = rc.as_void(rc.keys(B))
    pos = np.clip(np.searchsorted(table_v, KB), 0, len(table_v) - 1)
    hitB = table_v[pos] == KB
    out["steady_state_100k"] = {"hit_rate": float(hitB.mean()), "new_distinct_keys_on_miss": int(len(np.unique(KB[~hitB])))}
    truthB = brute_vec(inst, B)
    # ---- Q1: interleaved per repetition (thread CPU time per query)
    Q = B.subset(np.arange(q)); truthQ = (truthB[0][:q], truthB[1][:q])
    meth = ["B1_brute", "RQ_index", "B7_cache_steady", "B7_cache_cold", "B7_key_only"]
    per = {m: [] for m in meth}; per_p50 = {m: [] for m in meth}; agreeQ = {m: 1.0 for m in meth if m != "B7_key_only"}
    hitstats = []
    for rep in range(REPS):
        order = meth[rep % len(meth):] + meth[:rep % len(meth)]
        for m in order:
            lat = np.empty(q); ok = 0
            st = [0, 0]; overlay = {}
            if m == "B7_cache_cold":
                saved, rc.table = rc.table, {}
            for i in range(q):
                x = args(Q, i)
                t = tns()
                if m == "B1_brute":
                    r = brute1(comp, fuel, *x)
                elif m == "RQ_index":
                    r = ix.query1(*x)
                elif m == "B7_key_only":
                    r = rc.key1(*x)
                else:
                    r = rc.query1(overlay, *x, stats=st)
                lat[i] = tns() - t
                if m != "B7_key_only":
                    ok += (r[0] == truthQ[0][i] and r[1] == truthQ[1][i])
            if m == "B7_cache_cold":
                rc.table = saved
            per[m].append(lat.mean() / 1e3); per_p50[m].append(np.median(lat) / 1e3)
            if m in agreeQ:
                agreeQ[m] = min(agreeQ[m], ok / q)
            if m == "B7_cache_steady":
                hitstats.append(st[0] / q)
    out["Q1_us"] = {m: {"mean_us": summ(per[m]), "p50_us": summ(per_p50[m]), **({"agree": agreeQ[m]} if m in agreeQ else {})}
                    for m in meth}
    out["Q1_us"]["B7_cache_steady"]["hit_rate_on_queries"] = float(np.mean(hitstats))
    out["Q1_speedup_vs_B1_mean"] = {m: summ(np.array(per["B1_brute"]) / np.array(per[m])) for m in meth if m != "B1_brute"}
    # ---- Q2: batch of the 100k steady stream
    meth2 = ["B1_brute_vec", "RQ_batch", "B7_batch_cold", "B7_batch_steady"]
    t2 = {m: [] for m in meth2}; agree2 = {m: 1.0 for m in meth2}; misses = {}
    for rep in range(REPS):
        order = meth2[rep % len(meth2):] + meth2[:rep % len(meth2)]
        for m in order:
            t = tns()
            if m == "B1_brute_vec":
                bb, aa = brute_vec(inst, B)
            elif m == "RQ_batch":
                bb, aa = ix.query_batch(B)
            elif m == "B7_batch_cold":
                bb, aa, nm, nh, _ = batch_lookup(rc, B, None, None); misses[m] = nm
            else:
                bb, aa, nm, nh, _ = batch_lookup(rc, B, table_v, table_ans); misses[m] = nm
            t2[m].append((tns() - t) / 1e9)
            agree2[m] = min(agree2[m], float(((bb == truthB[0]) & (aa == truthB[1])).mean()))
    out["Q2_100k_s"] = {m: {"cpu_s": summ(t2[m]), "agree": agree2[m], **({"distinct_missing_keys": misses[m]} if m in misses else {})}
                        for m in meth2}
    out["Q2_speedup_vs_B1"] = {m: summ(np.array(t2["B1_brute_vec"]) / np.array(t2[m])) for m in meth2 if m != "B1_brute_vec"}
    # ---- invalidation: one cell price edit (s, z, k) and one fuel-week edit
    rng = np.random.default_rng(seed)
    Kfirst = K[first]
    Prep = A.subset(first)          # one representative parcel per cached key
    F0rep = Prep.base_feats()
    sig_col = Kfirst[:, 0]
    inv_log = []
    for _ in range(n_edits):
        s = inst.services[int(rng.integers(len(inst.services)))]
        zs, ks = np.nonzero(s.price < INF)
        j = int(rng.integers(len(zs))); z, k = int(zs[j]), int(ks[j])
        newp = int(s.price[z, k] * rng.uniform(0.85, 1.15))
        # cache: entries whose signature maps s to z and whose (tier-raised) band of s is k
        t = tns()
        cand = np.nonzero(ix.zvec[sig_col, s.sid] == z)[0]
        _, zz, tt, _ = eval_service(s, Prep.subset(cand), F0rep[cand], inst.fuel, return_parts=True)
        kb = np.clip(np.searchsorted(s.breaks, tt - 1e-9, side="left"), 0, len(s.breaks) - 1)
        aff = cand[kb == k]
        for r_ in Kfirst[aff].tolist():
            rc.table.pop(tuple(r_), None)
        t_find = (tns() - t) / 1e9
        old = int(s.price[z, k])
        t = tns(); ix.update_price(s.sid, z, k, newp); t_rq = (tns() - t) / 1e9
        # refill the invalidated entries (RQ batch on their representatives) and check exactness
        t = tns()
        if len(aff):
            nb, na = ix.query_batch(Prep.subset(aff))
        t_refill = (tns() - t) / 1e9
        ok = 1.0
        if len(aff):
            tb, ta = brute_vec(inst, Prep.subset(aff))
            ok = float(((nb == tb) & (na == ta)).mean())
            changed = int(((nb != table_ans[0][aff]) | (na != table_ans[1][aff])).sum())
            for r_, c_, s_ in zip(Kfirst[aff].tolist(), nb.tolist(), na.tolist()):
                rc.table[tuple(r_)] = (c_, s_)
            table_ans[0][aff] = nb; table_ans[1][aff] = na
        else:
            changed = 0
        inv_log.append({"invalidated": int(len(aff)), "frac": len(aff) / len(first), "answers_changed": changed,
                        "find_drop_s": t_find, "refill_s": t_refill, "rq_index_update_s": t_rq, "refill_agree": ok})
    # entries of the table whose answer is stale after the edits but not invalidated? (must be 0)
    tb, ta = brute_vec(inst, Prep)
    stale = int(((tb != table_ans[0]) | (ta != table_ans[1])).sum())
    out["cell_edit_invalidation"] = {k_: summ([x[k_] for x in inv_log]) for k_ in inv_log[0]}
    out["cell_edit_invalidation"]["edits"] = n_edits
    out["cell_edit_invalidation"]["stale_entries_after_all_edits"] = stale
    fp = Kfirst[:, -1]
    out["fuel_week_edit_invalidation"] = {"entries_per_fuel_period": summ(np.bincount(fp)[np.bincount(fp) > 0]),
                                          "frac_of_table_mean": float(np.mean(np.bincount(fp)[np.bincount(fp) > 0]) / len(first))}
    out["load_end"] = load(); out["uptime_end"] = uptime(); out["wall_s"] = time.time() - t0
    return out


if __name__ == "__main__":
    insts = sys.argv[1].split(",") if len(sys.argv) > 1 else ["PUB2", "PUB2x60", "PUB2x240"]
    wls = sys.argv[2].split(",") if len(sys.argv) > 2 else ["olist", "furn"]
    tag = sys.argv[3] if len(sys.argv) > 3 else ""
    res = {"_uptime_start": uptime()}
    for iname in insts:
        for wl in wls:
            r = run(iname, wl, N_big=int(os.environ.get('REV1_NBIG', 1_000_000)), N_ss=int(os.environ.get('REV1_NSS', 100_000)),
                    q=int(os.environ.get('REV1_Q', 1000)))
            res[f"{iname}/{wl}"] = r
            print(iname, wl, json.dumps({k: r[k] for k in ("cold_start", "steady_state_100k", "table_after_1M")}), flush=True)
            print("  Q1", {m: round(v["mean_us"]["median"], 1) for m, v in r["Q1_us"].items()},
                  "Q2", {m: round(v["cpu_s"]["median"], 3) for m, v in r["Q2_100k_s"].items()}, flush=True)
            save(f"b7_cache{tag}", res)
