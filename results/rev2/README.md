<!-- Artifact copy of results/rev2/README.md (published as results/rev2/README.md). -->
# results/rev2: round-2 revision experiments

Public data only. The Table III re-run is on the Linux server (Intel Xeon Platinum 8336C), with
each process pinned with `taskset` to its own physical core (core 4 is left to the C++ timing). The
rule-edit stream and the what-if runs ran on the Mac (Apple M4, load about 14–60), because the
server was out of memory at the time (see the DuckDB notes below); their `_env` and
load fields record this. Timer:
`time.thread_time_ns` (thread CPU time), except DuckDB, which uses wall time plus process CPU time
because it is a multi-threaded engine. Every run records `os.getloadavg()`. Summary objects have the
form `{median, mean, ci95 (t-interval of the mean), min, max, reps}`. Scripts: `code/exp_rev2_*.py`.

## Table III re-run (round-2 item B3): `results/pilot_E{1,2}_{pub2,syn}_server.json`

Produced by `code/exp_rev2_pilot_reps.py`. The script imports `pilot.py` and reuses its instances,
workloads, seeds and method objects; it does not edit `pilot.py`. Its own repetition loop runs 5
repetitions, interleaving the methods within each repetition.

- Schema: the same as `pilot_E1_pub2.json` / `pilot_E2_pub2.json` (E1: `mean_us`, `p50_us`, `p95_us`,
  `agree`, `q`, `evals`; E2: `s`, `agree`, `evals_per_parcel`). Every value is the **median over
  repetitions**. Each method also carries:
  - `reps`: the value of every repetition;
  - `ci95`: per key;
  - `n_reps`;
  - `p99_us` (E1).
- `pub2` = PUB2, PUB2x60, PUB2x240, PUB2x1000. `syn` = SYN200, which replaces `pilot_E1.json` and
  `pilot_E2_tuned.json` and is now computed with the current code.
- `B2_duckdb_t1` / `B2_duckdb_t8`: tuned ASOF DuckDB with 1 and 8 threads, measured on **all 100,000
  parcels at every n** (`measured_on` = 100000); no scaling.
  - `s` = wall seconds; `process_cpu_s` = process CPU time.
  - 5 repetitions at n=19 and n=60 and on SYN200, 3 at n=240, and 2 at n=1000 (deadline).
  - The 1-thread runs are pinned to one core. The 8-thread runs use 8–10 logical CPUs (CPUs 14–19 or
    20–27).
  - `memory_limit` field: DuckDB memory cap. 25 GB for n ≤ 240 and 55 GB at n = 1000; beyond the
    cap DuckDB spills to disk. At n = 1000, single-thread wall time is 888 s against 785 s of process
    CPU, so about 12% is disk I/O.
  - The caps were set after a first launch with default limits (80% of RAM per process) ran the
    server out of memory. That launch's outputs are not
    used.
- `speedup_duckdb_t1_over_RQ` (E2) = DuckDB 1-thread time / RQ-Index batch time.
- `B2_naive` (the BETWEEN-join variant, not in Table III) is not re-run.
- E1 `speedup_p50_best_RQ_over_B1`: per repetition, B1 p50 divided by the best RQ variant's p50.

## Local rule-edit stream (round-2 item B1): `rule_stream_<INST>.json`

Produced by `code/exp_rev2_rule_stream.py`. Instances: PUB2, PUB2x60 and PUB2x240, each with 200k
stored parcels (100k olist + 100k furniture). Each instance gets 38 local rule edits derived from the
public data:

| Kind | Count | What changes |
|---|---|---|
| `amount` | 16 | One surcharge rule of one service is re-priced by a factor drawn from the per-cell UPS 2026/2025 base-rate ratios. |
| `resi` | 4 | The Residential surcharge rule of one service, same factor pool. |
| `threshold` | 12 | One threshold of one rule moves: weight ±5 lb, length/width ±2 in, length+girth ±5 in, cubic ±10%. |
| `das_list` | 6 | One carrier's DAS ZIP list is revised (+200/−200 ZIPs), for every service that shares the list. |

The whole stream is replayed 5 times, each time on a fresh instance.

Strategies compared on every event:

- `pred`: predicate-level support, with the logic of `erp_pilot.predicate_support`.
- `pred_fast`: the same support set, computed by a feature-aware scan with a per-predicate mask
  cache; equality with `pred` is asserted.
- `service`: service-level support.
- `full`: top-2 recomputation of all stored parcels.

Fields:

- `total_*_s` (summaries over repetitions);
- `speedup_service_over_pred[_fast]`, `speedup_full_over_pred[_fast]`;
- `support_share_*`: support divided by the coverage of the edited service(s);
- `by_kind`: per-kind totals and speedups;
- `per_event_median`: per-event medians over the repetitions;
- `exact_final_state_all_reps`: the final state equals a full recomputation in every repetition;
- `pred_equals_service_all_events_all_reps`: the predicate-level and service-level states are equal
  after every event;
- `factor_pool_quantiles`: quantiles of the re-pricing factor pool.

## What-if contract evaluation (round-2 item B2, Python): `whatif_<n>.json`

Produced by `code/exp_rev2_whatif.py`. There are 100 candidate contracts, each a rewrite of the UPS
Ground rate table (per-zone discount U(0, 35%) plus a per-ten-band adjustment N(0, 2%)). They are
evaluated over 2M stored parcels at n = 19 (PUB2) and n = 60 (PUB2x60). Methods, rotated per
contract:

- `brute`: a full batch per contract;
- `rateq`: a table rewrite followed by top-2 repair on the service's coverage;
- `exclude_one`: the cheapest service among the others is computed once; each contract then only
  evaluates UPS Ground.

Fields:

- `contract_cpu_s` (summary over contracts), `total_cpu_s`;
- `end_to_end_cpu_s`: includes the one-time initialisation of each method;
- `rateq_init_s`, `exclude_one_init_s`;
- `contracts_exact`: contracts on which every per-parcel answer and the total equal brute force;
- `best_contract`, `best_total_usd`, `base_total_usd`;
- `rateq_recomputed_mean`: parcels recomputed in full per contract.
