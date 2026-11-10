#!/usr/bin/env bash
# Rebuild RateQ and rerun the public-data experiments of the paper.
#
#   scripts/reproduce.sh setup      create .venv from requirements.txt and build cpp/rq          (~2 min)
#   scripts/reproduce.sh check      regenerate numbers, tables and figures from the shipped results
#                                   and compare with paper/ (no experiment is rerun)            (~1 min)
#   scripts/reproduce.sh smoke      exactness smoke test of the Python engine on all generators   (~2 min)
#   scripts/reproduce.sh cpp-smoke  export PUB2 inputs and verify the C++ engine against Python  (~2 min)
#   scripts/reproduce.sh pilot      Python E1-E5 on PUB2, PUB2x60, PUB2x240, PUB2x1000 (pilot_E*_pub2.json)
#   scripts/reproduce.sh synthetic  Python E1, E2 (tuned SQL baseline), E3 on SYN200 and the other
#                                   generators (pilot_E1.json, pilot_E2_tuned.json, pilot_E3.json)
#   scripts/reproduce.sh cpp        export all public C++ inputs, verify, and time E1/E2 (cpp_*.json)
#   scripts/reproduce.sh rev1       ablations, B7 cache, B8 IVM, sensitivity, 2M/20M scale (results/rev1/)
#   scripts/reproduce.sh numbers    regenerate paper/numbers.tex, paper/tables, paper/figs in place
#   scripts/reproduce.sh all        setup, smoke, cpp-smoke, pilot, synthetic, cpp, rev1, numbers
#
# Experiments overwrite the shipped result files under results/ (compare with `git diff --stat`;
# restore with `git checkout -- results paper`). Timings depend on the machine and its load; the
# reported numbers are ratios measured within one run. See README.md for the runtimes measured by
# the authors. Enterprise experiments need the company data and cannot be rerun from this repository.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
CODE="$ROOT/code"
export PYTHONPATH="$CODE" MPLBACKEND=Agg
run() { echo "+ $*" >&2; (cd "$CODE" && "$@"); }

setup() {
  if [ ! -x "$ROOT/.venv/bin/python" ]; then
    "${BASE_PYTHON:-python3}" -m venv "$ROOT/.venv"
    "$ROOT/.venv/bin/python" -m pip install --upgrade pip > /dev/null
    "$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"
  fi
  make -C "$ROOT/cpp" rq
}
check() { PYTHON="$PY" "$ROOT/scripts/check_numbers.sh"; }
smoke() { run "$PY" smoke.py 5000; }
cpp_smoke() {
  run "$PY" export_bin.py pub2 PUB2
  run "$PY" run_cpp.py verify pub2 PUB2
}
pilot() {
  PILOT_INSTS=PUB2,PUB2x60,PUB2x240,PUB2x1000 PILOT_TAG=_pub2 run "$PY" pilot.py E1 E2 E3 E4 E5
}
synthetic() {
  run "$PY" pilot.py E1 E3
  PILOT_TAG=_tuned run "$PY" pilot.py E2
}
cpp() {
  run "$PY" export_bin.py pub2 PUB2,PUB2x60,PUB2x240,PUB2x1000,PUB2n11,PUB2n31
  run "$PY" run_cpp.py verify pub2 PUB2,PUB2x60,PUB2x240,PUB2x1000
  run "$PY" run_cpp.py verify pub2r PUB2n11,PUB2n31
  # timing protocol of the paper: one pinned core (Linux: taskset -c <core>), 5 seeds of 1000 queries
  # (E1) and of 100,000 parcels (E2); for n >= 240 E2 runs on 20,000 parcels per seed (scaled to
  # 100,000) without the slow per-service brute force B1; n = 1000 uses 3 seeds.
  export WAIT_S=5 LOAD_MAX=4 E2_REPS=5
  local PIN=()
  if command -v taskset > /dev/null; then PIN=(taskset -c "${PIN_CORE:-2}"); fi
  run ${PIN[@]+"${PIN[@]}"} "$PY" run_cpp.py time pub2r PUB2n11,PUB2n31
  run ${PIN[@]+"${PIN[@]}"} "$PY" run_cpp.py time pub2 PUB2,PUB2x60
  RQ_E2_N=20000 RQ_E2_SKIP=B1 run ${PIN[@]+"${PIN[@]}"} "$PY" run_cpp.py time pub2 PUB2x240
  RQ_E2_N=20000 RQ_E2_SKIP=B1 E1_SEEDS=11,12,13 E2_SEEDS=22,23,24 run ${PIN[@]+"${PIN[@]}"} "$PY" run_cpp.py time pub2 PUB2x1000
  echo "C++ timings were written to results/cpp_E1E2_pub2.json and results/cpp_E1E2_pub2r.json." >&2
  echo "The paper reads the server copies results/cpp_E1E2_pub2_server.json and cpp_E1E2_pub2r_server.json;" >&2
  echo "copy the new files over them to regenerate the paper numbers from your run." >&2
  run "$PY" run_cpp.py tables
}
rev1() {
  # each command writes results/rev1/<name>.json; the paper reads the copies in results/rev1/server/
  run "$PY" exp_rev1_ablations.py bound PUB2,PUB2x240 olist,furn _pub2_x240
  run "$PY" exp_rev1_ablations.py bound PUB2x1000 olist,furn _x1000
  run "$PY" exp_rev1_ablations.py topk 19,60,240 _v2
  for i in PUB2 PUB2x60 PUB2x240; do run "$PY" exp_rev1_b7_cache.py "$i" olist,furn "_$i"; done
  run "$PY" exp_rev1_b8_ivm.py 19,60,240 _n19_240_fuelfix
  run "$PY" exp_rev1_b8_ivm.py 1000 _n1000_fuelfix
  run "$PY" exp_rev1_sensitivity.py 240 all _n240
  run "$PY" exp_rev1_sensitivity.py 1000 0.05:0,0.05:0.3,0.05:0.6,0.15:0,0.15:0.3,0.15:0.6 _n1000a
  run "$PY" exp_rev1_sensitivity.py 1000 0.35:0,0.35:0.3,0.35:0.6,0.6:0,0.6:0.3,0.6:0.6 _n1000b
  run "$PY" exp_rev1_sensitivity.py 1000 worst _n1000w
  for n in 19 60 240; do run "$PY" exp_rev1_scale.py 2000000 "$n"; done
  REV1_FULLREPS=3 run "$PY" exp_rev1_scale.py 20000000 19     # 20M parcels: 2.2 GB of features + 1.6 GB of state; use a machine with >= 16 GB RAM
  mkdir -p "$ROOT/results/rev1/server"
  for f in "$ROOT"/results/rev1/{ablation_bound_pub2_x240,ablation_bound_x1000,ablation_topk_v2,b7_cache_PUB2,b7_cache_PUB2x60,b7_cache_PUB2x240,b8_ivm_n19_240_fuelfix,b8_ivm_n1000_fuelfix,sensitivity_n240,sensitivity_n1000a,sensitivity_n1000b,sensitivity_n1000w,scale_q3_2000000_19,scale_q3_2000000_60,scale_q3_2000000_240,scale_q3_20000000_19}.json; do
    [ -f "$f" ] && cp "$f" "$ROOT/results/rev1/server/"
  done
}
numbers() {
  run "$PY" make_numbers.py
  run "$PY" make_figs.py
}

[ $# -ge 1 ] || { sed -n '2,18p' "$0"; exit 1; }
for step in "$@"; do
  case "$step" in
    setup) setup ;; check) check ;; smoke) smoke ;; cpp-smoke) cpp_smoke ;; pilot) pilot ;;
    synthetic) synthetic ;; cpp) cpp ;; rev1) rev1 ;; numbers) numbers ;;
    all) setup; smoke; cpp_smoke; pilot; synthetic; cpp; rev1; numbers ;;
    *) echo "unknown step: $step" >&2; exit 1 ;;
  esac
done
