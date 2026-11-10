"""Q3: incremental maintenance of (best, runner-up) over historical parcels under rule edits.

State per parcel: (c1, s1) best and (c2, s2) runner-up, ties broken by smaller sid.
For a single-service edit with support U (parcels whose cost under that service can change):
  s == s1 : keep if (c', s) <= (c2, s2), else recompute the parcel
  s == s2 : promote if (c', s) < (c1, s1); keep if c' <= old c2; else recompute the parcel
  other   : insert if (c', s) beats (c1, s1) or (c2, s2); otherwise nothing changes
Parcels outside U are untouched (their costs under every service are unchanged).
Support enumeration ("cell-level provenance"): the parcels whose signature maps service s to
zone z and whose billable-weight elementary interval lies in the edited band (plus parcels whose
oversize minimum lands in that band), read from a CSR bucket table keyed by (sigma, e).
"""
import numpy as np
from .model import INF, FI, GPred, billable, eval_service, Parcels


def _lt(ca, sa, cb, sb):
    return (ca < cb) | ((ca == cb) & (sa < sb))


def top2(inst, P, F0=None, services=None, known=None):
    """Top-2 (cost, sid) pairs of every parcel over all services. known = [(sid, costs)] gives
    already-evaluated cost arrays of some services (not re-evaluated); insertion order does not
    matter because pairs are compared lexicographically."""
    F0 = P.base_feats() if F0 is None else F0
    N = len(P)
    c1 = np.full(N, INF, np.int64); s1 = np.full(N, -1, np.int64)
    c2 = np.full(N, INF, np.int64); s2 = np.full(N, -1, np.int64)
    known = known or []
    skip = {sid for sid, _ in known}
    svcs = inst.services if services is None else services
    todo = [(s.sid, None, s) for s in svcs if s.sid not in skip] + [(sid, c, None) for sid, c in known]
    for sid, c, s in todo:
        if c is None:
            c = eval_service(s, P, F0, inst.fuel)
        b1 = _lt(c, sid, c1, s1)
        b2 = ~b1 & _lt(c, sid, c2, s2)
        c2 = np.where(b1, c1, np.where(b2, c, c2)); s2 = np.where(b1, s1, np.where(b2, sid, s2))
        c1 = np.where(b1, c, c1); s1 = np.where(b1, sid, s1)
    return c1, s1, c2, s2


class Maintainer:
    def __init__(self, inst, ix, P: Parcels):
        self.inst, self.ix, self.P = inst, ix, P
        self.check_tier_assumption(inst, ix)
        self.F0 = P.base_feats()
        self.c1, self.s1, self.c2, self.s2 = top2(inst, P, self.F0)
        self._build_buckets()
        self.os_cache = {}
        self.day_order = np.argsort(P.day, kind="stable")

    @staticmethod
    def check_tier_assumption(inst, ix):
        """Fail loudly unless the preconditions of the cell support (theory, Lemma cell support)
        hold. A5: tier predicates read only parcel-level features (not T, ZONE or DAS), so a tier
        mask can be evaluated once on the base features and shared across services; no tier
        minimum lies in (b, b + 1e-9] for a breakpoint b of its service. A6: distinct elements
        of each group's elementary-interval grid E differ by more than 1e-9."""
        bad = {"T", "ZONE", "DAS"}
        for s in inst.services:
            for p_, m_ in s.min_tiers:
                fs = p_.feats() if isinstance(p_, GPred) else {f for cl in p_.clauses for f in cl}
                assert not (fs & bad), f"A5: tier predicate of service {s.sid} {s.name} reads {sorted(fs & bad)}"
                assert not ((s.breaks < m_) & (m_ <= s.breaks + 1e-9)).any(), \
                    f"A5: tier minimum {m_} of service {s.sid} {s.name} lies within 1e-9 above a breakpoint"
        for g in ix.groups:
            assert (np.diff(g["E"]) > 1e-9).all(), f"A6: breakpoints closer than 1e-9 in group {g['key']}"

    def _build_buckets(self):
        """Signature of every parcel and the CSR bucket table keyed by (sigma, e). Rebuilt
        whenever the index signatures change (RQIndex.update_zone bumps ix.version)."""
        ix, inst, P = self.ix, self.inst, self.P
        sg = ix.sig_of[np.searchsorted(ix.U, P.key, side="right") - 1]
        self.sg = sg
        self.buckets = []
        for g in ix.groups:
            s0 = inst.services[int(g["sids"][0])]
            t = billable(s0, P.a, self.F0[:, FI["V"]])
            e = np.searchsorted(g["E"], t - 1e-9, side="left")
            code = sg.astype(np.int64) * (len(g["E"]) + 1) + e
            order = np.argsort(code, kind="stable")
            u, start = np.unique(code[order], return_index=True)
            end = np.r_[start[1:], len(order)]
            self.buckets.append({"nE": len(g["E"]) + 1, "order": order,
                                 "pos": {int(c): (int(a), int(b)) for c, a, b in zip(u, start, end)}})
        self.ix_version = ix.version

    def _os_mask(self, p):
        # Under A5 (checked at construction) a tier predicate reads only parcel-level features,
        # so its mask is the same for every service and can be cached by predicate identity.
        k = id(p)
        if k not in self.os_cache:
            self.os_cache[k] = np.nonzero(p.eval(self.F0))[0]
        return self.os_cache[k]

    def support_cell(self, sid, z, k):
        """Exact support U_cell of price cell (sid, z, k) (theory, Definition supports (S1))."""
        if self.ix_version != self.ix.version:  # signatures changed by a zone edit
            self._build_buckets()
        ix, s = self.ix, self.inst.services[sid]
        gi = ix.svc_group[sid]; g = ix.groups[gi]; B = self.buckets[gi]
        lo = s.breaks[k - 1] if k > 0 else -np.inf
        hi = s.breaks[k] if k < len(s.breaks) - 1 or not s.extrapolate else np.inf
        # I_k: elementary intervals whose right end lies in (lo, hi]; the tail interval ends at +inf
        right = np.r_[g["E"], np.inf]
        es = np.nonzero((right > lo + 1e-12) & (right <= hi + 1e-12))[0]
        sigs = np.nonzero(ix.zvec[:, sid] == z)[0]  # global signatures
        parts = []
        for sgi in sigs:
            base = int(sgi) * B["nE"]
            for e in es:
                p = B["pos"].get(base + int(e))
                if p:
                    parts.append(B["order"][p[0]:p[1]])
        for p_, m_ in s.min_tiers:  # parcels whose minimum billable weight lands in the band
            if lo < m_ <= hi:
                osm = self._os_mask(p_)
                parts.append(osm[np.isin(self.sg[osm], sigs)])
        if not parts:
            return np.zeros(0, np.int64)
        return np.unique(np.concatenate(parts))

    def support_zone(self, sid, lo, hi, z):
        """Support U_zone (S5) of the zone edit "keys [lo, hi] of service sid map to zone z".
        Call BEFORE RQIndex.update_zone (it reads the old zones); then apply(sid, U)."""
        s = self.inst.services[sid]
        m = (self.P.key >= lo) & (self.P.key <= hi)
        idx = np.nonzero(m)[0]
        return idx[s.zone_of(self.P.key[idx]) != z]

    def support_service(self, sid):
        s = self.inst.services[sid]
        return np.nonzero(s.zone_of(self.P.key) >= 0)[0]

    def support_days(self, d0, d1):
        """Parcels with ship day in [d0, d1]; d0 = -inf / d1 = +inf give open-ended ranges."""
        lo = 0 if d0 == -np.inf else np.searchsorted(self.P.day[self.day_order], d0)
        hi = len(self.day_order) if d1 == np.inf else np.searchsorted(self.P.day[self.day_order], d1 + 1)
        return np.sort(self.day_order[lo:hi])

    def support_fuel(self, fc, w):
        """Support U_fuel (S6) of an edit of segment w of fuel class fc: ship days in
        D_w = [starts[w], starts[w+1]), where segment 0 also covers every day before starts[0]
        (FuelSchedule.at clips to segment 0) and the last segment is open to the right."""
        st = self.inst.fuel.starts[fc]
        d0 = -np.inf if w == 0 else int(st[w])
        d1 = np.inf if w + 1 >= len(st) else int(st[w + 1]) - 1
        return self.support_days(d0, d1)

    def apply(self, sid, U):
        """Repair the state for an already-applied edit of service sid with support U."""
        return self.apply_many([sid], U)

    def apply_many(self, sids, U):
        """Repair the state for an already-applied edit that changed every service in sids, with a
        common support U (e.g. a fuel edit of one class: sids = all services of that class).

        The single-service repair (R1)-(R4) is applied once per service, in any order, on the
        local copy of the state of U (theory, Lemma "several edited services"). Recomputation is
        deferred: a parcel that reaches a recompute branch for any service is marked, and the
        marked parcels are recomputed ONCE at the end against the edited instance, which sets
        them to Top2(c') whatever their intermediate state. Unmarked parcels only went through
        non-recompute repairs, so they end at Top2(c') by Lemma "single-service repair". The
        support (and the features of U) are prepared once for all services of the edit.
        Returns (|U|, number of parcels recomputed)."""
        if len(U) == 0:
            return 0, 0
        PU, FU = self.P.subset(U), self.F0[U]
        c1, s1, c2, s2 = self.c1[U], self.s1[U], self.c2[U], self.s2[U]
        rec = np.zeros(len(U), bool)
        new_costs = []
        for sid in sids:
            cn = eval_service(self.inst.services[sid], PU, FU, self.inst.fuel)
            new_costs.append((sid, cn))
            is1, is2 = s1 == sid, s2 == sid
            other = ~is1 & ~is2
            # s was best
            keep1 = is1 & ~_lt(c2, s2, cn, sid)
            rec |= is1 & ~keep1
            n1c, n1s, n2c, n2s = c1.copy(), s1.copy(), c2.copy(), s2.copy()
            n1c[keep1] = cn[keep1]
            # s was runner-up
            prom = is2 & _lt(cn, sid, c1, s1)
            stay2 = is2 & ~prom & (cn <= c2)
            rec |= is2 & ~prom & ~stay2
            n1c[prom], n1s[prom], n2c[prom], n2s[prom] = cn[prom], sid, c1[prom], s1[prom]
            n2c[stay2] = cn[stay2]
            # s was neither
            b1 = other & _lt(cn, sid, c1, s1)
            b2 = other & ~b1 & _lt(cn, sid, c2, s2)
            n2c[b1], n2s[b1] = c1[b1], s1[b1]
            n1c[b1], n1s[b1] = cn[b1], sid
            n2c[b2], n2s[b2] = cn[b2], sid
            c1, s1, c2, s2 = n1c, n1s, n2c, n2s
        self.c1[U], self.s1[U], self.c2[U], self.s2[U] = c1, s1, c2, s2
        R = U[rec]
        if len(R):  # one recomputation per marked parcel for the whole edit; the new costs of the
            # edited services on R are already known and are not evaluated again
            r = top2(self.inst, self.P.subset(R), self.F0[R], known=[(sid, cn[rec]) for sid, cn in new_costs])
            self.c1[R], self.s1[R], self.c2[R], self.s2[R] = r
        return len(U), len(R)
