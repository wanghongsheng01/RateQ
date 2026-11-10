# C++ core: E1 (Q1 single query) and E2 (Q2 batch)

**Method.** Mac: Apple M4 (10 cores, shared with other sessions). C++17, single thread, `clang++ -O3 -march=native -ffp-contract=off`, process QoS user-interactive (prefers performance cores). All times are **thread CPU time** (CLOCK_THREAD_CPUTIME_ID). Methods are interleaved per query (E1) and inside every repetition (E2), so they see the same machine state. E1: 1000 queries per seed, one warm-up pass, every query timed once per method, the calibrated clock overhead (median CPU time of an empty measured body) subtracted. E2: 100,000 parcels per seed, one warm-up run, median of 5 repetitions. 5 seeds per configuration; values are mean over seeds ± 95% confidence half-width (Student t). 1-minute load average at the start of the runs: 19.4–71.0. Evaluations per parcel do not depend on the machine.

**Machines.** PUB2-family rows (machine = server) were timed by the lead on the Linux server (Intel Xeon Platinum 8336C, g++ -O3 -march=native -ffp-contract=off, pinned with taskset, same interleaved thread-CPU-time protocol; server 1-minute load at run start: 0.2–28.7; environment: {"cpu": "Intel Xeon Platinum 8336C (server, taskset-pinned)", "threads": 1, "compiler": "g++ (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0", "flags": "-O3 -march=native -std=c++17 -ffp-contract=off; g++ on Linux, pinned with taskset -c 4", "LOAD_MAX": 10.0, "WAIT_S": 5.0, "E1": "1000 queries per seed; one w). ERP rows and any row marked Mac were timed on the Mac as described above; the Mac numbers of the PUB2 family are kept as a secondary table at the end.

**brute** in E1 = scalar C++ brute force evaluating every service per parcel, with zone, DAS and billable-weight lookups shared across services that use the same chart (B1s, the faster scalar variant; B1 mirrors `brute1`). **brute** in E2 = column-at-a-time C++ brute force over blocks of 512 parcels (B1v, mirrors `brute_vec`, the Python E2 baseline; the fastest C++ brute force on batches). **RQ_base** = C++ RQ_base: same index, lower bounds and stop rule as `index.py` (query1 = best-first threshold algorithm in E1, query_batch = round-robin threshold algorithm in E2); a candidate's exact cost reads its zone from the parcel's signature. **agreement** = share of parcels on which every C++ method (B1, B1s, B1v, RQ_base single and batch) returns exactly the same service and cost as the Python brute force, over all E1 and E2 seeds. **RQ_base evals/parcel** = exact cost evaluations per parcel (brute force: n). For n ≥ 240 the slow per-service brute B1 is not timed in E2 (B1s and B1v are).

Instances: PUB2n11 = first 11 official PUB2 services (5 USPS + 6 UPS); PUB2 = 19 official services (5 USPS, 7 UPS, 7 FedEx); PUB2n31 = 19 official services + 12 seeded contract variants (build_pub2_scaled(31)); PUB2x60 = build_pub2_scaled(60); PUB2x240 = build_pub2_scaled(240); PUB2x1000 = build_pub2_scaled(1000); ERP = real enterprise rate cards (10 US services), real parcels.

## E1 single query (Q1): p50 thread CPU time per query, µs

| instance | workload | n | brute | RQ_base | speedup | agreement | RQ_base evals/parcel | machine |
|---|---|---|---|---|---|---|---|---|
| PUB2n11 | olist | 11 | 1.206 ± 0.007 | 0.342 ± 0.006 | 3.5 ± 0.1× | 1.000 (505,000/505,000) | 1.14 | server |
| PUB2n11 | furn | 11 | 1.129 ± 0.015 | 0.462 ± 0.021 | 2.4 ± 0.1× | 1.000 (505,000/505,000) | 1.38 | server |
| PUB2 | olist | 19 | 2.477 ± 0.004 | 0.427 ± 0.005 | 5.8 ± 0.1× | 1.000 (505,000/505,000) | 1.22 | server |
| PUB2 | furn | 19 | 2.409 ± 0.012 | 0.574 ± 0.027 | 4.2 ± 0.2× | 1.000 (505,000/505,000) | 1.93 | server |
| PUB2n31 | olist | 31 | 3.723 ± 0.006 | 0.428 ± 0.007 | 8.7 ± 0.1× | 1.000 (505,000/505,000) | 1.38 | server |
| PUB2n31 | furn | 31 | 3.592 ± 0.013 | 0.825 ± 0.041 | 4.4 ± 0.2× | 1.000 (505,000/505,000) | 2.97 | server |
| PUB2x60 | olist | 60 | 6.679 ± 0.022 | 0.448 ± 0.012 | 14.9 ± 0.4× | 1.000 (505,000/505,000) | 1.52 | server |
| PUB2x60 | furn | 60 | 6.248 ± 0.044 | 1.041 ± 0.150 | 6.1 ± 0.9× | 1.000 (505,000/505,000) | 4.11 | server |
| PUB2x240 | olist | 240 | 27.821 ± 7.896 | 1.032 ± 0.354 | 27.6 ± 5.9× | 1.000 (505,000/505,000) | 3.15 | server |
| PUB2x240 | furn | 240 | 34.212 ± 2.287 | 2.662 ± 0.586 | 13.2 ± 2.9× | 1.000 (505,000/505,000) | 13.56 | server |
| PUB2x1000 | olist | 1000 | 225.362 ± 46.739 | 2.042 ± 0.540 | 111.2 ± 8.4× | 1.000 (505,000/505,000) | 8.75 | server |
| PUB2x1000 | furn | 1000 | 169.960 ± 1.992 | 4.506 ± 2.212 | 45.5 ± 31.8× | 1.000 (505,000/505,000) | 54.48 | server |
| ERP | erp | 10 | 2.254 ± 0.628 | 2.538 ± 0.753 | 0.9 ± 0.1× | 1.000 (703,109/703,109) | 4.63 | Mac |

## E2 batch (Q2): thread CPU seconds per 100,000 parcels

| instance | workload | n | brute | RQ_base | speedup | agreement | RQ_base evals/parcel | machine |
|---|---|---|---|---|---|---|---|---|
| PUB2n11 | olist | 11 | 0.0908 ± 0.0001 | 0.0263 ± 0.0001 | 3.5 ± 0.0× | 1.000 (505,000/505,000) | 1.17 | server |
| PUB2n11 | furn | 11 | 0.1101 ± 0.0002 | 0.0424 ± 0.0001 | 2.6 ± 0.0× | 1.000 (505,000/505,000) | 1.42 | server |
| PUB2 | olist | 19 | 0.1651 ± 0.0010 | 0.0353 ± 0.0001 | 4.7 ± 0.0× | 1.000 (505,000/505,000) | 1.26 | server |
| PUB2 | furn | 19 | 0.1957 ± 0.0009 | 0.0682 ± 0.0004 | 2.9 ± 0.0× | 1.000 (505,000/505,000) | 1.99 | server |
| PUB2n31 | olist | 31 | 0.2253 ± 0.0009 | 0.0391 ± 0.0001 | 5.8 ± 0.0× | 1.000 (505,000/505,000) | 1.43 | server |
| PUB2n31 | furn | 31 | 0.2790 ± 0.0007 | 0.0900 ± 0.0002 | 3.1 ± 0.0× | 1.000 (505,000/505,000) | 3.06 | server |
| PUB2x60 | olist | 60 | 0.3771 ± 0.0111 | 0.0424 ± 0.0017 | 8.9 ± 0.1× | 1.000 (505,000/505,000) | 1.58 | server |
| PUB2x60 | furn | 60 | 0.4868 ± 0.0025 | 0.1183 ± 0.0005 | 4.1 ± 0.0× | 1.000 (505,000/505,000) | 4.28 | server |
| PUB2x240 | olist | 240 | 2.3514 ± 0.2553 | 0.1017 ± 0.0026 | 23.1 ± 2.1× | 1.000 (505,000/505,000) | 3.21 | server |
| PUB2x240 | furn | 240 | 2.5698 ± 0.4972 | 0.3586 ± 0.0774 | 7.2 ± 1.1× | 1.000 (505,000/505,000) | 13.97 | server |
| PUB2x1000 | olist | 1000 | 5.6303 ± 0.0517 | 0.1341 ± 0.0071 | 42.0 ± 2.0× | 1.000 (505,000/505,000) | 8.92 | server |
| PUB2x1000 | furn | 1000 | 7.2242 ± 0.0241 | 0.8267 ± 0.0071 | 8.7 ± 0.1× | 1.000 (505,000/505,000) | 56.29 | server |
| ERP | erp | 10 | 0.3976 ± 0.1059 | 0.3953 ± 0.1295 | 1.0 ± 0.1× | 1.000 (703,109/703,109) | 4.66 | Mac |

## E1 latency distribution (thread CPU µs per query: p50 / p95 / p99 / mean)

| instance/workload | n | seeds | B1 p50 / p95 / p99 / mean | B1s p50 / p95 / p99 / mean | RQ_base p50 / p95 / p99 / mean | speedup vs B1s p50 / p95 / p99 / mean | speedup vs B1 p50 | stream CPU mean B1s / RQ_base |
|---|---|---|---|---|---|---|---|---|
| PUB2n11/olist | 11 | 5 | 1.645 / 1.876 / 2.071 / 1.655 | 1.206 / 1.405 / 1.534 / 1.233 | 0.342 / 0.715 / 0.971 / 0.388 | 3.5× / 2.0× / 1.6× / 3.2× | 4.8 ± 0.1× | 1.152 ± 0.005 / 0.301 ± 0.010 |
| PUB2n11/furn | 11 | 5 | 1.581 / 1.876 / 2.084 / 1.504 | 1.129 / 1.402 / 1.520 / 1.079 | 0.462 / 0.920 / 1.059 / 0.500 | 2.4× / 1.5× / 1.4× / 2.2× | 3.4 ± 0.2× | 1.073 ± 0.010 / 0.450 ± 0.008 |
| PUB2/olist | 19 | 5 | 3.075 / 3.367 / 3.623 / 3.082 | 2.477 / 2.704 / 2.833 / 2.483 | 0.427 / 1.126 / 1.426 / 0.504 | 5.8× / 2.4× / 2.0× / 4.9× | 7.2 ± 0.1× | 2.468 ± 0.014 / 0.392 ± 0.007 |
| PUB2/furn | 19 | 5 | 2.969 / 3.363 / 3.566 / 2.776 | 2.409 / 2.732 / 2.846 / 2.199 | 0.574 / 1.456 / 1.964 / 0.762 | 4.2× / 1.9× / 1.5× / 2.9× | 5.2 ± 0.3× | 2.244 ± 0.022 / 0.713 ± 0.020 |
| PUB2n31/olist | 31 | 5 | 4.841 / 5.227 / 6.649 / 4.843 | 3.723 / 4.021 / 4.397 / 3.710 | 0.428 / 1.195 / 1.808 / 0.537 | 8.7× / 3.4× / 2.4× / 6.9× | 11.3 ± 0.2× | 3.810 ± 0.012 / 0.440 ± 0.013 |
| PUB2n31/furn | 31 | 5 | 4.699 / 5.316 / 6.461 / 4.397 | 3.592 / 4.154 / 4.673 / 3.284 | 0.825 / 1.883 / 2.650 / 0.975 | 4.4× / 2.2× / 1.8× / 3.4× | 5.7 ± 0.3× | 3.420 ± 0.041 / 0.947 ± 0.022 |
| PUB2x60/olist | 60 | 5 | 8.896 / 9.467 / 12.228 / 8.814 | 6.679 / 7.232 / 9.848 / 6.617 | 0.448 / 1.498 / 2.288 / 0.585 | 14.9× / 4.8× / 4.3× / 11.3× | 19.8 ± 0.5× | 6.816 ± 0.028 / 0.468 ± 0.011 |
| PUB2x60/furn | 60 | 5 | 9.171 / 11.424 / 15.870 / 8.719 | 6.248 / 7.274 / 7.546 / 5.698 | 1.041 / 2.990 / 3.899 / 1.364 | 6.1× / 2.4× / 1.9× / 4.2× | 8.9 ± 1.4× | 6.324 ± 0.131 / 1.362 ± 0.054 |
| PUB2x240/olist | 240 | 5 | 52.210 / 75.181 / 86.551 / 53.539 | 27.821 / 43.827 / 53.612 / 29.437 | 1.032 / 4.973 / 8.734 / 1.519 | 27.6× / 9.0× / 6.2× / 19.4× | 51.7 ± 8.5× | 45.121 ± 13.267 / 1.115 ± 0.280 |
| PUB2x240/furn | 240 | 5 | 72.062 / 90.059 / 98.811 / 67.536 | 34.212 / 48.640 / 55.509 / 32.250 | 2.662 / 11.604 / 18.416 / 4.618 | 13.2× / 4.2× / 3.0× / 7.0× | 27.7 ± 5.7× | 49.919 ± 3.170 / 4.325 ± 0.264 |
| PUB2x1000/olist | 1000 | 5 | 300.269 / 371.015 / 400.319 / 302.347 | 225.362 / 271.077 / 290.087 / 225.115 | 2.042 / 17.639 / 46.417 / 4.305 | 111.2× / 15.7× / 6.4× / 52.7× | 147.2 ± 8.2× | 230.742 ± 51.517 / 2.134 ± 0.637 |
| PUB2x1000/furn | 1000 | 5 | 217.830 / 264.287 / 281.746 / 202.004 | 169.960 / 207.548 / 216.975 / 151.802 | 4.506 / 41.347 / 64.619 / 14.170 | 45.5× / 5.0× / 3.4× / 10.7× | 58.2 ± 40.5× | 163.728 ± 8.195 / 10.217 ± 0.509 |
| ERP/erp | 10 | 5 | 7.279 / 22.662 / 48.155 / 9.747 | 2.254 / 6.060 / 15.253 / 3.318 | 2.538 / 7.284 / 16.159 / 3.634 | 0.9× / 0.9× / 1.0× / 0.9× | 2.9 ± 0.3× | 5.484 ± 2.638 / 3.887 ± 1.632 |

Wall-clock E1 (µs, p50 / p95 / p99), for reference (Mac rows are inflated by the shared machine; server rows ran on an idle pinned core):

| instance/workload | B1s | RQ_base |
|---|---|---|
| PUB2n11/olist | 1.206 / 1.403 / 1.511 | 0.351 / 0.716 / 0.967 |
| PUB2n11/furn | 1.133 / 1.398 / 1.506 | 0.472 / 0.924 / 1.054 |
| PUB2/olist | 2.460 / 2.686 / 2.815 | 0.434 / 1.125 / 1.423 |
| PUB2/furn | 2.393 / 2.715 / 2.819 | 0.584 / 1.453 / 1.940 |
| PUB2n31/olist | 3.704 / 4.004 / 4.341 | 0.437 / 1.195 / 1.801 |
| PUB2n31/furn | 3.573 / 4.127 / 4.597 | 0.827 / 1.869 / 2.612 |
| PUB2x60/olist | 6.660 / 7.212 / 8.801 | 0.458 / 1.491 / 2.279 |
| PUB2x60/furn | 6.225 / 7.245 / 7.520 | 1.044 / 2.977 / 3.878 |
| PUB2x240/olist | 27.818 / 43.732 / 53.425 | 1.051 / 4.947 / 8.710 |
| PUB2x240/furn | 34.159 / 48.587 / 55.316 | 2.631 / 11.565 / 18.165 |
| PUB2x1000/olist | 225.034 / 270.692 / 289.665 | 2.068 / 17.650 / 46.382 |
| PUB2x1000/furn | 169.673 / 207.108 / 216.548 | 4.442 / 41.282 / 64.430 |
| ERP/erp | 2.288 / 7.069 / 36.024 | 2.546 / 8.152 / 48.450 |

## RQ_base exact cost evaluations per parcel vs n (machine-independent; brute force evaluates n services per parcel)

| instance | n | workload | E1 query1 evals/parcel | E2 batch evals/parcel | evaluations saved vs brute (E1) | Python RQ_base evals (E1, seed 11) |
|---|---|---|---|---|---|---|
| PUB2n11 | 11 | olist | 1.14 | 1.17 | 9.7× | — |
| PUB2n11 | 11 | furn | 1.38 | 1.42 | 8.0× | — |
| PUB2 | 19 | olist | 1.22 | 1.26 | 15.5× | 1.20 |
| PUB2 | 19 | furn | 1.93 | 1.99 | 9.8× | 1.94 |
| PUB2n31 | 31 | olist | 1.38 | 1.43 | 22.5× | — |
| PUB2n31 | 31 | furn | 2.97 | 3.06 | 10.4× | — |
| PUB2x60 | 60 | olist | 1.52 | 1.58 | 39.4× | 1.50 |
| PUB2x60 | 60 | furn | 4.11 | 4.28 | 14.6× | 4.12 |
| PUB2x240 | 240 | olist | 3.15 | 3.21 | 76.2× | 2.98 |
| PUB2x240 | 240 | furn | 13.56 | 13.97 | 17.7× | 13.49 |
| PUB2x1000 | 1000 | olist | 8.75 | 8.92 | 114.2× | 8.20 |
| PUB2x1000 | 1000 | furn | 54.48 | 56.29 | 18.4× | 54.59 |
| ERP | 10 | erp | 4.63 | 4.66 | 2.2× | — |

## Detail E2: all C++ variants (thread CPU seconds per 100,000 parcels; wall clock in the last columns)

B1 = per-service brute (mirrors `brute1`); B1s = shared-lookup brute; B1v = column-at-a-time brute (mirrors `brute_vec`); RQ_base batch = round-robin threshold algorithm (mirrors `RQIndex.query_batch`); RQ_base q1-loop = best-first `query1` per parcel.

| instance/workload | seeds | parcels timed per seed | B1 | B1s | B1v | RQ_base batch | RQ_base q1-loop | evals/parcel (batch / q1) | batch vs B1 | batch vs B1s | batch vs B1v | q1-loop vs B1v | wall B1v | wall RQ_base batch | load1 at start |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PUB2n11/olist | 5 | 100,000 | 0.1555 ± 0.0005 | 0.1145 ± 0.0003 | 0.0908 ± 0.0001 | 0.0263 ± 0.0001 | 0.0280 ± 0.0002 | 1.17 / 1.15 | 5.9 ± 0.0× | 4.4 ± 0.0× | 3.5 ± 0.0× | 3.2 ± 0.0× | 0.0908 ± 0.0001 | 0.0263 ± 0.0001 | 0.2–0.3 |
| PUB2n11/furn | 5 | 100,000 | 0.1419 ± 0.0006 | 0.1070 ± 0.0006 | 0.1101 ± 0.0002 | 0.0424 ± 0.0001 | 0.0431 ± 0.0002 | 1.42 / 1.41 | 3.4 ± 0.0× | 2.5 ± 0.0× | 2.6 ± 0.0× | 2.6 ± 0.0× | 0.1101 ± 0.0002 | 0.0424 ± 0.0001 | 0.3–0.4 |
| PUB2/olist | 5 | 100,000 | 0.3002 ± 0.0007 | 0.2467 ± 0.0009 | 0.1651 ± 0.0010 | 0.0353 ± 0.0001 | 0.0374 ± 0.0002 | 1.26 / 1.23 | 8.5 ± 0.0× | 7.0 ± 0.0× | 4.7 ± 0.0× | 4.4 ± 0.0× | 0.1651 ± 0.0010 | 0.0353 ± 0.0001 | 0.9–1.1 |
| PUB2/furn | 5 | 100,000 | 0.2713 ± 0.0008 | 0.2246 ± 0.0007 | 0.1957 ± 0.0009 | 0.0682 ± 0.0004 | 0.0701 ± 0.0004 | 1.99 / 1.97 | 4.0 ± 0.0× | 3.3 ± 0.0× | 2.9 ± 0.0× | 2.8 ± 0.0× | 0.1957 ± 0.0009 | 0.0682 ± 0.0004 | 1.1–1.4 |
| PUB2n31/olist | 5 | 100,000 | 0.4742 ± 0.0006 | 0.3799 ± 0.0005 | 0.2253 ± 0.0009 | 0.0391 ± 0.0001 | 0.0414 ± 0.0001 | 1.43 / 1.38 | 12.1 ± 0.0× | 9.7 ± 0.0× | 5.8 ± 0.0× | 5.4 ± 0.0× | 0.2253 ± 0.0009 | 0.0391 ± 0.0001 | 0.5–0.7 |
| PUB2n31/furn | 5 | 100,000 | 0.4269 ± 0.0003 | 0.3415 ± 0.0005 | 0.2790 ± 0.0007 | 0.0900 ± 0.0002 | 0.0934 ± 0.0003 | 3.06 / 3.04 | 4.7 ± 0.0× | 3.8 ± 0.0× | 3.1 ± 0.0× | 3.0 ± 0.0× | 0.2791 ± 0.0007 | 0.0900 ± 0.0002 | 0.8–0.9 |
| PUB2x60/olist | 5 | 100,000 | 0.8878 ± 0.0304 | 0.6994 ± 0.0268 | 0.3771 ± 0.0111 | 0.0424 ± 0.0017 | 0.0446 ± 0.0018 | 1.58 / 1.53 | 20.9 ± 0.1× | 16.5 ± 0.1× | 8.9 ± 0.1× | 8.4 ± 0.1× | 0.3771 ± 0.0111 | 0.0424 ± 0.0017 | 1.4–2.9 |
| PUB2x60/furn | 5 | 100,000 | 0.8079 ± 0.0038 | 0.6309 ± 0.0023 | 0.4868 ± 0.0025 | 0.1183 ± 0.0005 | 0.1227 ± 0.0006 | 4.28 / 4.20 | 6.8 ± 0.0× | 5.3 ± 0.0× | 4.1 ± 0.0× | 4.0 ± 0.0× | 0.4868 ± 0.0025 | 0.1183 ± 0.0005 | 4.3–6.7 |
| PUB2x240/olist | 5 | 100,000 | — | 5.2549 ± 0.6001 | 2.3514 ± 0.2553 | 0.1017 ± 0.0026 | 0.1043 ± 0.0029 | 3.21 / 3.18 | — | 51.6 ± 4.8× | 23.1 ± 2.1× | 22.5 ± 2.0× | 2.3786 ± 0.2910 | 0.1017 ± 0.0026 | 15.5–27.8 |
| PUB2x240/furn | 5 | 100,000 | — | 4.7883 ± 1.0086 | 2.5698 ± 0.4972 | 0.3586 ± 0.0774 | 0.3885 ± 0.0756 | 13.97 / 13.92 | — | 13.4 ± 1.5× | 7.2 ± 1.1× | 6.6 ± 0.8× | 2.5824 ± 0.5127 | 0.3592 ± 0.0779 | 25.7–28.2 |
| PUB2x1000/olist | 5 | 100,000 | — | 20.1881 ± 1.0456 | 5.6303 ± 0.0517 | 0.1341 ± 0.0071 | 0.1550 ± 0.0066 | 8.92 / 8.89 | — | 150.6 ± 2.8× | 42.0 ± 2.0× | 36.3 ± 1.3× | 5.6304 ± 0.0516 | 0.1341 ± 0.0071 | 6.8–19.0 |
| PUB2x1000/furn | 5 | 100,000 | — | 17.1048 ± 0.0801 | 7.2242 ± 0.0241 | 0.8267 ± 0.0071 | 0.9722 ± 0.0069 | 56.29 / 56.19 | — | 20.7 ± 0.1× | 8.7 ± 0.1× | 7.4 ± 0.1× | 7.2245 ± 0.0240 | 0.8268 ± 0.0070 | 5.0–9.3 |
| ERP/erp | 5 | 100,000 | 0.5512 ± 0.1503 | 0.5577 ± 0.1923 | 0.3976 ± 0.1059 | 0.3953 ± 0.1295 | 0.3965 ± 0.0972 | 4.66 / 4.64 | 1.4 ± 0.2× | 1.4 ± 0.2× | 1.0 ± 0.1× | 1.0 ± 0.1× | 1.7281 ± 1.7001 | 1.3465 ± 1.3280 | 51.9–71.0 |

## C++ versus the earlier Python numbers (Python = results/pilot_E1_pub2.json, pilot_E2_pub2.json, erp_pilot_USA.json; Python wall time, seed 11 / 22; C++ thread CPU time, mean over seeds)

| instance/workload | E1 p50 Python brute1 µs | E1 p50 C++ brute (B1s) µs | E1 p50 Python RQ_base µs | E1 p50 C++ RQ_base µs | E1 speedup Python | E1 speedup C++ | E2 Python brute_vec s | E2 C++ brute (B1v) s | E2 Python RQ_base s | E2 C++ RQ_base s | E2 speedup Python | E2 speedup C++ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PUB2/olist | 123.1 | 2.477 | 10.2 | 0.427 | 12.0× | 5.8× | 0.406 | 0.1651 | 0.058 | 0.0353 | 7.0× | 4.7× |
| PUB2/furn | 120.2 | 2.409 | 13.1 | 0.574 | 9.2× | 4.2× | 0.420 | 0.1957 | 0.084 | 0.0682 | 5.0× | 2.9× |
| PUB2x60/olist | 405.9 | 6.679 | 10.4 | 0.448 | 39.1× | 14.9× | 1.236 | 0.3771 | 0.070 | 0.0424 | 17.7× | 8.9× |
| PUB2x60/furn | 385.2 | 6.248 | 28.3 | 1.041 | 13.6× | 6.0× | 1.332 | 0.4868 | 0.184 | 0.1183 | 7.2× | 4.1× |
| PUB2x240/olist | 1,602 | 27.821 | 10.1 | 1.032 | 158.9× | 26.9× | 4.696 | 2.3514 | 0.161 | 0.1017 | 29.2× | 23.1× |
| PUB2x240/furn | 1,573 | 34.212 | 45.0 | 2.662 | 35.0× | 12.9× | 6.135 | 2.5698 | 0.673 | 0.3586 | 9.1× | 7.2× |
| PUB2x1000/olist | 8,669 | 225.362 | 10.8 | 2.042 | 800.1× | 110.4× | 19.104 | 5.6303 | 0.882 | 0.1341 | 21.7× | 42.0× |
| PUB2x1000/furn | 10,294 | 169.960 | 152.9 | 4.506 | 67.3× | 37.7× | 20.074 | 7.2242 | 4.065 | 0.8267 | 4.9× | 8.7× |
| ERP/erp | 1,615 | 2.254 | 1,609 | 2.538 | 1.0× | 0.9× | 12.663 | 0.3976 | 11.826 | 0.3953 | 1.1× | 1.0× |

Python had no n = 11 / 31 runs. ERP Python reference: E1 on 1000 sampled parcels, E2 on all real parcels scaled to 100,000; C++ ERP: 5 random samples of 1000 (E1) and 100,000 (E2) real parcels.

## Secondary: Mac (Apple M4, shared, thread CPU time) numbers for the PUB2 family

| instance | workload | n | E1 brute p50 µs | E1 RQ_base p50 µs | E1 speedup | E2 brute s/100k | E2 RQ_base s/100k | E2 speedup | Mac load1 at start |
|---|---|---|---|---|---|---|---|---|---|
| PUB2n11 | olist | 11 | 1.400 ± 0.107 | 0.483 ± 0.028 | 2.9 ± 0.2× | 0.0957 ± 0.0146 | 0.0327 ± 0.0065 | 3.0 ± 0.4× | 55.3–69.1 |
| PUB2n11 | furn | 11 | 0.858 ± 0.316 | 0.442 ± 0.202 | 2.0 ± 0.4× | 0.0896 ± 0.0164 | 0.0460 ± 0.0139 | 2.0 ± 0.5× | 50.3–62.2 |
| PUB2 | olist | 19 | 2.970 ± 1.132 | 0.792 ± 0.312 | 3.8 ± 0.8× | 0.1420 ± 0.0202 | 0.0388 ± 0.0083 | 3.8 ± 1.1× | 47.7–71.0 |
| PUB2 | furn | 19 | 2.079 ± 1.129 | 0.950 ± 0.422 | 2.2 ± 0.6× | 0.1727 ± 0.0404 | 0.1014 ± 0.0210 | 1.7 ± 0.3× | 56.5–69.0 |
| PUB2n31 | olist | 31 | 3.950 ± 1.999 | 0.708 ± 0.341 | 5.7 ± 1.6× | 0.2490 ± 0.0203 | 0.0575 ± 0.0023 | 4.3 ± 0.4× | 43.8–68.2 |
| PUB2n31 | furn | 31 | 2.767 ± 1.110 | 1.025 ± 0.352 | 2.7 ± 0.7× | 0.2021 ± 0.0548 | 0.0915 ± 0.0308 | 2.3 ± 0.5× | 48.5–57.9 |
| PUB2x60 | olist | 60 | 5.400 ± 2.390 | 0.792 ± 0.325 | 7.0 ± 2.1× | 0.3830 ± 0.0659 | 0.0588 ± 0.0069 | 6.5 ± 0.9× | 45.7–56.2 |
| PUB2x60 | furn | 60 | 4.417 ± 0.563 | 1.333 ± 0.240 | 3.3 ± 0.5× | 0.2737 ± 0.1122 | 0.0863 ± 0.0526 | 3.3 ± 0.5× | 19.4–60.0 |

## C++ RQ_base index (built in C++, compared array by array with the Python RQIndex)

| instance | n | elementary key intervals | signatures | local signatures | groups | elementary weight intervals | index MB | C++ build s (CPU-contended) | identical to Python |
|---|---|---|---|---|---|---|---|---|---|
| PUB2n11 | 11 | 1,415 | 31 | 26 | 3 | 377 | 0.15 | 0.001 | yes |
| PUB2 | 19 | 1,494 | 71 | 42 | 5 | 679 | 0.27 | 0.002 | yes |
| PUB2n31 | 31 | 1,494 | 71 | 42 | 5 | 679 | 0.43 | 0.004 | yes |
| PUB2x60 | 60 | 1,494 | 71 | 42 | 5 | 679 | 0.82 | 0.004 | yes |
| PUB2x240 | 240 | 1,494 | 71 | 42 | 5 | 679 | 3.17 | 0.019 | yes |
| PUB2x1000 | 1000 | 1,494 | 71 | 42 | 5 | 679 | 13.01 | 0.052 | yes |
| ERP | 10 | 366,290 | 148 | 65 | 4 | 1555 | 4.91 | 0.170 | yes |
