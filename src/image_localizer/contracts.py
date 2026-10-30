"""Strict task contracts; semantic judgements remain the agent's responsibility."""
from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import ImageColor


class ContractError(ValueError):
    """A task is incomplete, inconsistent, or unsafe to render."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def number(value: object, label: str, minimum: float, maximum: float) -> float:
    require(type(value) in (int, float) and math.isfinite(value), f"{label}: expected finite number")
    require(minimum <= value <= maximum, f"{label}: expected {minimum}..{maximum}")
    return value


def box(value: object, width: int, height: int, label: str) -> list[int]:
    require(isinstance(value, list) and len(value) == 4, f"{label}: expected [x,y,w,h]")
    require(all(type(item) is int for item in value), f"{label}: coordinates must be integers")
    x, y, w, h = value
    require(x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= width and y + h <= height,
            f"{label}: outside canvas or empty")
    return value


def overlaps(first: list[int], second: list[int]) -> bool:
    x, y, w, h = first
    a, b, c, d = second
    return x < a + c and a < x + w and y < b + d and b < y + h


def color(value: object, label: str) -> None:
    require(isinstance(value, str), f"{label}: expected color string")
    try:
        parsed = ImageColor.getrgb(value)
        require(len(parsed) == 3, f"{label}: use opaque RGB colors")
    except (ValueError, TypeError) as error:
        raise ContractError(f"{label}: invalid RGB color") from error


def validate_manifest(manifest: dict, *, ready: bool = True) -> None:
    require(isinstance(manifest, dict) and manifest.get("schema_version") == 1, "Unsupported manifest version")
    require(isinstance(manifest.get("canvas"), dict), "Missing canvas")
    width, height = manifest["canvas"].get("width"), manifest["canvas"].get("height")
    require(type(width) is int and type(height) is int and width > 0 and height > 0, "Invalid canvas size")
    require(isinstance(manifest.get("target_language"), str) and bool(manifest["target_language"].strip()), "Target language required")
    source = manifest.get("source", {})
    require(isinstance(source, dict) and re.fullmatch(r"[a-f0-9]{64}", source.get("sha256", "")) is not None,
            "Missing source SHA256")
    analysis = manifest.get("analysis", {})
    require(isinstance(analysis, dict) and isinstance(analysis.get("observations"), list), "Analysis observations required")
    if ready:
        require(analysis.get("complete") is True and bool(analysis["observations"]), "Actual image analysis is incomplete")
    regions = manifest.get("regions")
    require(isinstance(regions, list), "regions must be an array")
    if ready:
        require(not analysis.get("unresolved"), "Analysis has unresolved questions")
        require(bool(regions) or analysis.get("no_text_verified") is True, "Empty region inventory requires explicit full-image no-text verification")
    require(isinstance(manifest.get("protected_regions"), list), "protected_regions must be an array")
    identifiers: set[str] = set()
    translated_boxes: list[tuple[str, list[int]]] = []
    protected_boxes: list[tuple[str, list[int]]] = []
    for region in regions:
        require(isinstance(region, dict), "Each region must be an object")
        region_id = region.get("id")
        require(isinstance(region_id, str) and bool(region_id.strip()) and region_id not in identifiers, "Duplicate or empty region ID")
        identifiers.add(region_id)
        bbox = box(region.get("bbox"), width, height, region_id)
        require(region.get("action") in ("translate", "preserve"), f"{region_id}: invalid action")
        require(isinstance(region.get("source_text"), str) and bool(region["source_text"].strip()), f"{region_id}: source_text required")
        number(region.get("confidence"), f"{region_id}.confidence", 0, 1)
        if ready:
            require(region["confidence"] >= 0.9 or region.get("recognition_verified") is True,
                    f"{region_id}: low-confidence recognition needs explicit verification")
        require(isinstance(region.get("notes"), str), f"{region_id}: notes required")
        if region["action"] == "preserve":
            require(bool(region["notes"].strip()), f"{region_id}: preservation reason required")
            protected_boxes.append((region_id, bbox))
            continue
        translated_boxes.append((region_id, bbox))
        require(isinstance(region.get("target_text"), str) and bool(region["target_text"].strip()), f"{region_id}: target_text required")
        style = region.get("style")
        require(isinstance(style, dict), f"{region_id}: style required")
        require(isinstance(style.get("font_path"), str) and bool(style["font_path"]), f"{region_id}: explicit font_path required")
        minimum = number(style.get("min_font_size", 14), "min_font_size", 8, 1024)
        maximum = number(style.get("max_font_size", 64), "max_font_size", minimum, 1024)
        require(type(minimum) is int and type(maximum) is int, "Font sizes must be integers")
        require(style.get("align", "center") in ("left", "center", "right"), f"{region_id}: invalid alignment")
        for key in ("padding", "line_spacing", "stroke_width"):
            require(type(style.get(key, {"padding": 4, "line_spacing": 4, "stroke_width": 0}[key])) is int,
                    f"{region_id}: {key} must be integer pixels")
        number(style.get("padding", 4), "padding", 0, min(bbox[2:]) / 2 - 0.01)
        number(style.get("line_spacing", 4), "line_spacing", 0, 256)
        number(style.get("stroke_width", 0), "stroke_width", 0, 16)
        color(style.get("color", "#000000"), "text color")
        color(style.get("stroke_fill", "#000000"), "stroke color")
        repair = region.get("repair", {})
        require(isinstance(repair, dict) and repair.get("mode") in ("solid", "clean_plate"), f"{region_id}: explicit repair mode required")
        if repair["mode"] == "solid":
            color(repair.get("color"), f"{region_id}: solid repair color")
    for protected in manifest["protected_regions"]:
        require(isinstance(protected, dict) and isinstance(protected.get("id"), str), "Invalid protected region")
        protected_boxes.append((protected["id"], box(protected.get("bbox"), width, height, "protected region")))
    for index, (region_id, bbox) in enumerate(translated_boxes):
        for other_id, other_box in translated_boxes[index + 1:] + protected_boxes:
            require(not overlaps(bbox, other_box), f"{region_id} overlaps {other_id}; refine text/repair regions")
    glossary = manifest.get("glossary", [])
    require(isinstance(glossary, list), "glossary must be an array")
    for term in glossary:
        require(isinstance(term, dict) and all(isinstance(term.get(key), str) and term[key].strip() for key in ("source", "target")), "Invalid glossary term")


def advisory_checks(manifest: dict) -> list[dict]:
    """Conservative hints, never an automated semantic pass/fail."""
    findings = []
    for region in manifest["regions"]:
        if region["action"] != "translate":
            continue
        source_numbers = re.findall(r"\d+(?:[.,]\d+)*", region["source_text"])
        target_numbers = re.findall(r"\d+(?:[.,]\d+)*", region["target_text"])
        if source_numbers != target_numbers:
            findings.append({"region_id": region["id"], "kind": "numbers", "note": "Check numbers/decimal localization; differences require human judgement."})
        for term in manifest.get("glossary", []):
            if term["source"].casefold() in region["source_text"].casefold() and term["target"] not in region["target_text"]:
                findings.append({"region_id": region["id"], "kind": "glossary", "note": f"Expected terminology: {term['target']}"})
    return findings
