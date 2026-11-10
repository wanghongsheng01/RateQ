"""results/cpp_E1E2_*.json + cpp_verify_*.json (+ Python pilot numbers) -> results/tables_cpp.md.

Main tables (stable format for the paper): one E1 table and one E2 table with columns
instance | workload | n | brute | RQ_base | speedup | agreement | RQ_base evals/parcel.
All times are thread CPU time.
"""
import json
import os

from run_cpp import aggregate

RES = os.path.join(os.path.dirname(__file__), "..", "results")
INSTS = ["PUB2n11", "PUB2", "PUB2n31", "PUB2x60", "PUB2x240", "PUB2x1000", "ERP"]
SETS = ("pub2r", "pub2", "erp")
DESC = {"PUB2n11": "first 11 official PUB2 services (5 USPS + 6 UPS)",
        "PUB2": "19 official services (5 USPS, 7 UPS, 7 FedEx)",
        "PUB2n31": "19 official services + 12 seeded contract variants (build_pub2_scaled(31))",
        "PUB2x60": "build_pub2_scaled(60)", "PUB2x240": "build_pub2_scaled(240)", "PUB2x1000": "build_pub2_scaled(1000)",
        "ERP": "real enterprise rate cards (10 US services), real parcels"}


def J(name):
    p = os.path.join(RES, name)
    return json.load(open(p)) if os.path.exists(p) else {}


def f(x, d=2):
    if x is None:
        return "—"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    return f"{x:.{d}f}"


def mc(c, scale=1.0, d=2):
    """mean ± 95% CI half-width (Student t over seeds)."""
    if c is None:
        return "—"
    if c.get("ci95") is None:
        return f(c["mean"] * scale, d)
    return f"{f(c['mean'] * scale, d)} ± {f(c['ci95'] * scale, d)}"


def agreement(ver, inst, wl):
    v = ver.get(inst)
    if not v:
        return "—"
    N = bad = 0
    for name, r in v["runs"].items():
        if name.startswith(wl + "_") or (wl == "erp" and name == "all"):
            N += r["N"]
            m = r["mismatch_vs_py_brute_vec"]
            bad += max(m.values())
    return f"{(N - bad) / N:.3f} ({N - bad:,}/{N:,})" if N else "—"


def evals(ver, inst, wl, kind):
    v = ver.get(inst)
    if not v:
        return None
    tot = N = 0
    for name, r in v["runs"].items():
        if name.startswith(f"{wl}_{kind}_"):
            tot += r["evals_q1" if kind == "e1" else "evals_batch"] * r["N"]; N += r["N"]
    return tot / N if N else None


def main():
    rows, loads, mac_rows, srv_loads = [], [], [], []
    VER = {}
    srv_env = None

    def collect(T, machine, dest, ld):
        for k, r in T.items():
            if k.startswith("_") or not r.get("E1") or not r.get("E2"):
                continue
            inst, wl = k.split("/")
            r["agg"] = aggregate(r); r["machine"] = machine
            ld += [x.get("load1_at_start", float("nan")) for x in r["E1"].values()] + [x.get("load1_at_start", float("nan")) for x in r["E2"].values()]
            dest.append((INSTS.index(inst), wl != "olist", inst, wl, r))
    for setname in SETS:
        VER.update(J(f"cpp_verify_{setname}.json"))
        mac = J(f"cpp_E1E2_{setname}.json")
        srv = J(f"cpp_E1E2_{setname}_server.json") if setname in ("pub2", "pub2r") else {}
        if srv:  # PUB2 family: server numbers are primary, Mac numbers secondary
            srv_env = srv.get("_env", srv_env)
            collect(srv, "server", rows, srv_loads)
            collect(mac, "Mac", mac_rows, loads)
        else:
            collect(mac, "Mac", rows, loads)
    srv_keys = {(x[2], x[3]) for x in rows if x[4]["machine"] == "server"}
    rows += [x for x in mac_rows if (x[2], x[3]) not in srv_keys]  # Mac fills any gap the server did not cover
    mac_rows = [x for x in mac_rows if (x[2], x[3]) in srv_keys]
    rows.sort(key=lambda x: (x[0], x[1])); mac_rows.sort(key=lambda x: (x[0], x[1]))
    loads = [x for x in loads if x == x]; srv_loads = [x for x in srv_loads if x == x]
    py1, py2 = J("pilot_E1_pub2.json"), J("pilot_E2_pub2.json")
    erp_py = J("erp_pilot_USA.json")
    lr = f"{min(loads):.1f}–{max(loads):.1f}" if loads else "—"
    slr = f"{min(srv_loads):.1f}–{max(srv_loads):.1f}" if srv_loads else "—"
    L = ["# C++ core: E1 (Q1 single query) and E2 (Q2 batch)", "",
         f"**Method.** Mac: Apple M4 (10 cores, shared with other sessions). C++17, single thread, "
         f"`clang++ -O3 -march=native -ffp-contract=off`, process QoS user-interactive (prefers performance cores). All times are **thread CPU time** "
         f"(CLOCK_THREAD_CPUTIME_ID). Methods are interleaved per query (E1) and inside every repetition (E2), so they see the same machine state. "
         f"E1: 1000 queries per seed, one warm-up pass, every query timed once per method, the calibrated clock overhead (median CPU time of an empty "
         f"measured body) subtracted. E2: 100,000 parcels per seed, one warm-up run, median of 5 repetitions. 5 seeds per configuration; "
         f"values are mean over seeds ± 95% confidence half-width (Student t). 1-minute load average at the start of the runs: {lr}. "
         f"Evaluations per parcel do not depend on the machine.", "",
         ("**Machines.** PUB2-family rows (machine = server) were timed by the lead on the Linux server (Intel Xeon Platinum 8336C, g++ "
          f"-O3 -march=native -ffp-contract=off, pinned with taskset, same interleaved thread-CPU-time protocol; server 1-minute load at run start: {slr}"
          + (f"; environment: {json.dumps(srv_env)[:300]}" if srv_env else "") + "). ERP rows and any row marked Mac were timed on the Mac as described above; "
          "the Mac numbers of the PUB2 family are kept as a secondary table at the end.") if srv_loads or srv_env else
         "**Machine.** All rows were timed on the Mac (machine column).", "",
         "**brute** in E1 = scalar C++ brute force evaluating every service per parcel, with zone, DAS and billable-weight lookups shared across services "
         "that use the same chart (B1s, the faster scalar variant; B1 mirrors `brute1`). **brute** in E2 = column-at-a-time C++ brute force over blocks of 512 "
         "parcels (B1v, mirrors `brute_vec`, the Python E2 baseline; the fastest C++ brute force on batches). **RQ_base** = C++ RQ_base: same index, lower bounds "
         "and stop rule as `index.py` (query1 = best-first threshold algorithm in E1, query_batch = round-robin threshold algorithm in E2); a candidate's exact "
         "cost reads its zone from the parcel's signature. **agreement** = share of parcels on which every C++ method (B1, B1s, B1v, RQ_base single and batch) "
         "returns exactly the same service and cost as the Python brute force, over all E1 and E2 seeds. **RQ_base evals/parcel** = exact cost evaluations per "
         "parcel (brute force: n). For n ≥ 240 the slow per-service brute B1 is not timed in E2 (B1s and B1v are).", "",
         "Instances: " + "; ".join(f"{k} = {v}" for k, v in DESC.items()) + ".", ""]
    L += ["## E1 single query (Q1): p50 thread CPU time per query, µs", "",
          "| instance | workload | n | brute | RQ_base | speedup | agreement | RQ_base evals/parcel | machine |", "|---|---|---|---|---|---|---|---|---|"]
    for _, _, inst, wl, r in rows:
        a = r["agg"]["E1"]
        L.append(f"| {inst} | {wl} | {r['n']} | {mc(a.get('B1s_cpu_p50_ns'), 1e-3, 3)} | {mc(a.get('RQ_q1_cpu_p50_ns'), 1e-3, 3)} | "
                 f"{mc(a.get('speedup_cpu_p50_ns_B1s'), 1, 1)}× | {agreement(VER, inst, wl)} | {f(evals(VER, inst, wl, 'e1'), 2)} | {r['machine']} |")
    L += ["", "## E2 batch (Q2): thread CPU seconds per 100,000 parcels", "",
          "| instance | workload | n | brute | RQ_base | speedup | agreement | RQ_base evals/parcel | machine |", "|---|---|---|---|---|---|---|---|---|"]
    for _, _, inst, wl, r in rows:
        a = r["agg"]["E2"]
        L.append(f"| {inst} | {wl} | {r['n']} | {mc(a.get('B1v_s_per_100k'), 1, 4)} | {mc(a.get('RQ_batch_s_per_100k'), 1, 4)} | "
                 f"{mc(a.get('speedup_B1v_vs_RQ_batch'), 1, 1)}× | {agreement(VER, inst, wl)} | {f(evals(VER, inst, wl, 'e2'), 2)} | {r['machine']} |")
    # ---------------- E1 latency distribution
    L += ["", "## E1 latency distribution (thread CPU µs per query: p50 / p95 / p99 / mean)", "",
          "| instance/workload | n | seeds | B1 p50 / p95 / p99 / mean | B1s p50 / p95 / p99 / mean | RQ_base p50 / p95 / p99 / mean | speedup vs B1s p50 / p95 / p99 / mean | speedup vs B1 p50 | stream CPU mean B1s / RQ_base |",
          "|---|---|---|---|---|---|---|---|---|"]
    u = 1e-3
    for _, _, inst, wl, r in rows:
        a = r["agg"]["E1"]
        q4 = lambda m: " / ".join(f(a[f"{m}_cpu_{q}_ns"]["mean"] * u, 3) if f"{m}_cpu_{q}_ns" in a else "—" for q in ("p50", "p95", "p99", "mean"))
        sp = " / ".join(f(a[f"speedup_cpu_{q}_ns_B1s"]["mean"], 1) + "×" if f"speedup_cpu_{q}_ns_B1s" in a else "—" for q in ("p50", "p95", "p99", "mean"))
        L.append(f"| {inst}/{wl} | {r['n']} | {len(r['E1'])} | {q4('B1')} | {q4('B1s')} | {q4('RQ_q1')} | {sp} | "
                 f"{mc(a.get('speedup_cpu_p50_ns_B1'), 1, 1)}× | {mc(a.get('B1s_stream_cpu_mean_ns'), u, 3)} / {mc(a.get('RQ_q1_stream_cpu_mean_ns'), u, 3)} |")
    L += ["", "Wall-clock E1 (µs, p50 / p95 / p99), for reference (Mac rows are inflated by the shared machine; server rows ran on an idle pinned core):", "",
          "| instance/workload | B1s | RQ_base |", "|---|---|---|"]
    for _, _, inst, wl, r in rows:
        a = r["agg"]["E1"]
        w = lambda m: " / ".join(f(a[f"{m}_{q}_ns"]["mean"] * u, 3) if f"{m}_{q}_ns" in a else "—" for q in ("p50", "p95", "p99"))
        L.append(f"| {inst}/{wl} | {w('B1s')} | {w('RQ_q1')} |")
    # ---------------- machine-independent
    L += ["", "## RQ_base exact cost evaluations per parcel vs n (machine-independent; brute force evaluates n services per parcel)", "",
          "| instance | n | workload | E1 query1 evals/parcel | E2 batch evals/parcel | evaluations saved vs brute (E1) | Python RQ_base evals (E1, seed 11) |",
          "|---|---|---|---|---|---|---|"]
    for inst in INSTS:
        if inst not in VER:
            continue
        n = VER[inst]["index"]["n"]
        for wl in (("erp",) if inst == "ERP" else ("olist", "furn")):
            e1, e2 = evals(VER, inst, wl, "e1"), evals(VER, inst, wl, "e2")
            pyk = f"{inst}/{wl}"
            pyv = py1[pyk]["RQ_base"].get("evals") if pyk in py1 else None
            L.append(f"| {inst} | {n} | {wl} | {f(e1, 2)} | {f(e2, 2)} | {(n / e1) if e1 else 0:.1f}× | {f(pyv, 2)} |")
    # ---------------- E2 detail
    L += ["", "## Detail E2: all C++ variants (thread CPU seconds per 100,000 parcels; wall clock in the last columns)", "",
          "B1 = per-service brute (mirrors `brute1`); B1s = shared-lookup brute; B1v = column-at-a-time brute (mirrors `brute_vec`); "
          "RQ_base batch = round-robin threshold algorithm (mirrors `RQIndex.query_batch`); RQ_base q1-loop = best-first `query1` per parcel.", "",
          "| instance/workload | seeds | parcels timed per seed | B1 | B1s | B1v | RQ_base batch | RQ_base q1-loop | evals/parcel (batch / q1) | batch vs B1 | batch vs B1s | batch vs B1v | q1-loop vs B1v | wall B1v | wall RQ_base batch | load1 at start |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, _, inst, wl, r in rows:
        a = r["agg"]["E2"]
        L.append(f"| {inst}/{wl} | {len(r['E2'])} | {', '.join(f'{x:,}' for x in a.get('measured_on', []))} | {mc(a.get('B1_s_per_100k'), 1, 4)} | "
                 f"{mc(a.get('B1s_s_per_100k'), 1, 4)} | {mc(a.get('B1v_s_per_100k'), 1, 4)} | {mc(a.get('RQ_batch_s_per_100k'), 1, 4)} | "
                 f"{mc(a.get('RQ_q1loop_s_per_100k'), 1, 4)} | {a['evals_per_parcel_RQ_batch']:.2f} / {a['evals_per_parcel_RQ_q1']:.2f} | "
                 f"{mc(a.get('speedup_B1_vs_RQ_batch'), 1, 1)}× | {mc(a.get('speedup_B1s_vs_RQ_batch'), 1, 1)}× | {mc(a.get('speedup_B1v_vs_RQ_batch'), 1, 1)}× | "
                 f"{mc(a.get('speedup_B1v_vs_RQ_q1loop'), 1, 1)}× | {mc(a.get('B1v_wall_s_per_100k'), 1, 4)} | {mc(a.get('RQ_batch_wall_s_per_100k'), 1, 4)} | "
                 f"{min(a['load1']):.1f}–{max(a['load1']):.1f} |")
    # ---------------- C++ vs Python
    L += ["", "## C++ versus the earlier Python numbers (Python = results/pilot_E1_pub2.json, pilot_E2_pub2.json, erp_pilot_USA.json; Python wall time, seed 11 / 22; C++ thread CPU time, mean over seeds)", "",
          "| instance/workload | E1 p50 Python brute1 µs | E1 p50 C++ brute (B1s) µs | E1 p50 Python RQ_base µs | E1 p50 C++ RQ_base µs | E1 speedup Python | E1 speedup C++ | "
          "E2 Python brute_vec s | E2 C++ brute (B1v) s | E2 Python RQ_base s | E2 C++ RQ_base s | E2 speedup Python | E2 speedup C++ |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, _, inst, wl, r in rows:
        a1, a2 = r["agg"]["E1"], r["agg"]["E2"]
        k = f"{inst}/{wl}"
        if inst == "ERP":
            if not erp_py:
                continue
            pb, pr = erp_py["R1_brute"]["p50_us"], erp_py["R1_RQ_base"]["p50_us"]
            sb, sr = erp_py["R2_brute_vec_s"] * 1e5 / erp_py["N"], erp_py["R2_RQ_base_s"] * 1e5 / erp_py["N"]
        elif k in py1 and k in py2:
            pb, pr = py1[k]["B1_brute"]["p50_us"], py1[k]["RQ_base"]["p50_us"]
            sb, sr = py2[k]["B1_brute_vec"]["s"], py2[k]["RQ_base"]["s"]
        else:
            continue
        if "B1s_cpu_p50_ns" not in a1 or "B1v_s_per_100k" not in a2:
            continue
        cb, cr = a1["B1s_cpu_p50_ns"]["mean"] * 1e-3, a1["RQ_q1_cpu_p50_ns"]["mean"] * 1e-3
        db, dr = a2["B1v_s_per_100k"]["mean"], a2["RQ_batch_s_per_100k"]["mean"]
        L.append(f"| {k} | {f(pb, 1)} | {f(cb, 3)} | {f(pr, 1)} | {f(cr, 3)} | {pb / pr:.1f}× | {cb / cr:.1f}× | "
                 f"{f(sb, 3)} | {f(db, 4)} | {f(sr, 3)} | {f(dr, 4)} | {sb / sr:.1f}× | {db / dr:.1f}× |")
    L += ["", "Python had no n = 11 / 31 runs. ERP Python reference: E1 on 1000 sampled parcels, E2 on all real parcels scaled to 100,000; "
          "C++ ERP: 5 random samples of 1000 (E1) and 100,000 (E2) real parcels."]
    # ---------------- secondary: Mac numbers for rows that have server numbers
    if mac_rows:
        L += ["", "## Secondary: Mac (Apple M4, shared, thread CPU time) numbers for the PUB2 family", "",
              "| instance | workload | n | E1 brute p50 µs | E1 RQ_base p50 µs | E1 speedup | E2 brute s/100k | E2 RQ_base s/100k | E2 speedup | Mac load1 at start |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for _, _, inst, wl, r in mac_rows:
            a1, a2 = r["agg"]["E1"], r["agg"]["E2"]
            L.append(f"| {inst} | {wl} | {r['n']} | {mc(a1.get('B1s_cpu_p50_ns'), 1e-3, 3)} | {mc(a1.get('RQ_q1_cpu_p50_ns'), 1e-3, 3)} | "
                     f"{mc(a1.get('speedup_cpu_p50_ns_B1s'), 1, 1)}× | {mc(a2.get('B1v_s_per_100k'), 1, 4)} | {mc(a2.get('RQ_batch_s_per_100k'), 1, 4)} | "
                     f"{mc(a2.get('speedup_B1v_vs_RQ_batch'), 1, 1)}× | {min(a1['load1'] + a2['load1']):.1f}–{max(a1['load1'] + a2['load1']):.1f} |")
    # ---------------- index
    L += ["", "## C++ RQ_base index (built in C++, compared array by array with the Python RQIndex)", "",
          "| instance | n | elementary key intervals | signatures | local signatures | groups | elementary weight intervals | index MB | C++ build s (CPU-contended) | identical to Python |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for inst in INSTS:
        if inst in VER:
            x = VER[inst]["index"]
            L.append(f"| {inst} | {x['n']} | {x['U']:,} | {x['signatures']} | {x['local_signatures']} | {x['groups']} | {x['elem_intervals']} | "
                     f"{x['bytes'] / 1e6:.2f} | {x['build_s']:.3f} | {'yes' if x['differs_from_python'] == 0 else 'NO'} |")
    L = [x.replace("—×", "—") for x in L]
    open(os.path.join(RES, "tables_cpp.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
