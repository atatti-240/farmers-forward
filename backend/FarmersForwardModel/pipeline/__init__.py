"""FarmersFirst soil-health ML pipeline package.

Modules
-------
thresholds   Single source of truth for Texas soil thresholds + crop table.
agronomy     Deterministic domain logic: health score, problem detection, crop viability.
preprocess   Cleaning, validation, feature engineering, canonical column contracts.
recommender  Rule-based specific fixes (rate / cost / timing) + experimental cost-benefit.
model        Multi-output ML models (score regressor, category classifier, crop multi-label).
zone_mapper  GPS grid aggregation + color-coded field-map rendering.
reporter     JSON + plain-English report builders.
"""

__version__ = "0.1.0"
