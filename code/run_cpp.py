"""Driver for cpp/rq: exactness verification and E1/E2 timing of the C++ engine.

  PYTHONPATH=. ../.venv/bin/python run_cpp.py verify pub2 [INST,...]
  PYTHONPATH=. ../.venv/bin/python run_cpp.py time   pub2 [INST,...]
  PYTHONPATH=. ../.venv/bin/python run_cpp.py verify erp | time erp
  PYTHONPATH=. ../.venv/bin/python run_cpp.py tables

Writes results/cpp_verify_<set>.json, results/cpp_E1E2_<set>.json (aggregate timings only) and
results/tables_cpp.md. Before every timing unit the 1-minute load average is checked; if it is above
LOAD_MAX the driver waits up to WAIT_S seconds, then measures anyway and records the load.
"""
import json
import math
import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RQ = os.path.join(ROOT, "cpp", "rq")
RES = os.path.join(ROOT, "results")
LOAD_MAX = float(os.environ.get("LOAD_MAX", "3.0"))
WAIT_S = float(os.environ.get("WAIT_S", "60"))
E1_SEEDS = [int(x) for x in os.environ.get("E1_SEEDS", "11,12,13,14,15").split(",")]
E2_SEEDS = [int(x) for x in os.environ.get("E2_SEEDS", "22,23,24,25,26").split(",")]
SETS = {
    "pub2": {"dir": os.path.join(ROOT, "bin", "pub2"), "insts": ["PUB2", "PUB2x60", "PUB2x240", "PUB2x1000"],
             "wls": ["olist", "furn"]},
    "pub2r": {"dir": os.path.join(ROOT, "bin", "pub2"), "insts": ["PUB2n11", "PUB2n31"], "wls": ["olist", "furn"]},
    "erp": {"dir": os.path.join(ROOT, "_private", "bin"), "insts": ["ERP"], "wls": ["erp"]},
}
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}


def load1():
    return os.getloadavg()[0]


def wait_load():
    t0 = time.time(); l0 = load1()
    while load1() > LOAD_MAX and time.time() - t0 < WAIT_S:
        time.sleep(5)
    return {"load1_at_request": round(l0, 2), "load1_at_start": round(load1(), 2), "waited_s": round(time.time() - t0, 1)}


def run(args):
    out = subprocess.run([RQ] + args, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr)
    return [json.loads(l) for l in out.stdout.splitlines() if l.strip()], out.stderr


def ci(xs):
    k = len(xs); m = sum(xs) / k
    if k < 2:
        return {"mean": m, "ci95": None, "k": k}
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (k - 1))
    return {"mean": m, "ci95": T975[k - 1] * sd / math.sqrt(k), "k": k, "values": xs}


def verify(setname, insts):
    S = SETS[setname]; d = S["dir"]
    path = os.path.join(RES, f"cpp_verify_{setname}.json")
    out = json.load(open(path)) if os.path.exists(path) else {}
    for inst in insts:
        args = [os.path.join(d, f"{inst}.rqi"), os.path.join(d, f"{inst}.idx")]
        files = []
        for wl in S["wls"]:
            for e, seeds in (("e1", E1_SEEDS), ("e2", E2_SEEDS)):
                for s in seeds:
                    files.append(f"{wl}_{e}_s{s}")
        if setname == "erp":
            files.append("all")
        for f in files:
            args += [os.path.join(d, f"{f}.par"), os.path.join(d, f"{inst}__{f}.tru")]
        t0 = time.time()
        lines, err = run(["verify"] + args)
        rec = {"index": lines[0]["index"], "runs": {}, "wall_s": time.time() - t0, "stderr": err[-2000:]}
        for l in lines[1:]:
            rec["runs"][os.path.basename(l.pop("par")).replace(".par", "")] = l
        tot = {k: 0 for k in ("B1", "B1s", "B1v", "RQ_q1", "RQ_batch")}
        tot1 = dict(tot); N = 0; N1 = 0
        for name, l in rec["runs"].items():
            N += l["N"]
            for k in tot:
                tot[k] += l["mismatch_vs_py_brute_vec"][k]
            if l["has_py_brute1"]:
                N1 += l["N"]
                for k in tot:
                    tot1[k] += l["mismatch_vs_py_brute1"][k]
        rec["summary"] = {"parcels": N, "mismatch_vs_py_brute_vec": tot, "parcels_with_py_brute1": N1,
                          "mismatch_vs_py_brute1": tot1, "index_identical_to_python": lines[0]["index"]["differs_from_python"] == 0}
        if setname == "erp":  # aggregate only: no per-parcel data, no indices of parcels
            for l in rec["runs"].values():
                l.pop("first_bad", None)
            rec["stderr"] = "(suppressed for ERP)" if err else ""
        out[inst] = rec
        json.dump(out, open(path, "w"), indent=1)
        print(inst, rec["summary"], round(rec["wall_s"], 1), "s", flush=True)


def timing(setname, insts):
    S = SETS[setname]; d = S["dir"]
    path = os.path.join(RES, f"cpp_E1E2_{setname}.json")
    out = json.load(open(path)) if os.path.exists(path) else {}
    out["_env"] = {"cpu": "Apple M4 (4P+6E cores)", "threads": 1, "compiler": subprocess.run(["clang++", "--version"], capture_output=True, text=True).stdout.splitlines()[0],
                   "flags": "-O3 -march=native -std=c++17 -ffp-contract=off; QOS_CLASS_USER_INTERACTIVE", "LOAD_MAX": LOAD_MAX, "WAIT_S": WAIT_S,
                   "E1": "1000 queries per seed; one warm-up pass per method; then every query is timed once per method with the three methods interleaved per query; per-query thread CPU time (CLOCK_THREAD_CPUTIME_ID, 41.67 ns tick) minus the calibrated clock overhead (median of an empty measured body), and wall time; stream = 1000 back-to-back queries, best of 3",
                   "E2": "100,000 parcels per seed; one warm-up run per method, then E2_REPS repetitions with all methods interleaved inside each repetition; median per method reported; primary metric = thread CPU time (CLOCK_THREAD_CPUTIME_ID), wall clock also recorded"}
    for inst in insts:
        rqi = os.path.join(d, f"{inst}.rqi")
        for wl in S["wls"]:
            key = f"{inst}/{wl}"
            rec = out.get(key, {"E1": {}, "E2": {}})
            for s in E1_SEEDS:
                ld = wait_load()
                lines, _ = run(["e1", rqi, os.path.join(d, f"{wl}_e1_s{s}.par")])
                r = lines[0]; r.update(ld); r["load1_after"] = round(load1(), 2)
                rec["E1"][str(s)] = r
                print(key, "E1", s, {m: round(r[m]["p50_ns"]) for m in ("B1", "B1s", "RQ_q1")}, ld, flush=True)
            for s in E2_SEEDS:
                ld = wait_load()
                lines, _ = run(["e2", rqi, os.path.join(d, f"{wl}_e2_s{s}.par"), os.environ.get("E2_REPS", "5")])
                r = lines[0]; r.update(ld); r["load1_after"] = round(load1(), 2)
                rec["E2"][str(s)] = r
                print(key, "E2", s, {m: round(r[m]["s_median"], 4) for m in ("B1", "B1s", "B1v", "RQ_batch", "RQ_q1loop") if m in r}, ld, flush=True)
            rec["n"] = rec["E1"][str(E1_SEEDS[0])].get("n")
            out[key] = rec
            out[key]["agg"] = aggregate(rec)
            json.dump(out, open(path, "w"), indent=1)


def aggregate(rec):
    a = {"E1": {}, "E2": {}}
    e1 = [rec["E1"][k] for k in sorted(rec["E1"])]
    Q = ("p50_ns", "p95_ns", "p99_ns", "mean_ns", "cpu_p50_ns", "cpu_p95_ns", "cpu_p99_ns", "cpu_mean_ns", "stream_mean_ns", "stream_cpu_mean_ns")
    for m in ("B1", "B1s", "RQ_q1"):
        for q in Q:
            if all(q in r[m] for r in e1):
                a["E1"][f"{m}_{q}"] = ci([r[m][q] for r in e1])
    for b in ("B1", "B1s"):
        for q in Q:
            if all(q in r[b] for r in e1):
                a["E1"][f"speedup_{q}_{b}"] = ci([r[b][q] / r["RQ_q1"][q] for r in e1])
    a["E1"]["load1"] = [r["load1_at_start"] for r in e1]
    e2 = [rec["E2"][k] for k in sorted(rec["E2"])]
    for m in ("B1", "B1s", "B1v", "RQ_batch", "RQ_q1loop"):
        if not all(m in r for r in e2):
            continue
        a["E2"][f"{m}_s_per_100k"] = ci([r[m]["cpu_s_median"] * 100000 / r["N"] for r in e2])      # thread CPU time
        a["E2"][f"{m}_wall_s_per_100k"] = ci([r[m]["s_median"] * 100000 / r["N"] for r in e2])    # wall clock
    for b in ("B1", "B1s", "B1v"):
        if not all(b in r for r in e2):
            continue
        for q in ("RQ_batch", "RQ_q1loop"):
            a["E2"][f"speedup_{b}_vs_{q}"] = ci([r[b]["cpu_s_median"] / r[q]["cpu_s_median"] for r in e2])
            a["E2"][f"speedup_wall_{b}_vs_{q}"] = ci([r[b]["s_median"] / r[q]["s_median"] for r in e2])
    a["E2"]["evals_per_parcel_RQ_batch"] = sum(r["RQ_batch"]["evals_per_parcel"] for r in e2) / len(e2)
    a["E2"]["evals_per_parcel_RQ_q1"] = sum(r["RQ_q1loop"]["evals_per_parcel"] for r in e2) / len(e2)
    a["E2"]["load1"] = [r["load1_at_start"] for r in e2]
    a["E2"]["measured_on"] = sorted({r["N"] for r in e2})
    return a


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "tables":
        import make_cpp_tables
        make_cpp_tables.main()
    else:
        setname = sys.argv[2]
        insts = sys.argv[3].split(",") if len(sys.argv) > 3 else SETS[setname]["insts"]
        (verify if cmd == "verify" else timing)(setname, insts)
