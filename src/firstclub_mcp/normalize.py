"""Turn FirstClub's deeply nested server-driven responses into compact rows for an LLM."""
from __future__ import annotations

import re
from typing import Any, Iterator


def walk(o: Any) -> Iterator[dict]:
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def image_url(u: str | None, size: str = "m") -> str | None:
    """CDN sizes: 's', 'l', or 'm' (= original path without a size segment), as in the app."""
    if not u:
        return None
    u = u if u.startswith("http") else "https://" + u
    return u.replace("/resolution/", "/" if size == "m" else f"/{size}/")


def _pack(qty: Any, uom: Any) -> str | None:
    q = str(qty).strip() if qty not in (None, "") else ""
    u = str(uom).strip() if uom not in (None, "") else ""
    if u and u.lower() not in q.lower():
        q = f"{q} {u}".strip()
    return q or None


def product(p: dict) -> dict:
    price = p.get("price") or {}
    one = price.get("oneTimePrice") or {}
    inv = p.get("inventory") or {}
    brand = p.get("brand")
    img = p.get("displayAssetUrl") or next((a.get("cdnBaseUrl") or a.get("url") for a in p.get("assets") or [] if isinstance(a, dict)), None)
    row = {
        "fsn": p.get("fsn") or p.get("fsnId"),
        "listingId": p.get("listingId"),
        "name": p.get("displayName") or p.get("productDisplayName") or p.get("name"),
        "brand": brand.get("name") if isinstance(brand, dict) else brand,
        "pack": _pack(p.get("quantity"), p.get("uom")),
        "price": one.get("memberPrice") or price.get("sellingPrice"),
        "nonMemberPrice": one.get("nonMemberPrice"),
        "mrp": price.get("mrp"),
        "inStock": inv.get("inStock"),
        "veg": p.get("veg"),
        "labReport": p.get("labReportAvailable") or None,
        "image": image_url(img),
    }
    return {k: v for k, v in row.items() if v not in (None, "", [])}


def products_in(o: Any) -> list[dict]:
    """Every distinct product-looking object (has fsn + a name) anywhere in a response."""
    seen, out = set(), []
    for d in walk(o):
        fsn = d.get("fsn")
        if fsn and isinstance(fsn, str) and (d.get("displayName") or d.get("name")) and fsn not in seen:
            seen.add(fsn)
            out.append(product(d))
    return out


def categories_in(o: Any) -> list[dict]:
    seen, out = set(), []
    for d in walk(o):
        cid = d.get("categoryId")
        if isinstance(cid, str) and cid not in seen and d.get("displayName"):
            seen.add(cid)
            out.append({"categoryId": cid, "name": d["displayName"]})
    return out


_ROW = re.compile(r"^\s*\|(.+)\|\s*$")


def markdown_table(md: str | None) -> list[list[str]]:
    rows = []
    for line in (md or "").splitlines():
        m = _ROW.match(line)
        if m and not re.fullmatch(r"[\s|:\-]+", m.group(1)):
            rows.append([c.strip().strip("*") for c in m.group(1).split("|")])
    return rows


def product_detail(p: dict) -> dict:
    """FirstClub has no structured nutrition: macros live only in the label photos (`images`)."""
    row = product(p)
    cat, sub = p.get("category") or {}, p.get("subcategory") or {}
    desc = p.get("productDescription")
    row.update({
        "category": " / ".join(x for x in (cat.get("name"), sub.get("name")) if x) or None,
        "remaining": (p.get("inventory") or {}).get("remainingQuantity"),
        "veg": p.get("vegMarker", p.get("veg")),
        "allergens": p.get("allergensMarker"),
        "ingredients": p.get("baseIngredientsMarker"),
        "nutrition_table": markdown_table(p.get("nutritionalInformationMarker")) or p.get("nutritionalInformationMarker"),
        "highlights": [h.get("label") for h in p.get("productHighlights") or [] if isinstance(h, dict) and h.get("label")],
        "keyFeatures": p.get("keyFeatures"), "specifications": p.get("specifications"),
        "storage": p.get("storageCondition"),
        "description": desc.get("value") if isinstance(desc, dict) else desc,
        "images": [image_url(a.get("cdnBaseUrl"), "l") for a in p.get("assets") or [] if isinstance(a, dict) and a.get("cdnBaseUrl")],
    })
    return {k: v for k, v in row.items() if v not in (None, "", [], {})}
