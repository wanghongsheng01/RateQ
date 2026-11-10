#!/usr/bin/env bash
# Maintainer helper: regenerate the reference outputs (paper/numbers.tex, paper/tables/*.tex,
# paper/figs/*.pdf, results/theta_sweep_erp.json, results/fig_example_facts.json, results/tables_cpp.md)
# from the shipped result files, in place. Used by sync_from_project.sh with REGEN_REFS=1 when the
# project's paper outputs are older than its result files. A figure is replaced only if its content
# changed (the /CreationDate timestamp alone does not count). Optional argument: a directory with
# the project's paper outputs, which are then compared with the regenerated ones (report only).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CMP="${1:-}"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"; else PY="$(command -v python3)"; fi
fi
TMP="$(mktemp -d "${TMPDIR:-/tmp}/rateq_regen.XXXXXX")"
[ -n "${KEEP_TMP:-}" ] || trap 'rm -rf "$TMP"' EXIT  # KEEP_TMP=1 keeps the work directory
cp -R "$ROOT/code" "$ROOT/data" "$ROOT/results" "$TMP/"
mkdir -p "$TMP/paper"
export MPLBACKEND=Agg
(cd "$TMP/code" && PYTHONPATH=. "$PY" make_numbers.py)
(cd "$TMP/code" && PYTHONPATH=. "$PY" make_figs.py > /dev/null)
(cd "$TMP/code" && PYTHONPATH=. "$PY" run_cpp.py tables > /dev/null)
mkdir -p "$ROOT/paper/tables" "$ROOT/paper/figs"
cp "$TMP/paper/numbers.tex" "$ROOT/paper/numbers.tex"
cp "$TMP"/paper/tables/*.tex "$ROOT/paper/tables/"
for f in "$TMP"/paper/figs/*.pdf; do
  b="$ROOT/paper/figs/$(basename "$f")"
  if [ ! -f "$b" ] || ! "$PY" "$ROOT/scripts/same_pdf.py" "$f" "$b"; then cp "$f" "$b"; fi
done
for f in theta_sweep_erp.json fig_example_facts.json tables_cpp.md; do cp "$TMP/results/$f" "$ROOT/results/$f"; done
echo "reference outputs regenerated from the shipped results"
if [ -n "$CMP" ]; then
  echo "comparison with the project's current paper outputs ($CMP):"
  d=0
  for f in numbers.tex $(cd "$ROOT/paper" && ls tables/*.tex); do
    if cmp -s "$ROOT/paper/$f" "$CMP/$f"; then :; else echo "  differs: paper/$f"; d=1; fi
  done
  for f in "$ROOT"/paper/figs/*.pdf; do
    "$PY" "$ROOT/scripts/same_pdf.py" "$f" "$CMP/figs/$(basename "$f")" || { echo "  differs: paper/figs/$(basename "$f")"; d=1; }
  done
  [ $d -eq 0 ] && echo "  identical: the project's paper outputs are up to date" || \
    echo "  NOTE: the project's paper outputs are older than its results; rebuild the paper (paper/build.sh) and resync."
fi
