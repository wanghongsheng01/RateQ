"""Rebuild data/public/zcta_pop_acs5y2023.json from the US Census Bureau ACS 2023 5-year table B01003.

Input: the table-based summary file acsdt5y2023-b01003.dat, downloaded from
https://www2.census.gov/programs-surveys/acs/summary_file/2023/table-based-SF/data/5YRData/acsdt5y2023-b01003.dat
Output: {zcta5: total population} for the ZCTAs listed in data/public/zcta.json (same procedure as
the population step of the ent-geo workload preparation used for the paper).
Usage: python scripts/prep_zcta_pop.py <path to acsdt5y2023-b01003.dat>
"""
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def main(path):
    z = set(json.load(open(os.path.join(ROOT, "data", "public", "zcta.json"))))
    pop, tot_all = {}, 0
    with open(path) as f:
        for line in f:
            if not line.startswith("860Z200US"):
                continue
            g, e, _ = line.strip().split("|")
            v = int(e) if e.lstrip("-").isdigit() and int(e) >= 0 else 0
            tot_all += v
            k = int(g[-5:])
            if k in z:
                pop[k] = v
    json.dump({str(k): v for k, v in sorted(pop.items())},
              open(os.path.join(ROOT, "data", "public", "zcta_pop_acs5y2023.json"), "w"))
    print({"zcta_in_workload_list": len(z), "zcta_with_population": len(pop),
           "population_covered": int(sum(pop.values())), "population_all_zcta": tot_all})


if __name__ == "__main__":
    main(sys.argv[1])
