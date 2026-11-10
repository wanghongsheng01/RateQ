# RateQ

Exact cheapest-service queries and incremental maintenance over rule-defined shipping rate cards.

This repository is the reproducibility artifact of the ICDE 2027 submission *RateQ*. A rate card
prices a parcel by rules: zone charts keyed by origin and destination ZIP code, weight bands,
dimensional weight, rounding rules, eligibility limits, surcharges, tier raises and weekly fuel
ratios. RateQ answers three queries exactly, with the same answer as evaluating every service:

* **Q1** single query: the cheapest eligible service (and its exact charge) for one parcel;
* **Q2** batch query: the same for a batch of parcels;
* **Q3** maintenance: keep the stored (best, runner-up) answers of historical parcels exact under
  edits of prices, bands, zones, fuel ratios and general rate increases.

The repository contains the RateQ index (RQ-Index and RQ+) and the maintenance algorithm in Python,
a same-language C++ core, all baselines (brute force, precomputation, R-tree, MIP, DuckDB SQL,
kinetic sweep, result cache, all-candidates IVM), the parsers of the official 2026 UPS, FedEx and
USPS rate publications, the workload generators, every experiment script, every result file read by
the paper, and the generators that turn these result files into every number, table and figure of
the paper.

## Repository layout

| Path | Content |
|---|---|
| `code/rateq/` | Python package: `model.py` (rule model, exact evaluation, vectorised brute force), `index.py` (RQ-Index, RQ+), `maintain.py` (Q3 maintenance), `baselines.py` (B1-B6), `instances.py` (instance and workload builders) |
| `code/pilot.py` | Python experiments E1 (Q1), E2 (Q2), E3 (memory, build), E4 (index updates), E5 (Q3) |
| `code/exp_rev1_*.py` | Additional experiments: bound and top-k ablations, B7 result cache, B8 all-candidates IVM, generator sensitivity, Q3 at 2M/20M stored parcels, ent-geo workload |
| `code/export_bin.py`, `code/run_cpp.py` | Export instances, workloads and Python ground truth to binary files; drive the C++ verification and timing |
| `code/make_numbers.py`, `code/make_figs.py` | Generate `paper/numbers.tex`, `paper/tables/*.tex` and `paper/figs/*.pdf` from `results/` |
| `code/make_cpp_tables.py`, `code/make_tables.py` | Markdown summaries of the C++ and Python results |
| `code/parse_public.py`, `code/parse_manual.py` | Parsers of the downloaded official publications into `data/public/*.json` |
| `code/smoke.py` | Exactness smoke test (index answers equal brute-force answers) |
| `cpp/rq.cpp`, `cpp/Makefile` | C++17 core: RQ-Index build, Q1/Q2 queries, scalar and column-wise brute force, exactness check against the Python export |
| `data/public/` | Parsed official rate cards, zone charts, DAS lists, fuel history and surcharge amounts (2026), Olist parcel shapes, ZCTA list and ZCTA population |
| `results/` | Result files read by the paper (see the table below); `results/rev1/README.md` documents the fields of the `rev1` files |
| `paper/` | Reference outputs: `numbers.tex` (all numeric macros of the paper), `tables/*.tex`, `figs/*.pdf` |
| `theory/rateq_theory.tex`, `.pdf` | Technical companion: full proofs, assumption checks and the code-to-theorem map |
| `scripts/` | `reproduce.sh` (build and rerun), `check_numbers.sh` (regenerate and compare), `prep_zcta_pop.py`, maintainer scripts (`sync_from_project.sh` and helpers, `security_scan.sh`) |

## Requirements

* Python 3.9 (tested with CPython 3.9.6 on macOS 15, Apple M4) and the pinned packages in
  `requirements.txt` (numpy 2.0.2, matplotlib 3.9.4, duckdb 1.4.5, pandas 2.3.3, highspy 1.15.1,
  rtree 1.4.1, sortedcontainers 2.4.0, openpyxl 3.1.5, pdfplumber 0.11.8 and their dependencies).
  Byte-identical figures require the pinned matplotlib version.
* A C++17 compiler: Apple clang 16 (macOS) or g++ 11 (Ubuntu 22.04) were used; `make`.
* Memory: 8 GB for every experiment except Q3 with 20M stored parcels (16 GB or more).

## Build

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
make -C cpp rq                 # clang++ by default; on Linux: make -C cpp rq CXX=g++
```

or `scripts/reproduce.sh setup`, which does the same. The Python package needs no build step;
scripts run from `code/` with `PYTHONPATH=.`, for example
`cd code && PYTHONPATH=. ../.venv/bin/python smoke.py 5000`.

## Regenerate the paper numbers, tables and figures

```bash
scripts/check_numbers.sh       # about 1 minute
```

The script copies `code/`, `data/` and `results/` to a temporary directory, runs
`make_numbers.py`, `make_figs.py` and `run_cpp.py tables` there, and compares the output with the
reference files: `paper/numbers.tex`, the nine tables in `paper/tables/`,
`results/theta_sweep_erp.json`, `results/fig_example_facts.json`, `results/tables_cpp.md`
(byte-identical) and the five figures in `paper/figs/` (byte-identical except the `/CreationDate`
timestamp that matplotlib writes into every PDF). It prints `OK` and exits with status 0 when
everything is identical. To regenerate in place instead, run
`cd code && PYTHONPATH=. ../.venv/bin/python make_numbers.py && PYTHONPATH=. ../.venv/bin/python make_figs.py`.

### Paper elements and their sources

All macros used in the text (`\QOne...`, `\Cpp...`, `\Erp...`, `\BSeven...`, `\Sens...`, ...) are
written to `paper/numbers.tex` by `code/make_numbers.py`; the tables below are written by the same
script, the figures by `code/make_figs.py`.

| Paper element | Generated file | Result files (in `results/`) | Produced by |
|---|---|---|---|
| Fig. `fig:example` (Introduction): exact charge vs. weight, official 2026 data | `paper/figs/example.pdf` | none; computed from `data/public/` (facts in `fig_example_facts.json`) | `make_figs.py` |
| Table `tab:data` (Setup): instances and index structure | `paper/tables/datasets.tex` | `pilot_E3_pub2.json`, `pilot_E3.json`, `erp_pilot_USA.json` | `pilot.py E3`; enterprise run |
| Table `tab:cpp` (RQ1, RQ2): compiled comparison | `paper/tables/cpp.tex` | `cpp_E1E2_pub2_server.json`, `cpp_E1E2_pub2r_server.json`, `cpp_E1E2_erp.json`, `cpp_verify_pub2.json`, `cpp_verify_pub2r.json`, `cpp_verify_erp.json` | `run_cpp.py verify/time` |
| Table `tab:q12` (RQ1, RQ2): Python Q1 latency and Q2 batch time | `paper/tables/q12.tex` | `pilot_E1_pub2.json`, `pilot_E2_pub2.json` (or `*_server.json` copies when present), `pilot_E1.json`, `pilot_E2_tuned.json` | `pilot.py E1 E2` |
| Table `tab:q3` and Fig. `fig:q3` (RQ3): Q3 maintenance | `paper/tables/q3.tex`, `paper/figs/q3_maint.pdf` | `pilot_E5_pub2.json` | `pilot.py E5` |
| Table `tab:scale` (RQ3): Q3 at 2M and 20M stored parcels | `paper/tables/scale.tex` | `rev1/server/scale_q3_*.json` | `exp_rev1_scale.py` |
| Fig. `fig:erp` (RQ3): logged enterprise rule changes | `paper/figs/erp_replay.pdf` | `erp_pilot_USA.json` | enterprise run |
| Table `tab:upd` (RQ4): index updates | `paper/tables/updates.tex` | `pilot_E4_pub2.json` | `pilot.py E4` |
| Table `tab:erp` (RQ5): enterprise deployment | `paper/tables/erp.tex` | `erp_pilot_USA.json`, `erp_replay_USA_after.json` | enterprise run |
| Text macros: B7 result cache | `paper/numbers.tex` | `rev1/server/b7_cache_PUB2*.json`, `rev1/b7_cache_entgeo_*.json` | `exp_rev1_b7_cache.py` |
| Text macros: B8 all-candidates IVM | `paper/numbers.tex` | `rev1/server/b8_ivm_*_fuelfix.json` (else `b8_ivm_*.json`) | `exp_rev1_b8_ivm.py` |
| Text macros: lower-bound and top-k ablations | `paper/numbers.tex` | `rev1/server/ablation_bound_*.json`, `rev1/server/ablation_topk_v2.json` | `exp_rev1_ablations.py` |
| Text macros: generator sensitivity | `paper/numbers.tex` | `rev1/server/sensitivity_*.json` | `exp_rev1_sensitivity.py` |
| Text macros: ent-geo workload | `paper/numbers.tex` | `rev1/entgeo_E1E2_*.json`, `rev1/entgeo_workload_stats.json` | `exp_rev1_entgeo.py` (needs enterprise shapes) |
| Text macros: adaptive-rule sensitivity to theta | `paper/numbers.tex` | `erp_pilot_USA.json` (writes `theta_sweep_erp.json`) | `make_numbers.py` |
| Not in the current paper: Q1 and Q2 plots, separate Q1 and Q2 tables | `paper/figs/q1_latency.pdf`, `paper/figs/q2_batch.pdf`, `paper/tables/q1.tex`, `paper/tables/q2.tex` | `pilot_E1_pub2.json`, `pilot_E2_pub2.json` | `pilot.py E1 E2` |

`make_numbers.py` selects some inputs by rule (for example, a server rerun before the original run,
or an earlier enterprise run in `results/archive_1109_prefix/` when the latest one recorded a machine
load above the acceptance threshold); the rule is stated next to each selection in the script.
`results/tables_cpp.md` (`run_cpp.py tables`) lists the full C++ results with 95% confidence
intervals, wall-clock times and the per-instance exactness counts.

## Rerun the experiments

Every experiment checks each answer against brute force and stores the agreement in its result
file. Experiments overwrite the shipped files under `results/`; compare with `git diff --stat` and
restore with `git checkout -- results paper`. Absolute times depend on the machine; the paper
reports ratios measured within one run (methods interleaved, thread CPU time). Python runs of the
authors used an Apple M4 (single thread, shared machine); C++ timings and the `rev1/server` runs
used an Intel Xeon Platinum 8336C, one pinned core per process.

| Step | Command | Output | Runtime of the authors' run |
|---|---|---|---|
| Smoke test | `scripts/reproduce.sh smoke` | stdout (`exact True`, `scalar_bad 0`) | 1 min |
| C++ exactness on PUB2 | `scripts/reproduce.sh cpp-smoke` | `bin/pub2/` (inputs), `results/cpp_verify_pub2.json` (1,010,000 parcels, 0 mismatches) | about 1 min (export 40 s, verify 30 s) |
| Python E1-E5, PUB2 family | `scripts/reproduce.sh pilot` | `results/pilot_E{1..5}_pub2.json` | E1 25 min, E2 45 min, E3 2 min, E4 2 min, E5 5-40 min (M4) |
| Python E1-E3, synthetic generators | `scripts/reproduce.sh synthetic` | `results/pilot_E1.json`, `pilot_E2_tuned.json`, `pilot_E3.json` | about 1 h (M4) |
| C++ export, verify, time | `scripts/reproduce.sh cpp` | `results/cpp_verify_pub2*.json`, `results/cpp_E1E2_pub2*.json`, `results/tables_cpp.md` | export 30 min; verify 65 min (PUB2 1 min, x60 3 min, x240 8 min, x1000 53 min); timing 35 min (Xeon) |
| Additional experiments | `scripts/reproduce.sh rev1` | `results/rev1/*.json`, copied to `results/rev1/server/` | bound ablation 11 min, top-k 12 min, B7 15 min, B8 25 min, sensitivity 65 min, 2M scale 70 min, 20M scale 67 min (Xeon, run in parallel on separate cores) |
| Paper outputs | `scripts/reproduce.sh numbers` | `paper/numbers.tex`, `paper/tables/`, `paper/figs/` | 1 min |

`scripts/reproduce.sh` prints each command it runs; the commands can also be run one by one from
`code/` (the usage line of every script is in its docstring). Notes:

* `pilot.py` selects instances with `PILOT_INSTS` and names the output with `PILOT_TAG`
  (`PILOT_INSTS=PUB2,PUB2x60,PUB2x240,PUB2x1000 PILOT_TAG=_pub2 python pilot.py E1 E2 E3 E4 E5`).
  Instances: `PUB2` (19 official 2026 services: 5 USPS, 7 UPS, 7 FedEx), `PUB2x60/x240/x1000`
  (plus seeded contract variants), `SYN200` (synthetic), `PUB`/`PUBx*` (an earlier public instance
  with approximate UPS zones), `ERPSTYLE20` (synthetic values with the rounding, extrapolation and
  limit semantics of an enterprise rule language). Workloads: `olist` (Olist product shapes) and
  `furn` (furniture-like shapes); destinations uniform over real ZCTAs.
* The C++ timing driver (`run_cpp.py time`) waits up to `WAIT_S` seconds for the 1-minute load to
  fall below `LOAD_MAX` and records the load of every unit. `RQ_E2_N`, `RQ_E2_SKIP`, `E1_SEEDS` and
  `E2_SEEDS` set the reduced protocol for n = 240 and 1000 that the paper uses. On Linux the
  script pins the process with `taskset -c $PIN_CORE` (default 2). The paper reads the server copies
  `results/cpp_E1E2_pub2_server.json` and `results/cpp_E1E2_pub2r_server.json`; their `_env`
  block describes the server.
* The `rev1` scripts write `results/rev1/<name>.json`; the paper reads the copies in
  `results/rev1/server/`, which `reproduce.sh rev1` refreshes. `REV1_REPS` (default 5) sets the
  repetitions and `REV1_FULLREPS` the number of timed full recomputations in `exp_rev1_scale.py`.
  `scale_q3_20000000_60.json` holds an incomplete run (2 of 5 repetitions); `make_numbers.py` uses a
  configuration only when all five repetitions are present.
* `exp_rev1_entgeo.py` with the `olist` and `furn` workloads runs on public data; its `entgeo`
  workload draws parcel shapes from the enterprise's parcels and cannot be rerun here (see below).

### Binary inputs of the C++ core

`code/export_bin.py pub2 <INST,...>` writes the instance (`.rqi`), the Python index (`.idx`),
parcel workloads (`.par`, 5 seeds of 1000 and 5 seeds of 100,000 parcels per workload) and the
Python brute-force answers (`.tru`) to `bin/pub2/` (213 MB for all instances; not versioned). The
file formats are documented in the script. `cpp/rq verify <rqi> <idx> {<par> <tru>}...` checks that
the C++ index equals the Python index and that every C++ answer (B1, B1s, B1v, RQ single query, RQ
batch) equals the Python brute force; `cpp/rq e1 <rqi> <par>` and `cpp/rq e2 <rqi> <par> <reps>`
time Q1 and Q2.

## Data sources and licences

The parsed files in `data/public/` are included so that every experiment runs offline. They can be
rebuilt from the original publications: place the downloads under `data/public/raw/` as listed
below, then run `cd code && PYTHONPATH=. ../.venv/bin/python parse_public.py` and
`... parse_manual.py`. With the files the authors downloaded in November 2026 (rate cards effective
in 2026), both parsers reproduce the shipped JSON files byte for byte.

| Data | Source | Raw file(s) under `data/public/raw/` | Parsed file(s) | Licence / terms |
|---|---|---|---|---|
| USPS Priority Mail, Priority Mail Express, Ground Advantage prices (2026) | USPS Notice 123, https://pe.usps.com/text/dmm300/Notice123.htm | `usps_notice123.htm` | `usps_rates.json` | Official USPS price list, publicly available |
| USPS domestic zone charts, origins 088, 300, 606, 750, 917 | https://postcalc.usps.com/DomesticZoneChart/GetZoneChart?zipCode3Digit=ORIGIN&shippingDate=11/04/2026 | `usps_zone_<origin>.json` | `usps_zones.json` | Official USPS zone charts, publicly available |
| UPS 2026 daily rates (U.S. 48) | *UPS 2026 Rate and Service Guide, Daily Rates* (PDF), ups.com shipping rates page | `manual/UPS/UPS_2026_Rate_and_Service_Guide_Daily_Rates.pdf` | `ups_rates_2026.json` | UPS publication; parsed numbers included for research reproducibility |
| UPS zone charts, same five origins | https://www.ups.com/media/us/currentrates/zone-csv/ORIGIN.xls | `manual/<origin>.xls` | `ups_zones.json` | UPS publication |
| UPS delivery-area surcharge ZIP list (2026) | ups.com shipping rates page, *DAS ZIP Codes* | `manual/UPS/UPS_DAS_ZIP_Codes_2026.xlsx` | `das_ups.json` | UPS publication |
| UPS earlier daily rates (instance `PUB` only) | https://www.ups.com/assets/resources/webcontent/en_US/daily_rates.xlsx | `ups_daily_rates.xlsx` | `ups_rates.json` | UPS publication |
| FedEx 2026 standard list rates | fedex.com rates page, *FedEx 2026 Standard List Rates* (xlsx) | `manual/FedEx/FedEx_2026_Standard_List_Rates.xlsx` | `fedex_rates.json` | FedEx publication |
| FedEx zone charts, origins 08801, 30301, 60601, 75201, 91761 | fedex.com zone locator | `manual/FedEx/zone_charts/FedEx_Zone_Chart_<zip>.pdf` | `fedex_zones.json` | FedEx publication |
| FedEx delivery-area surcharge ZIP list (2026) | fedex.com rates page, *Delivery Area Surcharge ZIP Codes* (xlsx) | `manual/FedEx/FedEx_2026_DAS_ZIP_Codes.xlsx` | `das_fedex.json` | FedEx publication |
| Weekly fuel surcharges (UPS, FedEx) | ups.com and fedex.com fuel surcharge pages; the weekly tables copied as tab-separated text | `manual/fuel_history/ups_fuel_history.txt`, `manual/fuel_history/fedex_fuel_history.txt` | `fuel_history.json` | Carrier publications |
| Surcharge amounts | *UPS 2026 Rate and Service Guide* pp. 179-183; *FedEx Service Guide 2026* pp. 128-135, 142 (transcribed in `parse_manual.py`) | none | `official_surcharges.json` | Carrier publications |
| Parcel shapes (weight, dimensions) | Olist products dataset, https://raw.githubusercontent.com/olist/work-at-olist-data/master/datasets/olist_products_dataset.csv | `olist_products.csv` | `parcels_olist.json` | CC BY-NC-SA 4.0 (Olist); the derived file is distributed under the same licence |
| ZCTA list | US Census Bureau 2020 Gazetteer, https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_Gaz_zcta_national.zip | `gaz/2020_Gaz_zcta_national.txt` | `zcta.json` | US Census Bureau, public domain |
| ZCTA population | US Census Bureau ACS 2023 5-year estimates, table B01003, https://www2.census.gov/programs-surveys/acs/summary_file/2023/table-based-SF/data/5YRData/acsdt5y2023-b01003.dat | any path | `zcta_pop_acs5y2023.json` (`python scripts/prep_zcta_pop.py <dat file>`) | US Census Bureau, public domain |

Carrier rate cards, zone charts and service guides are copyrighted publications of UPS, FedEx and
USPS. The repository contains only the numeric tables parsed from them, for research
reproducibility; consult the official publications for current prices. The fuel-history text files
contain the weekly table rows (`MM/DD/YYYY<TAB>ground %<TAB>air %` for UPS; `MM/DD/YYYY–MM/DD/YYYY<TAB>ground %<TAB>week<TAB>air %` for FedEx); other lines are ignored.

## Enterprise data

Section RQ5 of the paper, the enterprise rows of Tables `tab:data` and `tab:cpp`, Table `tab:erp`,
Fig. `fig:erp` and the ent-geo workload use de-identified rate cards, rule-change logs and parcel
records of a manufacturing company (10 US services, its shipped US parcels and its logged rule
changes). These data are company data and are not distributed, and neither is the compiler that
reads them. The repository includes only aggregate results computed from them:
`results/erp_pilot_USA.json` (and the earlier accepted run in `results/archive_1109_prefix/`),
`results/erp_replay_USA_after.json`, `results/cpp_E1E2_erp.json`, `results/cpp_verify_erp.json`,
`results/theta_sweep_erp.json`, `results/rev1/entgeo_*.json`, `results/rev1/b7_cache_entgeo_*.json`
and `results/rev1/entgeo_workload_stats.json`. They hold timings, counts, shares and per-event
aggregates of the logged rule changes (date, change kind, support size, times); rate-card
templates appear under neutral ids (`svc01`, ...). No parcel record, price or customer identifier
is included. These files suffice to regenerate every enterprise number, table and figure of the
paper with `scripts/check_numbers.sh`; the enterprise experiments themselves cannot be rerun from
this repository. The code-to-theorem map in `theory/rateq_theory.pdf` cites lines of the enterprise driver
`code/erp_pilot.py`, which is not shipped because it reads the company data. Six aggregate statistics of the company's 2026 shipment table (parcel count,
warehouses, services per warehouse) are constants in `code/make_numbers.py`, and
`results/warehouse_us_channels.json` holds the same statistics restricted to US destinations.

## Licence

Code: MIT (see `LICENSE`). Data files keep the licences listed above.
