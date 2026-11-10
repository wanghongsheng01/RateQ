"""Maintainer helper: list the result files that the paper generators actually read.

Runs code/make_numbers.py, code/make_figs.py and `code/run_cpp.py tables` (in that order) inside
<tree> (a disposable copy of the project: the generators write into <tree>/paper and
<tree>/results) with builtins.open wrapped, and prints every file under <tree>/results that was
opened for reading, relative to results/. Usage: python trace_inputs.py <tree>
"""
import builtins
import os
import runpy
import sys

TREE = os.path.abspath(sys.argv[1])
CODE = os.path.join(TREE, "code")
RES = os.path.realpath(os.path.join(TREE, "results"))
seen = set()
_open = builtins.open


def tracing_open(file, mode="r", *a, **k):
    if isinstance(file, (str, bytes, os.PathLike)) and "r" in mode and "+" not in mode:
        p = os.path.realpath(os.fsdecode(file))
        if p.startswith(RES + os.sep):
            seen.add(os.path.relpath(p, RES))
    return _open(file, mode, *a, **k)


builtins.open = tracing_open
os.environ.setdefault("MPLBACKEND", "Agg")
os.chdir(CODE)
sys.path.insert(0, CODE)
devnull = _open(os.devnull, "w")
for script, argv in (("make_numbers.py", []), ("make_figs.py", []), ("run_cpp.py", ["tables"])):
    sys.argv = [script] + argv
    out = sys.stdout
    sys.stdout = devnull
    try:
        runpy.run_path(os.path.join(CODE, script), run_name="__main__")
    finally:
        sys.stdout = out
for f in sorted(seen):
    print(f)
