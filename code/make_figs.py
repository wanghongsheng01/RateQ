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

# Okabe-Ito colour-blind-safe palette; RateQ always uses the highlight blue (HL), baselines never do
HL = "#0072B2"
C = {"B1": "#000000", "B4": "#E69F00", "RQ": "#0072B2", "RQ+": "#009E73", "DB": "#CC79A7", "B5": "#D55E00",
     "svc": "#E69F00", "full": "#000000", "pred": "#56B4E9", "adapt": "#0072B2", "oracle": "#009E73", "USPS": "#CC79A7"}
MK = {"B1": "o", "B4": "s", "RQ": "^", "RQ+": "D", "DB": "v", "B5": "x"}
plt.rcParams.update({"font.family": "serif", "font.serif": ["Times New Roman", "Times", "STIXGeneral"],
                     "mathtext.fontset": "stix",  # Times-like text and math, as in the IEEEtran body
                     "font.size": 8, "axes.labelsize": 8, "legend.fontsize": 7.5, "xtick.labelsize": 7.5,
                     "ytick.labelsize": 7.5, "pdf.fonttype": 42, "ps.fonttype": 42, "axes.linewidth": 0.6,
                     "lines.linewidth": 1.2, "lines.markersize": 3.5, "hatch.linewidth": 0.6})
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
    fig.savefig(os.path.join(FIG, name), bbox_inches="tight", pad_inches=0.03)
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
    ax.set_title(WLN[wl], fontsize=6.8, pad=2)
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
    ax.set_title(WLN[wl], fontsize=6.8, pad=2)
    ax.grid(True, which="major", lw=0.3, alpha=0.5)
axs[0].set_ylabel("time for 100k parcels (s)")
h, l = axs[0].get_legend_handles_labels()
lab = {"DB": "DuckDB (ASOF)", "B5": "B5 kinetic", "B1": "B1-vec (NumPy)", "RQ": "RQ-Index", "RQ+": "RQ+ (warm)"}
finish(fig, axs, "q2_batch.pdf", h, [lab[x] for x in l], 3)

# ---------------------------------------------------------------- Q3 maintenance per edit type
E5 = L("pilot_E5_pub2.json")
# small multiples, one panel per edit type; colour = method (RateQ highlight blue, service-level orange, full recomputation grey)
fig, axs = plt.subplots(1, 3, figsize=(W, 1.45), sharey=True)
x = np.arange(len(INST))
full = np.array([E5[i]["full_recompute_s"] for i, _ in INST]) * 1e3
for ax, (et, title) in zip(axs, (("cell", "(a) cell edit"), ("band", "(b) band edit"), ("fuel", "(c) fuel edit"))):
    ax.plot(x, full, color="#7F7F7F", marker="^", ls="--", label="full recomputation")
    svc = [E5[i]["edits"][et].get("service_level_s") for i, _ in INST]
    if all(v is not None for v in svc):
        ax.plot(x, np.array(svc) * 1e3, color=C["svc"], marker="s", label="service-level")
    ax.plot(x, np.array([E5[i]["edits"][et]["ours_s"] for i, _ in INST]) * 1e3, color=HL, marker="o", lw=1.6, label="RateQ")
    ax.set_yscale("log")
    ax.set_xticks(x); ax.set_xticklabels([str(n) for _, n in INST])
    ax.set_xlim(-0.4, len(INST) - 0.6)
    ax.set_title(title, fontsize=7.5, pad=2)
    ax.grid(True, axis="y", which="major", lw=0.3, alpha=0.5)
axs[0].set_ylabel("time per edit (ms)")
axs[1].set_xlabel("services $n$")
h, l = axs[0].get_legend_handles_labels()
order = [l.index("RateQ"), l.index("service-level"), l.index("full recomputation")]
fig.legend([h[k] for k in order], [l[k] for k in order], loc="upper center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 1.02),
           handlelength=1.8, columnspacing=1.0)
fig.tight_layout(rect=(0, 0, 1, 0.88), pad=0.3, w_pad=0.4)
fig.savefig(os.path.join(FIG, "q3_maint.pdf"), bbox_inches="tight", pad_inches=0.03)
plt.close(fig)

# ---------------------------------------------------------------- enterprise: per-event time relative to the oracle
ERP = L("erp_pilot_USA.json")
ev = ERP["R5_real_events"]
orc = np.array([min(e["pred_s"], e["service_level_s"]) for e in ev])
fig, ax = plt.subplots(figsize=(W, 1.45))
k = np.arange(1, len(ev) + 1)
for lab, key, col, ls in (("service-level", "service_level_s", C["svc"], "-"), ("predicate-level", "pred_s", C["pred"], "--"),
                          ("adaptive ($\\theta=0.3$, default)", "adaptive_s", HL, "-")):
    r = np.sort(np.array([e[key] for e in ev]) / orc)
    ax.step(k, r, where="post", color=col, ls=ls, label=lab, lw=1.1)
ax.set_xlabel("logged rule changes, sorted by ratio")
ax.set_ylabel("time / oracle time")
ax.set_xlim(1, len(ev))
ax.grid(True, lw=0.3, alpha=0.5)
ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), frameon=False, ncol=3, handlelength=1.6, columnspacing=0.8,
          borderaxespad=0.2)
fig.tight_layout(pad=0.3)
fig.savefig(os.path.join(FIG, "erp_replay.pdf"), bbox_inches="tight", pad_inches=0.03)
plt.close(fig)

# ---------------------------------------------------------------- motivating example: exact cost vs weight, official 2026 data
from rateq.instances import build_pub2, parcels_olist
from rateq.model import Parcels, eval_service, INF

inst = build_pub2()
key0 = int(parcels_olist(50, seed=3).key[0])  # a real destination ZIP from origin index stored in the key
names = {"UPS_Ground": ("UPS Ground", "#E69F00", "-"), "FedEx_Ground": ("FedEx Ground", "#D55E00", "--"),
         "USPS_GA_Commercial": ("USPS Ground Adv.", "#CC79A7", "-."), "USPS_PM_Commercial": ("USPS Priority", "#009E73", ":")}
ws = np.round(np.arange(1, 70.01, 0.25), 2)
N = len(ws)
fig, ax = plt.subplots(figsize=(W, 1.6))
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
ax.plot(ws, best, color=HL, lw=3.2, alpha=0.35, solid_capstyle="butt", label="cheapest = RateQ", zorder=0)
ax.set_xlabel("actual weight (lb) of a 49$\\times$14$\\times$8 in carton")
ax.set_ylabel("exact charge (USD)")
ax.set_xlim(1, 70)
ax.grid(True, lw=0.3, alpha=0.5)
ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), frameon=False, ncol=3, handlelength=1.8, columnspacing=0.7,
          borderaxespad=0.2)
fig.tight_layout(pad=0.3)
fig.savefig(os.path.join(FIG, "example.pdf"), bbox_inches="tight", pad_inches=0.03)
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
