"""GPS zone aggregation + color-coded field-map rendering.

Farmers sample in a zigzag/grid across a field (composite samples, ~10 acres
max each, avoiding fencerows/dung piles/limestone outcrops). This module bins
those points into management zones -- using farmer-provided zone labels if
present, otherwise an automatic lat/lon grid -- aggregates each zone, and draws a
red->green soil-health map.
"""

from __future__ import annotations

import math
import warnings
from typing import Dict, Tuple

import matplotlib
matplotlib.use("Agg")  # headless: render straight to PNG, no display needed
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from . import agronomy, thresholds as T

_CMAP = plt.get_cmap("RdYlGn")   # red = unhealthy, green = healthy
_NORM = Normalize(vmin=0, vmax=100)


def assign_zones(df: pd.DataFrame, target_per_zone: int = 4,
                 max_grid: int = 5) -> Tuple[pd.DataFrame, Dict]:
    """Attach a `zone` label to each sample.

    If every row already has a farmer-supplied zone, respect it. Otherwise build
    the smallest sensible NxN lat/lon grid (aiming for ~target_per_zone samples
    per cell) and label zones Z{row}-{col}.
    """
    df = df.copy()
    has_zone_col = "zone" in df.columns and len(df)
    provided_mask = df["zone"].notna() if has_zone_col else None

    # All rows labeled -> respect farmer zones exactly.
    if has_zone_col and provided_mask.all():
        df["zone"] = df["zone"].astype(str)
        return df, {"mode": "provided"}

    # Build an auto lat/lon grid for the (possibly all) unlabeled rows.
    n = len(df)
    dim = max(1, min(max_grid, math.ceil(math.sqrt(max(1, round(n / target_per_zone))))))
    lat = df["latitude"].astype(float).values
    lon = df["longitude"].astype(float).values
    lat_min, lat_max = float(lat.min()), float(lat.max())
    lon_min, lon_max = float(lon.min()), float(lon.max())
    # pad degenerate extents (all-same-point fields)
    if lat_max - lat_min < 1e-9:
        lat_min, lat_max = lat_min - 1e-4, lat_max + 1e-4
    if lon_max - lon_min < 1e-9:
        lon_min, lon_max = lon_min - 1e-4, lon_max + 1e-4
    lat_edges = np.linspace(lat_min, lat_max, dim + 1)
    lon_edges = np.linspace(lon_min, lon_max, dim + 1)

    def _cell(v, edges):
        i = int(np.searchsorted(edges, v, side="right") - 1)
        return min(max(i, 0), len(edges) - 2)

    rows = [_cell(v, lat_edges) for v in lat]
    cols = [_cell(v, lon_edges) for v in lon]
    df["_grid_row"] = rows
    df["_grid_col"] = cols
    df["zone"] = ["Z%d-%d" % (r + 1, c + 1) for r, c in zip(rows, cols)]
    meta = {
        "mode": "grid", "dim": dim,
        "lat_edges": lat_edges.tolist(), "lon_edges": lon_edges.tolist(),
    }
    return df, meta


def aggregate_zones(df: pd.DataFrame) -> pd.DataFrame:
    """One row per zone: centroid, mean metrics, mean score/category, dominant
    problem, and which crops are viable from the zone's *average* conditions."""
    rows = []
    for zone, g in df.groupby("zone", sort=True):
        means = {c: float(g[c].mean()) for c in T.MEASUREMENT_COLUMNS}
        mean_score = float(g["health_score"].mean())
        probs = g.loc[g["primary_problem_code"] != "none", "primary_problem_code"]
        dominant = probs.mode().iloc[0] if len(probs) else "none"
        viability = agronomy.crop_viability(means)
        rows.append({
            "zone": str(zone),
            "n_samples": int(len(g)),
            "centroid_lat": round(float(g["latitude"].mean()), 6),
            "centroid_lon": round(float(g["longitude"].mean()), 6),
            "mean_health_score": round(mean_score, 1),
            "health_category": T.categorize(mean_score),
            "min_health_score": round(float(g["health_score"].min()), 1),
            "dominant_problem": dominant,
            "viable_crops": ", ".join(viability["viable"]) or "none",
            "n_viable_crops": len(viability["viable"]),
            **{"mean_" + c: round(means[c], 2) for c in T.MEASUREMENT_COLUMNS},
        })
    out = pd.DataFrame(rows).sort_values("mean_health_score").reset_index(drop=True)
    return out


def render_field_map(df: pd.DataFrame, zone_summary: pd.DataFrame,
                     grid_meta: Dict, out_png: str,
                     field_name: str = "Field") -> str:
    """Render and save the color-coded field map PNG. Returns the path."""
    fig, ax = plt.subplots(figsize=(10, 8))
    lon = df["longitude"].astype(float).values
    lat = df["latitude"].astype(float).values

    # Filled zone cells (only for auto-grid mode, where cell extents are known).
    if grid_meta.get("mode") == "grid":
        lat_edges = grid_meta["lat_edges"]
        lon_edges = grid_meta["lon_edges"]
        score_by_zone = dict(zip(zone_summary["zone"],
                                 zone_summary["mean_health_score"]))
        for zone, r, c in df[["zone", "_grid_row", "_grid_col"]].drop_duplicates().values:
            score = score_by_zone.get(zone)
            if score is None:
                continue
            x0, x1 = lon_edges[int(c)], lon_edges[int(c) + 1]
            y0, y1 = lat_edges[int(r)], lat_edges[int(r) + 1]
            ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0,
                                   facecolor=_CMAP(_NORM(score)), alpha=0.35,
                                   edgecolor="0.5", linewidth=0.8, zorder=1))
        for e in lon_edges:
            ax.axvline(e, color="0.75", linewidth=0.5, zorder=0)
        for e in lat_edges:
            ax.axhline(e, color="0.75", linewidth=0.5, zorder=0)

    # Sample points, colored by health score.
    sc = ax.scatter(lon, lat, c=df["health_score"].astype(float).values,
                    cmap=_CMAP, norm=_NORM, s=140, edgecolors="black",
                    linewidths=0.7, zorder=3)

    # Zone centroid labels with mean score.
    for _, z in zone_summary.iterrows():
        ax.annotate("%s\n%.0f" % (z["zone"], z["mean_health_score"]),
                    (z["centroid_lon"], z["centroid_lat"]),
                    ha="center", va="center", fontsize=8, fontweight="bold",
                    color="black", zorder=4,
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="0.4", alpha=0.75))

    cbar = fig.colorbar(sc, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Soil Health Score (0-100)")
    # category tick guide on the colorbar
    cbar.set_ticks([10, 30, 50, 70, 90])
    cbar.set_ticklabels(["Critical", "Poor", "Fair", "Good", "Excellent"])

    ax.set_title("FarmersFirst Soil Health Map -- %s\n(%d samples, %d zones)"
                 % (field_name, len(df), len(zone_summary)))
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_aspect("equal", adjustable="datalim")
    ax.margins(0.08)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_png
