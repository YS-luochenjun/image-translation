"""Strict, in-memory building blocks for layered image localization.

All rectangles use [x, y, width, height]. No function writes output files or
assigns visual approval; protection cuts explicitly require caller review.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont, features

from .rendering import _requires_raqm

_MAX_CANVAS_PIXELS = 50_000_000
_MAX_WORKING_PIXELS = 100_000_000


def _number(value, label: str, *, minimum=None, maximum=None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: expected a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ValueError(f"{label}: number outside allowed range")
    return value


def _integer(value, label: str, *, minimum=0, maximum=None) -> int:
    _number(value, label, minimum=minimum, maximum=maximum)
    if not isinstance(value, int):
        raise ValueError(f"{label}: expected an integer")
    return value


def _sequence(value, length: int, label: str):
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"{label}: expected {length} values")
    return value


def _size(value, label="size", *, limit=_MAX_CANVAS_PIXELS) -> tuple[int, int]:
    width, height = _sequence(value, 2, label)
    width = _integer(width, f"{label}.width", minimum=1)
    height = _integer(height, f"{label}.height", minimum=1)
    if width * height > limit:
        raise ValueError(f"{label}: pixel limit exceeded ({limit})")
    return width, height


def _bbox(value, size, label="bbox") -> tuple[int, int, int, int]:
    x, y, width, height = _sequence(value, 4, label)
    x = _integer(x, f"{label}.x")
    y = _integer(y, f"{label}.y")
    width = _integer(width, f"{label}.width", minimum=1)
    height = _integer(height, f"{label}.height", minimum=1)
    if x + width > size[0] or y + height > size[1]:
        raise ValueError(f"{label}: rectangle outside canvas")
    return x, y, x + width, y + height


def _fields(value, allowed: set[str], required: set[str], label: str):
    if not isinstance(value, dict):
        raise ValueError(f"{label}: expected an object")
    if set(value) - allowed:
        raise ValueError(f"{label}: unknown fields {sorted(map(str, set(value) - allowed))}")
    if required - set(value):
        raise ValueError(f"{label}: missing fields {sorted(required - set(value))}")


def _items(value, label: str):
    if not isinstance(value, list):
        raise ValueError(f"{label}: expected a list")
    return value


def _rgb(value) -> tuple[int, int, int]:
    if isinstance(value, str):
        try:
            rgba = ImageColor.getcolor(value, "RGBA")
        except (ValueError, TypeError) as error:
            raise ValueError("color: expected opaque RGB") from error
        if rgba[3] != 255:
            raise ValueError("color: expected opaque RGB")
        return rgba[:3]
    channels = _sequence(value, 3, "color")
    return tuple(_integer(channel, "color channel", maximum=255) for channel in channels)


def _local_path(value, *, base_dir=None) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("path: expected a local file path")
    if "://" in str(value) or str(value).lower().startswith(("data:", "file:")):
        raise ValueError("path: URLs are not supported")
    path = Path(value).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = Path(base_dir) / path
    try:
        path = path.resolve(strict=True)
    except (OSError, ValueError, RuntimeError) as error:
        raise ValueError(f"Missing local file: {value}") from error
    if not path.is_file():
        raise ValueError(f"Not a local file: {path}")
    return path


def _single_image(image: Image.Image, label: str, *, limit=_MAX_CANVAS_PIXELS):
    if not isinstance(image, Image.Image):
        raise ValueError(f"{label}: expected a Pillow image")
    if getattr(image, "n_frames", 1) != 1:
        raise ValueError(f"{label}: multi-frame images are not supported")
    _size(image.size, label, limit=limit)


def _identity(value, seen: set[str], label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value in seen:
        raise ValueError(f"{label}: id must be a unique nonempty string")
    seen.add(value)
    return value


def _histogram_median(histogram: list[int], count: int) -> int:
    """Return int(median(values)) without retaining donor-sized Python lists."""
    left_index, right_index = (count - 1) // 2, count // 2
    cumulative = 0
    left_value = None
    for value, frequency in enumerate(histogram):
        cumulative += frequency
        if left_value is None and cumulative > left_index:
            left_value = value
        if cumulative > right_index:
            return (left_value + value) // 2
    raise ValueError("sample_rows: no valid donor pixels")


def repair_background(source: Image.Image, operations: list[dict]) -> tuple[Image.Image, Image.Image, list[dict]]:
    """Apply solid/sample_rows/restore, sampling only immutable original pixels.

    The L mask is 255 over repaired rectangles and 0 over restored rectangles,
    even if a fill happens to equal its source. Restore also restores alpha.
    """
    _single_image(source, "source")
    _items(operations, "operations")
    original = source.convert("RGBA")
    result = original.copy()
    mask = Image.new("L", source.size)
    reports = []
    source_pixels = original.load()
    for index, operation in enumerate(operations):
        label = f"operations[{index}]"
        if not isinstance(operation, dict):
            raise ValueError(f"{label}: expected an object")
        kind = operation.get("type")
        allowed = {
            "solid": {"type", "bbox", "color"},
            "sample_rows": {"type", "bbox", "donors", "row_radius", "rgb_min", "rgb_max"},
            "restore": {"type", "bbox"},
        }
        if not isinstance(kind, str) or kind not in allowed:
            raise ValueError(f"{label}: unknown operation {kind!r}; clean_plate belongs to the orchestrator")
        _fields(operation, allowed[kind], allowed[kind], label)
        rectangle = _bbox(operation["bbox"], source.size)
        record = {"type": kind, "bbox": list(operation["bbox"])}
        if kind == "restore":
            result.paste(original.crop(rectangle), rectangle[:2])
            mask.paste(0, rectangle)
        elif kind == "solid":
            color = _rgb(operation["color"])
            result.paste((*color, 255), rectangle)
            mask.paste(255, rectangle)
            record["color"] = list(color)
        else:
            donors = _items(operation["donors"], "donors")
            if not donors:
                raise ValueError("sample_rows: no valid donor rectangles")
            donor_rectangles = [_bbox(box, source.size, "donor bbox") for box in donors]
            radius = _integer(operation["row_radius"], "row_radius", maximum=max(source.size))
            lower = _rgb(_sequence(operation["rgb_min"], 3, "rgb_min"))
            upper = _rgb(_sequence(operation["rgb_max"], 3, "rgb_max"))
            if any(low > high for low, high in zip(lower, upper)):
                raise ValueError("sample_rows: rgb_min exceeds rgb_max")
            for y in range(rectangle[1], rectangle[3]):
                channels = [[0] * 256 for _ in range(3)]
                count = 0
                for left, top, right, bottom in donor_rectangles:
                    for donor_y in range(max(top, y - radius), min(bottom, y + radius + 1)):
                        for donor_x in range(left, right):
                            pixel = source_pixels[donor_x, donor_y]
                            if all(lower[c] <= pixel[c] <= upper[c] for c in range(3)):
                                count += 1
                                for c in range(3):
                                    channels[c][pixel[c]] += 1
                if not count:
                    raise ValueError(f"sample_rows: no valid donor pixels at y={y}")
                color = tuple(_histogram_median(channel, count) for channel in channels)
                result.paste((*color, 255), (rectangle[0], y, rectangle[2], y + 1))
            mask.paste(255, rectangle)
            record.update(donors=[list(box) for box in donors], row_radius=radius,
                          rgb_min=list(lower), rgb_max=list(upper))
        reports.append(record)
    return result, mask, reports


def paint_text_layer(size: tuple[int, int], rows: list[dict], *, scale: int = 6,
                     preserve_boxes: list[list[int]] | None = None) -> tuple[Image.Image, list[dict]]:
    """Paint centered glyph crops at high resolution, then downsample once.

    Protection is applied at final resolution. Any cut nonzero alpha is reported
    with requires_review=True, never treated as automatic visual approval.
    """
    width, height = _size(size)
    _items(rows, "rows")
    scale = _integer(scale, "scale", minimum=1, maximum=16)
    working_size = _size((width * scale, height * scale), "working size", limit=_MAX_WORKING_PIXELS)
    protections = [] if preserve_boxes is None else _items(preserve_boxes, "preserve_boxes")
    rectangles = [_bbox(box, size, "preserve bbox") for box in protections]
    layer = Image.new("RGBA", working_size)
    reports = []
    seen = set()
    allowed = {"id", "text", "font_path", "font_size", "center", "color", "angle", "stroke"}
    for index, row in enumerate(rows):
        _fields(row, allowed, allowed - {"angle", "stroke"}, f"rows[{index}]")
        identity = _identity(row["id"], seen, "row")
        text = row["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 10_000 or any(c in text for c in "\r\n\x00"):
            raise ValueError(f"Row {identity}: expected nonempty single-line text")
        if _requires_raqm(text) and not features.check_feature("raqm"):
            raise ValueError(f"Row {identity}: complex-script text requires Pillow RAQM support")
        font_path = _local_path(row["font_path"])
        font_size = _number(row["font_size"], "font_size", minimum=1 / scale,
                            maximum=min(2048, max(size) * 4, 8192 / scale))
        stroke = _number(row.get("stroke", 0), "stroke", minimum=0, maximum=min(2048, font_size))
        angle = _number(row.get("angle", 0), "angle", minimum=-360, maximum=360)
        center = _sequence(row["center"], 2, "center")
        cx = _number(center[0], "center.x", minimum=0, maximum=width)
        cy = _number(center[1], "center.y", minimum=0, maximum=height)
        color = _rgb(row["color"])
        try:
            font = ImageFont.truetype(str(font_path), round(font_size * scale))
            stroke_scaled = round(stroke * scale)
            bounds = font.getbbox(text, stroke_width=stroke_scaled)
        except (OSError, ValueError) as error:
            raise ValueError(f"Row {identity}: cannot load or measure font/text") from error
        padding = 6 * scale
        patch_size = (bounds[2] - bounds[0] + 2 * padding, bounds[3] - bounds[1] + 2 * padding)
        _size(patch_size, "text patch", limit=_MAX_WORKING_PIXELS)
        # Include the working layer and both pre/post-rotation patches in budget.
        radians = math.radians(angle)
        rotated_width = math.ceil(abs(patch_size[0] * math.cos(radians)) + abs(patch_size[1] * math.sin(radians))) + 2
        rotated_height = math.ceil(abs(patch_size[0] * math.sin(radians)) + abs(patch_size[1] * math.cos(radians))) + 2
        if working_size[0] * working_size[1] + patch_size[0] * patch_size[1] + rotated_width * rotated_height > _MAX_WORKING_PIXELS:
            raise ValueError("text working pixels exceed limit")
        patch = Image.new("RGBA", patch_size)
        ImageDraw.Draw(patch).text((padding - bounds[0], padding - bounds[1]), text, font=font,
                                  fill=(*color, 255), stroke_width=stroke_scaled, stroke_fill=(*color, 255))
        ink = patch.getbbox()
        if ink is None:
            raise ValueError(f"Row {identity}: blank glyph bbox")
        patch = patch.crop(ink)
        if angle:
            patch = patch.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
        x = round(cx * scale - patch.width / 2)
        y = round(cy * scale - patch.height / 2)
        if x < 0 or y < 0 or x + patch.width > working_size[0] or y + patch.height > working_size[1]:
            raise ValueError(f"Row {identity}: text overflow outside canvas")
        layer.alpha_composite(patch, (x, y))
        reports.append({"id": identity, "text": text, "font_path": str(font_path), "font_size": font_size,
                        "center": [cx, cy], "color": list(color), "angle": angle, "stroke": stroke,
                        "bbox_px": [round(v / scale, 2) for v in (x, y, patch.width, patch.height)]})
    layer = layer.resize((width, height), Image.Resampling.LANCZOS)
    for box, rectangle in zip(protections, rectangles):
        histogram = layer.getchannel("A").crop(rectangle).histogram()
        cleared = sum(histogram[1:])
        layer.paste((0, 0, 0, 0), rectangle)
        reports.append({"type": "preserve", "bbox": list(box), "cleared_alpha_pixels": cleared,
                        "requires_review": bool(cleared),
                        "warnings": ["Protection cleared text alpha and may damage glyphs; actual visual review is required."] if cleared else []})
    return layer, reports


def compose_assets(size: tuple[int, int], assets: list[dict], *, base_dir: Path) -> tuple[Image.Image, list[dict]]:
    """Composite local assets in order, validating crop before optional resize."""
    size = _size(size)
    _items(assets, "assets")
    try:
        base_dir = Path(base_dir).resolve(strict=True)
    except (TypeError, OSError, ValueError, RuntimeError) as error:
        raise ValueError("base_dir: expected an existing local directory") from error
    if not base_dir.is_dir():
        raise ValueError("base_dir: expected an existing local directory")
    layer = Image.new("RGBA", size)
    reports = []
    seen = set()
    allowed = {"id", "path", "position", "size", "kind", "crop"}
    for index, asset in enumerate(assets):
        _fields(asset, allowed, {"id", "path", "position", "kind"}, f"assets[{index}]")
        identity = _identity(asset["id"], seen, "asset")
        if asset["kind"] not in ("compound", "text", "decoration"):
            raise ValueError(f"Asset {identity}: unknown kind")
        path = _local_path(asset["path"], base_dir=base_dir)
        try:
            with Image.open(path) as opened:
                _single_image(opened, "asset", limit=_MAX_WORKING_PIXELS)
                crop = _bbox(asset["crop"], opened.size, "asset crop") if "crop" in asset else None
                content_size = (crop[2] - crop[0], crop[3] - crop[1]) if crop else opened.size
                target = _size(asset["size"], "asset size", limit=_MAX_WORKING_PIXELS) if "size" in asset else content_size
                x, y = _sequence(asset["position"], 2, "asset position")
                rectangle = _bbox([x, y, *target], size, "asset placement")
                working_pixels = size[0] * size[1] + opened.width * opened.height
                working_pixels += content_size[0] * content_size[1] + target[0] * target[1]
                if working_pixels > _MAX_WORKING_PIXELS:
                    raise ValueError("asset working pixels exceed limit")
                image = opened.crop(crop).convert("RGBA") if crop else opened.convert("RGBA")
        except (OSError, Image.DecompressionBombError) as error:
            raise ValueError(f"Asset {identity}: cannot open local image") from error
        warnings = []
        if image.width * target[1] != image.height * target[0]:
            warnings.append("Non-uniform asset scaling changes the original aspect ratio; visual review is required.")
        if target != image.size:
            image = image.resize(target, Image.Resampling.LANCZOS)
        layer.alpha_composite(image, rectangle[:2])
        reports.append({"id": identity, "path": str(path), "kind": asset["kind"],
                        "position": [x, y], "size": list(target),
                        "crop": list(asset["crop"]) if crop else None,
                        "bbox_px": [x, y, *target], "warnings": warnings})
    return layer, reports
