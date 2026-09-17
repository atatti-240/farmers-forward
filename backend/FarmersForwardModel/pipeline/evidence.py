"""Keep planting presence distinct from observed crop performance."""
import pandas as pd


def apply_crop_evidence(frame):
    frame = frame.copy()
    if "dominant_crop" not in frame:
        frame["dominant_crop"] = pd.NA
    if "signal_strength" not in frame:
        frame["signal_strength"] = pd.NA
    if "crop_signal_source" not in frame:
        frame["crop_signal_source"] = pd.NA
    cdl = frame["signal_strength"].eq("presence_only").fillna(False)
    observed = frame["crop_signal_source"].isin(["yield", "trial"])
    presence = cdl & frame["dominant_crop"].notna() & ~observed
    frame.loc[presence, "crop_signal_source"] = "cdl"
    # CDL planting is weaker evidence than real yield/trial outcomes. When
    # Prairie View/AgriLife outcomes arrive, their matched sample/crop records
    # must replace CDL-derived labels entirely, not be appended as duplicates.
    # Weight is explicit so future ingestion cannot silently treat presence as yield.
    frame["evidence_weight"] = 1.0
    frame.loc[presence, "evidence_weight"] = 0.5
    # sample_weight keeps its original evidence-only meaning so the Fort Bend
    # dataset and anything reading it are unchanged.
    frame["sample_weight"] = frame["evidence_weight"]
    # Regional similarity is a separate axis from evidence strength. Rows with
    # no region_weight (synthetic samples, pre-tiering datasets) count in full;
    # build_real_dataset overrides region_weight from the row's region_tier.
    region = pd.to_numeric(frame.get("region_weight", pd.Series(1.0, index=frame.index)),
                           errors="coerce").fillna(1.0)
    frame["region_weight"] = region
    frame["final_weight"] = region * frame["evidence_weight"]
    return frame
