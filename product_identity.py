"""Conservative title matching; filenames never prove image contents."""

import re
import unicodedata


def compact(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower().replace("huawei", "").replace("华为", "")
    return re.sub(r"[\W_]+", "", value)


def model_names(value: str) -> set[str]:
    value = unicodedata.normalize("NFKC", value).lower()
    value = re.sub(r"[_\-]+", " ", value)
    pattern = r"matebook\s*(?:(?:gt|d|e)\s*)?\d+[a-z]*(?:\s+(?:pro|plus)(?![a-z]))?|matebook\s*x\s*(?:pro)?|matebook\s*fold"
    return {compact(match.group()) for match in re.finditer(pattern, value)}


def matches_product(product: str, title: str) -> bool:
    target, found = model_names(product), model_names(title)
    if target:
        # Reject comparison/collection files containing another named model too.
        return len(target) == 1 and found == target and compact(product) in compact(title)
    query = compact(product)
    return bool(query) and bool(re.search(re.escape(query) + r"(?![a-z0-9])", compact(title)))


def different_model(product: str, title: str) -> bool:
    target, found = model_names(product), model_names(title)
    target_years = set(re.findall(r"20[2-3]\d", product))
    found_years = set(re.findall(r"20[2-3]\d", title))
    return bool(target and found and (found != target or (target_years and found_years and target_years != found_years)))
