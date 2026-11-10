"""RQ-Index: signature partition x per-group elementary weight intervals x LB-sorted lists.

Build:
  * signature partition of the key domain: the common refinement of every service's zone
    intervals and DAS intervals; each elementary interval maps to a signature id sigma
    (the vector of zones and DAS levels of all services). |Sigma| <= #elementary intervals.
  * services are grouped by (divisor, dim threshold, rounding, fuel class); inside a group
    the billable weight t is the same for every service, and the fuel multiplier is common,
    so the order by base price is invariant to fuel updates.
  * for each group g, the elementary weight intervals E_g are the union of the bands of its
    services; for each (sigma, g, e) we store the services sorted by LB = suffix-min of the
    base price over bands >= e (a valid lower bound under oversize minimums, surcharges >= 0
    and non-monotone tables).
Query (threshold algorithm): visit candidates across groups in increasing fuel-scaled LB,
evaluate the exact cost, stop when the next LB exceeds the best exact cost.
"""
import bisect
import math
import numpy as np
from .model import INF, FI, FEATS, GPred, billable, eval_service, month_of, Service, Instance, Parcels

INF_I = int(INF)


class Compiled:
    """Plain-Python copy of a service for scalar evaluation."""
    __slots__ = ("sid", "zstart", "zzone", "breaks", "price", "D", "thr", "rounding", "extrap", "limit",
                 "bmax", "K", "os", "omin", "os2", "omin2", "inel", "sur", "das_s", "das_l", "fc", "q", "carry", "mode")

    def __init__(self, s: Service):
        self.sid = s.sid
        self.zstart = s.zstart.tolist(); self.zzone = s.zzone.tolist()
        self.breaks = s.breaks.tolist(); self.price = s.price.tolist()
        self.D = s.divisor; self.thr = s.dim_thr; self.rounding = s.rounding
        self.extrap = s.extrapolate; self.limit = s.limit_weight; self.bmax = self.breaks[-1]; self.K = len(self.breaks)
        cl = lambda p: None if p is None else (p if isinstance(p, GPred) else [[(FI[f], th) for f, th in c.items()] for c in p.clauses])
        self.os = cl(s.oversize); self.omin = s.oversize_min; self.inel = cl(s.inelig)
        self.os2 = cl(s.oversize2); self.omin2 = s.oversize2_min
        self.sur = [(cl(r.pred), r.price.tolist(), (r.group, r.fuel), r.kind, r.coef) for r in s.surcharges]
        self.q, self.carry, self.mode = s.erp_q, s.erp_carry, s.erp_mode
        self.das_s = None if s.das_start is None else s.das_start.tolist()
        self.das_l = None if s.das_level is None else s.das_level.tolist()
        self.fc = s.fuel_class


def _fire(clauses, f):
    if isinstance(clauses, GPred):
        return clauses.eval1(f)
    for c in clauses:
        for i, th in c:
            if not f[i] > th:
                break
        else:
            return True
    return False


def billable1(c: Compiled, a, V):
    if c.rounding == "erp":
        v = V / c.D if c.D > 0 else 0.0
        x = max(a, v) if c.mode == 1 else (a if c.mode == 2 else v)
        if c.carry == "ceil":
            return math.ceil(x / c.q - 1e-9) * c.q
        return math.floor(x / c.q + 0.5) * c.q
    if c.rounding == "erp_style":
        aw = math.ceil(math.floor(a * 10 + 1e-9) / 10 - 1e-9)
        vw0 = math.ceil(V / c.D * 1000 - 1e-9) / 1000
        vw = math.ceil(math.floor(vw0 * 10 + 1e-9) / 10 - 1e-9) if vw0 > 1 else 1.0
        if not V > c.thr:
            vw = 0.0
        return float(max(aw, vw))
    dim = V / c.D if V > c.thr else 0.0
    return max(a, dim)


def cost1(c: Compiled, key, a, L, M, S, res, day, fuel, t=None):
    """Exact scalar cost; mirrors model.eval_service."""
    i = bisect.bisect_right(c.zstart, key) - 1
    if i < 0:
        return INF_I
    z = c.zzone[i]
    if z < 0:
        return INF_I
    V = L * M * S
    if t is None:
        t = billable1(c, a, V)
    das = 0
    if c.das_s is not None:
        j = bisect.bisect_right(c.das_s, key) - 1
        das = c.das_l[j] if j >= 0 else 0
    f = [L, M, S, L + 2 * M + 2 * S, V, a, t, das, res, key % 100000, z, day, int(month_of(day)), L + M + S]
    if c.os2 is not None and _fire(c.os2, f):
        t = max(t, c.omin2); f[6] = t
    if c.os is not None and _fire(c.os, f):
        t = max(t, c.omin); f[6] = t
    if c.inel is not None and _fire(c.inel, f):
        return INF_I
    over = False
    if c.extrap:
        over = t > c.limit
    elif t > c.bmax:
        return INF_I
    k = bisect.bisect_left(c.breaks, t - 1e-9)
    if k >= c.K:
        k = c.K - 1
    row = c.price[z]
    base = row[k]
    if over:
        top = row[c.K - 1]
        if top >= INF_I:
            return INF_I
        base = int(math.ceil(min(top * t / c.bmax, 1e15) - 1e-6))
    if base >= INF_I:
        return INF_I
    sf = snf = 0
    if c.sur:
        groups = {}
        pct = 0.0
        for cls, pr, gk, kind, coef in c.sur:
            fired = _fire(cls, f)
            if kind == "pct_total":
                if fired:
                    pct += coef
                continue
            if kind == "fixed":
                amt = pr[z] if fired else 0
            elif kind == "per_weight":
                amt = int(round(coef * t)) if fired else 0
            else:
                amt = int(round(coef / 100.0 * base)) if fired else 0
            if amt > groups.get(gk, -1):
                groups[gk] = amt
        for (g, fu), amt in groups.items():
            if fu:
                sf += amt
            else:
                snf += amt
        bp = fuel(c.fc, day)
        cost = ((base + sf) * (10000 + bp) + 5000) // 10000 + snf
        if pct:
            cost = cost + int(round(cost * pct / 100.0))
        return cost
    bp = fuel(c.fc, day)
    return ((base + sf) * (10000 + bp) + 5000) // 10000 + snf


def fuel_fn(inst: Instance):
    st = {k: v.tolist() for k, v in inst.fuel.starts.items()}
    bp = {k: v.tolist() for k, v in inst.fuel.bps.items()}

    def f(fc, day):
        if fc not in st:
            return 0
        i = bisect.bisect_right(st[fc], day) - 1
        return bp[fc][max(i, 0)]
    return f


def brute1(comp, fuel, key, a, L, M, S, res, day):
    best, arg = INF_I, -1
    for c in comp:
        v = cost1(c, key, a, L, M, S, res, day, fuel)
        if v < best:
            best, arg = v, c.sid
    return best, arg


class RQIndex:
    def __init__(self, inst: Instance, kappa=None):
        self.inst = inst
        self.kappa = kappa
        self.build()

    # ------------------------------------------------------------------ build
    @staticmethod
    def check_assumptions(inst: Instance):
        """Fail loudly if an instance violates a precondition of the lower bound
        (theory/rateq_theory.tex). A1: prices, surcharge amounts and coefficients and fuel
        ratios are nonnegative; this also makes the scalar evaluator (cost1, group maxima
        initialised with -1) and the vector evaluator (model.eval_service, group maxima over
        amounts that are 0 when a rule does not fire) agree. A3: an extrapolating service has
        limit_weight >= its last breakpoint, which the hull (_hull) assumes."""
        for s in inst.services:  # .min(initial=0) >= 0 <=> no negative entry (cheap, also for empty arrays)
            assert s.price.min(initial=0) >= 0, f"A1: negative price in service {s.sid} {s.name}"
            for r in s.surcharges:
                assert r.price.min(initial=0) >= 0 and r.coef >= 0, \
                    f"A1: negative surcharge amount/coef in service {s.sid} {s.name} rule {r.name}"
            if s.extrapolate:
                assert s.limit_weight >= s.breaks[-1], \
                    f"A3: service {s.sid} {s.name} extrapolates with limit {s.limit_weight} < last breakpoint {s.breaks[-1]}"
        for fc, b in inst.fuel.bps.items():
            assert (np.asarray(b) >= 0).all(), f"A1: negative fuel ratio in class {fc}"

    def build(self):
        inst = self.inst
        svcs = inst.services
        self.check_assumptions(inst)
        self.version = 0  # bumped by update_zone; Maintainer uses it to refresh signatures
        self.comp = [Compiled(s) for s in svcs]
        self.fuel = fuel_fn(inst)
        # signature partition
        # LB rows depend on zones only (DAS affects surcharges, which LB ignores)
        cuts = [np.array([0], dtype=np.int64)]
        for s in svcs:
            cuts.append(s.zstart)
        U = np.unique(np.concatenate(cuts))
        Zm = np.stack([s.zone_of(U) for s in svcs], axis=1)          # (|U|, n)
        keep = np.r_[True, (Zm[1:] != Zm[:-1]).any(axis=1)]           # merge equal neighbours
        U, Zm = U[keep], Zm[keep]
        uniq, inv = np.unique(Zm, axis=0, return_inverse=True)
        self.U = U; self.Ul = U.tolist()
        self.sig_of = inv.ravel(); self.sig_of_l = self.sig_of.tolist()
        self.zvec = uniq                                                # (|Sigma|, n)
        self.n_sig = len(uniq)
        # groups
        gk = {}
        for s in svcs:
            gk.setdefault(s.group_key, []).append(s.sid)
        self.groups = []
        for key, sids in gk.items():
            E = np.unique(np.concatenate([svcs[i].breaks for i in sids]))
            self.groups.append(self._build_group(key, np.array(sids), E))
        self.svc_group = {}
        for gi, g in enumerate(self.groups):
            for s in g["sids"]:
                self.svc_group[int(s)] = gi

    def _hull(self, s: Service, z, E):
        """LB over each elementary interval of E (plus tail) for service s in zone z."""
        if z < 0:
            return np.full(len(E) + 1, INF, dtype=np.int64)
        row = s.price[z]
        suf = np.minimum.accumulate(row[::-1])[::-1]                  # suffix min over bands
        k = np.searchsorted(s.breaks, E - 1e-9, side="left")          # band covering interval ending at E_e
        lb = np.full(len(E) + 1, INF, dtype=np.int64)
        inside = k < len(s.breaks)
        lb[:len(E)][inside] = suf[k[inside]]
        if s.extrapolate:
            top = row[-1]
            lb[:len(E)][~inside] = top if top < INF else INF          # extrapolated >= top
            lb[len(E)] = top if top < INF else INF
            lb[:len(E)] = np.minimum(lb[:len(E)], top if top < INF else INF)
        return lb

    def _build_group(self, key, sids, E):
        svcs = self.inst.services
        nE = len(E) + 1
        lz, loc = np.unique(self.zvec[:, sids], axis=0, return_inverse=True)  # group-local signatures
        LB = np.empty((len(lz), nE, len(sids)), dtype=np.int64)
        for j, sid in enumerate(sids):
            s = svcs[sid]
            zs = lz[:, j]
            cache = {}
            for sg in range(len(lz)):
                z = int(zs[sg])
                if z not in cache:
                    cache[z] = self._hull(s, z, E)
                LB[sg, :, j] = cache[z]
        order = np.lexsort((np.broadcast_to(sids, LB.shape), LB), axis=-1)
        ORD = sids[order].astype(np.int32)
        LBs = np.take_along_axis(LB, order, axis=-1)
        if self.kappa is not None and self.kappa < len(sids):
            ORD, LBs = ORD[..., :self.kappa], LBs[..., :self.kappa]
        return {"key": key, "sids": sids, "lz": lz, "loc": loc.ravel().astype(np.int64), "E": E, "El": E.tolist(), "ORD": ORD, "LB": LBs,
                "ORDl": None, "LBl": None, "D": key[0], "thr": key[1], "rounding": key[2], "fc": key[3]}

    def nbytes(self):
        b = self.U.nbytes + self.sig_of.nbytes + self.zvec.nbytes
        for g in self.groups:
            b += g["ORD"].nbytes + g["LB"].astype(np.int32).nbytes + g["E"].nbytes + g["loc"].nbytes
        return b

    # ------------------------------------------------------------------ queries
    def _pylists(self):
        for g in self.groups:
            if g["ORDl"] is None:
                g["ORDl"] = g["ORD"].tolist(); g["LBl"] = g["LB"].tolist(); g["locl"] = g["loc"].tolist()

    def query1(self, key, a, L, M, S, res, day, stats=None):
        self._pylists()
        i = bisect.bisect_right(self.Ul, key) - 1
        sg = self.sig_of_l[i]
        V = L * M * S
        heads = []  # (next scaled LB, gi, ptr, row_ord, row_lb, bp, t)
        for gi, g in enumerate(self.groups):
            c0 = self.comp[int(g["sids"][0])]
            t = billable1(c0, a, V)
            e = bisect.bisect_left(g["El"], t - 1e-9)
            ls = g["locl"][sg]
            ro, rl = g["ORDl"][ls][e], g["LBl"][ls][e]
            bp = self.fuel(g["fc"], day)
            lb = rl[0]
            if lb < INF_I:
                heads.append([((lb * (10000 + bp) + 5000) // 10000), gi, 0, ro, rl, bp, t])
        best, arg, nev = INF_I, -1, 0
        while heads:
            h = min(heads, key=lambda x: x[0])
            if h[0] > best:
                break
            _, gi, p, ro, rl, bp, t = h
            sid = ro[p]
            v = cost1(self.comp[sid], key, a, L, M, S, res, day, self.fuel, t)
            nev += 1
            if v < best or (v == best and sid < arg):
                best, arg = v, sid
            p += 1
            if p < len(ro) and rl[p] < INF_I:
                h[0] = (rl[p] * (10000 + bp) + 5000) // 10000; h[2] = p
            else:
                if p == len(ro) and self.kappa is not None and len(ro) < len(self.groups[gi]["sids"]):
                    seen = set(ro)
                    for s2 in self.groups[gi]["sids"]:
                        if int(s2) not in seen:
                            v = cost1(self.comp[int(s2)], key, a, L, M, S, res, day, self.fuel, t); nev += 1
                            if v < best or (v == best and s2 < arg):
                                best, arg = v, int(s2)
                heads.remove(h)
        if stats is not None:
            stats.append(nev)
        return best, arg

    def query_batch(self, P: Parcels, stats=None):
        """Vectorised threshold algorithm over a batch; rounds advance one rank per group."""
        N = len(P)
        svcs = self.inst.services
        F0 = P.base_feats()
        i = np.searchsorted(self.U, P.key, side="right") - 1
        sg = self.sig_of[i]
        best = np.full(N, INF, dtype=np.int64)
        arg = np.full(N, -1, dtype=np.int64)
        nev = 0
        state = []
        for g in self.groups:
            s0 = svcs[int(g["sids"][0])]
            t = billable(s0, P.a, F0[:, FI["V"]])
            e = np.searchsorted(g["E"], t - 1e-9, side="left")
            bp = self.inst.fuel.at(g["fc"], P.day)
            state.append((g, e, bp))
        maxr = max(g["ORD"].shape[-1] for g in self.groups)
        active = [np.arange(N) for _ in self.groups]
        for r in range(maxr):
            any_work = False
            for gi, (g, e, bp) in enumerate(state):
                idx = active[gi]
                if len(idx) == 0 or r >= g["ORD"].shape[-1]:
                    active[gi] = idx[:0]
                    continue
                lsg = g["loc"][sg[idx]]
                lb = g["LB"][lsg, e[idx], r]
                lbs = np.where(lb < INF, (lb * (10000 + bp[idx]) + 5000) // 10000, INF)
                keep = (lbs <= best[idx]) & (lb < INF)
                idx = idx[keep]
                active[gi] = idx
                if len(idx) == 0:
                    continue
                any_work = True
                cand = g["ORD"][g["loc"][sg[idx]], e[idx], r]
                for sid in np.unique(cand):
                    sub = idx[cand == sid]
                    v = eval_service(svcs[sid], P.subset(sub), F0[sub], self.inst.fuel)
                    nev += len(sub)
                    better = (v < best[sub]) | ((v == best[sub]) & (sid < arg[sub]))
                    best[sub] = np.where(better, v, best[sub]); arg[sub] = np.where(better, sid, arg[sub])
            if not any_work:
                break
        # kappa fallback: groups whose truncated list was exhausted while still active
        if self.kappa is not None:
            for gi, (g, e, bp) in enumerate(state):
                idx = active[gi]
                if len(idx) == 0 or g["ORD"].shape[-1] >= len(g["sids"]):
                    continue
                for sid in g["sids"]:
                    v = eval_service(svcs[sid], P.subset(idx), F0[idx], self.inst.fuel)
                    nev += len(idx)
                    better = (v < best[idx]) | ((v == best[idx]) & (sid < arg[idx]))
                    best[idx] = np.where(better, v, best[idx]); arg[idx] = np.where(better, sid, arg[idx])
        if stats is not None:
            stats.append(nev)
        return best, arg

    # ------------------------------------------------------------------ updates
    def update_price(self, sid, z, k, new_price):
        """Set price[z][k] of service sid; repair the affected (sigma, e) rows in place."""
        s = self.inst.services[sid]
        g = self.groups[self.svc_group[sid]]
        old = self._hull(s, z, g["E"])
        s.price[z, k] = new_price
        self.comp[sid].price[z][k] = int(new_price)
        new = self._hull(s, z, g["E"])
        es = np.nonzero(old != new)[0]
        j = int(np.nonzero(g["sids"] == sid)[0][0])
        sigs = np.nonzero(g["lz"][:, j] == z)[0]                      # group-local signatures
        touched = 0
        for sgi in sigs:
            for e in es:
                ro, rl = g["ORD"][sgi, e], g["LB"][sgi, e]
                pos = int(np.nonzero(ro == sid)[0][0]) if (ro == sid).any() else None
                if pos is None:  # truncated list: rebuild row
                    self._rebuild_row(g, sgi, e); touched += 1; continue
                o2 = np.delete(ro, pos); l2 = np.delete(rl, pos)
                nv = new[e]
                after = (l2 > nv) | ((l2 == nv) & (o2 > sid))  # first entry ordered after (nv, sid); no overflow at INF
                ins = int(np.argmax(after)) if after.any() else len(l2)
                g["ORD"][sgi, e] = np.insert(o2, ins, sid)
                g["LB"][sgi, e] = np.insert(l2, ins, new[e])
                touched += 1
        g["ORDl"] = None
        return touched

    def _rebuild_row(self, g, sgi, e):
        svcs = self.inst.services
        lbs = np.array([self._hull(svcs[s], int(g["lz"][sgi, j]), g["E"])[e] for j, s in enumerate(g["sids"])])
        o = np.lexsort((g["sids"], lbs))[:g["ORD"].shape[-1]]
        g["ORD"][sgi, e] = g["sids"][o]; g["LB"][sgi, e] = lbs[o]

    def update_fuel(self, fc, start_day, bp):
        """Fuel ratio change: index rows are fuel-invariant, only the schedule changes."""
        f = self.inst.fuel
        st, b = f.starts[fc], f.bps[fc]
        i = np.searchsorted(st, start_day)
        if i < len(st) and st[i] == start_day:
            b[i] = bp
        else:
            f.starts[fc] = np.insert(st, i, start_day); f.bps[fc] = np.insert(b, i, bp)
        self.fuel = fuel_fn(self.inst)
        return 0

    def update_zone(self, sid, lo, hi, z):
        """Zone rule edit: key range [lo, hi] of service sid now maps to zone z.
        Refines the signature partition locally; new signatures get fresh rows."""
        s = self.inst.services[sid]
        after = int(s.zone_of(np.array([hi + 1]))[0])
        m = (s.zstart < lo) | (s.zstart > hi + 1)
        st = np.r_[s.zstart[m], lo, hi + 1]; zz = np.r_[s.zzone[m], z, after]
        o = np.argsort(st, kind="stable"); s.zstart, s.zzone = st[o], zz[o]
        self.comp[sid] = Compiled(s)
        # local refinement of U and signatures inside [lo, hi+1]
        newcuts = np.setdiff1d(np.array([lo, hi + 1]), self.U)
        if len(newcuts):
            pos = np.searchsorted(self.U, newcuts, side="right") - 1
            self.U = np.insert(self.U, pos + 1, newcuts)
            self.sig_of = np.insert(self.sig_of, pos + 1, self.sig_of[pos])
        a, b = np.searchsorted(self.U, lo), np.searchsorted(self.U, hi + 1)
        created = 0
        cache = {}
        for ui in range(a, b):
            row = self.zvec[self.sig_of[ui]].copy(); row[sid] = z
            dk = tuple(row.tolist())
            if dk in cache:
                self.sig_of[ui] = cache[dk]; continue
            # existing signature with identical zone vector and DAS? (zone-only match is enough
            # for LB rows; DAS of the elementary interval is unchanged)
            match = np.nonzero((self.zvec == row).all(axis=1))[0]
            if len(match):
                cache[dk] = int(match[0]); self.sig_of[ui] = cache[dk]; continue
            self.zvec = np.vstack([self.zvec, row]); new_sg = self.zvec.shape[0] - 1
            for g in self.groups:
                lrow = row[g["sids"]]
                m = np.nonzero((g["lz"] == lrow).all(axis=1))[0]
                if len(m):
                    g["loc"] = np.r_[g["loc"], m[0]]
                else:
                    rowsO, rowsL = self._fresh_rows(g, row)
                    g["ORD"] = np.concatenate([g["ORD"], rowsO[None]], axis=0)
                    g["LB"] = np.concatenate([g["LB"], rowsL[None]], axis=0)
                    g["lz"] = np.vstack([g["lz"], lrow]); g["loc"] = np.r_[g["loc"], len(g["lz"]) - 1]
                g["ORDl"] = None
            self.n_sig += 1; created += 1
            cache[dk] = new_sg; self.sig_of[ui] = new_sg
        self.Ul = self.U.tolist(); self.sig_of_l = self.sig_of.tolist()
        self.version += 1  # signatures changed: Maintainer buckets must be rebuilt
        return created

    def _fresh_rows(self, g, zrow):
        svcs = self.inst.services
        LB = np.stack([self._hull(svcs[s], int(zrow[s]), g["E"]) for s in g["sids"]], axis=1)
        order = np.lexsort((np.broadcast_to(g["sids"], LB.shape), LB), axis=-1)
        O = g["sids"][order].astype(np.int32); L = np.take_along_axis(LB, order, axis=-1)
        kk = g["ORD"].shape[-1]
        return O[:, :kk], L[:, :kk]


GEOM = {FI[f] for f in ("L", "M", "S", "G", "V", "A", "RES", "SUMD", "DAY", "MONTH")}


def _geometric(pred, das_ok=False):
    ok = GEOM | ({FI["DAS"]} if das_ok else set())
    if pred is None:
        return False
    if isinstance(pred, GPred):
        return all(FI[f] in ok for f in pred.feats())
    return all(FI[f] in ok for cl in pred.clauses for f in cl)


class RQIndexPlus(RQIndex):
    """Surcharge-class refinement. Within a group, the distinct geometric predicates (over the
    parcel-level features in GEOM: L, M, S, G, V, A, RES, SUMD, DAY, MONTH, plus DAS when all
    services of the group share one delivery-area map) partition parcel space into classes C
    (bitmask of fired predicates).
    Row (local sigma, e, C) lists services by key = hull base at the (oversize-raised) interval
    + sum over fuel-applicable groups of the max geometric surcharge fired in C. The bound
    (key * (1 + fuel)) <= exact cost, so the threshold algorithm stays exact. Rows are built
    lazily on first use and cached (memory grows with the occupied cells only)."""

    def build(self):
        super().build()
        svcs = self.inst.services
        for g in self.groups:
            preds = {}
            # Assumption A7: DAS may be treated as a parcel-level feature only if every service of
            # the group has the same delivery-area map, i.e. the same start AND level arrays.
            s0 = svcs[int(g["sids"][0])]
            das_objs = {id(svcs[int(sid)].das_start) for sid in g["sids"]}
            g["das_ok"] = (len(das_objs) == 1 and s0.das_start is not None
                           and all(svcs[int(sid)].das_level is s0.das_level
                                   or np.array_equal(svcs[int(sid)].das_level, s0.das_level) for sid in g["sids"]))
            for sid in g["sids"]:
                s = svcs[int(sid)]
                for p in [s.oversize, s.oversize2, s.inelig] + [r.pred for r in s.surcharges]:
                    if _geometric(p, g["das_ok"]):
                        preds.setdefault(id(p), p)
            g["preds"] = list(preds.values())
            g["pidx"] = {k: i for i, k in enumerate(preds)}
            self._prep_group(g)

    def _prep_group(self, g):
        """Vectorised row-construction tables (unsorted hulls, rule arrays) and empty row store."""
        svcs = self.inst.services
        n_g = len(g["sids"])
        sid2j = {int(s): j for j, s in enumerate(g["sids"])}
        J = np.vectorize(sid2j.get)(g["ORD"]) if g["ORD"].size else g["ORD"]
        H = np.empty_like(g["LB"]); np.put_along_axis(H, J, g["LB"], axis=-1)
        g["H"] = H
        E = g["E"]
        g["os_p"] = np.array([g["pidx"].get(id(svcs[int(s)].oversize), -1) for s in g["sids"]])
        g["os_e"] = np.array([int(np.searchsorted(E, svcs[int(s)].oversize_min - 1e-9, side="left")) for s in g["sids"]])
        g["os2_p"] = np.array([g["pidx"].get(id(svcs[int(s)].oversize2), -1) if svcs[int(s)].oversize2 is not None else -1 for s in g["sids"]])
        g["os2_e"] = np.array([int(np.searchsorted(E, svcs[int(s)].oversize2_min - 1e-9, side="left")) for s in g["sids"]])
        g["in_p"] = np.array([g["pidx"].get(id(svcs[int(s)].inelig), -1) for s in g["sids"]])
        rj, rp, rg, rprice, gks = [], [], [], [], {}
        zmax = max(svcs[int(s)].price.shape[0] for s in g["sids"])
        for j, s in enumerate(g["sids"]):
            for rr in svcs[int(s)].surcharges:
                if id(rr.pred) in g["pidx"] and rr.kind == "fixed":  # other kinds stay out of the key (>= 0)
                    rj.append(j); rp.append(g["pidx"][id(rr.pred)])
                    rg.append(gks.setdefault((rr.group, rr.fuel), len(gks)))
                    pr = np.zeros(zmax, dtype=np.int64); pr[:len(rr.price)] = rr.price; rprice.append(pr)
        g["r_j"], g["r_p"], g["r_g"] = np.array(rj, dtype=np.int64), np.array(rp, dtype=np.int64), np.array(rg, dtype=np.int64)
        g["r_price"] = np.array(rprice).reshape(len(rj), zmax)
        g["n_gk"] = max(1, len(gks))
        g["gk_nonfuel"] = np.array([not fu for (gg, fu), i in sorted(gks.items(), key=lambda x: x[1])] or [False])
        g["rows"] = {}
        g["RO"] = np.zeros((64, n_g), dtype=np.int32); g["RK"] = np.zeros((64, n_g), dtype=np.int64)
        g["RNF"] = np.zeros(64, dtype=bool); g["nrows"] = 0
        g["rowO"], g["rowK"] = [], []

    def _class(self, g, F, key):
        """Per-parcel class as a Python-int bitmask, via unique fired patterns."""
        if g["das_ok"]:
            F = F.copy(); F[:, FI["DAS"]] = self.inst.services[int(g["sids"][0])].das_of(key)
        if not g["preds"]:
            return np.zeros(F.shape[0], dtype=np.int64), [0]
        B = np.stack([p.eval(F) for p in g["preds"]], axis=1)
        u, inv = np.unique(np.packbits(B, axis=1), axis=0, return_inverse=True)
        masks = []
        for row in np.unpackbits(u, axis=1)[:, :B.shape[1]]:
            masks.append(sum(1 << i for i, v in enumerate(row) if v))
        return inv.ravel(), masks

    def _class1(self, g, f, key=None):
        if g["das_ok"]:
            c0 = self.comp[int(g["sids"][0])]
            j = bisect.bisect_right(c0.das_s, key) - 1
            f = list(f); f[FI["DAS"]] = c0.das_l[j] if j >= 0 else 0
        m = 0
        for i, p in enumerate(g["preds"]):
            if p.eval1(f):
                m |= 1 << i
        return m

    def _row(self, g, ls, e, C):
        key = (ls, e, C)
        r = g["rows"].get(key)
        if r is not None:
            return r
        n_g = len(g["sids"])
        npred = len(g["preds"])
        fired = np.array([(C >> i) & 1 for i in range(npred)] + [0], dtype=bool)  # index -1 -> False
        cols = np.arange(n_g)
        z = g["lz"][ls]
        ee = np.where(fired[g["os2_p"]] & (g["os2_p"] >= 0), np.maximum(e, g["os2_e"]), e)
        ee = np.where(fired[g["os_p"]] & (g["os_p"] >= 0), np.maximum(ee, g["os_e"]), ee)
        b = g["H"][ls, ee, cols]
        b = np.where((g["in_p"] >= 0) & fired[g["in_p"]], INF, b)   # geometric ineligibility
        b = np.where(z < 0, INF, b)
        nf_any = False
        if len(g["r_j"]):
            fr = fired[g["r_p"]]
            zr = z[g["r_j"]]
            amt = np.where(fr & (zr >= 0), g["r_price"][np.arange(len(fr)), np.clip(zr, 0, None)], 0)
            Mx = np.zeros((n_g, g["n_gk"]), dtype=np.int64)
            np.maximum.at(Mx, (g["r_j"], g["r_g"]), amt)
            add = Mx.sum(axis=1)
            nf_any = bool(((Mx[:, g["gk_nonfuel"]] > 0).any(axis=1) & (b < INF)).any())
            b = np.where(b < INF, b + add, INF)
        o = np.lexsort((g["sids"], b))
        rid = g["nrows"]
        if rid == g["RO"].shape[0]:
            g["RO"] = np.concatenate([g["RO"], np.zeros_like(g["RO"])])
            g["RK"] = np.concatenate([g["RK"], np.zeros_like(g["RK"])])
            g["RNF"] = np.concatenate([g["RNF"], np.zeros_like(g["RNF"])])
        g["RO"][rid] = g["sids"][o]; g["RK"][rid] = b[o]; g["RNF"][rid] = nf_any
        g["nrows"] = rid + 1
        g["rows"][key] = rid
        return rid

    def _rowlists(self, g, rid):
        while len(g["rowO"]) <= rid:
            q = len(g["rowO"])
            g["rowO"].append(g["RO"][q].tolist()); g["rowK"].append(g["RK"][q].tolist())
        return g["rowO"][rid], g["rowK"][rid]

    def nbytes(self):
        """Occupied rows (4-byte ids + 4-byte keys) + row dictionary + group tables."""
        b = self.U.nbytes + self.sig_of.nbytes + self.zvec.nbytes
        for g in self.groups:
            b += g["nrows"] * len(g["sids"]) * 8 + 16 * len(g["rows"]) + g["loc"].nbytes + g["E"].nbytes
            b += g["H"].size * 4
        return b

    def query1(self, key, a, L, M, S, res, day, stats=None):
        i = bisect.bisect_right(self.Ul, key) - 1
        sg = self.sig_of_l[i]
        V = L * M * S
        f = [L, M, S, L + 2 * M + 2 * S, V, a, 0.0, 0, res, key % 100000, -1, day, int(month_of(day)), L + M + S]
        heads = []
        for gi, g in enumerate(self.groups):
            if "locl" not in g:
                g["locl"] = g["loc"].tolist()
            c0 = self.comp[int(g["sids"][0])]
            t = billable1(c0, a, V)
            e = bisect.bisect_left(g["El"], t - 1e-9)
            rid = self._row(g, g["locl"][sg], e, self._class1(g, f, key))
            ro, rk = self._rowlists(g, rid)
            bp = 0 if g["RNF"][rid] else self.fuel(g["fc"], day)
            if rk[0] < INF_I:
                heads.append([(rk[0] * (10000 + bp) + 5000) // 10000, gi, 0, ro, rk, bp, t])
        best, arg, nev = INF_I, -1, 0
        while heads:
            h = min(heads, key=lambda x: x[0])
            if h[0] > best:
                break
            _, gi, p, ro, rk, bp, t = h
            sid = ro[p]
            v = cost1(self.comp[sid], key, a, L, M, S, res, day, self.fuel, t)
            nev += 1
            if v < best or (v == best and sid < arg):
                best, arg = v, sid
            p += 1
            if p < len(ro) and rk[p] < INF_I:
                h[0] = (rk[p] * (10000 + bp) + 5000) // 10000; h[2] = p
            else:
                heads.remove(h)
        if stats is not None:
            stats.append(nev)
        return best, arg

    def query_batch(self, P, stats=None):
        N = len(P)
        svcs = self.inst.services
        F0 = P.base_feats()
        sg = self.sig_of[np.searchsorted(self.U, P.key, side="right") - 1]
        best = np.full(N, INF, dtype=np.int64); arg = np.full(N, -1, dtype=np.int64)
        nev = 0
        state = []
        for g in self.groups:
            s0 = svcs[int(g["sids"][0])]
            t = billable(s0, P.a, F0[:, FI["V"]])
            e = np.searchsorted(g["E"], t - 1e-9, side="left")
            Ci, masks = self._class(g, F0, P.key)
            nc, ne = len(masks), len(g["E"]) + 1
            code = (g["loc"][sg] * ne + e) * nc + Ci
            u, inv = np.unique(code, return_inverse=True)
            rids = np.array([self._row(g, int(c // nc // ne), int(c // nc % ne), masks[int(c % nc)]) for c in u])
            O, K = g["RO"], g["RK"]
            rr = rids[inv.ravel()]
            bpv = np.where(g["RNF"][rr], 0, self.inst.fuel.at(g["fc"], P.day))
            state.append((rr, O, K, bpv, np.arange(N)))
        # per-parcel merge across groups: always expand the head with the smallest bound
        m = len(state)
        ptr = np.zeros((N, m), dtype=np.int64)
        lens = np.array([st[1].shape[1] for st in state])

        def head(gi, rows):
            rid, O, K, bp, _ = state[gi]
            p = ptr[rows, gi]
            ok = p < lens[gi]
            k = np.where(ok, K[rid[rows], np.minimum(p, lens[gi] - 1)], INF)
            return np.where(k < INF, (k * (10000 + bp[rows]) + 5000) // 10000, INF)

        H = np.stack([head(gi, np.arange(N)) for gi in range(m)], axis=1)
        act = np.arange(N)
        while len(act):
            hv = H[act]
            gsel = np.argmin(hv, axis=1)
            lb = hv[np.arange(len(act)), gsel]
            live = (lb < INF) & (lb <= best[act])
            act, gsel = act[live], gsel[live]
            if len(act) == 0:
                break
            for gi in np.unique(gsel):
                rows = act[gsel == gi]
                rid, O, K, bp, _ = state[gi]
                cand = O[rid[rows], ptr[rows, gi]]
                for sid in np.unique(cand):
                    sub = rows[cand == sid]
                    v = eval_service(svcs[sid], P.subset(sub), F0[sub], self.inst.fuel)
                    nev += len(sub)
                    better = (v < best[sub]) | ((v == best[sub]) & (sid < arg[sub]))
                    best[sub] = np.where(better, v, best[sub]); arg[sub] = np.where(better, sid, arg[sub])
                ptr[rows, gi] += 1
                H[rows, gi] = head(gi, rows)
        if stats is not None:
            stats.append(nev)
        return best, arg

    def update_price(self, sid, z, k, new_price):
        """Lazy rows: invalidate the rows of the edited service's group whose local signature maps
        sid to zone z (they are rebuilt on next use)."""
        s = self.inst.services[sid]
        g = self.groups[self.svc_group[sid]]
        s.price[z, k] = new_price
        self.comp[sid].price[z][k] = int(new_price)
        j = int(np.nonzero(g["sids"] == sid)[0][0])
        badl = np.nonzero(g["lz"][:, j] == z)[0]
        g["H"][badl, :, j] = self._hull(s, z, g["E"])[None, :]
        bad = set(badl.tolist())
        drop = [key for key in g["rows"] if key[0] in bad]
        for key in drop:
            del g["rows"][key]
        return len(drop)
