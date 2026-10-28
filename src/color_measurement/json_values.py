"""JSON object admission shared by native messages and persisted records."""


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    """Refuse duplicate fields at every object depth before values are overwritten."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result
