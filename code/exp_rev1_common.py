"""Shared helpers for the round-1 revision experiments (exp_rev1_*.py).

Timing: time.thread_time_ns (CPU time of the measuring thread; robust to the machine load),
5 repetitions with methods interleaved per repetition; we report the median over repetitions,
the mean with a 95% t-confidence interval (t_{0.975,4} = 2.776 for 5 reps), min and max.
Every run records os.getloadavg() (the `uptime` load averages) before and after.
"""
import json, os, platform, subprocess, time
import numpy as np
from rateq.instances import build_pub2, build_pub2_scaled, parcels_olist, parcels_furniture, _pack, DATA, ORIGINS, KEYSPAN
from rateq.model import Parcels

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "results", "rev1")
os.makedirs(OUT, exist_ok=True)
REPS = int(os.environ.get("REV1_REPS", "5"))
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}

INSTS = {"PUB2": build_pub2, "PUB2x60": lambda: build_pub2_scaled(60), "PUB2x240": lambda: build_pub2_scaled(240),
         "PUB2x1000": lambda: build_pub2_scaled(1000)}


def tns():
    return time.thread_time_ns()


def load():
    return [round(x, 2) for x in os.getloadavg()]


def uptime():
    try:
        return subprocess.run(["uptime"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return str(load())


def summ(xs):
    """Summary over repetitions: median, mean, 95% t-CI of the mean, min, max, reps."""
    x = np.asarray(xs, dtype=float)
    n = len(x)
    m = float(x.mean())
    if n > 1:
        h = T975.get(n - 1, 1.96) * float(x.std(ddof=1)) / np.sqrt(n)
    else:
        h = float("nan")
    return {"median": float(np.median(x)), "mean": m, "ci95": [m - h, m + h], "min": float(x.min()),
            "max": float(x.max()), "reps": n}


_ZP = None


def _zpop():
    global _ZP
    if _ZP is None:
        d = json.load(open(os.path.join(DATA, "zcta_pop_acs5y2023.json")))
        z = np.array([int(k) for k in d], dtype=np.int64)
        p = np.array([float(v) for v in d.values()])
        _ZP = (z, p / p.sum())
    return _ZP


_SH = None


def _shapes():
    global _SH
    if _SH is None:
        f = np.load(os.path.join(ROOT, "_private", "rev1_ent_shapes.npz"))
        _SH = {k: f[k] for k in ("a", "L", "M", "S", "res")}
    return _SH


def parcels_entgeo(N, seed=0):
    """ent-geo: (weight, dimensions, residential flag) drawn jointly from the enterprise's real US
    parcels (with replacement), destination ZCTA drawn with probability proportional to its
    population (ACS 2023 5-year B01003), origin uniform over the five origins, ship day uniform."""
    rng = np.random.default_rng(seed)
    sh = _shapes()
    i = rng.integers(0, len(sh["a"]), size=N)
    z, p = _zpop()
    dest = z[rng.choice(len(z), size=N, p=p)]
    o = rng.integers(0, len(ORIGINS), size=N)
    key = o * KEYSPAN + dest
    day = rng.integers(0, 365, size=N)
    return Parcels(key.astype(np.int64), sh["a"][i].astype(float), sh["L"][i].astype(float), sh["M"][i].astype(float),
                   sh["S"][i].astype(float), sh["res"][i].astype(float), day.astype(np.int64))


WLS = {"olist": parcels_olist, "furn": parcels_furniture, "entgeo": parcels_entgeo}


def args(P, i):
    return (int(P.key[i]), float(P.a[i]), float(P.L[i]), float(P.M[i]), float(P.S[i]), float(P.res[i]), int(P.day[i]))


def concat(*Ps):
    return Parcels(*(np.concatenate([getattr(P, f) for P in Ps]) for f in ("key", "a", "L", "M", "S", "res", "day")))


def _cpu():
    if platform.system() == "Darwin":
        return "Apple M4 (10 cores, shared with other sessions)"
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip() + f" ({os.cpu_count()} logical CPUs)"
    except OSError:
        pass
    return platform.processor()


def save(name, obj):
    obj["_env"] = {"python": platform.python_version(), "numpy": np.__version__, "host": platform.node(),
                   "machine": platform.machine(), "cpu": _cpu(), "pinned_cpus": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
                   "threads": 1, "timer": "time.thread_time_ns", "reps": REPS, "written": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "uptime_at_save": uptime()}
    path = os.path.join(OUT, f"{name}.json")
    json.dump(obj, open(path, "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    return path
