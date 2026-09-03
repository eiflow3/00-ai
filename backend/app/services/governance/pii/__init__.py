"""The PII stage: detect candidate entities, classify whose data they are,
and act on them per policy.  Detection (`detector`), classification
(`classifier`) and action (`actions`) are deliberately separate modules so
"what we do about it" can change without touching "how we find it"."""
