"""Parse the downloaded public rate cards and zone charts into compact JSON.

Inputs (data/public/raw/): usps_notice123.htm, usps_zone_<o>.json, ups_daily_rates.xlsx,
olist_products.csv, gaz/2020_Gaz_zcta_national.txt.
Outputs (data/public/): usps_rates.json, ups_rates.json, usps_zones.json, parcels_olist.json, zcta.json
"""
import csv, html, json, re, sys
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data/public/raw"
OUT = RAW.parent


def _tables(h):
    for m in re.finditer(r"<table.*?</table>", h, re.S):
        rows = []
        for r in re.finditer(r"<tr.*?</tr>", m.group(0), re.S):
            cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                     for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r.group(0), re.S)]
            rows.append(cells)
        yield m.start(), rows


def _weight(lbl):
    lbl = lbl.replace(" ", " ").strip()
    m = re.match(r"^([\d.]+)\s*oz", lbl)
    if m:
        return float(m.group(1)) / 16.0
    m = re.match(r"^([\d.]+)$", lbl)
    return float(m.group(1)) if m else None


def _money(c):
    c = c.replace("$", "").replace(",", "").strip()
    try:
        return round(float(c) * 100)
    except ValueError:
        return None


def parse_usps():
    h = open(RAW / "usps_notice123.htm", encoding="utf-8", errors="ignore").read()
    tabs = list(_tables(h))
    # table start offsets located by inspecting section headings (see README.md, section Data sources)
    spec = {
        "USPS_PM_Retail": [80199, 110310],
        "USPS_PM_Commercial": [307294, 338127],
        "USPS_PME_Retail": [22998, 54031],
        "USPS_GA_Retail": [141992],
        "USPS_GA_Commercial": [394003],
    }
    out = {}
    for name, starts in spec.items():
        rows = {}
        for st in starts:
            t = min(tabs, key=lambda x: abs(x[0] - st))[1]
            for r in t:
                if len(r) < 10:
                    continue
                w = _weight(r[0])
                vals = [_money(c) for c in r[1:10]]
                if w is None or any(v is None for v in vals):
                    continue
                rows[w] = vals  # zones 1..9
        ws = sorted(rows)
        out[name] = {"breaks": ws, "zones": list(range(1, 10)),
                     "price": [[rows[w][z] for w in ws] for z in range(9)]}
        print(name, len(ws), "weights", ws[:3], "...", ws[-1], file=sys.stderr)
    json.dump(out, open(OUT / "usps_rates.json", "w"))


def parse_ups():
    import openpyxl
    wb = openpyxl.load_workbook(RAW / "ups_daily_rates.xlsx", read_only=True, data_only=True)
    sheets = {"UPS_Ground": "UPS Ground", "UPS_3DA": "UPS 3DA Select", "UPS_2DA": "UPS 2DA",
              "UPS_2DA_AM": "UPS 2DA A.M.", "UPS_NDA_Saver": "UPS NDA Saver",
              "UPS_NDA": "UPS NDA", "UPS_NDA_Early": "UPS NDA Early"}
    out = {}
    for name, sn in sheets.items():
        rows = [[c for c in r if c is not None] for r in wb[sn].iter_rows(values_only=True)]
        zones = None
        table = {}
        for r in rows:
            if not r:
                continue
            if r[0] == "Zones":
                zs = [int(z) % 100 for z in r[1:] if isinstance(z, (int, float))]
                zones = zones or zs
                cur = zs
                continue
            if zones is None:
                continue
            lbl = r[0]
            if isinstance(lbl, str):
                m = re.match(r"^(\d+)\s*Lbs", lbl)
                if not m:
                    continue
                w = float(m.group(1))
            elif isinstance(lbl, (int, float)):
                w = float(lbl)
            else:
                continue
            vals = r[1:1 + len(cur)]
            if len(vals) == len(cur) and all(isinstance(v, (int, float)) for v in vals):
                table[w] = {z: round(v * 100) for z, v in zip(cur, vals)}
        ws = sorted(table)
        zs = sorted({z for w in ws for z in table[w]})
        out[name] = {"breaks": ws, "zones": zs,
                     "price": [[table[w].get(z) for w in ws] for z in zs]}
        print(name, len(ws), "weights; zones", zs, file=sys.stderr)
    json.dump(out, open(OUT / "ups_rates.json", "w"))


def parse_zones():
    out = {}
    for f in sorted(RAW.glob("usps_zone_*.json")):
        o = f.stem.split("_")[-1]
        d = json.load(open(f))
        iv = []
        for col in ["Column0", "Column1", "Column2", "Column3"]:
            for e in d.get(col, []):
                a, _, b = e["ZipCodes"].partition("---")
                b = b or a
                z = int(re.sub(r"\D", "", e["Zone"]) or 0)
                if len(a) == 5:  # 5-digit ranges inside the 3-digit columns
                    iv.append([int(a), int(b), z])
                else:
                    iv.append([int(a) * 100, int(b) * 100 + 99, z])
        exc = []
        for e in d.get("Zip5Digit", []):
            a, _, b = e["ZipCodes"].partition("---")
            b = b or a
            exc.append([int(a), int(b), int(re.sub(r"\D", "", e["Zone"]) or 0), e["MailService"]])
        out[o] = {"effective": d.get("EffectiveDate"), "intervals": sorted(iv), "exceptions": exc}
        print("origin", o, len(iv), "intervals,", len(exc), "5-digit exceptions", file=sys.stderr)
    json.dump(out, open(OUT / "usps_zones.json", "w"))


def parse_parcels():
    rows = []
    with open(RAW / "olist_products.csv") as f:
        for r in csv.DictReader(f):
            try:
                g, l, h, w = (float(r[k]) for k in ("product_weight_g", "product_length_cm",
                                                    "product_height_cm", "product_width_cm"))
            except ValueError:
                continue
            if g <= 0 or l <= 0 or h <= 0 or w <= 0:
                continue
            rows.append([round(g / 453.59237, 4), round(l / 2.54, 3), round(w / 2.54, 3), round(h / 2.54, 3)])
    json.dump(rows, open(OUT / "parcels_olist.json", "w"))
    print("olist parcels", len(rows), file=sys.stderr)
    z = []
    with open(RAW / "gaz/2020_Gaz_zcta_national.txt") as f:
        next(f)
        for line in f:
            z.append(int(line.split("\t")[0]))
    json.dump(sorted(z), open(OUT / "zcta.json", "w"))
    print("zcta", len(z), file=sys.stderr)


if __name__ == "__main__":
    parse_usps(); parse_ups(); parse_zones(); parse_parcels()
