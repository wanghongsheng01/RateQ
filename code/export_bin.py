"""Serialise a built Instance, its Python RQIndex (for cross-checking the C++ build), parcel
workloads and the Python brute-force answers into flat little-endian binaries for cpp/.

Usage (from code/):
  PYTHONPATH=. ../.venv/bin/python export_bin.py pub2   -> ../bin/pub2/
  PYTHONPATH=. ../.venv/bin/python export_bin.py erp    -> ../_private/bin/   (de-identified data stays in _private)

File formats (all integers int64 '<q', all reals float64 '<d'; arrays are length-prefixed):
  <inst>.rqi   : magic 'RQI1', array pool, services, fuel schedule, month table
  <inst>.idx   : magic 'RQX1', Python RQIndex arrays (U, sig_of, groups: sids, E, loc, ORD, LB)
  <wl>_s<seed>.par : magic 'RQP1', N, key, a, L, M, S, res, day
  <inst>__<wl>_s<seed>.tru : magic 'RQT1', N, best cost (brute_vec), sid (brute_vec), brute1 flag + brute1 cost/sid
"""
import json
import os
import struct
import sys
import time

import numpy as np

from rateq.model import Pred, GPred, FI, INF, _MONTH, _D0, brute_vec, Parcels
from rateq.index import RQIndex, Compiled, fuel_fn, brute1

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OPC = {">": 0, ">=": 1, "<": 2, "<=": 3, "in": 4, "notin": 5, "ranges": 6}
RND = {"carrier": 0, "erp_style": 1, "erp": 2}
KIND = {"fixed": 0, "per_weight": 1, "pct_base": 2, "pct_total": 3}


class W:
    def __init__(self, path, magic):
        self.f = open(path, "wb")
        self.f.write(magic)

    def i(self, x):
        self.f.write(struct.pack("<q", int(x)))

    def d(self, x):
        self.f.write(struct.pack("<d", float(x)))

    def ai(self, a):
        a = np.ascontiguousarray(np.asarray(a), dtype="<i8")
        self.i(a.size); self.f.write(a.tobytes())

    def ad(self, a):
        a = np.ascontiguousarray(np.asarray(a, dtype=np.float64), dtype="<f8")
        self.i(a.size); self.f.write(a.tobytes())

    def close(self):
        self.f.close()


def write_pred(w, p):
    if p is None:
        w.i(-1); return
    if isinstance(p, GPred):
        cls = p.clauses
    else:
        cls = [[(FI[f], ">", th) for f, th in c.items()] for c in p.clauses]
    w.i(len(cls))
    for cl in cls:
        w.i(len(cl))
        for fi, op, v in cl:
            w.i(fi); w.i(OPC[op])
            if op in ("in", "notin"):
                w.ad(np.asarray(v, dtype=np.float64).ravel())
            elif op == "ranges":
                w.ad(v[0]); w.ad(v[1])
            else:
                w.d(v)


def write_instance(inst, path):
    pool, pool_arr = {}, []

    def reg(a, kind):  # kind 0 = int64, 1 = float64
        if id(a) not in pool:
            pool[id(a)] = len(pool_arr); pool_arr.append((kind, a))
        return pool[id(a)]
    refs = []
    for s in inst.services:
        r = {"zstart": reg(s.zstart, 0), "zzone": reg(s.zzone, 0), "breaks": reg(s.breaks, 1)}
        if s.das_start is not None:
            r["das_s"] = reg(s.das_start, 0); r["das_l"] = reg(s.das_level, 0)
        refs.append(r)
    w = W(path, b"RQI1")
    w.i(len(pool_arr))
    for kind, a in pool_arr:
        w.i(kind)
        (w.ai if kind == 0 else w.ad)(a)
    w.i(len(inst.services))
    for s, r in zip(inst.services, refs):
        w.i(s.sid); w.i(s.fuel_class); w.i(RND[s.rounding]); w.i(int(bool(s.extrapolate)))
        w.d(s.limit_weight); w.d(s.divisor); w.d(s.dim_thr); w.d(s.erp_q)
        w.i(0 if s.erp_carry == "ceil" else 1); w.i(s.erp_mode)
        w.i(r["zstart"]); w.i(r["zzone"]); w.i(r["breaks"])
        w.i(r.get("das_s", -1)); w.i(r.get("das_l", -1))
        pr = np.asarray(s.price, dtype=np.int64)
        w.i(pr.shape[0]); w.i(pr.shape[1]); w.ai(pr.ravel())
        write_pred(w, s.oversize2); w.d(s.oversize2_min)
        write_pred(w, s.oversize); w.d(s.oversize_min)
        write_pred(w, s.inelig)
        w.i(len(s.surcharges))
        for x in s.surcharges:
            write_pred(w, x.pred); w.ai(np.asarray(x.price, dtype=np.int64)); w.i(x.group); w.i(int(bool(x.fuel)))
            w.i(KIND[x.kind]); w.d(x.coef)
    fs = inst.fuel
    w.i(len(fs.starts))
    for fc in fs.starts:
        w.i(fc); w.ai(fs.starts[fc]); w.ai(fs.bps[fc])
    w.i(_D0); w.ai(_MONTH)
    w.close()


def write_index(ix, path):
    w = W(path, b"RQX1")
    w.ai(ix.U); w.ai(ix.sig_of)
    w.i(len(ix.groups))
    for g in ix.groups:
        w.ai(g["sids"]); w.ad(g["E"]); w.ai(g["loc"])
        w.i(g["ORD"].shape[0]); w.i(g["ORD"].shape[1]); w.i(g["ORD"].shape[2])
        w.ai(g["ORD"].ravel()); w.ai(g["LB"].ravel())
    w.close()


def _complete(path, size):
    return os.path.exists(path) and os.path.getsize(path) == size


def write_parcels(P, path):
    if _complete(path, 4 + 8 + 7 * (8 + 8 * len(P))):  # resume: identical content already on disk
        return
    w = W(path, b"RQP1")
    w.i(len(P))
    w.ai(P.key); w.ad(P.a); w.ad(P.L); w.ad(P.M); w.ad(P.S); w.ad(P.res); w.ai(P.day)
    w.close()


def args(P, i):
    return (int(P.key[i]), float(P.a[i]), float(P.L[i]), float(P.M[i]), float(P.S[i]), float(P.res[i]), int(P.day[i]))


def write_truth(inst, P, path, with_brute1):
    n = len(P)
    base = 4 + 8 + 2 * (8 + 8 * n) + 8
    if _complete(path, base) or _complete(path, base + 2 * (8 + 8 * n)):  # with or without the brute1 block
        return {"resumed": True}
    t0 = time.perf_counter()
    best, arg = brute_vec(inst, P)
    tv = time.perf_counter() - t0
    w = W(path, b"RQT1")
    w.i(len(P)); w.ai(best); w.ai(arg)
    info = {"brute_vec_s": tv}
    if with_brute1:
        comp, fuel = [Compiled(s) for s in inst.services], fuel_fn(inst)
        b1 = np.empty(len(P), np.int64); a1 = np.empty(len(P), np.int64)
        for i in range(len(P)):
            b1[i], a1[i] = brute1(comp, fuel, *args(P, i))
        info["brute1_vs_brute_vec_mismatch"] = int(((b1 != best) | (a1 != arg)).sum())
        w.i(1); w.ai(b1); w.ai(a1)
    else:
        w.i(0)
    w.close()
    return info


E1_SEEDS = [11, 12, 13, 14, 15]
E2_SEEDS = [22, 23, 24, 25, 26]


def pub2(names):
    from rateq.instances import build_pub2, build_pub2_scaled, parcels_olist, parcels_furniture
    out = os.path.join(ROOT, "bin", "pub2"); os.makedirs(out, exist_ok=True)
    WLS = {"olist": parcels_olist, "furn": parcels_furniture}
    wl = {}
    for w_, fn in WLS.items():
        for s in E1_SEEDS:
            wl[(w_, "e1", s)] = fn(1000, seed=s)
        for s in E2_SEEDS:
            wl[(w_, "e2", s)] = fn(100000, seed=s)
    for (w_, e, s), P in wl.items():
        write_parcels(P, os.path.join(out, f"{w_}_{e}_s{s}.par"))
    from rateq.model import Instance

    def first(n):  # realistic per-warehouse scale: the first n official services of PUB2 (sids 0..n-1)
        inst = build_pub2()
        return Instance(f"PUB2n{n}", inst.services[:n], inst.fuel, inst.n_zones)
    INSTS = {"PUB2": build_pub2, "PUB2x60": lambda: build_pub2_scaled(60), "PUB2x240": lambda: build_pub2_scaled(240),
             "PUB2x1000": lambda: build_pub2_scaled(1000),
             "PUB2n11": lambda: first(11),                 # 5 USPS + 6 UPS official services
             "PUB2n31": lambda: build_pub2_scaled(31)}     # 19 official services + 12 seeded contract variants
    for name in names:
        mp = os.path.join(out, f"export_meta_{name}.json")
        meta = json.load(open(mp)) if os.path.exists(mp) else {}
        inst = INSTS[name]()
        if os.environ.get("SKIP_EXISTING_INDEX") and os.path.exists(os.path.join(out, f"{name}.idx")):
            bt = meta.get(name, {}).get("py_build_s")  # instance + index already exported by an earlier run
        else:
            write_instance(inst, os.path.join(out, f"{name}.rqi"))
            t0 = time.perf_counter(); ix = RQIndex(inst); bt = time.perf_counter() - t0
            write_index(ix, os.path.join(out, f"{name}.idx"))
        meta[name] = {"n": len(inst.services), "py_build_s": bt, "truth": meta.get(name, {}).get("truth", {})}
        for (w_, e, s), P in wl.items():
            info = write_truth(inst, P, os.path.join(out, f"{name}__{w_}_{e}_s{s}.tru"), with_brute1=(e == "e1" and str(s) in os.environ.get("BRUTE1_SEEDS", "11,12,13,14,15").split(",")))
            meta[name]["truth"][f"{w_}_{e}_s{s}"] = info
            if info.get("resumed"):
                continue
            print(name, w_, e, s, info, flush=True)
        json.dump(meta, open(mp, "w"), indent=1)


def erp():
    from rateq.erp import Compiler
    out = os.path.join(ROOT, "_private", "bin"); os.makedirs(out, mode=0o700, exist_ok=True)
    c = Compiler("USA", "USD", "lb")
    inst = c.build()
    P, _meta = c.parcels(inst)
    name = "ERP"
    write_instance(inst, os.path.join(out, f"{name}.rqi"))
    t0 = time.perf_counter(); ix = RQIndex(inst); bt = time.perf_counter() - t0
    write_index(ix, os.path.join(out, f"{name}.idx"))
    meta = {name: {"n": len(inst.services), "N": len(P), "py_build_s": bt, "truth": {}}}
    # all real parcels (verification), plus E1 samples of 1000 and E2 samples of 100,000 parcels
    wl = {("all", "all", 0): P}
    for s in E1_SEEDS:
        wl[("erp", "e1", s)] = P.subset(np.random.default_rng(s).choice(len(P), 1000, replace=False))
    for s in E2_SEEDS:
        wl[("erp", "e2", s)] = P.subset(np.random.default_rng(s).choice(len(P), 100000, replace=False))
    for (w_, e, s), Q in wl.items():
        fn = f"{w_}_{e}_s{s}" if w_ != "all" else "all"
        write_parcels(Q, os.path.join(out, f"{fn}.par"))
        info = write_truth(inst, Q, os.path.join(out, f"{name}__{fn}.tru"), with_brute1=(e in ("e1", "all")))
        meta[name]["truth"][fn] = info
        print(name, fn, info, flush=True)
    json.dump(meta, open(os.path.join(out, "export_meta.json"), "w"), indent=1)
    for f in os.listdir(out):
        os.chmod(os.path.join(out, f), 0o600)


if __name__ == "__main__":
    what = sys.argv[1]
    if what == "pub2":
        pub2(sys.argv[2].split(",") if len(sys.argv) > 2 else ["PUB2", "PUB2x60", "PUB2x240", "PUB2x1000"])
    elif what == "erp":
        erp()
