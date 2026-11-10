"""Instance and workload builders.

PUB   : 5 USPS + 7 UPS services from the public price lists (USPS Notice 123, UPS daily
        rates), USPS zone charts for 5 origins. UPS zones are approximated from the USPS
        chart (official UPS zone files are not programmatically retrievable); surcharge
        amounts follow the public schedule structure with approximate values.
PUBxN : PUB plus contract-discount variants up to N services (3PL with many accounts).
SYN   : fully synthetic generator (n services, K bands, Z zones, R zone intervals).
ERPSTYLE : services with the erp-style rule semantics (rounding, 150-lb linear extrapolation,
        oversize minimum billable weight, cost groups, fuel on base + flagged surcharges,
        limit rules); all values synthetic (no real rate card available yet).
"""
import json
from pathlib import Path
import numpy as np
from .model import Service, Surcharge, Pred, FuelSchedule, Instance, Parcels, INF

DATA = Path(__file__).resolve().parents[2] / "data/public"
ORIGINS = ["088", "300", "606", "750", "917"]
KEYSPAN = 100000


def _usps_zone_intervals():
    z = json.load(open(DATA / "usps_zones.json"))
    per = {}
    for oi, o in enumerate(ORIGINS):
        iv = sorted(z[o]["intervals"])
        cuts = []  # (start, zone) over zip5, gaps -> 0 (not served)
        pos = 0
        for a, b, zone in iv:
            if a > pos:
                cuts.append((pos, 0))
            cuts.append((a, zone))
            pos = b + 1
        cuts.append((pos, 0))
        # 5-digit exceptions override (applied to all USPS services)
        seen = set()
        for a, b, zone, _svc in z[o]["exceptions"]:
            if (a, b) in seen:  # same range listed per mail service; keep the first
                continue
            seen.add((a, b))
            cuts = sorted(cuts)
            after = [zz for s, zz in cuts if s <= b + 1][-1]  # original zone at b+1
            cuts = [c for c in cuts if not (a <= c[0] <= b + 1)] + [(a, zone), (b + 1, after)]
        cuts = [c for c in sorted(dict(sorted(cuts)).items()) if c[0] < KEYSPAN]
        per[oi] = cuts
    return per


def _tile(per, zmap):
    """Concatenate per-origin cut lists into key-domain arrays; zmap maps raw zone -> index or -1."""
    st, zz = [], []
    for oi in range(len(ORIGINS)):
        for s, z in per[oi]:
            st.append(oi * KEYSPAN + s)
            zz.append(zmap(z))
    return np.array(st, dtype=np.int64), np.array(zz, dtype=np.int64)


def _das(rng, frac1=0.08, frac2=0.02, nblocks=600):
    """Synthetic delivery-area-surcharge ZIP intervals (clustered), tiled over origins."""
    starts = np.sort(rng.choice(np.arange(500, 99900, 50), size=nblocks, replace=False))
    st, lv = [0], [0]
    for s in starts:
        r = rng.random()
        lvl = 2 if r < frac2 / (frac1 + frac2) else 1
        st += [int(s), int(s) + int(rng.integers(5, 120))]
        lv += [lvl, 0]
    order = np.argsort(st, kind="stable")
    st, lv = np.array(st)[order], np.array(lv)[order]
    keep = np.r_[True, np.diff(st) > 0]
    st, lv = st[keep], lv[keep]
    S = np.concatenate([oi * KEYSPAN + st for oi in range(len(ORIGINS))])
    L = np.concatenate([lv for _ in ORIGINS])
    return S.astype(np.int64), L.astype(np.int64)


def _fuel(rng, classes, days=365, base_bp=None):
    starts, bps = {}, {}
    for fc in classes:
        st = np.arange(0, days, 7)
        b0 = (base_bp or {}).get(fc, 1850)
        walk = b0 + np.cumsum(rng.integers(-25, 26, size=len(st)))
        starts[fc], bps[fc] = st, walk.astype(np.int64)
    return FuelSchedule(starts, bps)


def _zone_price(per_zone):
    return np.array(per_zone, dtype=np.int64)


def build_pub(seed=0):
    rng = np.random.default_rng(seed)
    usps = json.load(open(DATA / "usps_rates.json"))
    ups = json.load(open(DATA / "ups_rates.json"))
    per = _usps_zone_intervals()
    ust, uzz = _tile(per, lambda z: z - 1 if 1 <= z <= 9 else -1)
    # UPS contiguous zones 2..8 approximated from the USPS zone; AK/HI/territories not served
    def upsz(z):
        return -1 if z <= 0 or z >= 9 else max(2, z) - 2
    pst, pzz = _tile(per, upsz)
    akhi = [(99500, 99999), (96700, 96899), (96900, 96999)]
    for oi in range(len(ORIGINS)):
        for a, b in akhi:
            lo, hi = oi * KEYSPAN + a, oi * KEYSPAN + b
            m = (pst >= lo) & (pst <= hi)
            pzz[m] = -1
    das_s, das_l = _das(rng)
    svcs = []
    # USPS (no fuel surcharge; dim weight only above 1 cubic foot; nonstandard fees)
    ns = lambda Z: [
        Surcharge("NS_len22", Pred([{"L": 22}]), np.full(Z, 400), 1, False),
        Surcharge("NS_len30", Pred([{"L": 30}]), np.full(Z, 1500), 1, False),
        Surcharge("NS_vol2cf", Pred([{"V": 3456}]), np.full(Z, 1500), 2, False),
    ]
    for name, maxg in [("USPS_GA_Retail", 130), ("USPS_GA_Commercial", 130), ("USPS_PM_Retail", 108),
                       ("USPS_PM_Commercial", 108), ("USPS_PME_Retail", 108)]:
        t = usps[name]
        price = np.array([[INF if v is None else v for v in row] for row in t["price"]], dtype=np.int64)
        svcs.append(Service(len(svcs), name, "USPS", 0, ust, uzz, np.array(t["breaks"]), price,
                            166.0, 1728.0, inelig=Pred([{"A": 70}, {"G": maxg}]), surcharges=ns(price.shape[0])))
    # UPS (dim divisor 139 always; fuel class 1 ground, 2 air)
    for name in ["UPS_Ground", "UPS_3DA", "UPS_2DA", "UPS_2DA_AM", "UPS_NDA_Saver", "UPS_NDA", "UPS_NDA_Early"]:
        t = ups[name]
        cols = {}
        for zi, z in enumerate(t["zones"]):
            zz = z if z < 10 else z % 10
            if (z < 10) or name in ("UPS_2DA_AM", "UPS_NDA_Saver"):
                if 2 <= zz <= 8:
                    cols[zz - 2] = t["price"][zi]
        Z = 7
        price = np.full((Z, len(t["breaks"])), INF, dtype=np.int64)
        for zi, row in cols.items():
            price[zi] = [INF if v is None else v for v in row]
        fc = 1 if name == "UPS_Ground" else 2
        zf = np.linspace(1.0, 1.35, Z)
        sur = [
            Surcharge("AHS_dim", Pred([{"L": 48}, {"M": 30}, {"G": 105}]), np.round(2850 * zf).astype(np.int64), 1, True),
            Surcharge("AHS_wt", Pred([{"A": 50}]), np.round(4500 * zf).astype(np.int64), 1, True),
            Surcharge("LargePkg", Pred([{"G": 130}, {"L": 96}]), np.round(24000 * zf).astype(np.int64), 1, True),
            Surcharge("DAS", Pred([{"DAS": 0}]), np.full(Z, 620), 2, True),
            Surcharge("DAS_ext", Pred([{"DAS": 1}]), np.full(Z, 830), 2, True),
            Surcharge("Resi", Pred([{"RES": 0}]), np.full(Z, 610 if fc == 1 else 665), 3, True),
        ]
        svcs.append(Service(len(svcs), name, "UPS", fc, pst, pzz, np.array(t["breaks"]), price, 139.0, 0.0,
                            oversize=Pred([{"G": 130}, {"L": 96}]), oversize_min=90.0,
                            inelig=Pred([{"A": 150}, {"L": 108}, {"G": 165}]), surcharges=sur,
                            das_start=das_s, das_level=das_l))
    fuel = _fuel(rng, [1, 2], base_bp={1: 1900, 2: 2200})
    return Instance("PUB", svcs, fuel, 9)


def _variant(s: Service, sid, rng, regional=False):
    """Contract-discount variant of a public service (3PL account / regional carrier)."""
    Z, K = s.price.shape
    d = rng.uniform(0.0, 0.35)
    dz = rng.normal(0, 0.03, size=(Z, 1))
    tier = np.repeat(rng.normal(0, 0.02, size=(1, (K + 9) // 10)), 10, axis=1)[:, :K]
    mult = np.clip(1 - d - dz - tier, 0.4, 1.1)
    price = np.where(s.price < INF, np.round(s.price * mult), INF).astype(np.int64)
    zzone = s.zzone.copy()
    if regional:  # serve only near zones
        cap = int(rng.integers(2, 6))
        zzone = np.where(zzone > cap, -1, zzone)
    sm = rng.uniform(0.6, 1.0)
    sur = [Surcharge(r.name, r.pred, np.round(r.price * sm).astype(np.int64), r.group, r.fuel) for r in s.surcharges]
    return Service(sid, f"{s.name}~v{sid}", s.carrier, s.fuel_class, s.zstart, zzone, s.breaks, price,
                   s.divisor, s.dim_thr, rounding=s.rounding, extrapolate=s.extrapolate, limit_weight=s.limit_weight,
                   oversize=s.oversize, oversize_min=s.oversize_min, oversize2=s.oversize2, oversize2_min=s.oversize2_min,
                   inelig=s.inelig, surcharges=sur, das_start=s.das_start, das_level=s.das_level)


def build_pub_scaled(n, seed=0):
    inst = build_pub(seed)
    rng = np.random.default_rng(seed + 1000)
    base = list(inst.services)
    svcs = list(base)
    while len(svcs) < n:
        s = base[int(rng.integers(len(base)))]
        svcs.append(_variant(s, len(svcs), rng, regional=rng.random() < 0.3))
    return Instance(f"PUBx{n}", svcs, inst.fuel, inst.n_zones)


def build_syn(n=200, K=100, Z=8, R=400, divisors=(139.0, 166.0, 194.0, 250.0), seed=0, monotone=True,
              n_schemes=6):
    """Fully synthetic: latent distance per zip3 drives zones; prices a + b_z * w^gamma.
    Services draw their zone chart from a pool of n_schemes zone schemes (carriers share zone
    charts), optionally capped to near zones (regional carriers)."""
    rng = np.random.default_rng(seed)
    n_orig = len(ORIGINS)
    lat = rng.random((n_orig, 1000))  # latent distance per (origin, zip3)
    das_s, das_l = _das(rng)
    fuel = _fuel(rng, list(range(1, 5)), base_bp={1: 1800, 2: 2100, 3: 1500, 4: 1200})
    schemes = []
    for _ in range(n_schemes):
        cuts = np.sort(rng.choice(np.arange(1, 1000), size=max(2, R // n_orig) - 1, replace=False))
        starts, zones = [], []
        for oi in range(n_orig):
            bounds = np.r_[0, cuts, 1000]
            for a, b in zip(bounds[:-1], bounds[1:]):
                lv = lat[oi, a:b].mean() + rng.normal(0, 0.05)
                starts.append(oi * KEYSPAN + a * 100)
                zones.append(int(np.clip(lv * Z, 0, Z - 1)))
        schemes.append((np.array(starts, dtype=np.int64), np.array(zones, dtype=np.int64)))
    svcs = []
    for sid in range(n):
        st0, zz0 = schemes[int(rng.integers(n_schemes))]
        starts, zones = st0, zz0.copy()
        if rng.random() < 0.3:
            zones = np.where(zones > int(rng.integers(2, Z)), -1, zones)
        Kb = int(rng.integers(max(10, K // 2), K + 1))
        wmax = float(rng.choice([50, 70, 100, 150]))
        step = max(1, int(round(wmax / Kb)))
        breaks = np.unique(np.r_[0.5, np.arange(step, wmax + 1, step), wmax]).astype(float)  # whole-lb bands
        a0 = rng.uniform(500, 1500); gamma = rng.uniform(0.7, 1.0)
        bz = rng.uniform(30, 120) * (1 + np.arange(Z) * rng.uniform(0.08, 0.25))
        price = np.round(a0 + bz[:, None] * breaks[None, :] ** gamma)
        if not monotone:
            price += rng.normal(0, 40, size=price.shape)
        price = np.maximum(price, 100).astype(np.int64)
        fc = int(rng.integers(1, 5))
        D = float(rng.choice(divisors))
        sur = []
        for g in range(int(rng.integers(1, 6))):
            f = rng.choice(["L", "M", "G", "A", "V", "DAS", "RES"])
            th = {"L": rng.uniform(30, 60), "M": rng.uniform(20, 35), "G": rng.uniform(90, 130),
                  "A": rng.uniform(30, 70), "V": rng.uniform(2000, 6000), "DAS": 0, "RES": 0}[f]
            sur.append(Surcharge(f"r{g}", Pred([{f: float(th)}]),
                                 np.round(rng.uniform(300, 4000) * (1 + 0.05 * np.arange(Z))).astype(np.int64),
                                 int(rng.integers(0, 3)), bool(rng.random() < 0.7)))
        svcs.append(Service(sid, f"syn{sid}", f"c{fc}", fc, np.array(starts, dtype=np.int64),
                            np.array(zones, dtype=np.int64), breaks, price, D, 0.0,
                            inelig=Pred([{"A": wmax}, {"G": float(rng.choice([130, 165]))}]),
                            surcharges=sur, das_start=das_s, das_level=das_l))
    return Instance(f"SYN{n}", svcs, fuel, Z)


def build_erp_style(n=20, seed=0):
    """Services shaped like erp-style supplier products (values synthetic)."""
    rng = np.random.default_rng(seed)
    per = _usps_zone_intervals()
    das_s, das_l = _das(rng)
    fuel = _fuel(rng, [1, 2, 3], base_bp={1: 1700, 2: 2000, 3: 1200})
    svcs = []
    for sid in range(n):
        shift = int(rng.integers(-1, 2))
        st, zz = _tile(per, lambda z: -1 if z <= 0 or z >= 9 else int(np.clip(z - 1 + shift, 0, 7)))
        breaks = np.arange(1.0, 151.0)
        a0 = rng.uniform(700, 1300); bz = rng.uniform(40, 90) * (1 + np.arange(8) * 0.15)
        price = np.round(a0 + bz[:, None] * breaks[None, :] ** rng.uniform(0.75, 0.95)).astype(np.int64)
        Z = 8
        sur = [
            Surcharge("AHS", Pred([{"L": 48}, {"M": 30}]), np.round(rng.uniform(2000, 3500) * np.ones(Z)).astype(np.int64), 1, True),
            Surcharge("AHS_wt", Pred([{"A": 50}]), np.round(rng.uniform(3000, 5000) * np.ones(Z)).astype(np.int64), 1, True),
            Surcharge("Oversize", Pred([{"G": 130}, {"L": 96}]), np.round(rng.uniform(9000, 20000) * np.ones(Z)).astype(np.int64), 1, True),
            Surcharge("DAS", Pred([{"DAS": 0}]), np.full(Z, int(rng.uniform(300, 700))), 2, True),
            Surcharge("Sign", Pred([{"RES": 0, "A": 20}]), np.full(Z, 300), 3, False),
        ]
        svcs.append(Service(sid, f"sp{sid}", f"g{sid % 3}", 1 + sid % 3, st, zz, breaks, price,
                            float(rng.choice([139.0, 166.0, 250.0])), 0.0, rounding="erp_style",
                            extrapolate=True, limit_weight=150.0,
                            oversize=Pred([{"G": 130}, {"L": 96}]), oversize_min=90.0,
                            inelig=Pred([{"L": 108}, {"G": 165}]), surcharges=sur,
                            das_start=das_s, das_level=das_l))
    return Instance(f"ERPSTYLE{n}", svcs, fuel, 8)


# ---------------------------------------------------------------- workloads

def _dest(rng, N):
    z = np.array(json.load(open(DATA / "zcta.json")))
    return z[rng.integers(0, len(z), size=N)]


def _pack(rng, N, a, dims):
    dims = -np.sort(-dims, axis=1)
    o = rng.integers(0, len(ORIGINS), size=N)
    key = o * KEYSPAN + _dest(rng, N)
    res = (rng.random(N) < 0.7).astype(float)
    day = rng.integers(0, 365, size=N)
    return Parcels(key.astype(np.int64), a, dims[:, 0], dims[:, 1], dims[:, 2], res, day)


def parcels_olist(N, seed=0):
    rng = np.random.default_rng(seed)
    rows = np.array(json.load(open(DATA / "parcels_olist.json")))
    r = rows[rng.integers(0, len(rows), size=N)]
    return _pack(rng, N, r[:, 0], r[:, 1:4])


def parcels_furniture(N, seed=0):
    """Heavy, bulky parcels (desk frames, table tops, chairs, accessories): stresses surcharges."""
    rng = np.random.default_rng(seed)
    kind = rng.choice(4, size=N, p=[0.35, 0.25, 0.2, 0.2])
    a = np.where(kind == 0, rng.uniform(0.3, 12, N),
        np.where(kind == 1, rng.uniform(35, 95, N),
        np.where(kind == 2, rng.uniform(25, 70, N), rng.uniform(20, 160, N))))
    L = np.where(kind == 0, rng.uniform(6, 24, N), np.where(kind == 1, rng.uniform(45, 82, N),
        np.where(kind == 2, rng.uniform(26, 40, N), rng.uniform(30, 100, N))))
    M = np.where(kind == 0, rng.uniform(4, 16, N), np.where(kind == 1, rng.uniform(10, 32, N),
        np.where(kind == 2, rng.uniform(20, 30, N), rng.uniform(15, 40, N))))
    S = np.where(kind == 0, rng.uniform(2, 10, N), np.where(kind == 1, rng.uniform(4, 10, N),
        np.where(kind == 2, rng.uniform(10, 26, N), rng.uniform(6, 30, N))))
    return _pack(rng, N, np.round(a, 2), np.stack([L, M, S], axis=1).round(1))


def parcels_uniform(N, seed=0):
    rng = np.random.default_rng(seed)
    a = np.exp(rng.uniform(np.log(0.1), np.log(150), N))
    dims = np.exp(rng.uniform(np.log(2), np.log(60), (N, 3)))
    return _pack(rng, N, np.round(a, 2), dims.round(1))


# ---------------------------------------------------------------- PUB2: official 2026 files
FEDEX_ORIGIN = {"088": "088", "300": "303", "606": "606", "750": "752", "917": "917"}


def _fedex_zone_tile():
    z = json.load(open(DATA / "fedex_zones.json"))
    st, zz = [], []
    for oi, o in enumerate(ORIGINS):
        iv = z[FEDEX_ORIGIN[o]]["intervals"]
        pos = 0
        for a, b, zone in iv:
            if a > pos:
                st.append(oi * KEYSPAN + pos); zz.append(-1)
            st.append(oi * KEYSPAN + a); zz.append(zone - 2 if 2 <= zone <= 8 else -1)
            pos = b + 1
        if pos < KEYSPAN:
            st.append(oi * KEYSPAN + pos); zz.append(-1)
    st, zz = np.array(st, dtype=np.int64), np.array(zz, dtype=np.int64)
    keep = np.r_[True, np.diff(st) > 0]
    return st[keep], zz[keep]


def _ups_zone_tile():
    """Official UPS zone charts (per origin, per destination ZIP3) on the key domain; adjacent equal zones merged."""
    z = json.load(open(DATA / "ups_zones.json"))
    st, zz = [], []
    for oi, o in enumerate(ORIGINS):
        zone = np.full(1000, -1, dtype=np.int64)
        for z3, zn in z[o]:
            zone[z3] = zn - 2 if zn >= 0 else -1
        prev = None
        for z3 in range(1000):
            if zone[z3] != prev:
                st.append(oi * KEYSPAN + z3 * 100); zz.append(int(zone[z3])); prev = zone[z3]
    return np.array(st, dtype=np.int64), np.array(zz, dtype=np.int64)


def _das_tile(name):
    d = json.load(open(DATA / f"das_{name}.json"))
    lvl = np.zeros(KEYSPAN, dtype=np.int64)
    for level, k in ((1, "DAS"), (2, "DAS_Extended"), (3, "Remote")):
        lvl[np.array(d[k], dtype=np.int64)] = np.maximum(lvl[np.array(d[k], dtype=np.int64)], level)
    ch = np.nonzero(np.r_[True, lvl[1:] != lvl[:-1]])[0]
    S = np.concatenate([oi * KEYSPAN + ch for oi in range(len(ORIGINS))]).astype(np.int64)
    L = np.concatenate([lvl[ch] for _ in ORIGINS]).astype(np.int64)
    return S, L


def _fuel_official():
    f = json.load(open(DATA / "fuel_history.json"))
    starts, bps = {}, {}
    day0 = np.datetime64("2026-01-01")
    for fc, (carrier, col) in {1: ("ups", 1), 2: ("ups", 2), 3: ("fedex", 1), 4: ("fedex", 2)}.items():
        rows = f[carrier]
        d = np.array([(np.datetime64(r[0]) - day0).astype(int) for r in rows])
        b = np.array([int(round(r[col] * 100)) for r in rows])
        d[0] = min(d[0], 0)  # the earliest published week also covers earlier ship days (history limited to ~90 days)
        starts[fc], bps[fc] = d, b
    return FuelSchedule(starts, bps)


def _zone_band(Z, offset=0):
    """Per-zone index -> official zone band (2, 3-4, 5-6, 7+) for zone indices 0..Z-1 = zones 2..8."""
    zn = np.arange(Z) + 2 + offset
    return np.where(zn <= 2, 0, np.where(zn <= 4, 1, np.where(zn <= 6, 2, 3)))


def build_pub2(seed=0):
    """Official 2026 data: USPS (Notice 123, USPS zones), UPS (2026 daily rates parsed from the guide PDF, official
    UPS zone charts, 2026 surcharges, UPS DAS ZIPs), FedEx (2026 Standard List Rates, official zone charts,
    2026 surcharges, FedEx DAS ZIPs). Fuel: official weekly history (earliest week extended backwards)."""
    usps = json.load(open(DATA / "usps_rates.json"))
    ups = json.load(open(DATA / "ups_rates_2026.json"))
    fdx = json.load(open(DATA / "fedex_rates.json"))
    sur = json.load(open(DATA / "official_surcharges.json"))
    per = _usps_zone_intervals()
    ust, uzz = _tile(per, lambda z: z - 1 if 1 <= z <= 9 else -1)
    fst, fzz = _fedex_zone_tile()
    ust2, uzz2 = _ups_zone_tile()
    ud, ul = _das_tile("ups"); fd, fl = _das_tile("fedex")
    svcs = []
    ns = lambda Z: [
        Surcharge("NS_len22", Pred([{"L": 22}]), np.full(Z, 400), 1, False),
        Surcharge("NS_len30", Pred([{"L": 30}]), np.full(Z, 1500), 1, False),
        Surcharge("NS_vol2cf", Pred([{"V": 3456}]), np.full(Z, 1500), 2, False),
    ]
    for name, maxg in [("USPS_GA_Retail", 130), ("USPS_GA_Commercial", 130), ("USPS_PM_Retail", 108),
                       ("USPS_PM_Commercial", 108), ("USPS_PME_Retail", 108)]:
        t = usps[name]
        price = np.array([[INF if v is None else v for v in row] for row in t["price"]], dtype=np.int64)
        svcs.append(Service(len(svcs), name, "USPS", 0, ust, uzz, np.array(t["breaks"]), price,
                            166.0, 1728.0, inelig=Pred([{"A": 70}, {"G": maxg}]), surcharges=ns(price.shape[0])))
    c = lambda x: np.round(np.array(x) * 100).astype(np.int64)

    # Additional Handling by dimension (length > 48, width > 30, length + girth > 105, cubic > 10,368):
    # 40 lb minimum billable weight (UPS guide p. 179; FedEx Service Guide p. 128)
    AHS_DIM = Pred([{"L": 48}, {"M": 30}, {"G": 105}, {"V": 10368}])

    def carrier_sur(o, Z, resi):
        band = _zone_band(Z)
        if o == "ups":
            s = sur["ups"]
            return [
                Surcharge("AHS_dim", Pred([{"L": 48}, {"M": 30}, {"G": 105}, {"V": 10368}]), c(s["AHS_dimension"])[band], 1, True),
                Surcharge("AHS_wt", Pred([{"A": 50}]), c(s["AHS_weight"])[band], 1, True),
                Surcharge("LargePkg", Pred([{"G": 130}, {"L": 96}]), c(s["LargePackage_residential"])[band], 1, True),
                Surcharge("DAS_com", Pred([{"DAS": 0}]), np.full(Z, int(s["DAS_commercial"] * 100)), 2, True),
                Surcharge("DAS_res", Pred([{"DAS": 0, "RES": 0}]), np.full(Z, int(s["DAS_residential"] * 100)), 2, True),
                Surcharge("DAS_ext_com", Pred([{"DAS": 1}]), np.full(Z, int(s["DAS_ext_commercial"] * 100)), 2, True),
                Surcharge("DAS_ext_res", Pred([{"DAS": 1, "RES": 0}]), np.full(Z, int(s["DAS_ext_residential"] * 100)), 2, True),
                Surcharge("Resi", Pred([{"RES": 0}]), np.full(Z, int(resi * 100)), 3, True)]
        s = sur["fedex"]
        return [
            Surcharge("AHS_dim", Pred([{"L": 48}, {"M": 30}, {"G": 105}, {"V": 10368}]), c(s["AHS_dimension"])[band], 1, True),
            Surcharge("AHS_wt", Pred([{"A": 50}]), c(s["AHS_weight"])[band], 1, True),
            Surcharge("Oversize", Pred([{"G": 130}, {"L": 96}]), c(s["Oversize"])[band], 1, True),
            Surcharge("DAS_com", Pred([{"DAS": 0}]), np.full(Z, int(s["DAS_commercial"] * 100)), 2, True),
            Surcharge("DAS_res", Pred([{"DAS": 0, "RES": 0}]), np.full(Z, int(s["DAS_residential"] * 100)), 2, True),
            Surcharge("DAS_ext_com", Pred([{"DAS": 1}]), np.full(Z, int(s["DAS_ext_commercial"] * 100)), 2, True),
            Surcharge("DAS_ext_res", Pred([{"DAS": 1, "RES": 0}]), np.full(Z, int(s["DAS_ext_residential"] * 100)), 2, True),
            Surcharge("DAS_remote", Pred([{"DAS": 2}]), np.full(Z, int(s["DAS_remote"] * 100)), 2, True),
            Surcharge("Resi", Pred([{"RES": 0}]), np.full(Z, int(resi * 100)), 3, True)]

    for name in ["UPS_Ground", "UPS_3DA", "UPS_2DA", "UPS_2DA_AM", "UPS_NDA_Saver", "UPS_NDA", "UPS_NDA_Early"]:
        t = ups[name]
        cols = {}
        for zi, z in enumerate(t["zones"]):
            zz = z if z < 10 else z % 10
            if (z < 10) or name in ("UPS_2DA_AM", "UPS_NDA_Saver"):
                if 2 <= zz <= 8:
                    cols[zz - 2] = t["price"][zi]
        price = np.full((7, len(t["breaks"])), INF, dtype=np.int64)
        for zi, row in cols.items():
            price[zi] = [INF if v is None else v for v in row]
        fc = 1 if name == "UPS_Ground" else 2
        resi = sur["ups"]["Residential_ground"] if fc == 1 else sur["ups"]["Residential_air"]
        svcs.append(Service(len(svcs), name, "UPS", fc, ust2, uzz2, np.array(t["breaks"]), price, 139.0, 0.0,
                            oversize=Pred([{"G": 130}, {"L": 96}]), oversize_min=90.0,
                            oversize2=AHS_DIM, oversize2_min=40.0,
                            inelig=Pred([{"A": 150}, {"L": 108}, {"G": 165}]), surcharges=carrier_sur("ups", 7, resi),
                            das_start=ud, das_level=ul))
    for name in ["FedEx_Ground", "FedEx_Express_Saver", "FedEx_2Day", "FedEx_2Day_AM", "FedEx_Standard_Overnight",
                 "FedEx_Priority_Overnight", "FedEx_First_Overnight"]:
        t = fdx[name]
        price = np.array(t["price"], dtype=np.int64)
        fc = 3 if name == "FedEx_Ground" else 4
        resi = sur["fedex"]["Residential_ground_hd"] if fc == 3 else sur["fedex"]["Residential_express"]
        svcs.append(Service(len(svcs), name, "FedEx", fc, fst, fzz, np.array(t["breaks"], dtype=float), price, 139.0, 0.0,
                            oversize=Pred([{"G": 130}, {"L": 96}]), oversize_min=90.0,
                            oversize2=AHS_DIM, oversize2_min=40.0,
                            inelig=Pred([{"A": 150}, {"L": 108}, {"G": 165}]), surcharges=carrier_sur("fedex", 7, resi),
                            das_start=fd, das_level=fl))
    return Instance("PUB2", svcs, _fuel_official(), 9)


def build_pub2_scaled(n, seed=0):
    inst = build_pub2(seed)
    rng = np.random.default_rng(seed + 1000)
    base = list(inst.services)
    svcs = list(base)
    while len(svcs) < n:
        s = base[int(rng.integers(len(base)))]
        svcs.append(_variant(s, len(svcs), rng, regional=rng.random() < 0.3))
    return Instance(f"PUB2x{n}", svcs, inst.fuel, inst.n_zones)
