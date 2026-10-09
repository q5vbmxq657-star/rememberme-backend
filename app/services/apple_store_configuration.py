"""Shared product identity and trust roots; neither grants purchase access."""
import json
import os
from pathlib import Path

from app.services.apple_purchase_verifier import SubscriptionProduct


def configured_products():
    raw = os.environ.get("STAY_APPLE_PRODUCTS")
    values = json.loads(raw) if raw is not None else {
        f"stay.{plan}.{cadence}": {"plan": plan, "cadence": cadence}
        for plan in ("plus", "family") for cadence in ("monthly", "annual")
    }
    if not isinstance(values, dict) or not values:
        raise ValueError("Missing product mapping")
    result = {}
    for identity, terms in values.items():
        if not isinstance(identity, str) or not identity.strip() or identity != identity.strip():
            raise ValueError("Invalid product identity")
        result[identity] = SubscriptionProduct(**terms)
    return result


def configured_roots():
    raw = os.environ.get("STAY_APPLE_ROOT_CERTIFICATES")
    paths = json.loads(raw) if raw is not None else [
        str(Path(__file__).resolve().parents[1] / "resources/apple/AppleRootCA-G3.cer")
    ]
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or not p for p in paths):
        raise ValueError("Missing Apple trust roots")
    return [Path(path).read_bytes() for path in paths]
