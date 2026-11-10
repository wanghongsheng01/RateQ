"""Parse the official UPS / FedEx files downloaded by the author (data/public/raw/manual/) into JSON.

Outputs (data/public/): fedex_rates.json, fedex_zones.json, das_ups.json, das_fedex.json, fuel_history.json,
official_surcharges.json (amounts transcribed from the 2026 guides, page references inside).
"""
import json, re, sys
from pathlib import Path
import openpyxl, pdfplumber

M = Path(__file__).resolve().parent.parent / "data/public/raw/manual"
OUT = M.parent.parent


def _w(lbl):
    if isinstance(lbl, (int, float)):
        return float(lbl)
    m = re.match(r"^\s*(\d+)\s*lbs?\.", str(lbl))
    return float(m.group(1)) if m else None


def fedex_rates():
    wb = openpyxl.load_workbook(M / "FedEx/FedEx_2026_Standard_List_Rates.xlsx", read_only=True, data_only=True)
    out = {}
    rows = list(wb["2026 Ground & FHD rates"].iter_rows(values_only=True))
    zones = [int(z) for z in rows[5][1:8]]
    tab = {}
    for r in rows[6:157]:
        w = _w(r[0])
        if w is not None and all(isinstance(v, (int, float)) for v in r[1:8]):
            tab[w] = [round(v * 100) for v in r[1:8]]
    ws = sorted(tab)
    out["FedEx_Ground"] = {"breaks": ws, "zones": zones, "price": [[tab[w][j] for w in ws] for j in range(len(zones))]}
    rows = list(wb["2026 U.S. rates"].iter_rows(values_only=True))
    blocks = [(3, "FedEx_First_Overnight"), (160, "FedEx_Priority_Overnight"), (317, "FedEx_Standard_Overnight"),
              (474, "FedEx_2Day_AM"), (631, "FedEx_2Day"), (788, "FedEx_Express_Saver")]
    for (st, name), nxt in zip(blocks, [b[0] for b in blocks[1:]] + [1100]):
        zrow = rows[st + 1]
        zones = [int(z) for z in zrow[1:8]]
        tab = {}
        for r in rows[st + 2:nxt]:
            w = _w(r[0])
            if w is not None and all(isinstance(v, (int, float)) for v in r[1:8]):
                tab[w] = [round(v * 100) for v in r[1:8]]
        ws = sorted(tab)
        out[name] = {"breaks": ws, "zones": zones, "price": [[tab[w][j] for w in ws] for j in range(len(zones))]}
    for k, v in out.items():
        print(k, len(v["breaks"]), "weights", v["breaks"][0], "..", v["breaks"][-1], "zones", v["zones"], file=sys.stderr)
    json.dump(out, open(OUT / "fedex_rates.json", "w"))


def fedex_zones():
    out = {}
    for f in sorted((M / "FedEx/zone_charts").glob("FedEx_Zone_Chart_*.pdf")):
        o = f.stem.split("_")[-1][:3]
        with pdfplumber.open(f) as pdf:
            t = pdf.pages[0].extract_text()
        cont = t.split("Contiguous U.S.")[1].split("Alaska, Hawaii, and Puerto Rico")[0]
        iv = []
        for a, b, z in re.findall(r"(\d{5})-(\d{5})\s+(NA|\d+)", cont):
            iv.append([int(a), int(b), -1 if z == "NA" else int(z)])
        iv.sort()
        out[o] = {"effective": re.search(r"Effective:\s*([A-Za-z]+ \d+, \d{4})", t).group(1), "intervals": iv}
        print("fedex zones origin", o, len(iv), "intervals, covers", sum(b - a + 1 for a, b, z in iv), file=sys.stderr)
    json.dump(out, open(OUT / "fedex_zones.json", "w"))


def _zips(ws):
    s = set()
    for r in ws.iter_rows(values_only=True):
        for c in r:
            if c is None:
                continue
            for m in re.findall(r"(?<!\d)(\d{5})(?!\d)", str(c).zfill(5) if isinstance(c, int) else str(c)):
                s.add(int(m))
    return sorted(s)


def das():
    wb = openpyxl.load_workbook(M / "UPS/UPS_DAS_ZIP_Codes_2026.xlsx", read_only=True)
    ups = {"DAS": _zips(wb["US 48 Zip"]), "DAS_Extended": _zips(wb["US 48 Zip DAS Extended"]), "Remote": _zips(wb["Remote US 48 Zip"])}
    wb = openpyxl.load_workbook(M / "FedEx/FedEx_2026_DAS_ZIP_Codes.xlsx", read_only=True)
    fdx = {"DAS": _zips(wb["DAS_ContUS"]), "DAS_Extended": _zips(wb["DAS_ContUSExt"]), "Remote": _zips(wb["DAS_ContUSRem"])}
    for n, d in (("ups", ups), ("fedex", fdx)):
        print("das", n, {k: len(v) for k, v in d.items()}, file=sys.stderr)
        json.dump(d, open(OUT / f"das_{n}.json", "w"))


def fuel():
    ups, fdx = [], []
    for l in open(M / "fuel_history/ups_fuel_history.txt", encoding="utf-8"):
        m = re.match(r"^(\d\d)/(\d\d)/(\d{4})\t([\d.]+)%\t([\d.]+)%", l)
        if m:
            ups.append([f"{m.group(3)}-{m.group(1)}-{m.group(2)}", float(m.group(4)), float(m.group(5))])
    for l in open(M / "fuel_history/fedex_fuel_history.txt", encoding="utf-8"):
        m = re.match(r"^(\d\d)/(\d\d)/(\d{4})–\S+\t([\d.]+)%\t\S+\t([\d.]+)%", l)
        if m:
            fdx.append([f"{m.group(3)}-{m.group(1)}-{m.group(2)}", float(m.group(4)), float(m.group(5))])
    out = {"ups": sorted(ups), "fedex": sorted(fdx), "_cols": ["start", "ground_pct", "air_pct"]}
    print("fuel weeks", len(ups), len(fdx), file=sys.stderr)
    json.dump(out, open(OUT / "fuel_history.json", "w"))


def ups_rates_2026():
    """U.S. 48 domestic daily-rate tables of the 2026 UPS Rate & Service Guide (PDF pages 53-56, 61-64,
    69-72, 75-78, 81-84, 87-90, 91-94 in 1-based numbering). Zone codes are taken mod 100 as in the 2025 file."""
    pdf = pdfplumber.open(M / "UPS/UPS_2026_Rate_and_Service_Guide_Daily_Rates.pdf")
    blocks = {"UPS_NDA_Early": 52, "UPS_NDA": 60, "UPS_NDA_Saver": 68, "UPS_2DA_AM": 74, "UPS_2DA": 80, "UPS_3DA": 86, "UPS_Ground": 90}
    out = {}
    for name, p0 in blocks.items():
        zones, tab = None, {}
        for i in range(p0, p0 + 4):
            t = pdf.pages[i].extract_text()
            assert "U.S. 48" in t, (name, i)
            for l in t.split("\n"):
                if l.startswith("Zones"):
                    z = [int(re.sub(r"\D", "", x)) % 100 for x in l.split()[1:]]
                    assert zones in (None, z), (name, i, z); zones = z
                    continue
                m = re.match(r"^(\d+)(?: Lbs?\.)?((?: \$?[\d,]+\.\d\d)+)\s*$", l)
                if m and zones and 1 <= int(m.group(1)) <= 150:
                    v = [round(float(x.replace("$", "").replace(",", "")) * 100) for x in m.group(2).split()]
                    if len(v) == len(zones):
                        tab[float(m.group(1))] = v
        ws = sorted(tab)
        assert ws == [float(w) for w in range(1, 151)], (name, len(ws))
        out[name] = {"breaks": ws, "zones": zones, "price": [[tab[w][j] for w in ws] for j in range(len(zones))]}
        print("ups 2026", name, len(ws), "weights, zones", zones, "1 lb zone 2:", out[name]["price"][0][0], file=sys.stderr)
    json.dump(out, open(OUT / "ups_rates_2026.json", "w"))


def ups_zones():
    """UPS daily-rate zone charts for the five origins (xlsx content saved as .xls by ups.com), downloaded by
    the authors on 2026-11-08 into manual/. Rows: destination ZIP3 and the zone code of Ground, 3 Day Select,
    2nd Day Air, 2nd Day Air A.M., Next Day Air Saver, Next Day Air. Every air code is the ground zone plus a
    service prefix (e.g. 105 = zone 5), which is asserted; one zone per ZIP3 is stored (-1 = not served by
    the contiguous-48 tables: '-', Alaska/Hawaii codes 44-46). The 5-digit Alaska/Hawaii lists in the
    footnotes only choose between zones 44 and 46 and are not needed."""
    import io
    out = {}
    for o in ("088", "300", "606", "750", "917"):
        wb = openpyxl.load_workbook(io.BytesIO((M / f"{o}.xls").read_bytes()), read_only=True, data_only=True)
        rows = list(wb.worksheets[0].iter_rows(values_only=True))
        hdr = [r for r in rows if r and r[0] == "Dest. ZIP"][0]
        assert list(hdr[1:7]) == ["Ground", "3 Day Select", "2nd Day Air", "2nd Day Air A.M.", "Next Day Air Saver", "Next Day Air"], hdr
        z3 = []
        for r in rows:
            if r and r[0] is not None and re.fullmatch(r"\d{3}", str(r[0])):
                codes = [str(c) for c in r[1:7]]
                g = codes[0]
                zone = int(g) if g.isdigit() and 2 <= int(g) <= 8 else -1
                for c in codes[1:]:
                    assert c == "-" or zone == -1 or int(c) % 10 == zone, (o, r)
                z3.append([int(r[0]), zone])
        out[o] = sorted(z3)
        print("ups zones origin", o, len(z3), "zip3 rows,", sum(z >= 0 for _, z in z3), "served", file=sys.stderr)
    json.dump(out, open(OUT / "ups_zones.json", "w"))


def surcharges():
    """Amounts as printed in the 2026 guides (USD); verified against the PDF text on 2026-11-07."""
    out = {
        "ups": {"_source": "2026 UPS Rate & Service Guide (Daily Rates, updated 2026-09-07), printed pp. 179-183 (PDF 181-185)",
                "zone_bands": ["2", "3-4", "5-6", "7+"],
                "AHS_weight": [46.50, 50.75, 56.25, 58.75], "AHS_dimension": [30.00, 33.25, 38.50, 40.50],
                "LargePackage_residential": [254.50, 274.50, 320.50, 331.00],
                "DAS_commercial": 4.50, "DAS_residential": 6.55, "DAS_ext_commercial": 5.70, "DAS_ext_residential": 8.85,
                "Residential_ground": 6.50, "Residential_air": 7.00, "OverMax": 1875.00},
        "fedex": {"_source": "FedEx Service Guide 2026 (updated 2026-09-11), pp. 128-135, 142",
                  "zone_bands": ["2", "3-4", "5-6", "7+"],
                  "AHS_dimension": [29.50, 32.75, 38.50, 40.75], "AHS_weight": [46.00, 50.25, 56.25, 58.75],
                  "Oversize": [255.00, 275.00, 320.00, 330.00],
                  "DAS_commercial": 4.45, "DAS_ext_commercial": 5.55, "DAS_residential": 6.60, "DAS_ext_residential": 8.80,
                  "DAS_remote": 16.75, "Residential_ground_hd": 6.45, "Residential_express": 6.95,
                  "AHS_dimension_min_billable_lb": 40, "Oversize_min_billable_lb": 90},
    }
    json.dump(out, open(OUT / "official_surcharges.json", "w"), indent=1)


if __name__ == "__main__":
    fedex_rates(); fedex_zones(); das(); fuel(); surcharges(); ups_rates_2026(); ups_zones()
