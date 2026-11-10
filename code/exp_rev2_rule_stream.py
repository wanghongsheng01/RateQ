"""Rev-2 B1: a stream of LOCAL rule edits on the official public rate cards, replayed on 200k stored
parcels, comparing predicate-level supports, service-level supports and full recomputation.

The edits are derived from data already in data/public (no new downloads):
  amount    one surcharge rule of one service (AHS dimension/weight, Large Package/Oversize, DAS
            commercial/residential/extended/remote, Residential, USPS nonstandard fees) is re-priced by
            a factor drawn from the observed UPS 2025 -> 2026 change of the published base rates
            (ups_rates.json = 2025 daily rates, ups_rates_2026.json = 2026 guide; per-cell ratios).
  resi      the Residential rule of one service, same factor distribution.
  threshold one clause threshold of one surcharge rule of one service moves by a realistic step
            (weight +-5 lb, length/width +-2 in, length+girth +-5 in, cubic size +-10%).
  das_list  a revision of one carrier's DAS ZIP list (all services that share that list): 200 ZIP
            codes added at the base DAS level and 200 removed, drawn from the current list.
Predicate-level support (as erp_pilot.predicate_support): parcels of the edited service's coverage
on which an old or new version of a changed rule fires (features incl. billable weight after the
minimum-billable tiers, zone and DAS level); for a DAS list revision, the covered parcels whose
destination ZIP changed level. Service-level support: the whole coverage of the edited service(s).
Full: top-2 recomputation of every stored parcel. pred_fast: the same support computed by a
feature-aware scan (rules reading only parcel-level features and DAS are evaluated on the stored base
features, once per predicate and DAS map, with the mask cached across events; asserted equal). Repairs use rateq.maintain.Maintainer.apply_many.
Exactness: per event the predicate-level and service-level states are compared, and the final state
is compared with a full recomputation. 5 repetitions of the whole stream (fresh instance each);
thread CPU time. Outputs results/rev2/rule_stream_<INST>.json.
Usage: python exp_rev2_rule_stream.py <INST> [reps]
"""
import collections, copy, json, os, sys, time, platform
import numpy as np
from exp_rev1_common import INSTS, concat, summ, load, uptime
from rateq.instances import parcels_olist, parcels_furniture, DATA, KEYSPAN, ORIGINS
from rateq.model import Pred, Surcharge, FI, billable, INF
from rateq.index import RQIndex
from rateq.maintain import Maintainer, top2

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "results", "rev2")
os.makedirs(OUT, exist_ok=True)
STEP = {"A": 5.0, "L": 2.0, "M": 2.0, "S": 2.0, "G": 5.0}


def ups_ratio_pool():
    a = json.load(open(DATA / "ups_rates.json")); b = json.load(open(DATA / "ups_rates_2026.json"))
    r = []
    for name in a:
        ta, tb = a[name], b[name]
        za = {z: i for i, z in enumerate(ta["zones"])}; zb = {z: i for i, z in enumerate(tb["zones"])}
        wa = {w: i for i, w in enumerate(ta["breaks"])}; wb = {w: i for i, w in enumerate(tb["breaks"])}
        for z in set(za) & set(zb):
            for w in set(wa) & set(wb):
                x, y = ta["price"][za[z]][wa[w]], tb["price"][zb[z]][wb[w]]
                if x and y:
                    r.append(y / x)
    return np.array(r)


def rkey(r):
    return (r.name, r.kind, r.coef, tuple(r.price.tolist()), r.group, r.fuel, repr(r.pred.clauses))


def make_events(inst, rng, n_amount=16, n_resi=4, n_thr=12, n_das=6):
    pool = ups_ratio_pool()
    svcs = inst.services
    ev = []
    for _ in range(n_amount):
        s = svcs[int(rng.integers(len(svcs)))]
        cand = [i for i, r in enumerate(s.surcharges) if r.name != "Resi"]
        ev.append(("amount", s.sid, int(rng.choice(cand)), float(rng.choice(pool))))
    for _ in range(n_resi):
        s = svcs[int(rng.integers(19, len(svcs)) if len(svcs) > 19 else rng.integers(5, 19))]
        while not any(r.name == "Resi" for r in s.surcharges):
            s = svcs[int(rng.integers(5, len(svcs)))]
        ev.append(("resi", s.sid, [r.name for r in s.surcharges].index("Resi"), float(rng.choice(pool))))
    for _ in range(n_thr):
        while True:
            s = svcs[int(rng.integers(len(svcs)))]
            cand = [(i, ci, f) for i, r in enumerate(s.surcharges) for ci, cl in enumerate(r.pred.clauses)
                    for f in cl if f in STEP or f == "V"]
            if cand:
                break
        i, ci, f = cand[int(rng.integers(len(cand)))]
        ev.append(("threshold", s.sid, (i, ci, f), float(rng.choice([-1, 1]))))
    for _ in range(n_das):
        ev.append(("das_list", None, str(rng.choice(["ups", "fedex"])), int(rng.integers(1 << 30))))
    order = rng.permutation(len(ev))
    return [ev[i] for i in order]


def edited_rule(r, kind, arg):
    if kind in ("amount", "resi"):
        return Surcharge(r.name, r.pred, np.round(r.price * arg).astype(np.int64), r.group, r.fuel, r.kind, r.coef)
    _, ci, f = kind
    cls = [dict(c) for c in r.pred.clauses]
    th = cls[ci][f]
    cls[ci][f] = th * (1 + 0.1 * arg) if f == "V" else th + STEP[f] * arg
    return Surcharge(r.name, Pred(cls), r.price.copy(), r.group, r.fuel, r.kind, r.coef)


def das_revision(s0, rng_seed, zcta):
    """New (start, level) arrays: 200 ZIPs added at level 1 and 200 removed (level -> 0)."""
    rng = np.random.default_rng(rng_seed)
    st, lv = s0.das_start, s0.das_level
    keys0 = np.int64(1) * zcta  # destination ZIPs (origin 0 view); a ZIP list applies to every origin
    lvl = s0.das_of(keys0)
    add = rng.choice(zcta[lvl == 0], 200, replace=False); rem = rng.choice(zcta[lvl == 1], 200, replace=False)
    # the list is per destination ZIP: apply to every origin block of the key domain
    los = np.concatenate([o * KEYSPAN + np.r_[add, rem] for o in range(len(ORIGINS))])
    newl = np.concatenate([np.r_[np.full(len(add), 1), np.full(len(rem), 0)] for _ in range(len(ORIGINS))])
    cuts = np.unique(np.r_[st, los, los + 1])
    lev = s0.das_of(cuts).astype(np.int64)
    pos = np.searchsorted(cuts, los)
    lev[pos] = newl
    keep = np.r_[True, lev[1:] != lev[:-1]]
    return cuts[keep], lev[keep], np.r_[add, rem]


def run(iname, reps):
    t0 = time.time()
    zcta = np.array(json.load(open(DATA / "zcta.json")), dtype=np.int64)
    P = concat(parcels_olist(100_000, seed=55), parcels_furniture(100_000, seed=56))
    per_rep = []
    for rep in range(reps):
        inst = INSTS[iname]()
        ix = RQIndex(inst)
        mt = Maintainer(inst, ix, P)
        rng = np.random.default_rng(2026)          # the same stream in every repetition
        events = make_events(inst, rng)
        t = time.thread_time_ns(); top2(inst, P, mt.F0); full = (time.thread_time_ns() - t) / 1e9
        log = []

        def state():
            return mt.c1.copy(), mt.s1.copy(), mt.c2.copy(), mt.s2.copy()

        def restore(x):
            mt.c1[:], mt.s1[:], mt.c2[:], mt.s2[:] = x

        def support_pred(old, changed):
            zone_ok = np.nonzero(old.zone_of(P.key) >= 0)[0]
            Fz = mt.F0[zone_ok].copy()
            tt = billable(old, P.a[zone_ok], Fz[:, FI["V"]]); Fz[:, FI["T"]] = tt
            Fz[:, FI["ZONE"]] = old.zone_of(P.key[zone_ok]); Fz[:, FI["DAS"]] = old.das_of(P.key[zone_ok])
            for p_, m_ in old.min_tiers:
                tt = np.where(p_.eval(Fz), np.maximum(tt, m_), tt); Fz[:, FI["T"]] = tt
            m = np.zeros(len(zone_ok), bool)
            for r in changed:
                m |= r.pred.eval(Fz)
            return zone_ok[m], zone_ok

        mask_cache = {}
        PARCEL = {"L", "M", "S", "G", "V", "A", "RES", "SUMD", "DAY", "MONTH", "ZIP"}

        def support_fast(old, changed, zone_ok):
            """Feature-aware predicate scan: a rule that reads only parcel-level features (and DAS)
            is evaluated on the stored base features, once per predicate (and DAS map), and the mask
            is cached across events; rules reading T or ZONE fall back to the full scan."""
            m = np.zeros(len(P), bool)
            for r in changed:
                fs = {f for cl in r.pred.clauses for f in cl}
                if not fs <= PARCEL | {"DAS"}:
                    U_, _ = support_pred(old, [r]); m[U_] = True; continue
                key = (id(r.pred), id(old.das_start) if "DAS" in fs else None)
                mk = mask_cache.get(key)
                if mk is None:
                    F = mt.F0
                    if "DAS" in fs:
                        F = mt.F0.copy(); F[:, FI["DAS"]] = old.das_of(P.key)
                    mk = r.pred.eval(F); mask_cache[key] = (mk, r.pred)   # keep pred alive so id() stays unique
                else:
                    mk = mk[0]
                m |= mk
            return zone_ok[m[zone_ok]]

        for k, (kind, sid, arg1, arg2) in enumerate(events):
            s0 = state()
            if kind == "das_list":
                carrier = "UPS" if arg1 == "ups" else "FedEx"
                sids = [s.sid for s in inst.services if s.carrier == carrier]
                ref = inst.services[sids[0]]
                newst, newlv, zips = das_revision(ref, arg2, zcta)
                olds = {i: inst.services[i] for i in sids}
                news = {}
                for i in sids:
                    ns = copy.copy(olds[i]); ns.das_start, ns.das_level = newst, newlv; news[i] = ns
                cov = np.zeros(len(P), bool)   # union coverage of the carrier's services (static; untimed for both)
                for i in sids:
                    cov |= olds[i].zone_of(P.key) >= 0
                # predicate level: covered parcels whose destination ZIP changed DAS level
                t = time.thread_time_ns()
                hit = np.isin(P.key % KEYSPAN, zips)
                cand = np.nonzero(hit & cov)[0]
                U = cand[ref.das_of(P.key[cand]) != news[sids[0]].das_of(P.key[cand])]
                for i in sids:
                    inst.services[i] = news[i]
                nU, nR = mt.apply_many(sids, U)
                t_pred = (time.thread_time_ns() - t) / 1e9
                t_fast = t_pred
                s_pred = state(); restore(s0)
                t = time.thread_time_ns()
                Us = np.nonzero(cov)[0]
                mt.apply_many(sids, Us)
                t_svc = (time.thread_time_ns() - t) / 1e9
                cov_n = int(len(Us)); nchanged = len(zips)
            else:
                old = inst.services[sid]
                new = copy.copy(old)
                new.surcharges = list(old.surcharges)
                if kind == "threshold":
                    i = arg1[0]; new.surcharges[i] = edited_rule(old.surcharges[i], ("thr",) + tuple(arg1[1:]), arg2)
                else:
                    i = arg1; new.surcharges[i] = edited_rule(old.surcharges[i], kind, arg2)
                t = time.thread_time_ns()
                ko = collections.Counter(rkey(r) for r in old.surcharges); kn = collections.Counter(rkey(r) for r in new.surcharges)
                changed = [r for r in old.surcharges if ko[rkey(r)] > kn[rkey(r)]] + [r for r in new.surcharges if kn[rkey(r)] > ko[rkey(r)]]
                t_diff = (time.thread_time_ns() - t) / 1e9
                t = time.thread_time_ns()
                U, zone_ok = support_pred(old, changed)
                t_sup = (time.thread_time_ns() - t) / 1e9
                t = time.thread_time_ns()
                zone_ok2 = np.nonzero(old.zone_of(P.key) >= 0)[0]
                U2 = support_fast(old, changed, zone_ok2)
                t_sup_fast = (time.thread_time_ns() - t) / 1e9
                assert np.array_equal(U, U2), "fast predicate support differs"
                inst.services[sid] = new
                t = time.thread_time_ns()
                nU, nR = mt.apply(sid, U)
                t_app = (time.thread_time_ns() - t) / 1e9
                t_pred = t_diff + t_sup + t_app
                t_fast = t_diff + t_sup_fast + t_app
                s_pred = state(); restore(s0)
                t = time.thread_time_ns()
                ko = collections.Counter(rkey(r) for r in old.surcharges); kn = collections.Counter(rkey(r) for r in new.surcharges)
                zone_ok = np.nonzero(old.zone_of(P.key) >= 0)[0]
                mt.apply(sid, zone_ok)
                t_svc = (time.thread_time_ns() - t) / 1e9
                cov_n = int(len(zone_ok)); nchanged = len(changed)
                sids = [sid]
            same = all((x == y).all() for x, y in zip(state(), s_pred))
            name = (inst.services[sids[0]].surcharges[arg1 if kind in ("amount", "resi") else arg1[0]].name
                    if kind != "das_list" else f"DAS list {arg1}")
            log.append({"kind": kind, "rule": name, "services": len(sids), "support": int(nU), "coverage": cov_n,
                        "support_share": nU / max(1, cov_n), "recomputed": int(nR), "changed": nchanged,
                        "pred_s": t_pred, "pred_fast_s": t_fast, "service_s": t_svc, "pred_equals_service": bool(same)})
        c1, s1, c2, s2 = top2(inst, P, mt.F0)
        exact = bool((c1 == mt.c1).all() and (s1 == mt.s1).all() and (c2 == mt.c2).all() and (s2 == mt.s2).all())
        per_rep.append({"full_s": full, "events": log, "exact_final_state": exact,
                        "pred_equals_service_all": all(e["pred_equals_service"] for e in log), "load": load()})
        print(iname, "rep", rep, "full", round(full, 2), "pred total", round(sum(e["pred_s"] for e in log), 3),
              "pred_fast total", round(sum(e["pred_fast_s"] for e in log), 3),
              "svc total", round(sum(e["service_s"] for e in log), 3), "exact", exact, per_rep[-1]["pred_equals_service_all"], flush=True)
    ev0 = per_rep[0]["events"]
    E = len(ev0)
    med = lambda key, j: float(np.median([r["events"][j][key] for r in per_rep]))
    events = [{**{k: v for k, v in ev0[j].items() if k not in ("pred_s", "pred_fast_s", "service_s")},
               "pred_s": med("pred_s", j), "pred_fast_s": med("pred_fast_s", j), "service_s": med("service_s", j)} for j in range(E)]
    tot_pred = [sum(e["pred_s"] for e in r["events"]) for r in per_rep]
    tot_fast = [sum(e["pred_fast_s"] for e in r["events"]) for r in per_rep]
    tot_svc = [sum(e["service_s"] for e in r["events"]) for r in per_rep]
    tot_full = [r["full_s"] * E for r in per_rep]
    by_kind = {}
    for kind in sorted({e["kind"] for e in events}):
        ee = [e for e in events if e["kind"] == kind]
        by_kind[kind] = {"events": len(ee), "support_share_median": float(np.median([e["support_share"] for e in ee])),
                         "support_share_mean": float(np.mean([e["support_share"] for e in ee])),
                         "pred_s_total": float(sum(e["pred_s"] for e in ee)), "service_s_total": float(sum(e["service_s"] for e in ee)),
                         "pred_fast_s_total": float(sum(e["pred_fast_s"] for e in ee)),
                         "speedup_service_over_pred_fast": float(sum(e["service_s"] for e in ee) / max(1e-12, sum(e["pred_fast_s"] for e in ee))),
                         "speedup_service_over_pred": float(sum(e["service_s"] for e in ee) / max(1e-12, sum(e["pred_s"] for e in ee)))}
    return {"n": len(INSTS[iname]().services), "N": len(P), "events": E, "reps": reps,
            "total_pred_s": summ(tot_pred), "total_service_s": summ(tot_svc), "total_full_s": summ(tot_full),
            "full_recompute_s": summ([r["full_s"] for r in per_rep]),
            "speedup_service_over_pred": summ(np.array(tot_svc) / np.array(tot_pred)),
            "total_pred_fast_s": summ(tot_fast),
            "speedup_service_over_pred_fast": summ(np.array(tot_svc) / np.array(tot_fast)),
            "speedup_full_over_pred_fast": summ(np.array(tot_full) / np.array(tot_fast)),
            "speedup_full_over_pred": summ(np.array(tot_full) / np.array(tot_pred)),
            "support_share_median": float(np.median([e["support_share"] for e in events])),
            "support_share_mean": float(np.mean([e["support_share"] for e in events])),
            "recomputed_total": int(sum(e["recomputed"] for e in events)),
            "exact_final_state_all_reps": all(r["exact_final_state"] for r in per_rep),
            "pred_equals_service_all_events_all_reps": all(r["pred_equals_service_all"] for r in per_rep),
            "by_kind": by_kind, "per_event_median": events,
            "factor_source": "per-cell ratios of UPS 2026 / 2025 published daily base rates (ups_rates_2026.json / ups_rates.json)",
            "factor_pool_quantiles": {q: float(np.percentile(ups_ratio_pool(), q)) for q in (5, 25, 50, 75, 95)},
            "load_per_rep": [r["load"] for r in per_rep], "uptime_end": uptime(), "wall_s": time.time() - t0}


if __name__ == "__main__":
    iname = sys.argv[1]; reps = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    r = run(iname, reps)
    r["_env"] = {"python": platform.python_version(), "numpy": np.__version__, "host": platform.node(),
                 "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
                 "timer": "time.thread_time_ns", "written": time.strftime("%Y-%m-%d %H:%M:%S")}
    json.dump(r, open(os.path.join(OUT, f"rule_stream_{iname}.json"), "w"), indent=1)
    print(json.dumps({k: r[k] for k in ("total_pred_s", "total_service_s", "speedup_service_over_pred", "support_share_median",
                                         "exact_final_state_all_reps", "pred_equals_service_all_events_all_reps")}), flush=True)
