"""Crop-specific thresholds from the Texas A&M tables in crop_data/.

resolve() turns the assessment form's crop name into the A&M table it is judged
against (thresholds.CROP_DEFAULT_YIELD). The Crop it returns answers what
agronomy and the recommender need: the crop's pH band, its salinity status and
its fertilizer rate at a soil-test ppm, all via crop_data/crop_lookup.py. A
crop with no A&M table resolves with table=None, and callers fall back to the
generic thresholds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Union

from crop_data import crop_lookup as CL

from . import thresholds as T

# Spellings farmers use for crops the form already knows.
_ALIASES = {
    "maize": "Corn", "milo": "Sorghum", "grain sorghum": "Sorghum",
    "soybean": "Soybeans", "soya": "Soybeans", "sunflower": "Sunflowers",
    "sweet potato": "Sweet potatoes", "tomato": "Tomatoes",
    "watermelon": "Watermelons", "cucumber": "Cucumbers", "pepper": "Peppers",
    "bell pepper": "Peppers", "bell peppers": "Peppers", "potato": "Potatoes",
    "peanut": "Peanuts", "pecan": "Pecans", "oat": "Oats",
    "blueberry": "Blueberries", "strawberry": "Strawberries",
    "hay": "Bermudagrass", "bermuda": "Bermudagrass", "bermuda grass": "Bermudagrass",
}


def to_ece(ec: float, method: str = T.SENSOR_EC_METHOD) -> float:
    """Convert an EC reading (dS/m) on the given extract basis to estimated ECe."""
    return ec * CL.ECE_FACTOR[method]


def normalize_crop_name(crop: Optional[str]) -> Optional[str]:
    """Match known crops without changing the stored custom crop label."""
    if not isinstance(crop, str) or not crop.strip():
        return None
    name = " ".join(crop.split())
    known = {key.casefold(): key for key in (*T.CROP_DEFAULT_YIELD, *T.CROPS)}
    return known.get(name.casefold(), _ALIASES.get(name.casefold(), name))


@dataclass(frozen=True)
class Crop:
    name: str                          # normalized form crop, e.g. "Corn"
    category: Optional[str] = None     # A&M category, e.g. "row"
    table: Optional[str] = None        # A&M table, e.g. "CORN (150 BU/A)"
    yield_goal: Optional[str] = None   # e.g. "150 bu/A"
    assumed: bool = False              # table is a default pick among A&M variants

    @property
    def mapped(self) -> bool:
        return self.table is not None

    @property
    def label(self) -> str:
        """The crop name as it reads mid-sentence ("corn", "sweet potatoes")."""
        return self.name.lower()

    def _entry(self) -> Dict:
        return CL.NPK[self.category][self.table]

    def profile(self) -> Dict:
        return CL.profile(self.category, self.table)

    def ph_band(self) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """((optimal low, high), (tolerable low, high))."""
        p = self.profile()
        return tuple(p["ph_opt"]), tuple(p["ph_tol"])

    def salinity(self, ece: float) -> Dict:
        return CL.salinity_status(self.category, self.table, ece=ece)

    def has_table(self, nutrient: str) -> bool:
        return self.mapped and bool(self._entry().get(nutrient))

    def fert_rec(self, nutrient: str, soil_ppm: float) -> Optional[float]:
        """lb/acre of N, P2O5 or K2O that A&M says to add at this soil test."""
        return CL.fert_rec(self.category, self.table, nutrient, soil_ppm)

    def sufficiency(self, nutrient: str) -> Optional[float]:
        return CL.sufficiency_ppm(self.category, self.table, nutrient)

    def am_note(self, nutrient: str) -> Optional[str]:
        """A&M's application note for a nutrient, e.g. split-timing advice."""
        note = (self._entry().get("notes") or {}).get(nutrient)
        return note.strip(" -") if note else None

    def basis(self) -> str:
        """One line for reports naming the table the readout is based on."""
        if self.yield_goal:
            return ("Based on a %s yield goal of %s (Texas A&M soil-test tables)."
                    % (self.label, self.yield_goal))
        if self.assumed:
            return 'Assumes the Texas A&M table "%s".' % re.sub(r"\s+,", ",", self.table)
        return "Based on the Texas A&M soil-test table for %s." % self.label


def _yield_label(table: str) -> str:
    """'CORN (180 BU/A)' -> '180 bu/A', for a table chosen by yield_goal."""
    match = re.search(r"\(([^)]*)\)", table)
    text = match.group(1) if match else table
    text = re.sub(r"\s+,", ",", text.lower()).replace("lbs", "lb")
    return text.replace("/acre", "/A").replace("/a", "/A")


def resolve(crop: Optional[str], yield_goal: Optional[str] = None) -> Optional[Crop]:
    """The Crop for a form crop name, or None when no crop was recorded.

    The A&M table defaults to the crop's CROP_DEFAULT_YIELD entry. For a crop
    with a yield goal, yield_goal overrides it with another A&M table for the
    same crop, named as in crop_npk_tables.json (e.g. "CORN (180 BU/A)").
    """
    name = normalize_crop_name(crop)
    if name is None:
        return None
    if name not in T.CROP_DEFAULT_YIELD:
        return Crop(name)
    category, table, goal, assumed = T.CROP_DEFAULT_YIELD[name]
    if yield_goal is not None and yield_goal != table:
        profile_of = lambda t: CL.CROP_TO_PROFILE.get("%s/%s" % (category, t))
        if goal is None or profile_of(yield_goal) != profile_of(table):
            raise ValueError("%r is not a Texas A&M yield-goal table for %s" % (yield_goal, name))
        table, goal = yield_goal, _yield_label(yield_goal)
    return Crop(name, category, table, goal, assumed)


def as_crop(crop: Union[Crop, str, None]) -> Optional[Crop]:
    """Accept either a resolved Crop or a form crop name."""
    return crop if isinstance(crop, Crop) else resolve(crop)


def assess(sample: Dict, crop: Union[Crop, str, None]) -> Optional[Dict]:
    """crop_lookup.assess() for a sample whose EC is already ECe.

    None when the crop has no A&M table.
    """
    crop = as_crop(crop)
    if crop is None or not crop.mapped:
        return None
    out = CL.assess(crop.category, crop.table, ph=float(sample["ph"]),
                    ece=float(sample["ec"]), n_ppm=float(sample["nitrogen"]),
                    p_ppm=float(sample["phosphorus"]), k_ppm=float(sample["potassium"]))
    out["yield_goal"] = crop.yield_goal
    return out
