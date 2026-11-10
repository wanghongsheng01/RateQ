"""Generate the manuscript figures (paper/figs/*.pdf) from the result files and the official instance.
Usage: cd code && PYTHONPATH=. ../.venv/bin/python make_figs.py
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "results")
FIG = os.path.join(HERE, "..", "paper", "figs")
os.makedirs(FIG, exist_ok=True)
L = lambda f: json.load(open(os.path.join(RES, f)))

# Okabe-Ito colour-blind-safe palette
C = {"B1": "#000000", "B4": "#E69F00", "RQ": "#0072B2", "RQ+": "#009E73", "DB": "#CC79A7", "B5": "#D55E00",
     "svc": "#E69F00", "full": "#000000", "pred": "#56B4E9", "adapt": "#0072B2", "oracle": "#009E73", "USPS": "#CC79A7"}
MK = {"B1": "o", "B4": "s", "RQ": "^", "RQ+": "D", "DB": "v", "B5": "x"}
plt.rcParams.update({"font.size": 7.5, "axes.labelsize": 7.5, "legend.fontsize": 6.8, "xtick.labelsize": 7,
                     "ytick.labelsize": 7, "pdf.fonttype": 42, "ps.fonttype": 42, "axes.linewidth": 0.6,
                     "lines.linewidth": 1.2, "lines.markersize": 3.5})
W = 3.45
INST = [("PUB2", 19), ("PUB2x60", 60), ("PUB2x240", 240), ("PUB2x1000", 1000)]
WLN = {"olist": "(a) olist", "furn": "(b) furniture"}


def two_panel(h=1.55):
    fig, axs = plt.subplots(1, 2, figsize=(W, h), sharey=True)
    return fig, axs


def finish(fig, axs, name, handles, labels, ncol):
    fig.legend(handles, labels, loc="upper center", ncol=ncol, frameon=False, bbox_to_anchor=(0.5, 1.02),
               handlelength=1.6, columnspacing=1.0)
    fig.tight_layout(rect=(0, 0, 1, 0.78), pad=0.3, w_pad=0.6)
    fig.savefig(os.path.join(FIG, name), bbox_inches="tight", pad_inches=0.01)
    plt.close(fig)


# ---------------------------------------------------------------- Q1 latency vs n
E1 = L("pilot_E1_pub2.json")
fig, axs = two_panel()
ns = [n for _, n in INST]
for ax, wl in zip(axs, ("olist", "furn")):
    for key, lab in (("B1_brute", "B1"), ("B4_precompute", "B4"), ("RQ_base", "RQ"), ("RQ_plus", "RQ+")):
        y = [E1[f"{i}/{wl}"][key]["p50_us"] for i, _ in INST]
        ax.plot(ns, y, marker=MK[lab], color=C[lab], label=lab)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xticks(ns); ax.set_xticklabels([str(n) for n in ns])
    ax.minorticks_off()
    ax.set_xlabel("services $n$")
    ax.text(0.04, 0.95, WLN[wl], transform=ax.transAxes, va="top", ha="left", fontsize=6.8)
    ax.grid(True, which="major", lw=0.3, alpha=0.5)
axs[0].set_ylabel("p50 latency (µs)")
h, l = axs[0].get_legend_handles_labels()
lab = {"B1": "B1 brute force (production)", "B4": "B4 precompute", "RQ": "RQ-Index", "RQ+": "RQ+"}
finish(fig, axs, "q1_latency.pdf", h, [lab[x] for x in l], 2)

# ---------------------------------------------------------------- Q2 batch time vs n
E2 = L("pilot_E2_pub2.json")
fig, axs = two_panel()
for ax, wl in zip(axs, ("olist", "furn")):
    for key, lab in (("B2_duckdb_t1", "DB"), ("B5_kinetic", "B5"), ("B1_brute_vec", "B1"), ("RQ_base", "RQ"), ("RQ_plus_warm", "RQ+")):
        y = [E2[f"{i}/{wl}"][key]["s"] for i, _ in INST]
        ax.plot(ns, y, marker=MK[lab], color=C[lab], label=lab)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xticks(ns); ax.set_xticklabels([str(n) for n in ns]); ax.minorticks_off()
    ax.set_xlabel("services $n$")
    ax.text(0.04, 0.95, WLN[wl], transform=ax.transAxes, va="top", ha="left", fontsize=6.8)
    ax.grid(True, which="major", lw=0.3, alpha=0.5)
axs[0].set_ylabel("time for 100k parcels (s)")
h, l = axs[0].get_legend_handles_labels()
lab = {"DB": "DuckDB (ASOF)", "B5": "B5 kinetic", "B1": "B1-vec (NumPy)", "RQ": "RQ-Index", "RQ+": "RQ+ (warm)"}
finish(fig, axs, "q2_batch.pdf", h, [lab[x] for x in l], 3)

# ---------------------------------------------------------------- Q3 maintenance per edit type
E5 = L("pilot_E5_pub2.json")
fig, ax = plt.subplots(figsize=(W, 1.6))
x = np.arange(len(INST))
bars = [("cell", "ours_s", "cell edit (ours)", "#0072B2", ""), ("cell", "service_level_s", "cell edit (service-level)", "#56B4E9", ""),
        ("band", "ours_s", "band edit (ours)", "#009E73", ""), ("band", "service_level_s", "band edit (service-level)", "#8FD9C1", ""),
        ("fuel", "ours_s", "fuel edit (ours)", "#E69F00", ""), ("full", None, "full recomputation", "#000000", "")]
w = 0.13
for j, (et, k, lab, col, hatch) in enumerate(bars):
    if et == "full":
        y = [E5[i]["full_recompute_s"] for i, _ in INST]
    else:
        y = [E5[i]["edits"][et][k] for i, _ in INST]
    ax.bar(x + (j - 2.5) * w, np.array(y) * 1e3, w, color=col, label=lab, edgecolor="none")
ax.set_yscale("log")
ax.set_xticks(x); ax.set_xticklabels([f"$n$={n}" for _, n in INST])
ax.set_ylabel("time per edit (ms)")
ax.grid(True, axis="y", which="major", lw=0.3, alpha=0.5)
ax.set_axisbelow(True)
h, l = ax.get_legend_handles_labels()
fig.legend(h, l, loc="upper center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 1.04), handlelength=1.2, columnspacing=0.8)
fig.tight_layout(rect=(0, 0, 1, 0.8), pad=0.3)
fig.savefig(os.path.join(FIG, "q3_maint.pdf"), bbox_inches="tight", pad_inches=0.01)
plt.close(fig)

# ---------------------------------------------------------------- enterprise: per-event time relative to the oracle
ERP = L("erp_pilot_USA.json")
ev = ERP["R5_real_events"]
orc = np.array([min(e["pred_s"], e["service_level_s"]) for e in ev])
fig, ax = plt.subplots(figsize=(W, 1.55))
k = np.arange(1, len(ev) + 1)
for lab, key, col, ls in (("service-level", "service_level_s", C["svc"], "-"), ("predicate-level", "pred_s", C["pred"], "--"),
                          ("adaptive ($\\theta=0.3$)", "adaptive_s", C["adapt"], "-")):
    r = np.sort(np.array([e[key] for e in ev]) / orc)
    ax.step(k, r, where="post", color=col, ls=ls, label=lab, lw=1.1)
ax.set_xlabel("logged rule changes, sorted by ratio")
ax.set_ylabel("time / per-event oracle")
ax.set_xlim(1, len(ev))
ax.grid(True, lw=0.3, alpha=0.5)
ax.legend(loc="upper left", frameon=False, ncol=1, handlelength=1.8)
fig.tight_layout(pad=0.3)
fig.savefig(os.path.join(FIG, "erp_replay.pdf"), bbox_inches="tight", pad_inches=0.01)
plt.close(fig)

# ---------------------------------------------------------------- motivating example: exact cost vs weight, official 2026 data
from rateq.instances import build_pub2, parcels_olist
from rateq.model import Parcels, eval_service, INF

inst = build_pub2()
key0 = int(parcels_olist(50, seed=3).key[0])  # a real destination ZIP from origin index stored in the key
names = {"UPS_Ground": ("UPS Ground", "#0072B2", "-"), "FedEx_Ground": ("FedEx Ground", "#D55E00", "--"),
         "USPS_GA_Commercial": ("USPS Ground Adv. (Comm.)", "#CC79A7", "-."), "USPS_PM_Commercial": ("USPS Priority (Comm.)", "#009E73", ":")}
ws = np.round(np.arange(1, 70.01, 0.25), 2)
N = len(ws)
fig, ax = plt.subplots(figsize=(W, 1.75))
box = (49.0, 14.0, 8.0)  # desk-frame carton: length > 48 in triggers Additional Handling (40 lb minimum) at UPS/FedEx
P = Parcels(np.full(N, key0, np.int64), ws, np.full(N, box[0]), np.full(N, box[1]), np.full(N, box[2]),
            np.ones(N), np.full(N, 300, np.int64))
F0 = P.base_feats()
costs = {}
for s in inst.services:
    if s.name in names:
        c = eval_service(s, P, F0, inst.fuel).astype(float)
        c[c >= float(INF)] = np.nan
        costs[s.name] = c / 100.0
        lab, col, ls = names[s.name]
        ax.plot(ws, c / 100.0, color=col, ls=ls, label=lab, lw=1.1)
allc = np.vstack([costs[k] for k in names])
best = np.nanmin(allc, axis=0)
ax.set_xlabel("actual weight (lb) of a 49$\\times$14$\\times$8 in carton")
ax.set_ylabel("exact charge (USD)")
ax.set_xlim(1, 70)
ax.grid(True, lw=0.3, alpha=0.5)
ax.legend(loc="upper left", frameon=False, ncol=2, handlelength=2.0, columnspacing=0.8)
ax.set_ylim(None, 225)
fig.tight_layout(pad=0.3)
fig.savefig(os.path.join(FIG, "example.pdf"), bbox_inches="tight", pad_inches=0.01)
plt.close(fig)
# facts for the text
winner = [list(names)[int(np.nanargmin(allc[:, i]))] if np.isfinite(best[i]) else None for i in range(N)]
changes = [(float(ws[i]), winner[i - 1], winner[i]) for i in range(1, N) if winner[i] != winner[i - 1]]
json.dump({"key": key0, "box": box, "day": 300, "winner_changes": changes,
           "eligible_PM": bool(np.isfinite(costs["USPS_PM_Commercial"]).any()),
           "cost_at_1": {k: (None if np.isnan(v[0]) else float(v[0])) for k, v in costs.items()},
           "cost_at_39": {k: (None if np.isnan(v[ws.tolist().index(39.0)]) else float(v[ws.tolist().index(39.0)])) for k, v in costs.items()},
           "cost_at_41": {k: (None if np.isnan(v[ws.tolist().index(41.0)]) else float(v[ws.tolist().index(41.0)])) for k, v in costs.items()}},
          open(os.path.join(RES, "fig_example_facts.json"), "w"), indent=1)
print("figures written:", sorted(os.listdir(FIG)))
print(json.dumps(changes)[:600])
