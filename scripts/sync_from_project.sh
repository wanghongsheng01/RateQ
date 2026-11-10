#!/usr/bin/env bash
# Maintainer script: refresh this artifact from the authors' project tree, then verify it.
#
#   scripts/sync_from_project.sh [PROJECT_DIR]     (default: the parent directory of this repository)
#
# Steps: (1) copy code, C++ sources, parsed public data, result files and the reference outputs
# (paper/numbers.tex, paper/tables, paper/figs) through scripts/sync_deid.py, which removes internal
# identifiers; (2) rebuild theory/rateq_theory.pdf from the de-identified source if pdflatex exists;
# (3) regenerate all numbers, tables and figures from the shipped results and compare them with the
# references (scripts/check_numbers.sh); (4) scan for credentials and internal names
# (scripts/security_scan.sh). Files are only added or overwritten, never deleted: if a file was
# removed or renamed in the project, remove it here by hand. Commit and push only if all steps pass.
#
# If step 3 fails because the project's paper outputs are older than its result files (results were
# rerun after the last paper build), either rebuild the paper in the project (paper/build.sh) and
# rerun this script, or run it with REGEN_REFS=1, which regenerates the reference outputs from the
# shipped results and lists where they differ from the project's current paper outputs.
set -euo pipefail
ART="$(cd "$(dirname "$0")/.." && pwd)"
PROJ="$(cd "${1:-$ART/..}" && pwd)"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x "$PROJ/.venv/bin/python" ]; then PY="$PROJ/.venv/bin/python"; else PY="$(command -v python3)"; fi
fi
echo "== 0/4 trace the result files read by make_numbers.py, make_figs.py and run_cpp.py tables"
TR="$(mktemp -d "${TMPDIR:-/tmp}/rateq_trace.XXXXXX")"
mkdir -p "$TR/data/public" "$TR/paper"
cp -R "$PROJ/code" "$PROJ/results" "$TR/"
cp "$PROJ"/data/public/*.json "$TR/data/public/"
MPLBACKEND=Agg "$PY" "$ART/scripts/trace_inputs.py" "$TR" > "$TR/traced.txt"
sed 's/^/   read: /' "$TR/traced.txt"

echo "== 1/4 copy and de-identify ($PROJ -> $ART)"
python3 "$ART/scripts/sync_deid.py" "$PROJ" "$ART" "$TR/traced.txt"
echo "   (trace copy kept in $TR)"

echo "== 2/4 theory PDF"
for d in "$HOME/Library/TinyTeX/bin/universal-darwin" "$HOME/.TinyTeX/bin/x86_64-linux" /Library/TeX/texbin; do
  [ -d "$d" ] && PATH="$d:$PATH"
done
if command -v pdflatex > /dev/null; then
  T="$(mktemp -d "${TMPDIR:-/tmp}/rateq_tex.XXXXXX")"
  cp "$ART/theory/rateq_theory.tex" "$T/"
  (cd "$T" && for i in 1 2 3; do pdflatex -interaction=nonstopmode rateq_theory.tex > /dev/null || true; done)
  if [ -s "$T/rateq_theory.pdf" ]; then
    cp "$T/rateq_theory.pdf" "$ART/theory/rateq_theory.pdf"
    grep -E "^!|undefined" "$T/rateq_theory.log" | sort | uniq -c || true
    echo "theory/rateq_theory.pdf rebuilt"
  else
    echo "WARNING: pdflatex failed; theory/rateq_theory.pdf not updated" >&2
  fi
else
  echo "WARNING: pdflatex not found; theory/rateq_theory.pdf not updated" >&2
fi

if [ -n "${REGEN_REFS:-}" ]; then
  echo "== 2b regenerate the reference outputs from the shipped results (REGEN_REFS=1)"
  PYTHON="$PY" "$ART/scripts/regen_refs.sh" "$PROJ/paper"
fi

echo "== 3/4 regenerate and compare (python: $PY)"
PYTHON="$PY" "$ART/scripts/check_numbers.sh"

echo "== 4/4 security scan"
"$ART/scripts/security_scan.sh"

echo
echo "Sync complete. Review 'git -C $ART status' and 'git -C $ART diff --stat', then commit and push."
