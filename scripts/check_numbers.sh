#!/usr/bin/env bash
# Regenerate every number, table and figure of the paper from the shipped result files and compare
# them with the reference outputs in paper/ (numbers.tex, tables/*.tex, figs/*.pdf) and results/.
# Works on a temporary copy, so the repository is left untouched. Exit status 0 = all identical.
#
#   scripts/check_numbers.sh            # uses .venv/bin/python if present, else python3
#   PYTHON=/path/to/python scripts/check_numbers.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REF="${REF:-$ROOT/paper}"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"; else PY="$(command -v python3)"; fi
fi
TMP="$(mktemp -d "${TMPDIR:-/tmp}/rateq_check.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
cp -R "$ROOT/code" "$ROOT/data" "$ROOT/results" "$TMP/"
mkdir -p "$TMP/paper"
export MPLBACKEND=Agg
echo "python: $("$PY" --version 2>&1) ($PY)"
(cd "$TMP/code" && PYTHONPATH=. "$PY" make_numbers.py)
(cd "$TMP/code" && PYTHONPATH=. "$PY" make_figs.py > /dev/null)
(cd "$TMP/code" && PYTHONPATH=. "$PY" run_cpp.py tables > /dev/null)

fail=0
cmpf() {  # cmpf <regenerated> <reference> <label>
  if cmp -s "$1" "$2"; then echo "  identical  $3"; else echo "  DIFFERENT  $3"; diff "$2" "$1" | head -20 || true; fail=1; fi
}
echo "numbers and tables:"
cmpf "$TMP/paper/numbers.tex" "$REF/numbers.tex" "paper/numbers.tex"
n=0
for t in "$REF"/tables/*.tex; do
  cmpf "$TMP/paper/tables/$(basename "$t")" "$t" "paper/tables/$(basename "$t")"; n=$((n + 1))
done
for t in "$TMP"/paper/tables/*.tex; do
  [ -f "$REF/tables/$(basename "$t")" ] || { echo "  EXTRA      paper/tables/$(basename "$t") (no reference)"; fail=1; }
done
echo "derived result files:"
for f in theta_sweep_erp.json fig_example_facts.json tables_cpp.md; do
  cmpf "$TMP/results/$f" "$ROOT/results/$f" "results/$f"
done
echo "figures (PDF bytes compared with the /CreationDate timestamp masked):"
for f in "$REF"/figs/*.pdf; do
  if "$PY" "$ROOT/scripts/same_pdf.py" "$TMP/paper/figs/$(basename "$f")" "$f"; then
    echo "  identical  paper/figs/$(basename "$f")"
  else
    echo "  DIFFERENT  paper/figs/$(basename "$f")"; fail=1
  fi
done
if [ $fail -eq 0 ]; then
  echo "OK: numbers.tex, $n tables, derived results and all figures are identical to the references."
else
  echo "FAILED: see the differences above." >&2
fi
exit $fail
