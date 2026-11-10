"""Rule-defined piecewise cost functions: data model and exact evaluator.

All money is integer cents; fuel ratios are integer basis points. A parcel is
(key, a, L, M, S, res, day) where key = origin*100000 + zip5, a = actual weight (lb),
L >= M >= S are sorted dimensions (in), res = residential flag, day = ship day index.
Lifted features: L, M, S, G = L + 2M + 2S (length plus girth), V = L*M*S, A = a,
T = billable weight of the evaluated service, DAS = delivery-area level, RES.

Further lifted features: ZIP = zip5, ZONE = zone of the evaluated service, DAY, MONTH and
SUMD = L + M + S.

Predicates are disjunctions of conjunctions. Pred (public carrier schedules) uses strict lower
bounds "feature > threshold"; GPred (ERP rule fragment) also allows >=, <, <=, set membership
and ZIP ranges, so predicates need not be upward-closed and cost need not be monotone in the
features. Exactness does not rely on monotonicity: the index lower bound only uses
nonnegative surcharge amounts and the suffix minimum of the base table (see
theory/rateq_theory.tex, Assumptions A1-A7; RQIndex.build checks A1 and A3).
Minimum-billable-weight tiers are applied lower tier first (Service.min_tiers).
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import numpy as np

FEATS = ["L", "M", "S", "G", "V", "A", "T", "DAS", "RES", "ZIP", "ZONE", "DAY", "MONTH", "SUMD"]
FI = {f: i for i, f in enumerate(FEATS)}
INF = np.int64(1 << 60)
# day index 0 = 2026-01-01; month lookup for days in [-1000, 1000)
_D0 = -1000
_MONTH = np.array([(np.datetime64("2026-01-01") + np.timedelta64(d, "D")).astype("datetime64[M]").astype(int) % 12 + 1
                   for d in range(_D0, 1000)], dtype=np.int64)


def month_of(day):
    return _MONTH[np.clip(np.asarray(day, dtype=np.int64) - _D0, 0, len(_MONTH) - 1)]


@dataclass
class Pred:
    """Disjunction of conjunctions; each conjunction maps feature -> strict lower bound."""
    clauses: List[Dict[str, float]]

    def eval(self, F):  # F: (n, len(FEATS)) array
        out = np.zeros(F.shape[0], dtype=bool)
        for cl in self.clauses:
            m = np.ones(F.shape[0], dtype=bool)
            for f, th in cl.items():
                m &= F[:, FI[f]] > th
            out |= m
        return out

    def eval1(self, f):  # f: tuple/list of feature values
        for cl in self.clauses:
            ok = True
            for k, th in cl.items():
                if not f[FI[k]] > th:
                    ok = False
                    break
            if ok:
                return True
        return False


class GPred:
    """General predicate (ERP rule fragment): disjunction of conjunctions of conditions
    (feature, op, value) with op in {>, >=, <, <=, in, notin, ranges}; 'ranges' is a list of
    closed integer intervals (ZIP prefixes compile to ranges)."""

    def __init__(self, clauses):
        self.clauses = [[(FI[f], op, v) for f, op, v in cl] for cl in clauses]

    def feats(self):
        return {FEATS[i] for cl in self.clauses for i, _, _ in cl}

    @staticmethod
    def _cond(x, op, v):
        if op == ">":
            return x > v
        if op == ">=":
            return x >= v
        if op == "<":
            return x < v
        if op == "<=":
            return x <= v
        if op == "in":
            return np.isin(x, v)
        if op == "notin":
            return ~np.isin(x, v)
        if op == "ranges":
            lo, hi = v
            i = np.searchsorted(lo, x, side="right") - 1
            return (i >= 0) & (x <= hi[np.clip(i, 0, None)])
        raise ValueError(op)

    def eval(self, F):
        out = np.zeros(F.shape[0], dtype=bool)
        for cl in self.clauses:
            m = np.ones(F.shape[0], dtype=bool)
            for i, op, v in cl:
                m &= self._cond(F[:, i], op, v)
            out |= m
        return out

    def eval1(self, f):
        for cl in self.clauses:
            if all(bool(self._cond(np.asarray(f[i]), op, v)) for i, op, v in cl):
                return True
        return False


@dataclass
class Surcharge:
    name: str
    pred: Pred
    price: np.ndarray          # cents per zone index (0 = not applicable in that zone)
    group: int                 # max within a group, sum across groups
    fuel: bool                 # fuel surcharge applies on top
    kind: str = "fixed"        # fixed | per_weight (coef x T) | pct_base (coef% x base) | pct_total
    coef: float = 0.0


@dataclass
class Service:
    sid: int
    name: str
    carrier: str
    fuel_class: int
    zstart: np.ndarray         # sorted interval starts over the key domain
    zzone: np.ndarray          # zone index per interval (-1 = not served)
    breaks: np.ndarray         # ascending upper bounds of weight bands (lb), (b_{k-1}, b_k]
    price: np.ndarray          # (Z, K) int64 cents; INF where undefined
    divisor: float
    dim_thr: float             # dim weight applies only if V > dim_thr
    rounding: str = "carrier"  # "carrier" or "erp_style"
    extrapolate: bool = False  # erp-style: above max weight, unit price of the top band times weight
    limit_weight: float = 1e9  # erp-style weight limit (150 lb): extrapolation threshold
    oversize: Optional[Pred] = None
    oversize_min: float = 0.0  # minimum billable weight when oversize fires
    oversize2: Optional[Pred] = None  # lower minimum-billable tier (UPS/FedEx Additional Handling by dimension)
    oversize2_min: float = 0.0
    inelig: Optional[Pred] = None
    surcharges: List[Surcharge] = field(default_factory=list)
    das_start: Optional[np.ndarray] = None
    das_level: Optional[np.ndarray] = None
    erp_q: float = 0.0          # ERP rounding grid (1 / 0.1 / 0.01)
    erp_carry: str = "ceil"     # ceil | round
    erp_mode: int = 1           # 1 max(actual, volume), 2 actual, 3 volume

    @property
    def min_tiers(self):
        """Minimum-billable-weight tiers, lower tier first (nested predicates)."""
        return [(p, m) for p, m in ((self.oversize2, self.oversize2_min), (self.oversize, self.oversize_min)) if p is not None]

    @property
    def group_key(self):
        return (self.divisor, self.dim_thr, self.rounding, self.fuel_class, self.erp_q, self.erp_carry, self.erp_mode)

    def zone_of(self, key):
        i = np.searchsorted(self.zstart, key, side="right") - 1
        z = self.zzone[np.clip(i, 0, None)]
        return np.where(i < 0, -1, z)

    def das_of(self, key):
        if self.das_start is None:
            return np.zeros(np.shape(key), dtype=np.int64)
        i = np.searchsorted(self.das_start, key, side="right") - 1
        return np.where(i < 0, 0, self.das_level[np.clip(i, 0, None)])


@dataclass
class FuelSchedule:
    starts: Dict[int, np.ndarray]   # fuel_class -> sorted start days
    bps: Dict[int, np.ndarray]      # fuel_class -> basis points

    def at(self, fc, day):
        if fc not in self.starts:
            return np.zeros(np.shape(day), dtype=np.int64)
        i = np.searchsorted(self.starts[fc], day, side="right") - 1
        return self.bps[fc][np.clip(i, 0, None)]


@dataclass
class Instance:
    name: str
    services: List[Service]
    fuel: FuelSchedule
    n_zones: int


@dataclass
class Parcels:
    key: np.ndarray
    a: np.ndarray
    L: np.ndarray
    M: np.ndarray
    S: np.ndarray
    res: np.ndarray
    day: np.ndarray

    def __len__(self):
        return len(self.key)

    def subset(self, idx):
        return Parcels(*(getattr(self, f)[idx] for f in ("key", "a", "L", "M", "S", "res", "day")))

    def base_feats(self):
        F = np.zeros((len(self), len(FEATS)))
        F[:, FI["L"]] = self.L; F[:, FI["M"]] = self.M; F[:, FI["S"]] = self.S
        F[:, FI["G"]] = self.L + 2 * self.M + 2 * self.S
        F[:, FI["V"]] = self.L * self.M * self.S
        F[:, FI["A"]] = self.a; F[:, FI["RES"]] = self.res
        F[:, FI["ZIP"]] = self.key % 100000; F[:, FI["DAY"]] = self.day; F[:, FI["MONTH"]] = month_of(self.day)
        F[:, FI["SUMD"]] = self.L + self.M + self.S
        return F


def erp_round(x, q, carry):
    if carry == "ceil":
        return np.ceil(x / q - 1e-9) * q
    return np.floor(x / q + 0.5) * q


def billable(s: Service, a, V):
    """Billable weight before the oversize minimum; vectorised."""
    if s.rounding == "erp":
        v = V / s.divisor if s.divisor > 0 else np.zeros_like(V)
        x = np.maximum(a, v) if s.erp_mode == 1 else (a if s.erp_mode == 2 else v)
        return erp_round(x, s.erp_q, s.erp_carry)
    if s.rounding == "erp_style":
        # erp-style: round actual weight down to 0.1 then up to an integer; volume weight
        # = ceil-ish(V / divisor) with a floor of 1 (CalculateZoneAndChargeWeightServiceImpl)
        aw = np.ceil(np.floor(a * 10 + 1e-9) / 10 - 1e-9)
        vw0 = np.ceil(V / s.divisor * 1000 - 1e-9) / 1000
        vw = np.where(vw0 > 1, np.ceil(np.floor(vw0 * 10 + 1e-9) / 10 - 1e-9), 1.0)
        vw = np.where(V > s.dim_thr, vw, 0.0)
        return np.maximum(aw, vw)
    dim = np.where(V > s.dim_thr, V / s.divisor, 0.0)
    return np.maximum(a, dim)


def eval_service(s: Service, P: Parcels, F0, fuel: FuelSchedule, return_parts=False):
    """Exact cost (int64 cents) of service s for all parcels; INF if ineligible."""
    n = len(P)
    z = s.zone_of(P.key)
    F = F0.copy()
    t = billable(s, P.a, F[:, FI["V"]])
    F[:, FI["T"]] = t
    F[:, FI["DAS"]] = s.das_of(P.key)
    F[:, FI["ZONE"]] = z
    for p_, m_ in s.min_tiers:
        t = np.where(p_.eval(F), np.maximum(t, m_), t)
        F[:, FI["T"]] = t
    elig = z >= 0
    if s.inelig is not None:
        elig &= ~s.inelig.eval(F)
    bmax = s.breaks[-1]
    if s.extrapolate:
        over = t > s.limit_weight
    else:
        over = np.zeros(n, dtype=bool)
        elig &= t <= bmax
    k = np.searchsorted(s.breaks, t - 1e-9, side="left")
    k = np.clip(k, 0, len(s.breaks) - 1)
    zc = np.clip(z, 0, None)
    base = s.price[zc, k]
    if over.any():
        top = s.price[zc, len(s.breaks) - 1]
        ext = np.ceil(np.minimum(top * t / bmax, 1e15) - 1e-6).astype(np.int64)  # unit price x weight
        ext = np.where(top < INF, ext, INF)
        base = np.where(over, ext, base)
    elig &= base < INF
    base = np.where(elig, base, 0)
    sf = np.zeros(n, dtype=np.int64)
    snf = np.zeros(n, dtype=np.int64)
    pct = np.zeros(n, dtype=np.float64)
    if s.surcharges:
        groups = {}
        for r in s.surcharges:
            fires = r.pred.eval(F)
            if r.kind == "pct_total":
                pct += np.where(fires, r.coef, 0.0)
                continue
            if r.kind == "fixed":
                amt = np.where(fires, r.price[zc], 0)
            elif r.kind == "per_weight":
                amt = np.where(fires, np.round(r.coef * t).astype(np.int64), 0)
            else:  # pct_base
                amt = np.where(fires, np.round(r.coef / 100.0 * base).astype(np.int64), 0)
            key = (r.group, r.fuel)
            groups[key] = np.maximum(groups[key], amt) if key in groups else amt
        for (g, fu), amt in groups.items():
            if fu:
                sf += amt
            else:
                snf += amt
    bp = fuel.at(s.fuel_class, P.day)
    cost = ((base + sf) * (10000 + bp) + 5000) // 10000 + snf
    if s.surcharges and pct.any():
        cost = cost + np.round(cost * pct / 100.0).astype(np.int64)
    cost = np.where(elig, cost, INF)
    if return_parts:
        return cost, z, t, base
    return cost


def brute_vec(inst: Instance, P: Parcels):
    """Brute force, vectorised over parcels: one pass per service. Returns (best, sid)."""
    F0 = P.base_feats()
    best = np.full(len(P), INF, dtype=np.int64)
    arg = np.full(len(P), -1, dtype=np.int64)
    for s in inst.services:
        c = eval_service(s, P, F0, inst.fuel)
        better = c < best  # services are visited in sid order => ties keep smaller sid
        best = np.where(better, c, best)
        arg = np.where(better, s.sid, arg)
    return best, arg


def all_costs(inst: Instance, P: Parcels):
    F0 = P.base_feats()
    return np.stack([eval_service(s, P, F0, inst.fuel) for s in inst.services], axis=1)
