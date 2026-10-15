"""api/serializers.py — `JobStorage` dict → pydantic response model boundary.

Thin converters. `extra="forbid"` on every model means a `JobStorage` method
that grows an unexpected key fails here (and in the contract test) instead of
leaking into a response (US3-4).
"""
from collections.abc import Sequence


def to_model(model_cls, data):
    """Serialize one dict row into `model_cls`; None → None."""
    return model_cls(**data) if data is not None else None


def to_model_list(model_cls, rows: Sequence) -> list:
    """Serialize a list of dict rows into a list of `model_cls` instances."""
    return [model_cls(**r) for r in rows]
