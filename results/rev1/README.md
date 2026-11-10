# results/rev1: round-1 revision experiments

Scripts: `code/exp_rev1_*.py` (they import `code/rateq/*`, never modify it). Run with
`cd code && PYTHONPATH=. python exp_rev1_<name>.py ...`.

## Conventions (all files)

- **Timer**: `time.thread_time_ns` (CPU time of the measuring thread). Methods are interleaved
  within each repetition; 5 repetitions.
- **Summary objects** (`summ`): `{"median", "mean", "ci95": [lo, hi] (t-interval of the mean over
  repetitions), "min", "max", "reps"}`. Unless a field says otherwise, the value summarised is one
  number per repetition (e.g. the mean latency of 1000 queries in that repetition).
- **Speedups** are computed per repetition (paired, same repetition) and then summarised.
- **agree / exact** fields: fraction of answers (cost and service id) identical to brute force, or a
  boolean for a maintained state equal to a fresh full recomputation. Every reported answer is
  checked.
- **Load**: `load_start`/`load_end`/`load` are `os.getloadavg()` (1/5/15 min); `uptime*` fields
  hold the `uptime` line. `_env` gives host, CPU, Python/numpy versions, pinned CPUs.
- **Hosts**: public-data runs (PUB2 family, olist/furniture) ran on the Linux server (Intel Xeon
  Platinum 8336C, one pinned physical core per process, see `_env.pinned_cpus`); runs that read the
  enterprise parcels (`entgeo`) ran on the Mac (Apple M4, shared, heavily loaded). Do not compare
  absolute times across hosts; compare methods within a file.
- Files ending in `_test.json` (and `scale_q3_20000_19.json`) are smoke tests with tiny sizes; ignore.
- **Use `results/rev1/server/*.json` for every public-data result.** The root-level copies dated
  11-09 (`ablation_bound_pub2_x240.json`, `ablation_topk.json`, `b7_cache_PUB2*.json`,
  `b8_ivm_n19_240.json`) are partial runs from 11-09 that were interrupted by a file-access loss.
  They are superseded. The ent-geo files (`b7_cache_entgeo_*`, `entgeo_E1E2_*`, dated 11-10) at the
  root are current.
- Workloads: `olist`, `furn` (as in the paper), `entgeo` (weights/dimensions/residential flag drawn
  from the enterprise's 208,535 real US parcels; destination ZCTA weighted by population, ACS 2023
  5-year table B01003; origin and day uniform). `entgeo_workload_stats.json` holds only aggregates.

## Files

### `b7_cache_<INST>.json`, `b7_cache_entgeo_<INST>.json` (#4, B7 result cache)
Key = (zone signature, DAS level under each of the 3 DAS maps, elementary billable-weight interval
per service group, bitmask of all distinct geometric predicates, residential flag, fuel period).
Exactness preconditions are asserted in the script; all answers are checked against brute force.
Per `"<INST>/<wl>"`:
- `cold_start["10000"|"100000"|"1000000"]`: `distinct_keys`, `hit_rate` (= 1 − distinct/parcels) of
  an empty cache over the first N parcels of a 1M stream.
- `cold_start_time_ordered_100k`: same, stream sorted by ship day.
- `table_after_1M`: `entries`, `python_dict_MB` (tracemalloc of a dict {key tuple: (cost, sid)}),
  `packed_MB` (entries × (key columns × 8 + 12 B)), `fill_cpu_s_rq_batch` (answer every distinct
  key with the RQ-Index batch), `agree_all_1M_parcels`.
- `steady_state_100k.hit_rate`: a fresh 100k stream against the warm 1M table.
- `Q1_us[method]`: `mean_us`, `p50_us` (summaries over reps of per-query thread time, 1000 queries of
  the steady stream). Methods: `B1_brute`, `RQ_index`, `B7_cache_steady` (warm table; misses answered
  by RQ-Index), `B7_cache_cold` (empty table), `B7_key_only` (key computation alone).
  `Q1_speedup_vs_B1_mean`.
- `Q2_100k_s[method].cpu_s`: batch of the 100k steady stream: `B1_brute_vec`, `RQ_batch`,
  `B7_batch_cold` (keys + dedupe + RQ batch on one representative per key), `B7_batch_steady`
  (keys + lookup in the warm table + RQ batch on missing keys). `Q2_speedup_vs_B1`.
- `cell_edit_invalidation` (20 random cell edits ±15%): `invalidated` entries (exact: signature maps
  the service to the edited zone and the tier-raised band is the edited band), `frac` of the table,
  `answers_changed`, `find_drop_s` (CPU to find and drop them), `refill_s` (RQ batch on their
  representatives), `rq_index_update_s` (RQ-Index row repair for the same edit), `refill_agree`;
  `stale_entries_after_all_edits` (must be 0).
- `fuel_week_edit_invalidation`: entries per fuel period (a fuel change invalidates one period).
- `RQ_index_MB`, `signatures`, `groups`, `distinct_geometric_predicates`, `key_columns`.

### `b8_ivm_n19_240.json`, `b8_ivm_n1000.json` (#5, B8 all-candidates IVM)
E5 setup (200k stored parcels). Per instance:
- `B8_matrix_MB` (int32 |X|×n matrix as allocated), `B8_matrix_MB_int32/int64` (analytic),
  `B8_state_MB`, `B8_init_cpu_s`; `B8_failure` if the matrix could not be allocated.
- `RQ_state_MB` (top-2 arrays + signature ids + (σ,e) bucket table), `RQ_state_breakdown_MB`,
  `RQ_init_cpu_s`; `shared_parcels_and_features_MB` (needed by every method).
- `edit_cpu_s[kind][method]` with kind ∈ {cell, band, fuel, gri} and method ∈ {`RQ` (RateQ: support +
  top-2 repair), `B8` (same exact delta U, column cells recomputed, per-parcel MIN repaired, rescans
  of the retained row when the minimum increases), `B8_col` (whole column(s) recomputed; n ≤ 240)};
  value = mean per-edit CPU seconds in a repetition, summarised over reps.
- `speedup_B8_over_RQ`, `speedup_B8col_over_RQ` (>1 means RateQ is faster).
- `sizes_mean[kind]`: `support`, `RQ_recomputed` (|R|), `B8_rescans`.
- `full_recompute_cpu_s`, `exact_RQ_top2`, `exact_B8_min`, `exact_B8col_min`,
  `B8_matrix_spotcheck_cols_exact`.

### `ablation_bound_pub2_x240.json`, `ablation_bound_x1000.json` (#16a)
Per `"<INST>/<wl>"`: `Q1_evals_per_parcel[method]`, `Q1_mean_us`, `Q1_p50_us`, `Q1_p95_us`
(summaries), `Q1_agree`, `batch_evals_per_parcel`, `batch_cpu_s` (20k parcels), `batch_agree`,
`build_cpu_s`. Methods: `B1_brute` (n evaluations), `RowMin` (bound = minimum of the zone row; one
list per signature and group), `RQ_index` (suffix minimum per elementary interval), `RQ_plus`
(class keys).

### `ablation_topk.json` (first run: cell/band/fuel) and `ablation_topk_v2.json` (#16b; adds `cell_up` and `gri`)
Per instance: `recomputed_R_mean[kind]["top1"|"top2"|"top3"]` (mean |R| per edit),
`edit_cpu_s[kind][topk]` (support + repair CPU s per edit), `support_mean`, `state_MB`, `init_cpu_s`,
`exact_final_state`, `top2_matches_Maintainer_R` (the generic top-k code with k=2 recomputes exactly
the parcels that `rateq.maintain.Maintainer` recomputes). Kinds: `cell` (±15%), `cell_up` (+1..15%),
`band`, `gri` (+5.9% whole service), `fuel` (+75 bp on one week of one class).

### `scale_q3_<N>_<n>.json` (#13, N = 2,000,000 / 20,000,000 stored parcels; n = 19, 60, 240)
`edit_cpu_s[kind]["RQ"|"service"]`, `full_recompute_cpu_s`, `speedup_full_over_RQ`,
`speedup_service_over_RQ`, `sizes_mean` (support, recomputed, service_support),
`exact_each_checked_rep`, `full_reps_timed`, `RQ_state_MB`, `features_MB`, `RQ_init_cpu_s`.
"service" = re-evaluating the edited service(s) on every parcel they serve (as pilot E5; repair not
included, so a lower bound).

### `entgeo_E1E2_<INST>.json` (#17)
Per `"<INST>/<wl>"` (wl ∈ entgeo, olist, furn, same harness and machine): `E1` (1000 queries:
`evals_per_parcel`, `mean_us`, `p50_us`, `p95_us`, `speedup_mean_vs_B1`, `speedup_p50_vs_B1`,
`agree`; methods `B1_brute`, `RQ_index`, `RQ_plus`), `E2` (100k parcels: `cpu_s`, `evals_per_parcel`,
`speedup_vs_B1`, `agree`; methods `B1_brute_vec`, `RQ_batch`, `RQ_plus_cold`, `RQ_plus_warm`).
`entgeo_workload_stats.json`: aggregate statistics of the enterprise shapes (count, distinct shapes,
percentiles) and of the ZCTA population table.

### `sensitivity_n240.json`, `sensitivity_n1000{a,b,w}.json` (#15)
Keys `"n<n>/<mode>/d<dmax>/r<rho>/<wl>"`; mode ∈ {normal, near (d∈[0,1%], no noise, unscaled
surcharges, no regional), identical (copies tie their base)}. Fields: `Q1_evals_per_parcel`,
`Q1_mean_us`, `Q1_p50_us`, `speedup_mean_vs_B1`, `speedup_p50_vs_B1` (B1 / method, per rep),
`Q1_agree`, `batch_evals_per_parcel`, `batch_agree`. `_generator_default_reproduces_PUB2xn` asserts
that dmax=0.35, rho=0.3 regenerates the paper's PUB2xn exactly.
