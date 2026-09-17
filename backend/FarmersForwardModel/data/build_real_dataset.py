#!/usr/bin/env python3
"""Download USDA observations and retain missing values and method provenance.

Run from any directory. The national KSSL archive is large and is cached outside
Git. Use --offline to rebuild from the regional CSVs without networking.
No API keys are required. See real/README.md for units and limitations.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.cookiejar
import json
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import closing
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

MODEL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODEL_ROOT))
from pipeline import agronomy, preprocess, thresholds as T  # noqa: E402

SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
KSSL_URL = "https://ncsslabdatamart.sc.egov.usda.gov/database_download.aspx"
BOUNDS = {"south": 29.3, "north": 29.9, "west": -96.3, "east": -95.4}
FORT_BEND_AREASYMBOL = "TX157"

# Regional expansion. Tier 1 is the target county. Tier 2 is every soil survey
# area sharing Fort Bend's MLRA, which groups land by shared soil, climate, and
# water resources and is therefore a defensible similarity claim. Tier 3 is a
# wider Gulf Coast box: more volume, weaker similarity, so it is weighted down
# rather than treated as equivalent evidence.
GULF_BOUNDS = {"south": 28.0, "north": 33.0, "west": -98.0, "east": -82.0}
TIER_FORT_BEND = "fort_bend"
TIER_SAME_MLRA = "same_mlra"
TIER_REGIONAL = "gulf_coast_regional"

# Tune these three, not the call sites.
REGION_WEIGHT_FORT_BEND = 1.0
REGION_WEIGHT_SAME_MLRA = 0.7
REGION_WEIGHT_REGIONAL = 0.4
REGION_WEIGHTS = {TIER_FORT_BEND: REGION_WEIGHT_FORT_BEND,
                  TIER_SAME_MLRA: REGION_WEIGHT_SAME_MLRA,
                  TIER_REGIONAL: REGION_WEIGHT_REGIONAL}
SSURGO_COLUMNS = ["mukey", "cokey", "chkey", "hzname", "ph", "ec", "om_pct", "p_bray1", "cec"]
SSURGO_SQL = """SELECT mu.mukey, co.cokey, hz.chkey, hz.hzname,
hz.ph1to1h2o_r AS ph, hz.ec_r AS ec, hz.om_r AS om_pct,
hz.pbray1_r AS p_bray1, hz.cec7_r AS cec
FROM legend AS l
INNER JOIN mapunit AS mu ON mu.lkey = l.lkey
LEFT JOIN component AS co ON co.mukey = mu.mukey
LEFT JOIN chorizon AS hz ON hz.cokey = co.cokey
WHERE l.areasymbol = 'TX157'
ORDER BY mu.mukey, co.cokey, hz.chkey"""
SSURGO_GEO_SQL = """SELECT p.mukey, p.mupolygongeo.STEnvelope().STAsText() AS envelope_wkt
FROM mupolygon AS p INNER JOIN mapunit AS mu ON mu.mukey = p.mukey
INNER JOIN legend AS l ON l.lkey = mu.lkey WHERE l.areasymbol = 'TX157'"""
KSSL_CHEM_COLUMNS = [
    "objectid", "result_source_key", "prep_code", "ph_h2o", "ph_h2o_method",
    "electrical_conductivity_satx", "electrical_cond_satx_method",
    "organic_carbon_walkley_black", "oc_walkley_black_method", "estimated_organic_carbon",
    "phosphorus_bray1", "phosphorus_bray1_method", "phosphorus_mehlich_3",
    "phosphorus_mehlich_3_method", "phosphorus_mehlich3_extractable",
    "mehlich_3_extractable_method", "phosphorus_olsen", "phosphorus_olsen_method",
    "k_nh4_ph_7", "k_nh4_ph_7_method", "potassium_mehlich3_extractable",
    "nitrate_1m_kcl", "nitrate_1m_kcl_method",
]
KSSL_SQL = """SELECT s.site_key, p.pedon_key, p.user_pedon_id,
       l.layer_key, l.labsampnum, l.hzn_desgn AS hzname, l.hzn_top, l.hzn_bot,
       CAST(s.latitude_std_decimal_degrees AS REAL) AS latitude,
       CAST(s.longitude_std_decimal_degrees AS REAL) AS longitude,
       s.horizontal_datum_name AS original_horizontal_datum,
       %s
FROM lab_site AS s
JOIN lab_pedon AS p ON p.site_key = s.site_key
JOIN lab_layer AS l ON l.pedon_key = p.pedon_key
LEFT JOIN lab_chemical_properties_vw AS c ON c.layer_key = l.layer_key
WHERE CAST(s.latitude_std_decimal_degrees AS REAL) BETWEEN :south AND :north
  AND CAST(s.longitude_std_decimal_degrees AS REAL) BETWEEN :west AND :east
ORDER BY p.pedon_key, l.layer_key, c.objectid
""" % ", ".join("c." + name for name in KSSL_CHEM_COLUMNS)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sda_query(query, cache_path, refresh=False):
    """Cache only successful SDA responses; retry transient network failures."""
    if cache_path.exists() and not refresh:
        payload = json.loads(cache_path.read_text())
    else:
        request = urllib.request.Request(
            SDA_URL, data=json.dumps({"query": query, "format": "JSON+COLUMNNAME"}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "FarmersForwardResearch/1.0"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    payload = json.load(response)
                if "Table" in payload:
                    break
                # SDA answers a valid request with an empty object when it is
                # under load. That is a transient failure, not a verdict that
                # the county has no data, so retry it like a dropped socket.
                failure = ValueError("SDA returned no Table: %s" % payload)
            except (urllib.error.URLError, TimeoutError) as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code < 500:
                    raise RuntimeError("SDA rejected the query: %s" % exc.read().decode()) from exc
                failure = exc
            if attempt == 2:
                raise failure
            time.sleep(2 ** attempt)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload))
    table = payload.get("Table")
    if not table or len(table) == 1:
        raise ValueError("SDA returned no data rows; refusing to replace regional data.")
    if len(table) - 1 >= 100000:
        raise ValueError("SDA row limit reached; query needs pagination.")
    return pd.DataFrame(table[1:], columns=table[0])


def polygon_bbox(envelopes):
    coords = []
    for wkt in envelopes["envelope_wkt"]:
        values = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?", wkt)]
        coords.extend(zip(values[::2], values[1::2]))
    if not coords:
        raise ValueError("No SSURGO polygon coordinates returned.")
    return {"south": min(y for x, y in coords), "north": max(y for x, y in coords),
            "west": min(x for x, y in coords), "east": max(x for x, y in coords)}


class HiddenForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("type") == "hidden" and "name" in attrs:
            self.fields[attrs["name"]] = attrs.get("value", "")


def download_kssl(cache_dir, refresh=False):
    """Use the public ASP.NET download form with its session and view state."""
    archive = cache_dir / "kssl.zip"
    database = cache_dir / "NCSSLabDataMartSQLite.sqlite3"
    cache_dir.mkdir(parents=True, exist_ok=True)
    if database.exists() and not refresh:
        return database
    if not archive.exists() or refresh:
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        form = HiddenForm()
        with opener.open(KSSL_URL, timeout=60) as response:
            form.feed(response.read().decode())
        if "__VIEWSTATE" not in form.fields:
            raise ValueError("KSSL download form changed: missing view state.")
        form.fields.update(__EVENTTARGET="btnDownloadSQLiteFile", __EVENTARGUMENT="")
        temporary = archive.with_suffix(".zip.part")
        try:
            with opener.open(KSSL_URL, urllib.parse.urlencode(form.fields).encode(),
                             timeout=180) as response, temporary.open("wb") as output:
                if "zip" not in response.headers.get("Content-Type", ""):
                    raise ValueError("KSSL returned a page instead of the SQLite ZIP.")
                size, last = 0, time.monotonic()
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    size += len(chunk)
                    if time.monotonic() - last >= 20:
                        print("  KSSL downloaded %.0f MiB" % (size / 1024 ** 2), flush=True)
                        last = time.monotonic()
            with zipfile.ZipFile(temporary) as zipped:
                if not any(n.endswith(".sqlite3") for n in zipped.namelist()):
                    raise ValueError("KSSL ZIP contains no SQLite database.")
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    temporary = database.with_suffix(".sqlite3.part")
    try:
        with zipfile.ZipFile(archive) as zipped:
            members = [n for n in zipped.namelist() if n.endswith(".sqlite3")]
            if len(members) != 1:
                raise ValueError("Expected exactly one KSSL SQLite database.")
            # Fixed output path avoids extracting arbitrary archive paths.
            with zipped.open(members[0]) as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
        temporary.replace(database)
    finally:
        temporary.unlink(missing_ok=True)
    return database


def numeric(series):
    """Keep zero, but leave missing, censored, and nonfinite values unknown."""
    values = pd.to_numeric(series, errors="coerce")
    return values.where(values.notna() & values.ne(float("inf")) & values.ne(float("-inf")))


def choose_measurement(frame, candidates):
    """Prefer one lab method, retaining its identity instead of averaging methods."""
    values = pd.Series(float("nan"), index=frame.index)
    methods = pd.Series("", index=frame.index, dtype=object)
    for column, method, factor in candidates:
        candidate = numeric(frame[column]) * factor
        use = values.isna() & candidate.notna()
        values.loc[use] = candidate.loc[use]
        methods.loc[use] = method
    return values, methods


def extract_kssl(database, bounds=BOUNDS):
    # A read-only connection cannot mutate the downloaded source database.
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        frame = pd.read_sql_query(KSSL_SQL, connection, params=bounds)
        columns = KSSL_CHEM_COLUMNS + ["latitude_std_decimal_degrees", "longitude_std_decimal_degrees"]
        descriptions = pd.read_sql_query(
            "SELECT * FROM lab_column_descriptions WHERE column_name IN (%s)" %
            ",".join("?" for _ in columns), connection, params=columns).to_dict("records")
    if frame.empty:
        raise ValueError("No KSSL layers in the requested bounding box; refusing to overwrite exports.")
    # Keep preparation/source records distinct if USDA supplies several for a layer.
    keys = ["layer_key", "objectid"]
    if frame.duplicated(keys).any():
        raise ValueError("KSSL joins multiplied layer/chemical-record identities.")
    frame["ph"] = numeric(frame["ph_h2o"])
    frame["ec"] = numeric(frame["electrical_conductivity_satx"])
    frame["organic_carbon_pct"], frame["oc_method"] = choose_measurement(frame, [
        ("organic_carbon_walkley_black", "Walkley-Black", 1),
        ("estimated_organic_carbon", "USDA_estimated_organic_carbon", 1),
    ])
    frame["p_extractable"], frame["p_method"] = choose_measurement(frame, [
        ("phosphorus_bray1", "Bray-1", 1),
        ("phosphorus_mehlich_3", "Mehlich-3", 1),
        ("phosphorus_mehlich3_extractable", "Mehlich-3_elemental", 1),
        ("phosphorus_olsen", "Olsen", 1),
    ])
    # K is monovalent: 1 meq/100 g = 0.01 mol/kg * 39.0983 g/mol = 390.983 mg/kg.
    frame["k_extractable"], frame["k_method"] = choose_measurement(frame, [
        ("k_nh4_ph_7", "NH4OAc_pH7", 390.983),
        ("potassium_mehlich3_extractable", "Mehlich-3", 1),
    ])
    # KCl nitrate is NO3-N in mg/kg. Total N and solution meq/L are not substitutes.
    frame["nitrate_n"] = numeric(frame["nitrate_1m_kcl"])
    return frame, descriptions


def standardize(ssurgo, kssl, n_coefficient=T.N_CREDIT_PPM_PER_OM_PCT,
                oc_to_om=T.ORGANIC_CARBON_TO_OM_FACTOR):
    """Stack independent observations; never spatially fabricate a complete row.

    Apply the user's OM% * 15 ppm N-credit approximation via the shared agronomy
    helper. KSSL OC is first converted to estimated OM with USDA's factor 1.724.
    Both coefficients are recorded and may be overridden; None disables a step.
    """
    survey = ssurgo.copy()
    for column in ("mukey", "cokey", "chkey"):
        survey[column] = survey[column].astype("string").str.replace(r"\.0$", "", regex=True)
    survey["source"] = "ssurgo"
    survey["sample_id"] = ("ssurgo:" + survey["mukey"].astype("string") + ":" +
                           survey["cokey"].astype("string") + ":" +
                           survey["chkey"].astype("string").fillna("no_horizon"))
    survey["phosphorus"] = numeric(survey["p_bray1"])
    survey["potassium"] = float("nan")
    survey["nitrogen"] = float("nan")
    survey["latitude"] = float("nan")
    survey["longitude"] = float("nan")
    survey["ph_method"] = "1:1_water_representative_value"
    survey["ec_method"] = "saturation_extract_representative_value"
    survey["p_method"] = "Bray-1_representative_value"
    survey["k_method"] = ""
    survey["om_method"] = "SSURGO_representative_value"
    survey["om_derived"] = False
    survey["record_kind"] = "survey_horizon"
    survey.loc[survey["chkey"].isna(), "record_kind"] = "component_without_horizon"

    lab = kssl.copy()
    for column in ("site_key", "pedon_key", "layer_key", "objectid"):
        lab[column] = lab[column].astype("string").str.replace(r"\.0$", "", regex=True)
    lab["source"] = "kssl"
    lab["sample_id"] = ("kssl:" + lab["layer_key"].astype("string") + ":" +
                        lab["objectid"].astype("string").fillna("no_chemistry"))
    lab["phosphorus"] = numeric(lab["p_extractable"])
    lab["potassium"] = numeric(lab["k_extractable"])
    lab["nitrogen"] = numeric(lab["nitrate_n"])
    lab["om_pct"] = float("nan")
    lab["om_method"] = ""
    lab["om_derived"] = False
    lab["ph_method"] = "1:1_water"
    lab["ec_method"] = "saturation_extract"
    lab["record_kind"] = "lab_layer"
    if oc_to_om is not None:
        om = numeric(lab["organic_carbon_pct"]) * oc_to_om
        valid_om = om.between(0, 100)
        lab.loc[valid_om, "om_pct"] = om.loc[valid_om]
        lab.loc[valid_om, "om_derived"] = True
        lab.loc[valid_om, "om_method"] = "organic_carbon_times_conversion_factor"

    frames = []
    for frame in (survey, lab):
        frame["n_derived"] = False
        frame["n_method"] = ""
        frame.loc[frame["nitrogen"].notna(), "n_method"] = "measured_KCl_NO3-N"
        if n_coefficient is not None:
            om = numeric(frame["om_pct"])
            derive = frame["nitrogen"].isna() & om.between(0, 100)
            frame.loc[derive, "nitrogen"] = om.loc[derive].map(
                lambda value: agronomy.nitrogen_credit_from_organic_matter(
                    value, n_ppm_per_om_pct=n_coefficient))
            frame.loc[derive, "n_derived"] = True
            frame.loc[derive, "n_method"] = "OM_mineralization_credit_proxy"
        frame["region"] = "fort_bend_brazos"
        frames.append(frame)
    result = pd.concat(frames, ignore_index=True)
    for column in preprocess.FEATURE_COLUMNS:
        result[column] = numeric(result[column])
    result["complete_five_inputs"] = result[preprocess.FEATURE_COLUMNS].notna().all(axis=1)
    result["missing_inputs"] = result[preprocess.FEATURE_COLUMNS].apply(
        lambda row: ";".join(row.index[row.isna()]), axis=1)
    result["n_credit_ppm_per_om_pct"] = n_coefficient
    result["oc_to_om_factor"] = oc_to_om
    first = ["sample_id", "source", *preprocess.FEATURE_COLUMNS, "latitude", "longitude",
             "n_derived", "n_method", "complete_five_inputs", "missing_inputs"]
    return result[first + [column for column in result.columns if column not in first]]


def inside_bounds(frame, bounds):
    """Points strictly inside a WGS84 box, with unknown coordinates excluded."""
    latitude, longitude = numeric(frame["latitude"]), numeric(frame["longitude"])
    return (latitude.between(bounds["south"], bounds["north"]) &
            longitude.between(bounds["west"], bounds["east"])).fillna(False)


def tier_from_areas(areasymbols):
    """Fort Bend outranks its MLRA peers, which outrank the wider Gulf box."""
    symbols = pd.Series(areasymbols).astype("string")
    tiers = pd.Series(TIER_REGIONAL, index=symbols.index, dtype=object)
    tiers.loc[symbols.notna()] = TIER_SAME_MLRA
    tiers.loc[symbols.eq(FORT_BEND_AREASYMBOL)] = TIER_FORT_BEND
    return tiers


def apply_region_weights(frame):
    """Add region_tier weighting beside, never instead of, the evidence flags.

    final_weight multiplies the regional confidence by the crop-evidence weight,
    so a CDL-presence row in the wider Gulf box counts least and a Fort Bend row
    with real evidence counts most. n_derived, n_source, signal_strength, and
    sample_weight keep their existing meanings untouched.
    """
    frame = frame.copy()
    unknown = set(frame["region_tier"].dropna().unique()) - set(REGION_WEIGHTS)
    if unknown:
        raise ValueError("No region weight defined for tier(s): %s" % sorted(unknown))
    frame["region_weight"] = frame["region_tier"].map(REGION_WEIGHTS).astype(float)
    if frame["region_weight"].isna().any():
        raise ValueError("Every row needs a region_tier before weighting.")
    frame["final_weight"] = frame["region_weight"] * frame["evidence_weight"].astype(float)
    return frame


def point_bbox(frame):
    geo = frame[["latitude", "longitude"]].apply(numeric).dropna()
    if geo.empty:
        return None
    return {"south": float(geo.latitude.min()), "north": float(geo.latitude.max()),
            "west": float(geo.longitude.min()), "east": float(geo.longitude.max())}


def summarize(frame):
    measurements = frame[preprocess.FEATURE_COLUMNS].apply(numeric)
    complete = measurements.notna().all(axis=1)
    valid = complete.copy()
    for column, (low, high) in preprocess.CLIP_BOUNDS.items():
        valid &= measurements[column].between(low, high)
    return {"rows": len(frame), "complete_five_inputs": int(complete.sum()),
            "partial": int((~complete).sum()), "training_eligible": int(valid.sum()),
            "missing_by_input": {c: int(measurements[c].isna().sum()) for c in measurements},
            "missing_n_or_k": int(measurements[["nitrogen", "potassium"]].isna().any(axis=1).sum()),
            "n_derived": int(frame["n_derived"].sum()), "point_bbox": point_bbox(frame)}


REGIONAL_EXPORTS = ("ssurgo_mlra_region.csv", "kssl_gulf_region.csv",
                    "soilgrids_gulf_region.csv")


def _regional_exports_ready(provenance, out, args):
    """True when every regional CSV can be hash-verified instead of refetched."""
    if args.refresh:
        return False
    hashes = provenance.get("sha256", {})
    return all((out / name).exists() and hashes.get(name) for name in REGIONAL_EXPORTS)


def expanded_summary(frame, mlra):
    """Per-tier counts, so the cost of widening the region stays visible."""
    tiers = {}
    for tier, rows in frame.groupby("region_tier", sort=True):
        entry = summarize(rows)
        entry["region_weight"] = float(numeric(rows.region_weight).iloc[0])
        entry["by_source"] = {str(k): int(v) for k, v in rows.source.value_counts().items()}
        tiers[str(tier)] = entry
    return {
        "mlra": mlra,
        "region_weights": REGION_WEIGHTS,
        "weight_formula": "final_weight = region_weight * evidence_weight",
        "by_tier": tiers,
        "total": summarize(frame),
        "final_weight_counts": {str(k): int(v) for k, v in
                                numeric(frame.final_weight).round(4).value_counts().items()},
        "evidence_weight_counts": {str(k): int(v) for k, v in
                                   numeric(frame.evidence_weight).value_counts().items()},
        "nitrogen_source_breakdown": {
            "measured": int((frame.nitrogen.notna() & ~frame.n_derived).sum()),
            "derived": int(frame.n_derived.sum()),
            "null": int(frame.nitrogen.isna().sum())},
        "phosphorus_potassium_coverage": {
            column: {str(tier): int(numeric(rows[column]).notna().sum())
                     for tier, rows in frame.groupby("region_tier", sort=True)}
            for column in ("phosphorus", "potassium")},
        "cdl_context_note": ("CDL and WSS context exist for Fort Bend map units only; "
                             "rows outside them carry null crop and interpretive columns"),
    }


def discover_mlra(cache, refresh=False):
    """Derive Fort Bend's MLRA from SDA rather than assuming one."""
    frame = sda_query(spatial_module().mlra_for_area_sql(FORT_BEND_AREASYMBOL),
                      cache / "mlra-fort-bend.json", refresh)
    symbols = [str(value) for value in frame.mlra_symbol]
    names = dict(zip(frame.mlra_symbol.astype(str), frame.mlra_name.astype(str)))
    print("  %s falls within MLRA %s" % (FORT_BEND_AREASYMBOL,
          ", ".join("%s (%s)" % (symbol, names[symbol]) for symbol in symbols)), flush=True)
    return symbols, names


def spatial_module():
    from data import spatial_sources
    return spatial_sources


def regional_frames(args, out, provenance, statuses, units, database, polygons):
    """Pull the same-MLRA and wider Gulf tiers, reusing verified exports offline.

    Both pulls are supersets of the Fort Bend query, so the tier of each row is
    decided by where it actually is, not by which file it arrived in. Fort Bend
    outranks its MLRA peers, which outrank the wider box.
    """
    spatial = spatial_module()
    cache = args.cache_dir
    paths = {"ssurgo_mlra": out / "ssurgo_mlra_region.csv",
             "kssl_gulf": out / "kssl_gulf_region.csv",
             "soilgrids_gulf": out / "soilgrids_gulf_region.csv"}
    saved = {name: provenance.get("sha256", {}).get(path.name) for name, path in paths.items()}
    reusable = all(paths[n].exists() and saved[n] for n in paths) and not args.refresh
    if reusable:
        for name, path in paths.items():
            if sha256(path) != saved[name]:
                raise ValueError("Raw CSV changed since ingestion: %s" % path)
        frames = {name: pd.read_csv(path, low_memory=False, keep_default_na=False, na_values=[""],
                                    dtype={"mukey": "string", "source_depth_cm": "string",
                                           "areasymbol": "string", "survey_areasymbol": "string"})
                  for name, path in paths.items()}
        print("  Verified saved regional exports: %s" %
              ", ".join("%s=%d" % (n, len(f)) for n, f in frames.items()), flush=True)
        return frames, provenance.get("mlra", {}), statuses
    if args.offline:
        raise ValueError("Missing verified regional exports; run online ingestion first")

    print("[7/9] Deriving Fort Bend's MLRA from SDA ...", flush=True)
    symbols, names = discover_mlra(cache, args.refresh)
    areas = sda_query(spatial.mlra_areas_sql(symbols), cache / "mlra-areas.json", args.refresh)
    print("  %d soil survey areas share it (the 'US' national legend is excluded)"
          % len(areas), flush=True)
    mlra = {"fort_bend_areasymbol": FORT_BEND_AREASYMBOL, "mlra_symbols": symbols,
            "mlra_names": names, "survey_areas": areas.areasymbol.astype(str).tolist(),
            "survey_area_count": int(len(areas)),
            "derivation": "legend JOIN laoverlap WHERE areatypename='MLRA'",
            "excluded": {"areasymbol": spatial.NATIONAL_LEGEND,
                         "reason": "STATSGO2 national legend, not a soil survey area"}}

    print("[8/9] Pulling SSURGO horizons for every survey area in the MLRA ...", flush=True)
    mlra_survey = sda_query(spatial.mlra_ssurgo_sql(symbols), cache / "mlra-ssurgo.json", args.refresh)
    mlra_survey["region_tier"] = tier_from_areas(mlra_survey.areasymbol)
    statuses["ssurgo_mlra"] = {"status": "succeeded", "url": SDA_URL,
                               "sql": spatial.mlra_ssurgo_sql(symbols), "mlra_symbols": symbols,
                               "survey_areas": int(mlra_survey.areasymbol.nunique())}

    print("[9/9] Pulling the wider Gulf Coast tier ...", flush=True)
    boundaries = sda_query(spatial.mlra_boundary_sql(symbols), cache / "mlra-boundaries.json", args.refresh)
    areas_index = spatial.SoilPolygons(boundaries, key_column="areasymbol")
    gulf_lab, _ = extract_kssl(database, GULF_BOUNDS)
    gulf_lab["survey_areasymbol"] = areas_index.locate(gulf_lab.longitude, gulf_lab.latitude)
    gulf_lab["region_tier"] = tier_from_areas(gulf_lab.survey_areasymbol)
    # Persist the map-unit lookup the way the Fort Bend export does. enrich()
    # can only redo it while polygons are loaded, so an offline rebuild would
    # otherwise silently drop the CDL and WSS context from these rows.
    if polygons is not None:
        gulf_lab["mukey"] = polygons.locate(gulf_lab.longitude, gulf_lab.latitude)
    statuses["kssl_gulf"] = {"status": "succeeded", "url": KSSL_URL, "requested_bbox": GULF_BOUNDS,
                             "tiering": "survey-area outline lookup; Fort Bend and MLRA peers keep their tier"}

    # Cells inside the Fort Bend window already have native-resolution coverage.
    outside = ~inside_bounds(gulf_lab, BOUNDS)
    points = gulf_lab.loc[outside, ["longitude", "latitude"]].apply(numeric).dropna().drop_duplicates()
    gulf_grid, statuses["soilgrids_gulf"] = spatial.soilgrids_at_points(
        cache, GULF_BOUNDS, points.longitude, points.latitude, units, args.refresh,
        prefix="soilgrids_gulf")
    if not gulf_grid.empty:
        gulf_grid["survey_areasymbol"] = areas_index.locate(gulf_grid.longitude, gulf_grid.latitude)
        gulf_grid["region_tier"] = tier_from_areas(gulf_grid.survey_areasymbol)
    frames = {"ssurgo_mlra": mlra_survey, "kssl_gulf": gulf_lab, "soilgrids_gulf": gulf_grid}
    for name, path in paths.items():
        frames[name].to_csv(path, index=False, float_format="%.10g")
        frames[name] = pd.read_csv(path, low_memory=False, keep_default_na=False, na_values=[""],
                                   dtype={"mukey": "string", "source_depth_cm": "string",
                                          "areasymbol": "string", "survey_areasymbol": "string"})
    return frames, mlra, statuses


def positive_number(value):
    import math
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=MODEL_ROOT / "data" / "real")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path.home() / ".cache" / "farmers-forward-usda")
    parser.add_argument("--kssl-db", type=Path, help="use a previously downloaded KSSL SQLite database")
    parser.add_argument("--refresh", action="store_true", help="refresh public source downloads and regional exports")
    parser.add_argument("--offline", action="store_true", help="rebuild using saved raw regional CSVs")
    parser.add_argument("--skip-regional", action="store_true",
                        help="build only the Fort Bend dataset, without the MLRA and Gulf tiers")
    parser.add_argument("--n-credit-ppm-per-om-pct", type=positive_number,
                        default=T.N_CREDIT_PPM_PER_OM_PCT,
                        help="N-credit ppm per percentage point of OM (default: 15)")
    parser.add_argument("--oc-to-om-factor", type=positive_number,
                        default=T.ORGANIC_CARBON_TO_OM_FACTOR,
                        help="KSSL OC-to-OM conversion factor (default: 1.724, USDA NRCS)")
    args = parser.parse_args()
    if args.offline and (args.refresh or args.kssl_db):
        parser.error("--offline cannot be combined with --refresh or --kssl-db")
    out = args.out_dir
    ssurgo_path, kssl_path = out / "ssurgo_fort_bend.csv", out / "kssl_fort_bend.csv"
    manifest_path = out / "provenance.json"
    out.mkdir(parents=True, exist_ok=True)
    reuse_base = manifest_path.exists() and ssurgo_path.exists() and kssl_path.exists() and not args.refresh
    if args.offline or reuse_base:
        provenance = json.loads(manifest_path.read_text())
        for path in (ssurgo_path, kssl_path):
            if sha256(path) != provenance["sha256"][path.name]:
                raise ValueError("Raw CSV changed since ingestion: %s" % path)
        survey = pd.read_csv(ssurgo_path, keep_default_na=False, na_values=[""], dtype={c: "string" for c in SSURGO_COLUMNS[:4]})
        lab = pd.read_csv(kssl_path, keep_default_na=False, na_values=[""], dtype={c: "string" for c in
                          ("site_key", "pedon_key", "layer_key", "objectid", "labsampnum")})
    else:
        print("[1/9] Fetching Fort Bend SSURGO horizons and polygon coverage ...", flush=True)
        survey = sda_query(SSURGO_SQL, args.cache_dir / "ssurgo.json", args.refresh)
        if list(survey.columns) != SSURGO_COLUMNS:
            raise ValueError("SSURGO schema differs from the requested fields.")
        envelopes = sda_query(SSURGO_GEO_SQL, args.cache_dir / "ssurgo-geo.json", args.refresh)
        print("[2/9] Filtering KSSL bulk database to requested bounding box ...", flush=True)
        database = args.kssl_db or download_kssl(args.cache_dir, args.refresh)
        lab, descriptions = extract_kssl(database)
        provenance = {
            "retrieved_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "sources": {"ssurgo": SDA_URL, "kssl": KSSL_URL},
            "ssurgo_areasymbol": "TX157", "ssurgo_sql": SSURGO_SQL,
            "ssurgo_geo_sql": SSURGO_GEO_SQL, "ssurgo_polygon_bbox": polygon_bbox(envelopes),
            "ssurgo_polygon_count": len(envelopes), "kssl_sql": KSSL_SQL,
            "kssl_requested_bbox": BOUNDS, "kssl_sqlite_sha256": sha256(database),
            "kssl_field_definitions": descriptions,
            "coordinate_system": "WGS84; standardized KSSL coordinates, original datum retained separately",
        }
        survey.to_csv(ssurgo_path, index=False)
        lab.to_csv(kssl_path, index=False, float_format="%.10g")
    # Spatial dependencies are isolated from the synthetic training baseline.
    from data import spatial_sources as spatial
    extra_paths = {"soilgrids": out / "soilgrids_fort_bend.csv",
                   "cdl": out / "cdl_crop_by_mukey.csv",
                   "wss": out / "wss_interpretive_fort_bend.csv"}
    statuses = provenance.get("spatial_sources", {})
    polygons = None
    extras = {}
    if not args.offline:
        print("[3/9] Loading SSURGO polygons for location joins ...", flush=True)
        polygons = spatial.SoilPolygons(sda_query(spatial.POLYGON_SQL,
                                       args.cache_dir / "ssurgo-polygons.json", args.refresh))
        provenance["ssurgo_polygon_sql"] = spatial.POLYGON_SQL
        lab["mukey"] = polygons.locate(lab.longitude, lab.latitude)
        lab.to_csv(kssl_path, index=False, float_format="%.10g")
    for name, path in extra_paths.items():
        saved = provenance.get("sha256", {}).get(path.name)
        reusable = path.exists() and saved and not args.refresh
        if reusable:
            if sha256(path) != saved:
                raise ValueError("Raw CSV changed since ingestion: %s" % path)
            extras[name] = pd.read_csv(path, keep_default_na=False, na_values=[""], dtype={"mukey": "string", "source_depth_cm": "string"})
            print(f"  Verified saved {name}: {len(extras[name])} rows", flush=True)
        elif args.offline:
            raise ValueError(f"Missing verified {name} export; run online ingestion first")
        elif name == "soilgrids":
            print("[4/9] Fetching SoilGrids predictions ...", flush=True)
            extras[name], statuses[name] = spatial.soilgrids(args.cache_dir, BOUNDS, polygons, args.refresh)
        elif name == "cdl":
            print("[5/9] Joining county CDL crop presence by mukey ...", flush=True)
            extras[name], statuses[name] = spatial.cdl(args.cache_dir, polygons, survey.mukey, args.refresh)
        else:
            print("[6/9] Fetching WSS-backed interpretive attributes via SDA ...", flush=True)
            try:
                raw = sda_query(spatial.WSS_SQL, args.cache_dir / "wss-attributes.json", args.refresh)
                extras[name] = spatial.wss_attributes(raw)
                statuses[name] = {"status": "succeeded", "transport": "WSS underlying SSURGO tables via SDA",
                                  "url": SDA_URL, "sql": spatial.WSS_SQL,
                                  "soil_health_rating": "unavailable; no generic soil-health score in these tables"}
            except (OSError, ValueError, RuntimeError) as exc:
                statuses[name] = {"status": "skipped", "reason": str(exc),
                                  "manual_fallback": "See data/real/README.md WSS manual export steps"}
                extras[name] = pd.DataFrame(columns=spatial.WSS_COLUMNS)
                print(f"WSS skipped: {exc}. See README for manual export.", flush=True)
        if not reusable:
            extras[name].to_csv(path, index=False, float_format="%.10g")
            extras[name] = pd.read_csv(path, keep_default_na=False, na_values=[""],
                                       dtype={"mukey": "string", "source_depth_cm": "string"})
    provenance["spatial_sources"] = statuses
    print("Standardizing with explicit nutrient and crop provenance ...", flush=True)
    for frame in (survey, lab, extras["soilgrids"]):
        frame["region_tier"] = TIER_FORT_BEND
    standardized = standardize(survey, lab, args.n_credit_ppm_per_om_pct, args.oc_to_om_factor)
    standardized = spatial.enrich(standardized, extras["soilgrids"], extras["cdl"], extras["wss"],
                                  polygons, args.n_credit_ppm_per_om_pct, args.oc_to_om_factor)
    standardized = apply_region_weights(standardized)
    training_path = out / "fort_bend_real_training.csv"
    standardized.to_csv(training_path, index=False, float_format="%.10g")
    # Report the precision actually exported, so an offline rebuild has the
    # same coverage numbers as an online build. Preserve flood class "None".
    standardized = pd.read_csv(training_path, low_memory=False, keep_default_na=False, na_values=[""])
    summary = {source: summarize(rows) for source, rows in standardized.groupby("source", sort=True)}
    summary["total"] = summarize(standardized)
    summary["source_row_counts"] = {"ssurgo": len(survey), "kssl": len(lab),
                                   **{name: len(frame) for name, frame in extras.items()}}
    summary["nitrogen_source_breakdown"] = {
        "measured": int((standardized.nitrogen.notna() & ~standardized.n_derived).sum()),
        "derived": int(standardized.n_derived.sum()), "null": int(standardized.nitrogen.isna().sum())}
    summary["nitrogen_methods"] = standardized.n_source.value_counts().to_dict()
    # A zero "measured" count is a property of the sources, not a broken join.
    # Record both independent reasons so it never has to be re-investigated.
    total_n = numeric(standardized.get("soilgrids_total_nitrogen_ppm", pd.Series(dtype=float)))
    summary["nitrogen_measured_coverage"] = {
        "kssl_measured_nitrate_rows": int(numeric(lab.get("nitrate_n", pd.Series(dtype=float))).notna().sum()),
        "kssl_layers_queried": int(len(lab)),
        "kssl_note": "nitrate_1m_kcl is unpopulated for every layer in this bounding box",
        "soilgrids_total_nitrogen_rows": int(total_n.notna().sum()),
        "soilgrids_total_nitrogen_median_ppm": float(total_n.median()) if total_n.notna().any() else None,
        "soilgrids_excluded_because": (
            "SoilGrids predicts total N, which is mostly organic and not plant available. "
            "The model feature is NO3-N in ppm, deficient below %.0f and clipped at %.0f, so "
            "substituting total N would pin every grid row at the clip ceiling."
            % (T.NITROGEN["deficient"], preprocess.CLIP_BOUNDS["nitrogen"][1])),
        "derived_by_source": {str(source): int(rows.n_derived.sum())
                              for source, rows in standardized.groupby("source", sort=True)},
    }
    summary["phosphorus_potassium_coverage"] = {
        column: {str(source): int(numeric(rows[column]).notna().sum())
                 for source, rows in standardized.groupby("source", sort=True)}
        for column in ("phosphorus", "potassium")}
    summary["spatial_sources"] = statuses
    summary["cdl_presence_rows"] = int(standardized.signal_strength.eq("presence_only").sum())
    summary["sample_weight_counts"] = standardized.sample_weight.value_counts().to_dict()
    summary["ssurgo_map_units"] = int(survey["mukey"].nunique())
    summary["ssurgo_horizons"] = int(survey["chkey"].notna().sum())
    summary["kssl_pedons"] = int(lab["pedon_key"].nunique())
    summary["kssl_layers"] = int(lab["layer_key"].nunique())
    summary["ssurgo_polygon_bbox"] = provenance["ssurgo_polygon_bbox"]
    summary["nitrogen_derivation"] = {
        "n_credit_ppm_per_om_pct": args.n_credit_ppm_per_om_pct,
        "oc_to_om_factor": args.oc_to_om_factor,
        "formula": "nitrogen_ppm = om_pct * n_credit_ppm_per_om_pct",
        "coefficient_source": "user_specified" if args.n_credit_ppm_per_om_pct == T.N_CREDIT_PPM_PER_OM_PCT
                              else "CLI_override",
        "interpretation": "annual_mineralization_credit_proxy_not_measured_nitrate",
        "oc_to_om_reference": "https://directives.nrcs.usda.gov/sites/default/files2/1719846855/Part%20618%20Subpart%20A%20%E2%80%93%20General%20Information.pdf",
        "status": "applied",
    }
    written = [ssurgo_path, kssl_path, *extra_paths.values(), training_path]
    if not args.skip_regional:
        units = spatial.soilgrids_units(args.cache_dir, BOUNDS, args.refresh)
        database = args.kssl_db
        if database is None and not (args.offline or _regional_exports_ready(provenance, out, args)):
            database = download_kssl(args.cache_dir, args.refresh)
        regional, mlra, statuses = regional_frames(args, out, provenance, statuses,
                                                   units, database, polygons)
        provenance["mlra"] = mlra
        provenance["spatial_sources"] = statuses
        print("Standardizing the expanded region ...", flush=True)
        # Both regional pulls are supersets of the Fort Bend queries, so the
        # expanded set is built from them directly instead of appending the
        # narrow rows again and deduplicating afterwards.
        grid = pd.concat([extras["soilgrids"], regional["soilgrids_gulf"]], ignore_index=True)
        expanded = standardize(regional["ssurgo_mlra"], regional["kssl_gulf"],
                               args.n_credit_ppm_per_om_pct, args.oc_to_om_factor)
        expanded = spatial.enrich(expanded, grid, extras["cdl"], extras["wss"],
                                  polygons, args.n_credit_ppm_per_om_pct, args.oc_to_om_factor)
        expanded = apply_region_weights(expanded)
        expanded["region"] = expanded["region_tier"].map(
            {TIER_FORT_BEND: "fort_bend_brazos", TIER_SAME_MLRA: "mlra_" + "_".join(mlra["mlra_symbols"]).lower(),
             TIER_REGIONAL: "gulf_coast"})
        expanded_path = out / "expanded_real_training.csv"
        expanded.to_csv(expanded_path, index=False, float_format="%.10g")
        written += [out / "ssurgo_mlra_region.csv", out / "kssl_gulf_region.csv",
                    out / "soilgrids_gulf_region.csv", expanded_path]
        expanded = pd.read_csv(expanded_path, low_memory=False, keep_default_na=False, na_values=[""])
        summary["expanded"] = expanded_summary(expanded, mlra)
    provenance["standardized_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    provenance["sha256"] = {path.name: sha256(path) for path in written}
    provenance["nitrogen_derivation"] = summary["nitrogen_derivation"]
    manifest_path.write_text(json.dumps(provenance, indent=2) + "\n")
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print("Saved %s" % training_path)


if __name__ == "__main__":
    main()
