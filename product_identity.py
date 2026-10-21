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


def is_series_product(value: str) -> bool:
    return "系列" in unicodedata.normalize("NFKC", value)


def series_bases(value: str) -> set[str]:
    text = unicodedata.normalize("NFKC", value).lower()
    match = re.search(r"matebook\s*([a-z]+)\s*系列", text)
    return {compact("matebook " + match.group(1))} if match else set()


def matches_product(product: str, title: str) -> bool:
    target, found = model_names(product), model_names(title)
    if is_series_product(product) and found:
        bases = series_bases(product) or target
        return all(any(name.startswith(base) for base in bases) for name in found)
    if target:
        # A series card may contain concrete SKUs inside the same family, such
        # as “MateBook GT 系列” containing “GT 14” images. This does not allow
        # another family like D 14 or D 16 to pass.
        # Reject comparison/collection files containing another named model too.
        return len(target) == 1 and found == target and compact(product) in compact(title)
    query = compact(product)
    return bool(query) and bool(re.search(re.escape(query) + r"(?![a-z0-9])", compact(title)))


def different_model(product: str, title: str) -> bool:
    target, found = model_names(product), model_names(title)
    if is_series_product(product) and found:
        bases = series_bases(product) or target
        return not all(any(name.startswith(base) for base in bases) for name in found)
    target_years = set(re.findall(r"20[2-3]\d", product))
    found_years = set(re.findall(r"20[2-3]\d", title))
    return bool(target and found and (found != target or (target_years and found_years and target_years != found_years)))
