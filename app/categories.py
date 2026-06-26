import os

CATEGORIES = {
    "bim":   {"ext": [".ifc"], "max_mb": 500},
    "image": {"ext": [".png", ".jpg", ".jpeg", ".webp"], "max_mb": 50},
    "svg":   {"ext": [".svg"], "max_mb": 5},
}


def validate(category: str, filename: str, size_bytes: int) -> None:
    rule = CATEGORIES.get(category)
    if rule is None:
        raise ValueError(f"Unknown category: {category}")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in rule["ext"]:
        raise ValueError(f"Extension {ext} not allowed for category {category}")
    if size_bytes > rule["max_mb"] * 1024 * 1024:
        raise ValueError(f"File exceeds {rule['max_mb']} MB limit")
