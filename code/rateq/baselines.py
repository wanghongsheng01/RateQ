"""Baselines for cheapest-feasible-service queries.

B1  brute force (production practice): every service, exact evaluation (scalar: index.brute1;
    vectorised over parcels: model.brute_vec)
B2  SQL (DuckDB): range joins + aggregation + ROW_NUMBER() window
B3  R-tree on breakpoint cells: 4-d boxes (key, actual weight, volume, LB cost), cost-bounded
    window queries with doubling threshold, exact verification
B4  per-service precompute: dense base-price matrix per service over (zone, elementary weight
    interval of its group); evaluation skips band search
B5  kinetic sweep: per (signature, group) sweep over billable weight, kinetic sorted list
    of LB keys updated at breakpoint events (certificate failures), threshold scan per parcel
B6  MIP (HiGHS): per-parcel mixed-integer model of the rule program (band binaries with
    big-M linking to billable weight, surcharge group maxima), exact re-evaluation of the pick
"""
import bisect
import math
import time
import numpy as np
from .model import INF, FI, FEATS, billable, eval_service, Instance, Parcels
from .index import cost1, Compiled, fuel_fn, billable1, INF_I

# ---------------------------------------------------------------- B4 precompute

class Precompute:
    def __init__(self, inst: Instance):
        self.inst = inst
        self.comp = [Compiled(s) for s in inst.services]
        self.fuel = fuel_fn(inst)
        gk = {}
        for s in inst.services:
            gk.setdefault(s.group_key, []).append(s.sid)
        self.groups = []
        for key, sids in gk.items():
            E = np.unique(np.concatenate([inst.services[i].breaks for i in sids]))
            mats = {}
            for sid in sids:
                s = inst.services[sid]
                k = np.searchsorted(s.breaks, E - 1e-9, side="left")
                kk = np.clip(k, 0, len(s.breaks) - 1)
                suf = np.minimum.accumulate(s.price[:, ::-1], axis=1)[:, ::-1]  # safe under oversize raise
                M = suf[:, kk].copy()
                M[:, k >= len(s.breaks)] = INF if not s.extrapolate else M[:, k >= len(s.breaks)]
                mats[sid] = M.tolist()
            self.groups.append((E.tolist(), sids, mats, Compiled(inst.services[sids[0]])))

    def nbytes(self):
        return sum(8 * len(m) * len(m[0]) for _, _, mats, _ in self.groups for m in mats.values())

    def query1(self, key, a, L, M, S, res, day):
        V = L * M * S
        best, arg = INF_I, -1
        for E, sids, mats, c0 in self.groups:
            t = billable1(c0, a, V)
            e = bisect.bisect_left(E, t - 1e-9)
            for sid in sids:
                c = self.comp[sid]
                i = bisect.bisect_right(c.zstart, key) - 1
                if i < 0 or c.zzone[i] < 0:
                    continue
                # cheap reject: base alone (no fuel, no surcharge) already worse than best
                if e < len(E) and mats[sid][c.zzone[i]][e] > best:
                    continue
                v = cost1(c, key, a, L, M, S, res, day, self.fuel, t)
                if v < best or (v == best and sid < arg):
                    best, arg = v, sid
        return best, arg


# ---------------------------------------------------------------- B2 DuckDB

def _clause_rows(sid, kind, rid, pred):
    rows = []
    if pred is None:
        return rows
    for ci, cl in enumerate(pred.clauses):
        th = {f: -1e18 for f in FEATS}
        th.update(cl)
        rows.append([sid, kind, rid, ci] + [float(th[f]) for f in FEATS])
    return rows


class DuckSQL:
    def __init__(self, inst: Instance, threads=1):
        import duckdb
        self.con = duckdb.connect()
        self.con.execute(f"PRAGMA threads={threads}")
        self.inst = inst
        self._load()

    def _load(self):
        import pandas as pd
        svc, zones, das, bands, cls, surp, fuel = [], [], [], [], [], [], []
        dclass = {}
        for s in self.inst.services:
            dc = -1
            if s.das_start is not None:
                k = id(s.das_start)
                if k not in dclass:
                    dclass[k] = len(dclass)
                    hi = np.r_[s.das_start[1:] - 1, 10 ** 12]
                    for lo, h, lv in zip(s.das_start, hi, s.das_level):
                        if lv > 0:
                            das.append([dclass[k], int(lo), int(h), int(lv)])
                dc = dclass[k]
            svc.append([s.sid, s.divisor, s.dim_thr, 1 if s.rounding == "erp_style" else 0, int(s.extrapolate),
                        s.limit_weight, float(s.breaks[-1]), s.oversize_min, s.oversize2_min, s.fuel_class, dc])
            hi = np.r_[s.zstart[1:] - 1, 10 ** 12]
            for lo, h, z in zip(s.zstart, hi, s.zzone):
                if z >= 0:
                    zones.append([s.sid, int(lo), int(h), int(z)])
            lo_b = np.r_[-1e18, s.breaks[:-1]]
            for z in range(s.price.shape[0]):
                for k in range(len(s.breaks)):
                    if s.price[z, k] < INF:
                        bands.append([s.sid, z, float(lo_b[k]), float(s.breaks[k]), int(s.price[z, k]),
                                      int(k == len(s.breaks) - 1)])
            cls += _clause_rows(s.sid, 0, -1, s.oversize) + _clause_rows(s.sid, 1, -1, s.inelig) + _clause_rows(s.sid, 3, -1, s.oversize2)
            for ri, r in enumerate(s.surcharges):
                cls += _clause_rows(s.sid, 2, ri, r.pred)
                for z, p in enumerate(r.price):
                    surp.append([s.sid, ri, z, int(p), r.group, int(r.fuel)])
        for fc, st in self.inst.fuel.starts.items():
            hi = np.r_[st[1:] - 1, 10 ** 9]
            for lo, h, bp in zip(st, hi, self.inst.fuel.bps[fc]):
                fuel.append([fc, int(lo), int(h), int(bp)])
        cols = lambda *c: list(c)
        dfs = {
            "svc": pd.DataFrame(svc, columns=cols("sid", "D", "thr", "gd", "ex", "lim", "bmax", "omin", "omin2", "fc", "dc")),
            "zones": pd.DataFrame(zones, columns=cols("sid", "lo", "hi", "zone")),
            "das": pd.DataFrame(das or [[-9, 0, 0, 0]], columns=cols("dc", "lo", "hi", "lvl")),
            "bands": pd.DataFrame(bands, columns=cols("sid", "zone", "lo", "hi", "price", "top")),
            "cl": pd.DataFrame(cls, columns=["sid", "kind", "rid", "cid"] + ["t_" + f for f in FEATS]),
            "surp": pd.DataFrame(surp, columns=cols("sid", "rid", "zone", "price", "grp", "fu")),
            "fuel": pd.DataFrame(fuel or [[-9, 0, 0, 0]], columns=cols("fc", "lo", "hi", "bp")),
        }
        for k, df in dfs.items():
            self.con.register("_" + k, df)
            self.con.execute(f"CREATE OR REPLACE TABLE {k} AS SELECT * FROM _{k}")

    SQL = """
WITH pz AS (
  SELECT p.*, s.*, z.zone FROM parcels p JOIN zones z ON p.key BETWEEN z.lo AND z.hi JOIN svc s ON s.sid = z.sid),
pd AS (SELECT pz.*, coalesce(d.lvl, 0) AS das FROM pz LEFT JOIN das d ON d.dc = pz.dc AND pz.key BETWEEN d.lo AND d.hi),
pt AS (SELECT *, CASE WHEN gd = 1 THEN greatest(
           ceil(floor(a*10 + 1e-9)/10 - 1e-9),
           CASE WHEN V > thr THEN (CASE WHEN ceil(V/D*1000 - 1e-9)/1000 > 1
                THEN ceil(floor(ceil(V/D*1000 - 1e-9)/1000*10 + 1e-9)/10 - 1e-9) ELSE 1.0 END) ELSE 0.0 END)
         ELSE greatest(a, CASE WHEN V > thr THEN V/D ELSE 0.0 END) END AS t0 FROM pd),
osf2 AS (SELECT DISTINCT pt.pid, pt.sid FROM pt JOIN cl c ON c.sid = pt.sid AND c.kind = 3
        AND pt.L > c.t_L AND pt.M > c.t_M AND pt.S > c.t_S AND pt.G > c.t_G AND pt.V > c.t_V
        AND pt.a > c.t_A AND pt.t0 > c.t_T AND pt.das > c.t_DAS AND pt.res > c.t_RES),
pt1 AS (SELECT pt.* REPLACE (CASE WHEN o.pid IS NULL THEN t0 ELSE greatest(t0, omin2) END AS t0)
        FROM pt LEFT JOIN osf2 o ON o.pid = pt.pid AND o.sid = pt.sid),
osf AS (SELECT DISTINCT pt1.pid, pt1.sid FROM pt1 JOIN cl c ON c.sid = pt1.sid AND c.kind = 0
        AND pt1.L > c.t_L AND pt1.M > c.t_M AND pt1.S > c.t_S AND pt1.G > c.t_G AND pt1.V > c.t_V
        AND pt1.a > c.t_A AND pt1.t0 > c.t_T AND pt1.das > c.t_DAS AND pt1.res > c.t_RES),
pt2 AS (SELECT pt1.*, CASE WHEN o.pid IS NULL THEN t0 ELSE greatest(t0, omin) END AS t
        FROM pt1 LEFT JOIN osf o ON o.pid = pt1.pid AND o.sid = pt1.sid),
inel AS (SELECT DISTINCT pt2.pid, pt2.sid FROM pt2 JOIN cl c ON c.sid = pt2.sid AND c.kind = 1
        AND pt2.L > c.t_L AND pt2.M > c.t_M AND pt2.S > c.t_S AND pt2.G > c.t_G AND pt2.V > c.t_V
        AND pt2.a > c.t_A AND pt2.t > c.t_T AND pt2.das > c.t_DAS AND pt2.res > c.t_RES),
el AS (SELECT pt2.* FROM pt2 ANTI JOIN inel i ON i.pid = pt2.pid AND i.sid = pt2.sid),
based AS (
  SELECT el.pid, el.sid, el.zone, el.t, el.fc, el.day, el.L, el.M, el.S, el.G, el.V, el.a, el.das, el.res, b.price AS base
  FROM el JOIN bands b ON b.sid = el.sid AND b.zone = el.zone AND el.t - 1e-9 > b.lo AND el.t - 1e-9 <= b.hi
  WHERE NOT (el.ex = 1 AND el.t > el.lim)
  UNION ALL
  SELECT el.pid, el.sid, el.zone, el.t, el.fc, el.day, el.L, el.M, el.S, el.G, el.V, el.a, el.das, el.res,
         CAST(ceil(least(b.price * el.t / el.bmax, 1e15) - 1e-6) AS BIGINT) AS base
  FROM el JOIN bands b ON b.sid = el.sid AND b.zone = el.zone AND b.top = 1
  WHERE el.ex = 1 AND el.t > el.lim),
fired AS (SELECT DISTINCT b.pid, b.sid, c.rid FROM based b JOIN cl c ON c.sid = b.sid AND c.kind = 2
        AND b.L > c.t_L AND b.M > c.t_M AND b.S > c.t_S AND b.G > c.t_G AND b.V > c.t_V
        AND b.a > c.t_A AND b.t > c.t_T AND b.das > c.t_DAS AND b.res > c.t_RES),
gmax AS (SELECT f.pid, f.sid, sp.grp, sp.fu, max(sp.price) AS amt FROM fired f
        JOIN based b ON b.pid = f.pid AND b.sid = f.sid
        JOIN surp sp ON sp.sid = f.sid AND sp.rid = f.rid AND sp.zone = b.zone GROUP BY ALL),
sums AS (SELECT pid, sid, sum(CASE WHEN fu = 1 THEN amt ELSE 0 END) AS sf,
                         sum(CASE WHEN fu = 0 THEN amt ELSE 0 END) AS snf FROM gmax GROUP BY ALL),
costs AS (SELECT b.pid, b.sid,
         (CAST(b.base + coalesce(s.sf, 0) AS HUGEINT) * (10000 + coalesce(f.bp, 0)) + 5000) // 10000 + coalesce(s.snf, 0) AS cost
         FROM based b LEFT JOIN sums s ON s.pid = b.pid AND s.sid = b.sid
         LEFT JOIN fuel f ON f.fc = b.fc AND b.day BETWEEN f.lo AND f.hi)
SELECT pid, sid, cost FROM (
  SELECT *, row_number() OVER (PARTITION BY pid ORDER BY cost, sid) AS rn FROM costs) WHERE rn = 1
"""

    def query_batch(self, P: Parcels):
        import pandas as pd
        df = pd.DataFrame({"pid": np.arange(len(P)), "key": P.key, "a": P.a, "L": P.L, "M": P.M, "S": P.S,
                           "G": P.L + 2 * P.M + 2 * P.S, "V": P.L * P.M * P.S, "res": P.res, "day": P.day})
        self.con.register("_parcels", df)
        self.con.execute("CREATE OR REPLACE TABLE parcels AS SELECT * FROM _parcels")
        r = self.con.execute(self.SQL).fetchnumpy()
        best = np.full(len(P), INF, dtype=np.int64); arg = np.full(len(P), -1, dtype=np.int64)
        best[r["pid"]] = np.asarray(r["cost"]).astype(np.int64); arg[r["pid"]] = r["sid"]
        return best, arg

    def update_price(self, sid, z, k, price):
        s = self.inst.services[sid]
        self.con.execute("UPDATE bands SET price = ? WHERE sid = ? AND zone = ? AND hi = ?",
                         [int(price), sid, z, float(s.breaks[k])])


class DuckSQLTuned(DuckSQL):
    """B2 tuned: every interval lookup (zone, DAS ZIP list, weight band, fuel week) is an ASOF join on the
    interval start plus an end check, instead of a BETWEEN range join; the rule logic is unchanged."""
    SQL = (DuckSQL.SQL
           .replace("""pz AS (
  SELECT p.*, s.*, z.zone FROM parcels p JOIN zones z ON p.key BETWEEN z.lo AND z.hi JOIN svc s ON s.sid = z.sid),
pd AS (SELECT pz.*, coalesce(d.lvl, 0) AS das FROM pz LEFT JOIN das d ON d.dc = pz.dc AND pz.key BETWEEN d.lo AND d.hi),""",
                    """ps AS (SELECT p.*, s.* FROM parcels p CROSS JOIN svc s),
pz AS (SELECT ps.*, z.zone FROM ps ASOF JOIN zones z ON z.sid = ps.sid AND ps.key >= z.lo WHERE ps.key <= z.hi),
pd AS (SELECT pz.*, CASE WHEN pz.key <= d.hi THEN d.lvl ELSE 0 END AS das
       FROM pz ASOF LEFT JOIN das d ON d.dc = pz.dc AND pz.key >= d.lo),""")
           .replace("""  FROM el JOIN bands b ON b.sid = el.sid AND b.zone = el.zone AND el.t - 1e-9 > b.lo AND el.t - 1e-9 <= b.hi
  WHERE NOT (el.ex = 1 AND el.t > el.lim)""",
                    """  FROM el ASOF JOIN bands b ON b.sid = el.sid AND b.zone = el.zone AND el.t - 1e-9 > b.lo
  WHERE el.t - 1e-9 <= b.hi AND NOT (el.ex = 1 AND el.t > el.lim)""")
           .replace("""         LEFT JOIN fuel f ON f.fc = b.fc AND b.day BETWEEN f.lo AND f.hi)""",
                    """         ASOF LEFT JOIN fuel f ON f.fc = b.fc AND b.day >= f.lo)"""))
    assert SQL.count("ASOF") == 4, "DuckSQLTuned: base SQL changed"


# ---------------------------------------------------------------- B3 R-tree

class RTreeIdx:
    """Boxes (key, a, V, LB) per (service, zone interval, band); restricted to a key range."""

    def __init__(self, inst: Instance, key_lo=0, key_hi=10 ** 12):
        from rtree import index as rindex
        self.inst = inst
        self.comp = [Compiled(s) for s in inst.services]
        self.fuel = fuel_fn(inst)
        p = rindex.Property(); p.dimension = 4
        self.nboxes = 0

        def gen():
            for s in inst.services:
                hi = np.r_[s.zstart[1:] - 1, 10 ** 12]
                suf = np.minimum.accumulate(s.price[:, ::-1], axis=1)[:, ::-1]
                lo_b = np.r_[0.0, s.breaks[:-1]]
                D, thr = s.divisor, s.dim_thr
                for zlo, zhi, z in zip(s.zstart, hi, s.zzone):
                    if z < 0 or zhi < key_lo or zlo > key_hi:
                        continue
                    a0, a1 = float(max(zlo, key_lo)), float(min(zhi, key_hi))
                    for k in range(len(s.breaks)):
                        lb = suf[z, k]
                        if lb >= INF:
                            continue
                        lo, h = float(lo_b[k]), float(s.breaks[k])
                        # region A: a in [lo, h], d(V) <= h
                        yield (self.nboxes, (a0, lo, 0.0, float(lb), a1, h, max(thr, D * h), float(lb)), s.sid)
                        self.nboxes += 1
                        if D * h > thr:  # region B: a <= lo, d(V) in (lo, h]
                            yield (self.nboxes, (a0, 0.0, max(thr, D * lo), float(lb), a1, lo, D * h, float(lb)), s.sid)
                            self.nboxes += 1
        t = time.time()
        self.idx = rindex.Index(gen(), properties=p)
        self.build_s = time.time() - t

    def query1(self, key, a, L, M, S, res, day):
        V = L * M * S
        theta, done = 1500.0, set()
        best, arg = INF_I, -1
        while True:
            for sid in self.idx.intersection((key, a, V, 0.0, key, a, V, theta), objects="raw"):
                if sid in done:
                    continue
                done.add(sid)
                v = cost1(self.comp[sid], key, a, L, M, S, res, day, self.fuel)
                if v < best or (v == best and sid < arg):
                    best, arg = v, sid
            if best <= theta or theta > 1e7:
                break
            theta *= 2
        if best >= INF_I and theta > 1e7:  # nothing within any finite bound: verify remaining
            for c in self.comp:
                if c.sid not in done:
                    v = cost1(c, key, a, L, M, S, res, day, self.fuel)
                    if v < best:
                        best, arg = v, c.sid
        return best, arg


# ---------------------------------------------------------------- B5 kinetic sweep

def kinetic_batch(ix, P: Parcels, stats=None):
    """Batch-only kinetic sweep using the signature partition and groups of `ix` but no stored
    order: the sorted LB list is maintained by events while sweeping billable weight."""
    from sortedcontainers import SortedList
    inst = ix.inst
    svcs = inst.services
    F0 = P.base_feats()
    sg = ix.sig_of[np.searchsorted(ix.U, P.key, side="right") - 1]
    best = np.full(len(P), INF, dtype=np.int64); arg = np.full(len(P), -1, dtype=np.int64)
    nev = events = 0
    for g in ix.groups:
        s0 = svcs[int(g["sids"][0])]
        t = billable(s0, P.a, F0[:, FI["V"]])
        e = np.searchsorted(g["E"], t - 1e-9, side="left")
        bp = inst.fuel.at(g["fc"], P.day)
        order = np.lexsort((e, sg))
        sgs, es = sg[order], e[order]
        bounds = np.nonzero(np.r_[True, sgs[1:] != sgs[:-1], True])[0]
        for bi in range(len(bounds) - 1):
            idx_s = order[bounds[bi]:bounds[bi + 1]]
            sgi = int(sg[idx_s[0]])
            hulls = {int(s): ix._hull(svcs[int(s)], int(ix.zvec[sgi, int(s)]), g["E"]) for s in g["sids"]}  # no stored order
            sl = SortedList()
            cur = {}
            ev = es[bounds[bi]:bounds[bi + 1]]
            for ee in np.unique(ev):
                for s, h in hulls.items():  # certificate failures between consecutive intervals
                    if cur.get(s) != h[ee]:
                        if s in cur and cur[s] < INF:
                            sl.remove((cur[s], s))
                        cur[s] = h[ee]
                        if h[ee] < INF:
                            sl.add((h[ee], s))
                        events += 1
                sub = idx_s[ev == ee]
                for (lb, s) in sl:
                    lbs = (lb * (10000 + bp[sub]) + 5000) // 10000
                    sub = sub[lbs <= best[sub]]
                    if len(sub) == 0:
                        break
                    v = eval_service(svcs[s], P.subset(sub), F0[sub], inst.fuel); nev += len(sub)
                    better = (v < best[sub]) | ((v == best[sub]) & (s < arg[sub]))
                    best[sub] = np.where(better, v, best[sub]); arg[sub] = np.where(better, s, arg[sub])
    if stats is not None:
        stats.append((nev, events))
    return best, arg


# ---------------------------------------------------------------- B6 MIP

def mip_query1(inst: Instance, P: Parcels, i, fuel):
    """Per-parcel MIP of the rule program; returns (exact cost of the chosen service, sid)."""
    import highspy
    h = highspy.Highs(); h.setOptionValue("output_flag", False)
    key, a, L, M, S = int(P.key[i]), float(P.a[i]), float(P.L[i]), float(P.M[i]), float(P.S[i])
    V, G = L * M * S, L + 2 * M + 2 * S
    comp = [Compiled(s) for s in inst.services] if not hasattr(inst, "_comp") else inst._comp
    inst._comp = comp
    inf = highspy.kHighsInf
    ys = []
    big = 1e4
    for s, c in zip(inst.services, comp):
        j = bisect.bisect_right(c.zstart, key) - 1
        if j < 0 or c.zzone[j] < 0:
            continue
        z = c.zzone[j]
        das = 0
        if c.das_s is not None:
            q = bisect.bisect_right(c.das_s, key) - 1
            das = c.das_l[q] if q >= 0 else 0
        f0 = [L, M, S, G, V, a, 0.0, das, P.res[i]]
        bpv = fuel(c.fc, int(P.day[i])) / 10000.0
        dim = V / c.D if V > c.thr else 0.0
        # billable weight t is a decision variable bounded below by actual and dim weight
        tlb = billable1(c, a, V)
        f0[6] = tlb
        if c.os2 is not None and any(all(f0[fi] > th for fi, th in cl) for cl in c.os2):
            tlb = max(tlb, c.omin2); f0[6] = tlb
        if c.os is not None and any(all(f0[fi] > th for fi, th in cl) for cl in c.os):
            tlb = max(tlb, c.omin)
        f0[6] = tlb
        if c.inel is not None and any(all(f0[fi] > th for fi, th in cl) for cl in c.inel):
            continue
        y = h.getNumCol(); h.addVar(0, 1); h.changeColIntegrality(y, highspy.HighsVarType.kInteger)
        h.changeColCost(y, 1e-6 * s.sid)  # equal-cost ties go to the smaller service id, as in every other method
        tv = h.getNumCol(); h.addVar(0, 1e4)
        # t = tlb * y (billable weight is fixed by the rules; an inequality would let the solver
        # over-declare weight into a cheaper band of a non-monotone table)
        h.addRow(0, 0, 2, np.array([tv, y], dtype=np.int32), np.array([1.0, -tlb]))
        xs, ub_terms, lb_terms = [], [], []
        lo_b = [0.0] + c.breaks[:-1]
        for k in range(c.K):
            p = c.price[z][k]
            if p >= INF_I:
                continue
            x = h.getNumCol(); h.addVar(0, 1); h.changeColIntegrality(x, highspy.HighsVarType.kInteger)
            h.changeColCost(x, p * (1 + bpv))
            xs.append(x); ub_terms.append(c.breaks[k] + 1e-9); lb_terms.append(lo_b[k])
        if not xs:
            h.changeColBounds(y, 0, 0); continue
        # sum x = y ; t <= sum b_k x_k ; t >= sum b_{k-1} x_k
        h.addRow(0, 0, len(xs) + 1, np.array(xs + [y], dtype=np.int32), np.array([1.0] * len(xs) + [-1.0]))
        h.addRow(0, inf, len(xs) + 1, np.array(xs + [tv], dtype=np.int32), np.array(ub_terms + [-1.0]))
        h.addRow(0, inf, len(xs) + 1, np.array([tv] + xs, dtype=np.int32), np.array([1.0] + [-v for v in lb_terms]))
        # surcharges: group maximum u_g >= amt_r * y for every fired rule (features fixed per parcel)
        groups = {}
        for cls, pr, gk, *_ in c.sur:
            if any(all(f0[fi] > th for fi, th in cl) for cl in cls):
                groups.setdefault(gk, []).append(pr[z])
        for (g, fu), amts in groups.items():
            u = h.getNumCol(); h.addVar(0, inf); h.changeColCost(u, (1 + bpv) if fu else 1.0)
            for amt in amts:
                h.addRow(0, inf, 2, np.array([u, y], dtype=np.int32), np.array([1.0, -float(amt)]))
        ys.append((y, s.sid))
    if not ys:
        return INF_I, -1
    h.addRow(1, 1, len(ys), np.array([y for y, _ in ys], dtype=np.int32), np.ones(len(ys)))
    h.run()
    sol = h.getSolution().col_value
    sid = max(ys, key=lambda x: sol[x[0]])[1]
    c = comp[sid]
    v = cost1(c, key, a, L, M, S, float(P.res[i]), int(P.day[i]), fuel)
    return (v, sid) if v < INF_I else (INF_I, -1)  # MIP pick infeasible under the exact rules -> no service
