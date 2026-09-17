"""Public spatial context, with explicit provenance and no invented lab results.

SoilGrids bulk WCS avoids ~100,000 point REST calls (5/min fair-use limit).
CDL is planting presence only. WSS attributes use its underlying SSURGO tables
through SDA; this is additional context, not an independent soil measurement.
"""
from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import transform_geom
from rasterio.windows import Window
from pyproj import Transformer
import shapely
from shapely.geometry import shape, mapping

SG_CRS = "+proj=igh +datum=WGS84 +units=m +no_defs"
SG_CRS_URL = "http://www.opengis.net/def/crs/EPSG/0/152160"
SG_API = "https://rest.isric.org/soilgrids/v2.0/properties/query"
SG_WCS = "https://maps.isric.org/mapserv"
CDL_SERVICE = "https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer"
COUNTY_SERVICE = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query"
PROPERTIES = ("phh2o", "soc", "nitrogen", "cec", "bdod")
DEPTHS = ("0-5", "5-15")
POLYGON_SQL = """SELECT p.mukey, p.mupolygongeo.STAsText() AS polygon_wkt
FROM mupolygon p INNER JOIN mapunit mu ON mu.mukey=p.mukey
INNER JOIN legend l ON l.lkey=mu.lkey WHERE l.areasymbol='TX157'"""
WSS_SQL = """SELECT mu.mukey, co.cokey, co.comppct_r, co.drainagecl, co.tfact,
h.chkey,h.hzdept_r,h.hzdepb_r,h.kwfact,mo.flodfreqcl
FROM legend l JOIN mapunit mu ON mu.lkey=l.lkey
LEFT JOIN component co ON co.mukey=mu.mukey
LEFT JOIN chorizon h ON h.cokey=co.cokey
LEFT JOIN comonth mo ON mo.cokey=co.cokey
WHERE l.areasymbol='TX157'"""
WSS_COLUMNS = ["mukey", "drainage_class", "erosion_k_factor", "flood_frequency",
               "soil_health_rating", "erosion_t_factor", "representative_cokey"]
CDL_COLUMNS = ["mukey", "dominant_crop", "crop_pixel_pct", "signal_strength",
               "dominant_crop_code", "cdl_year", "valid_pixel_count", "crop_pixel_count"]

# MLRA (Major Land Resource Area) groups land by shared soil, climate, and water
# resources, so it is a defensible basis for "similar soil" where a hand-picked
# list of neighbouring states is not. SSURGO exposes it through laoverlap.
MLRA_AREATYPE = "MLRA"
# 'US' is the STATSGO2 national legend (areatypename 'Country', 9,562 generalized
# map units). It overlaps every MLRA and is not a soil survey area, so every
# survey-area query below excludes it explicitly.
NATIONAL_LEGEND = "US"
SYMBOL_PATTERN = re.compile(r"[A-Za-z0-9]{2,7}")


def quoted_symbols(symbols):
    """Reject anything that is not a plain survey/MLRA symbol before inlining it.

    SDA takes SQL as text with no parameter binding, so the symbols that reach a
    query are validated here rather than trusted from a caller.
    """
    symbols = [str(symbol).strip() for symbol in symbols]
    if not symbols:
        raise ValueError("At least one symbol is required.")
    for symbol in symbols:
        if not SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError("Refusing to inline an unexpected symbol: %r" % symbol)
    return ", ".join("'%s'" % symbol for symbol in symbols)


def mlra_for_area_sql(areasymbol):
    """Which MLRA(s) a soil survey area falls within, derived rather than assumed."""
    return """SELECT lo.areasymbol AS mlra_symbol, lo.areaname AS mlra_name
FROM legend AS l INNER JOIN laoverlap AS lo ON lo.lkey = l.lkey
WHERE l.areasymbol IN (%s) AND lo.areatypename = '%s'
ORDER BY lo.areasymbol""" % (quoted_symbols([areasymbol]), MLRA_AREATYPE)


def mlra_areas_sql(mlra_symbols):
    """Every soil survey area sharing the given MLRA(s)."""
    return """SELECT DISTINCT l.areasymbol, l.areaname
FROM legend AS l INNER JOIN laoverlap AS lo ON lo.lkey = l.lkey
WHERE lo.areatypename = '%s' AND lo.areasymbol IN (%s) AND l.areasymbol <> '%s'
ORDER BY l.areasymbol""" % (MLRA_AREATYPE, quoted_symbols(mlra_symbols), NATIONAL_LEGEND)


def mlra_ssurgo_sql(mlra_symbols):
    """The Fort Bend horizon fields, widened to every survey area in the MLRA."""
    return """SELECT l.areasymbol, mu.mukey, co.cokey, hz.chkey, hz.hzname,
hz.ph1to1h2o_r AS ph, hz.ec_r AS ec, hz.om_r AS om_pct,
hz.pbray1_r AS p_bray1, hz.cec7_r AS cec
FROM legend AS l
INNER JOIN laoverlap AS lo ON lo.lkey = l.lkey AND lo.areatypename = '%s'
    AND lo.areasymbol IN (%s)
INNER JOIN mapunit AS mu ON mu.lkey = l.lkey
LEFT JOIN component AS co ON co.mukey = mu.mukey
LEFT JOIN chorizon AS hz ON hz.cokey = co.cokey
WHERE l.areasymbol <> '%s'
ORDER BY l.areasymbol, mu.mukey, co.cokey, hz.chkey""" % (
        MLRA_AREATYPE, quoted_symbols(mlra_symbols), NATIONAL_LEGEND)


def mlra_boundary_sql(mlra_symbols):
    """Survey-area outlines, not map-unit polygons, for cheap point tiering."""
    return """SELECT sa.areasymbol, sa.sapolygongeo.STAsText() AS polygon_wkt
FROM sapolygon AS sa
INNER JOIN legend AS l ON l.areasymbol = sa.areasymbol
INNER JOIN laoverlap AS lo ON lo.lkey = l.lkey AND lo.areatypename = '%s'
    AND lo.areasymbol IN (%s)
WHERE l.areasymbol <> '%s'""" % (
        MLRA_AREATYPE, quoted_symbols(mlra_symbols), NATIONAL_LEGEND)


def url(base, params):
    return base + "?" + urllib.parse.urlencode(params, doseq=True)


def fetch(address, path, refresh=False):
    """Atomic cache writes, standard certificate validation, no authentication."""
    path = Path(path)
    if path.exists() and not refresh:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        with urllib.request.urlopen(address, timeout=180) as response, temporary.open("wb") as target:
            while chunk := response.read(1024 * 1024):
                target.write(chunk)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def fetch_json(address, path, refresh=False):
    result = json.loads(fetch(address, path, refresh).read_text())
    if "error" in result:
        raise ValueError(f"Public service rejected request: {result['error']}")
    return result


def identifiers(frame):
    frame = frame.copy()
    if "mukey" in frame:
        frame["mukey"] = frame.mukey.astype("string").str.replace(r"\.0$", "", regex=True)
    return frame


class SoilPolygons:
    def __init__(self, frame, key_column="mukey"):
        frame = identifiers(frame).sort_values(key_column, kind="stable")
        self.keys = frame[key_column].to_numpy()
        self.geometries = shapely.make_valid(shapely.from_wkt(frame.polygon_wkt.to_numpy()))
        self.tree = shapely.STRtree(self.geometries)

    def locate(self, longitude, latitude):
        points = shapely.points(np.asarray(longitude, float), np.asarray(latitude, float))
        pairs = self.tree.query(points, predicate="intersects")
        result = np.full(len(points), None, dtype=object)
        # Boundary ties choose the lexically first mukey, reproducibly.
        if pairs.size:
            order = np.lexsort((pairs[1], pairs[0]))
            point_ids, polygon_ids = pairs[:, order]
            keep = np.r_[True, point_ids[1:] != point_ids[:-1]]
            result[point_ids[keep]] = self.keys[polygon_ids[keep]]
        return pd.array(result, dtype="string")


def convert_soilgrids(native, units):
    """Validate REST units before converting the matching WCS integer layers."""
    expected = {"phh2o": ("pH*10", 10), "soc": ("dg/kg", 10),
                "nitrogen": ("cg/kg", 100), "cec": ("mmol(c)/kg", 10),
                "bdod": ("cg/cm³", 100)}
    for name, (mapped, divisor) in expected.items():
        actual = units[name]
        if actual["mapped_units"] != mapped or actual["d_factor"] != divisor:
            raise ValueError(f"SoilGrids units changed for {name}: {actual}")
    # pH * 10 / 10; SOC dg/kg / 10 = g/kg, / 10 = percent OC.
    # Total N cg/kg / 100 = g/kg, * 1000 = mg/kg (ppm), NOT available nitrate-N.
    # CEC mmol(c)/kg / 10 = cmol(c)/kg; bulk density / 100 = g/cm3.
    return {"ph": native["phh2o"] / 10,
            "organic_carbon_pct": native["soc"] / 100,
            "soilgrids_total_nitrogen_ppm": native["nitrogen"] * 10,
            "cec": native["cec"] / 10, "bulk_density_g_cm3": native["bdod"] / 100}


def soilgrids_units(cache, bounds, refresh=False):
    """One REST call confirming the native unit metadata the WCS layers use."""
    params = [("lon", (bounds["west"] + bounds["east"]) / 2),
              ("lat", (bounds["south"] + bounds["north"]) / 2)]
    params += [("property", p) for p in PROPERTIES]
    params += [("depth", d + "cm") for d in DEPTHS] + [("value", "mean")]
    probe = fetch_json(url(SG_API, params), cache / "soilgrids-units.json", refresh)
    return {layer["name"]: layer["unit_measure"] for layer in probe["properties"]["layers"]}


def soilgrids_window(bounds):
    """The native 250 m interrupted-homolosine window covering a WGS84 box."""
    forward = Transformer.from_crs(4326, SG_CRS, always_xy=True)
    left, bottom, right, top = forward.transform_bounds(
        bounds["west"], bounds["south"], bounds["east"], bounds["north"], densify_pts=21)
    left, bottom = math.floor(left / 250) * 250, math.floor(bottom / 250) * 250
    right, top = math.ceil(right / 250) * 250, math.ceil(top / 250) * 250
    shape = (int((top - bottom) / 250), int((right - left) / 250))
    return (left, bottom, right, top), from_origin(left, top, 250, 250), shape


def soilgrids_coverage(cache, prop, depth, bounds, refresh=False, stem=None):
    """Download one aligned WCS subset and confirm it is the requested grid."""
    (left, bottom, right, top), reference, expected_shape = soilgrids_window(bounds)
    params = {"map": f"/map/{prop}.map", "SERVICE": "WCS", "VERSION": "2.0.1",
              "REQUEST": "GetCoverage", "COVERAGEID": f"{prop}_{depth}cm_mean",
              "FORMAT": "GEOTIFF_INT16", "SUBSET": [f"X({left},{right})", f"Y({bottom},{top})"],
              "SUBSETTINGCRS": SG_CRS_URL, "OUTPUTCRS": SG_CRS_URL}
    name = stem or "sg"
    path = fetch(url(SG_WCS, params), cache / f"{name}-{prop}-{depth}.tif", refresh)
    with rasterio.open(path) as raster:
        if raster.shape != expected_shape or not raster.transform.almost_equals(reference):
            raise ValueError("SoilGrids response is not the requested aligned 250 m grid")
        values = raster.read(1, masked=True).astype(float).filled(np.nan)
    values[values < 0] = np.nan
    return values


def soilgrids_at_points(cache, bounds, longitude, latitude, units, refresh=False,
                        prefix="soilgrids_regional"):
    """Read SoilGrids only in the native cells that contain a lab pedon.

    A standalone grid cell can never complete a five-input row, because SoilGrids
    publishes no EC, extractable P, or extractable K layer. The regional tier
    therefore keeps only cells where KSSL actually sampled, which is exactly
    where enrich() attaches this context to a measured horizon by depth overlap.
    One WCS subset per property and depth still covers the whole box; the cells
    that hold no pedon are simply never written out.
    """
    (left, bottom, right, top), _, shape = soilgrids_window(bounds)
    forward = Transformer.from_crs(4326, SG_CRS, always_xy=True)
    inverse = Transformer.from_crs(SG_CRS, 4326, always_xy=True)
    x, y = forward.transform(np.asarray(longitude, float), np.asarray(latitude, float))
    finite = np.isfinite(x) & np.isfinite(y)
    rows = np.where(finite, np.floor((top - y) / 250), -1).astype(int)
    cols = np.where(finite, np.floor((x - left) / 250), -1).astype(int)
    inside = finite & (rows >= 0) & (rows < shape[0]) & (cols >= 0) & (cols < shape[1])
    if not inside.any():
        return pd.DataFrame(), {"status": "skipped", "reason": "no points inside the box"}
    # One row per distinct cell: several pedons in a 250 m cell share one
    # prediction, and duplicating it would imply independent observations.
    cells = np.unique(np.stack([rows[inside], cols[inside]], axis=1), axis=0)
    frames = []
    for depth in DEPTHS:
        native = {}
        for prop in PROPERTIES:
            print(f"  SoilGrids {prop} {depth} cm at {len(cells)} pedon cells", flush=True)
            grid = soilgrids_coverage(cache, prop, depth, bounds, refresh, prefix)
            native[prop] = grid[cells[:, 0], cells[:, 1]]
        missing = ~np.isfinite(native["phh2o"]) | (native["phh2o"] <= 0)
        for values in native.values():
            values[missing] = np.nan
        frame = pd.DataFrame(convert_soilgrids(native, units))
        centre_x = left + (cells[:, 1] + .5) * 250
        centre_y = top - (cells[:, 0] + .5) * 250
        frame["longitude"], frame["latitude"] = inverse.transform(centre_x, centre_y)
        frame["mukey"] = pd.array([None] * len(cells), dtype="string")
        frame["source_depth_cm"] = depth
        frame["grid_row"], frame["grid_col"] = cells[:, 0], cells[:, 1]
        # A distinct prefix keeps these ids from colliding with the Fort Bend
        # grid, whose row/col are relative to a different window origin.
        frame["sample_id"] = (f"{prefix}:" + frame.grid_row.astype(str) + ":" +
                              frame.grid_col.astype(str) + ":" + depth)
        frame["record_kind"] = "model_prediction"
        frames.append(frame)
    return pd.concat(frames, ignore_index=True), {
        "status": "succeeded", "transport": "WCS bulk subsets sampled at lab pedon cells",
        "urls": [SG_WCS], "unit_measure": units, "native_crs": SG_CRS, "resolution_m": 250,
        "requested_bbox": bounds, "window_shape": shape, "pedon_cells": int(len(cells)),
        "sampling": "cells containing a KSSL pedon only; standalone grid cells omitted",
        "reason": "SoilGrids has no EC/P/K layer, so a standalone cell cannot complete a five-input row",
        "nitrogen": "predicted_total_N_retained_separately_not_measured_nitrate"}


def soilgrids(cache, bounds, polygons, refresh=False):
    units = soilgrids_units(cache, bounds, refresh)
    inverse = Transformer.from_crs(SG_CRS, 4326, always_xy=True)
    (left, bottom, right, top), reference, expected_shape = soilgrids_window(bounds)
    rows, cols = np.indices(expected_shape)
    lon, lat = inverse.transform(left + (cols.ravel() + .5) * 250,
                                 top - (rows.ravel() + .5) * 250)
    inside = ((lon >= bounds["west"]) & (lon <= bounds["east"]) &
              (lat >= bounds["south"]) & (lat <= bounds["north"]))
    keys = polygons.locate(lon[inside], lat[inside])
    frames = []
    for depth in DEPTHS:
        native = {}
        for prop in PROPERTIES:
            print(f"  SoilGrids {prop} {depth} cm, 250 m cells", flush=True)
            native[prop] = soilgrids_coverage(cache, prop, depth, bounds, refresh).ravel()[inside]
        # WCS omits a NoData tag. pH 0 is background, not a soil measurement.
        missing = ~np.isfinite(native["phh2o"]) | (native["phh2o"] <= 0)
        for values in native.values():
            values[missing] = np.nan
        frame = pd.DataFrame(convert_soilgrids(native, units))
        frame["latitude"], frame["longitude"], frame["mukey"] = lat[inside], lon[inside], keys
        frame["source_depth_cm"] = depth
        frame["grid_row"], frame["grid_col"] = rows.ravel()[inside], cols.ravel()[inside]
        frame["sample_id"] = ("soilgrids:" + frame.grid_row.astype(str) + ":" +
                              frame.grid_col.astype(str) + ":" + depth)
        frame["record_kind"] = "model_prediction"
        frames.append(frame)
    return pd.concat(frames, ignore_index=True), {
        "status": "succeeded", "transport": "REST unit metadata + WCS bulk subsets",
        "urls": [SG_API, SG_WCS], "unit_measure": units, "native_crs": SG_CRS,
        "resolution_m": 250, "grid_transform": list(reference)[:6], "grid_shape": expected_shape,
        "nitrogen": "predicted_total_N_retained_separately_not_measured_nitrate",
        "unavailable_properties": ["ec", "extractable_phosphorus", "extractable_potassium"]}


def wss_attributes(raw):
    raw = identifiers(raw)
    for col in ("comppct_r", "hzdept_r", "kwfact", "tfact"):
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
    flood_rank = {"None": 0, "Very rare": 1, "Rare": 2, "Occasional": 3,
                  "Frequent": 4, "Very frequent": 5}
    rows = []
    for key, group in raw.groupby("mukey", sort=True):
        # Dominant component, then its shallowest horizon. Avoid horizon/month
        # Cartesian duplication and do not average drainage class strings.
        dominant = group.sort_values(["comppct_r", "cokey"], ascending=[False, True]).iloc[0]
        component = group.loc[group.cokey.eq(dominant.cokey)]
        surface = component.sort_values(["hzdept_r", "chkey"]).iloc[0]
        floods = component.flodfreqcl.dropna()
        flood = max(floods, key=lambda x: flood_rank.get(x, -1)) if len(floods) else None
        rows.append({"mukey": key, "drainage_class": dominant.drainagecl,
                     "erosion_k_factor": surface.kwfact, "flood_frequency": flood,
                     "soil_health_rating": None, "erosion_t_factor": dominant.tfact,
                     "representative_cokey": dominant.cokey})
    return pd.DataFrame(rows, columns=WSS_COLUMNS)


def is_crop_code(code):
    # USDA CDL legend: 61 fallow, 63 forest, 64 shrub, 65 barren,
    # 81+ noncrop. 176 is grass/pasture. Double-crop classes 225-254 are crops.
    return (1 <= code <= 60) or code in (66, 67, 68, 69, 71, 72, 74, 75, 76, 77) or 204 <= code <= 254


def crop_summary(counts, classes, year):
    rows = []
    for key, histogram in sorted(counts.items()):
        valid = int(sum(histogram.values()))
        crops = {code: count for code, count in histogram.items()
                 if is_crop_code(code) and code in classes and classes[code]}
        winner = min(crops, key=lambda code: (-crops[code], code)) if crops else None
        rows.append({"mukey": key, "dominant_crop": classes[winner] if winner else None,
                     # Denominator is ALL valid map-unit pixels, including noncrop.
                     "crop_pixel_pct": 100 * crops[winner] / valid if winner else None,
                     "signal_strength": "presence_only", "dominant_crop_code": winner,
                     "cdl_year": year, "valid_pixel_count": valid,
                     "crop_pixel_count": int(sum(crops.values()))})
    return pd.DataFrame(rows, columns=CDL_COLUMNS)


def cdl(cache, polygons, mukeys, refresh=False):
    county = fetch_json(url(COUNTY_SERVICE, {"where": "STATE='48' AND COUNTY='157'",
                        "outFields": "NAME,GEOID", "f": "geojson", "outSR": 4326}),
                        cache / "fort-bend-county.geojson", refresh)
    if len(county["features"]) != 1 or county["features"][0]["properties"]["GEOID"] != "48157":
        raise ValueError("County boundary does not uniquely identify Fort Bend")
    county_geom = shape(transform_geom(4326, 5070, county["features"][0]["geometry"]))
    catalog = fetch_json(url(CDL_SERVICE + "/query", {"where": "1=1", "outFields": "*",
                         "returnGeometry": "false", "f": "json"}), cache / "cdl-catalog.json", refresh)
    if catalog.get("exceededTransferLimit"):
        raise ValueError("CDL catalog was truncated")
    items = [f["attributes"] for f in catalog["features"] if f["attributes"]["Category"] == 1]
    latest = max(items, key=lambda item: item["Year"])
    year = int(latest["Year"])
    legend = fetch_json(CDL_SERVICE + "/rasterAttributeTable?f=json", cache / "cdl-legend.json", refresh)
    classes = {int(f["attributes"]["Value"]): f["attributes"]["Class_Names"] for f in legend["features"]}
    keys = sorted(set(str(k) for k in mukeys))
    numbers = {key: i + 1 for i, key in enumerate(keys)}
    # 10 m nearest-neighbor categorical output in equal-area EPSG:5070.
    # This is a resampled service export, not a claim of native-grid alignment.
    resolution = 10
    left, bottom, right, top = county_geom.bounds
    left, bottom = math.floor(left / resolution) * resolution, math.floor(bottom / resolution) * resolution
    right, top = math.ceil(right / resolution) * resolution, math.ceil(top / resolution) * resolution
    width, height = round((right-left)/resolution), round((top-bottom)/resolution)
    transform = from_origin(left, top, resolution, resolution)
    projected = []
    for key, geometry in zip(polygons.keys, polygons.geometries):
        if key in numbers:
            clipped = shape(transform_geom(4326, 5070, mapping(geometry))).intersection(county_geom)
            if not clipped.is_empty:
                projected.append((mapping(clipped), numbers[key]))
    counts = {key: {} for key in keys}
    raster_path = cache / f"cdl-{year}-fort-bend-10m.tif"
    with rasterio.open(raster_path, "w", driver="GTiff", width=width, height=height,
                       count=1, dtype="uint8", crs="EPSG:5070", transform=transform,
                       nodata=0, compress="deflate", tiled=True) as output:
        for row in range(0, height, 4000):
            for col in range(0, width, 4000):
                h, w = min(4000, height-row), min(4000, width-col)
                window = Window(col, row, w, h)
                local = output.window_transform(window)
                bbox = rasterio.windows.bounds(window, transform)
                stem = f"cdl-{year}-{row}-{col}"
                params = {"f": "json", "bbox": ",".join(map(str, bbox)), "bboxSR": 5070,
                          "imageSR": 5070, "size": f"{w},{h}", "format": "tiff", "pixelType": "U8",
                          "noData": 0, "interpolation": "RSP_NearestNeighbor",
                          "mosaicRule": json.dumps({"mosaicMethod": "esriMosaicLockRaster", "lockRasterIds": [latest["OBJECTID"]]}),
                          "renderingRule": json.dumps({"rasterFunction": "None"})}
                tile_path = cache / (stem + ".tif")
                if refresh or not tile_path.exists():
                    response = fetch_json(url(CDL_SERVICE + "/exportImage", params), cache / (stem + ".json"), True)
                    fetch(response["href"], tile_path, True)
                with rasterio.open(tile_path) as tile:
                    if tile.count != 1 or tile.shape != (h, w) or not tile.transform.almost_equals(local) or tile.crs != rasterio.crs.CRS.from_epsg(5070):
                        raise ValueError("CDL export changed grid, CRS, or categorical bands")
                    values = tile.read(1)
                mask = rasterize([(mapping(county_geom), 1)], out_shape=(h, w), transform=local, dtype="uint8")
                values[mask == 0] = 0
                output.write(values, 1, window=window)
                zones = rasterize(projected, out_shape=(h, w), transform=local, dtype="uint16")
                valid = (zones > 0) & (values > 0)
                encoded, frequency = np.unique(zones[valid].astype(np.int64) * 256 + values[valid], return_counts=True)
                for value, count in zip(encoded, frequency):
                    key, code = keys[value // 256 - 1], int(value % 256)
                    counts[key][code] = counts[key].get(code, 0) + int(count)
                print(f"  CDL {year}: county tile {row},{col} joined", flush=True)
    result = crop_summary(counts, classes, year)
    return result, {"status": "succeeded", "url": CDL_SERVICE, "year": year,
                    "county_boundary_url": COUNTY_SERVICE, "county_geoid": "48157",
                    "resolution_m": resolution, "crs": "EPSG:5070", "resampling": "nearest",
                    "signal_strength": "presence_only", "crop_pixel_pct_denominator": "all_valid_pixels",
                    "clipped_raster_cache": str(raster_path), "raster_catalog_item": latest,
                    "legacy_endpoint_note": "CropScape TLS certificate expired; using current USDA CroplandCROS service"}


def enrich(base, grid, crops, interpretations, polygons=None,
           n_coefficient=15, oc_to_om=1.724):
    """Join spatial context without copying a lab nutrient across a soil unit.

    Survey horizons retain their own IDs and no fictional point coordinates.
    Lab layers keep measured nutrients. SoilGrids total N is auxiliary only:
    converting mass units does not convert total N into plant-available nitrate.
    """
    from pipeline import evidence, preprocess
    base = identifiers(base)
    if polygons is not None:
        lab = base.source.eq("kssl")
        base.loc[lab, "mukey"] = polygons.locate(base.loc[lab, "longitude"], base.loc[lab, "latitude"])
    if not grid.empty:
        predicted = identifiers(grid)
        predicted["source"] = "soilgrids"
        predicted["ec"] = np.nan
        predicted["phosphorus"] = np.nan
        predicted["potassium"] = np.nan  # No extractable K property in SoilGrids v2.
        predicted["nitrogen"] = np.nan
        predicted["n_derived"] = False
        predicted["n_method"] = ""
        predicted["om_pct"] = predicted.organic_carbon_pct * oc_to_om
        predicted["om_derived"] = predicted.om_pct.between(0, 100)
        predicted["om_method"] = "predicted_organic_carbon_times_conversion_factor"
        valid = predicted.om_pct.between(0, 100)
        predicted.loc[valid, "nitrogen"] = predicted.loc[valid, "om_pct"] * n_coefficient
        predicted.loc[valid, "n_derived"] = True
        predicted.loc[valid, "n_method"] = "OM_mineralization_credit_proxy"
        predicted["n_credit_ppm_per_om_pct"] = n_coefficient
        predicted["oc_to_om_factor"] = oc_to_om
        # Add predicted context only if the two requested depths cover the whole
        # lab horizon. Use thickness-weighted means for straddling horizons, never
        # extrapolate below 15 cm or transfer nutrients from a nearby pedon.
        if "hzn_top" in base and "hzn_bot" in base:
            # Grid cell lookup in native coordinates, not angular nearest distance.
            transform = Transformer.from_crs(4326, SG_CRS, always_xy=True)
            grid_x, grid_y = transform.transform(grid.longitude.to_numpy(), grid.latitude.to_numpy())
            grid_lookup = {(int(math.floor(x / 250)), int(math.floor(y / 250)), d): i
                           for i, (x, y, d) in enumerate(zip(grid_x, grid_y, grid.source_depth_cm))}
            for index, row in base.loc[base.source.eq("kssl")].iterrows():
                if not (0 <= row.hzn_top < row.hzn_bot <= 15):
                    continue
                x, y = transform.transform(row.longitude, row.latitude)
                matches = []
                for depth in DEPTHS:
                    top, bottom = map(float, depth.split("-"))
                    overlap = max(0, min(bottom, row.hzn_bot) - max(top, row.hzn_top))
                    if not overlap:
                        continue
                    match = grid_lookup.get((math.floor(x / 250), math.floor(y / 250), depth))
                    if match is not None:
                        matches.append((grid.iloc[match], overlap))
                if sum(thickness for _, thickness in matches) != row.hzn_bot - row.hzn_top:
                    continue
                base.loc[index, "soilgrids_sample_id"] = ";".join(sample.sample_id for sample, _ in matches)
                for prop in ("soilgrids_total_nitrogen_ppm", "ph", "organic_carbon_pct", "cec", "bulk_density_g_cm3"):
                    if all(pd.notna(sample[prop]) for sample, _ in matches):
                        column = prop if prop.startswith("soilgrids_") else "soilgrids_" + prop
                        base.loc[index, column] = sum(sample[prop] * thickness for sample, thickness in matches) / (row.hzn_bot-row.hzn_top)
                base.loc[index, "soilgrids_join_method"] = "same_250m_cell_depth_overlap_weighted_prediction"
        base = pd.concat([base, predicted], ignore_index=True)
    for context in (crops, interpretations):
        context = identifiers(context)
        if context.mukey.duplicated().any():
            raise ValueError("Context join must contain one row per mukey")
        base = base.merge(context, on="mukey", how="left", validate="many_to_one")
    base = evidence.apply_crop_evidence(base)
    base["n_source"] = "null"
    base.loc[base.nitrogen.notna() & ~base.n_derived, "n_source"] = "kssl_measured_nitrate"
    base.loc[base.n_derived, "n_source"] = "om_derived"
    base["complete_five_inputs"] = base[preprocess.FEATURE_COLUMNS].notna().all(axis=1)
    # Vectorized construction is much faster than one Python Series per grid row.
    missing = pd.Series("", index=base.index)
    for name in preprocess.FEATURE_COLUMNS:
        missing += base[name].isna().map({True: name + ";", False: ""})
    base["missing_inputs"] = missing.str.rstrip(";")
    if base.sample_id.duplicated().any():
        raise ValueError("Spatial joins multiplied sample identities")
    return base
