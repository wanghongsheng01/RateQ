#!/usr/bin/env python3
"""Copy the public part of the RateQ project tree into this artifact and de-identify it.

Called by scripts/sync_from_project.sh; standard library only. Files are copied or overwritten,
never deleted. Source code, comments and result files are rewritten so that no internal
identifier remains: the internal product name of one rule semantics becomes "erp-style"
(identifier erp_style, instance prefix ERPSTYLE), enterprise rate-card template ids become
svc01, svc02, ... (bijective, so every count is unchanged), and host names in run environments
become "server" / "mac". Numeric content is never changed.

Usage: python3 sync_deid.py <project dir> <artifact dir> [file listing extra results/ paths to ship]
The optional list is written by scripts/trace_inputs.py (the files the generators actually read).
"""
import glob
import json
import os
import re
import shutil
import sys

PROJ, ART = (os.path.abspath(p) for p in sys.argv[1:3])
TRACED = [l.strip() for l in open(sys.argv[3]) if l.strip()] if len(sys.argv) > 3 else []

# ------------------------------------------------------------------ what is shipped
CODE = ["rateq/__init__.py", "rateq/baselines.py", "rateq/index.py", "rateq/instances.py", "rateq/maintain.py",
        "rateq/model.py",  # rateq/erp.py (enterprise compiler, reads company data) is not shipped
        "make_numbers.py", "make_figs.py", "make_tables.py", "make_cpp_tables.py", "run_cpp.py", "export_bin.py",
        "parse_public.py", "parse_manual.py", "pilot.py", "smoke.py",
        "exp_rev1_common.py", "exp_rev1_ablations.py", "exp_rev1_b7_cache.py", "exp_rev1_b8_ivm.py",
        "exp_rev1_scale.py", "exp_rev1_sensitivity.py", "exp_rev1_entgeo.py"]
CPP = ["rq.cpp", "Makefile"]
RESULTS = (["pilot_E%d_pub2.json" % i for i in range(1, 6)]
           + ["pilot_E1.json", "pilot_E2_tuned.json", "pilot_E3.json",
              "cpp_E1E2_pub2.json", "cpp_E1E2_pub2r.json", "cpp_E1E2_pub2_server.json", "cpp_E1E2_pub2r_server.json",
              "cpp_verify_pub2.json", "cpp_verify_pub2r.json",
              # aggregate-only enterprise results
              "erp_pilot_USA.json", "erp_replay_USA_after.json", "cpp_E1E2_erp.json", "cpp_verify_erp.json",
              "theta_sweep_erp.json", "fig_example_facts.json", "tables_cpp.md"])


def rev1_files():
    out = ["rev1/README.md"]
    out += sorted(os.path.relpath(p, os.path.join(PROJ, "results"))
                  for p in glob.glob(os.path.join(PROJ, "results", "rev1", "server", "*.json")) if "_test" not in p)
    # enterprise-shape (ent-geo) aggregates; the shapes themselves stay private
    out += sorted(os.path.relpath(p, os.path.join(PROJ, "results"))
                  for p in glob.glob(os.path.join(PROJ, "results", "rev1", "*entgeo*.json")) if "_test" not in p)
    return out


# ------------------------------------------------------------------ text rules (code, comments, tex, md)
# Rules live outside the repository (<project>/artifact_sync/deid_rules.json) because they name the
# internal identifiers they remove: "text_rules" (ordered [old, new] replacements for code, comments,
# tex and md), "json_string_rules" (replacements inside JSON keys and strings), "template_id_regex" and
# "template_id_new" (enterprise template ids -> neutral ids, assigned in sorted numeric order).
RULES = json.load(open(os.path.join(PROJ, "artifact_sync", "deid_rules.json"), encoding="utf-8"))
TEXT_RULES = [tuple(r) for r in RULES["text_rules"]]


def fix_text(s):
    for a, b in TEXT_RULES:
        s = s.replace(a, b)
    return s


# ------------------------------------------------------------------ JSON rules (results)
TPL = re.compile(RULES["template_id_regex"])


def collect_tpl(o, acc):
    if isinstance(o, dict):
        for k, v in o.items():
            m = TPL.match(k)
            if m:
                acc.add(int(m.group(1)))
            collect_tpl(v, acc)
    elif isinstance(o, list):
        for v in o:
            collect_tpl(v, acc)
    elif isinstance(o, str):
        m = TPL.match(o)
        if m:
            acc.add(int(m.group(1)))


def fix_str(s, tmap):
    m = TPL.match(s)
    if m:
        return tmap[int(m.group(1))]
    for a, b in RULES["json_string_rules"]:
        s = s.replace(a, b)
    return s


def fix_json(o, tmap):
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if k == "host" and isinstance(v, str):
                out[k] = "server" if o.get("machine") == "x86_64" else "mac"
                continue
            out[fix_str(k, tmap)] = fix_json(v, tmap)
        return out
    if isinstance(o, list):
        return [fix_json(v, tmap) for v in o]
    if isinstance(o, str):
        return fix_str(o, tmap)
    return o


def write_json_like(src_text, obj, dst):
    indent = None
    m = re.match(r"\{\n( +)\S", src_text)
    if m:
        indent = len(m.group(1))
    with open(dst, "w") as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False)


# ------------------------------------------------------------------ copy helpers
def ensure_dir(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)


def copy_text(src, dst):
    ensure_dir(dst)
    with open(src, encoding="utf-8") as f:
        s = f.read()
    t = fix_text(s)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(t)
    return s != t


def copy_bytes(src, dst):
    ensure_dir(dst)
    shutil.copyfile(src, dst)


def main():
    changed = []
    # code
    for f in CODE:
        if copy_text(os.path.join(PROJ, "code", f), os.path.join(ART, "code", f)):
            changed.append("code/" + f)
    for f in CPP:
        if copy_text(os.path.join(PROJ, "cpp", f), os.path.join(ART, "cpp", f)):
            changed.append("cpp/" + f)
    # theory (tex only; the PDF is rebuilt from the de-identified tex by sync_from_project.sh)
    if copy_text(os.path.join(PROJ, "theory", "rateq_theory.tex"), os.path.join(ART, "theory", "rateq_theory.tex")):
        changed.append("theory/rateq_theory.tex")
    # public data: parsed official files, Olist parcel shapes, ZCTA lists (data/public/raw is not shipped)
    for p in sorted(glob.glob(os.path.join(PROJ, "data", "public", "*.json"))):
        copy_bytes(p, os.path.join(ART, "data", "public", os.path.basename(p)))
    # results
    files = list(dict.fromkeys(RESULTS + rev1_files() + TRACED))
    bad = [f for f in files if f.startswith(("_private", "bin", "logs")) or "/_private" in f]
    if bad:
        sys.exit("refusing to ship private or binary inputs: %s" % bad)
    loaded = {}
    tpl = set()
    for f in files:
        src = os.path.join(PROJ, "results", f)
        if not os.path.exists(src):
            print("missing (skipped):", f)
            continue
        if f.endswith(".json"):
            txt = open(src).read()
            obj = json.loads(txt)
            loaded[f] = (txt, obj)
            collect_tpl(obj, tpl)
        else:
            loaded[f] = (open(src, encoding="utf-8").read(), None)
    tmap = {t: RULES["template_id_new"] % (i + 1) for i, t in enumerate(sorted(tpl))}
    for f, (txt, obj) in loaded.items():
        dst = os.path.join(ART, "results", f)
        ensure_dir(dst)
        if obj is None:
            t = fix_text(txt)
            open(dst, "w", encoding="utf-8").write(t)
            if t != txt:
                changed.append("results/" + f)
            continue
        new = fix_json(obj, tmap)
        if new == obj:
            copy_bytes(os.path.join(PROJ, "results", f), dst)
        else:
            write_json_like(txt, new, dst)
            changed.append("results/" + f)
    # reference outputs of the paper (regenerated by make_numbers.py / make_figs.py)
    copy_bytes(os.path.join(PROJ, "paper", "numbers.tex"), os.path.join(ART, "paper", "numbers.tex"))
    for p in sorted(glob.glob(os.path.join(PROJ, "paper", "tables", "*.tex"))):
        copy_bytes(p, os.path.join(ART, "paper", "tables", os.path.basename(p)))
    for p in sorted(glob.glob(os.path.join(PROJ, "paper", "figs", "*.pdf"))):
        copy_bytes(p, os.path.join(ART, "paper", "figs", os.path.basename(p)))
    print("de-identified files:", len(changed))
    for c in changed:
        print("  ", c)
    print("template ids mapped:", len(tmap))


if __name__ == "__main__":
    main()
