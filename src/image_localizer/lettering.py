"""Pure-memory lettering with inspectable effects and strict Unicode coverage.

Bounds are [x, y, width, height], including actual nonzero alpha halos.
Callers must check bounds against protected regions before compositing.
Components contain rows in order; final output composites each row's shadow,
contact, depth, face, highlight before the next row. No files are written.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import math
import struct

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont, features

from .layer_ops import (_fields, _identity, _integer, _items, _local_path,
                        _number, _sequence, _size, paint_text_layer)
from .rendering import _requires_raqm

_COMPONENTS = ("shadow", "contact", "depth", "face", "highlight")
_ROW_FIELDS = {"id", "text", "font_path", "font_size", "center", "color", "angle", "stroke", "effects"}
_REQUIRED = _ROW_FIELDS - {"angle", "stroke", "effects"}
_MAX_WORK = 64_000_000  # RGBA-equivalent live pixel budget.
_MAX_FONT_BYTES = 64 * 1024 * 1024


def _rgba(value):
    if isinstance(value, str):
        try:
            return ImageColor.getcolor(value, "RGBA")
        except (ValueError, TypeError) as error:
            raise ValueError("color: expected RGB or RGBA") from error
    if not isinstance(value, (list, tuple)) or len(value) not in (3, 4):
        raise ValueError("color: expected RGB or RGBA")
    channels = tuple(_integer(c, "color channel", maximum=255) for c in value)
    return channels if len(channels) == 4 else (*channels, 255)


def check_font_coverage(font_path, text: str, *, base_dir=None) -> dict:
    """Check cmap glyph IDs, never rendered tofu/bbox heuristics.

    Supports SFNT TTF/OTF and face zero of TTC, Unicode cmap formats 4/12/13.
    Other selected formats and variation sequences explicitly fail unsupported.
    Coverage proves character mapping, not shaping correctness.
    """
    path = _local_path(font_path, base_dir=base_dir)
    if not isinstance(text, str) or len(text) > 10_000:
        raise ValueError("font coverage: expected text of at most 10000 characters")
    wanted = sorted(set(map(ord, text)))
    if any(0xFE00 <= c <= 0xFE0F or 0xE0100 <= c <= 0xE01EF for c in wanted):
        raise ValueError("font coverage unsupported: variation sequences")
    try:
        with path.open("rb") as stream:
            data = stream.read(_MAX_FONT_BYTES + 1)
        if len(data) > _MAX_FONT_BYTES:
            raise ValueError("font coverage unsupported: font byte budget exceeded")

        def unpack(fmt, offset, end=None):
            length = struct.calcsize(fmt)
            if offset < 0 or offset + length > (len(data) if end is None else end):
                raise ValueError("font coverage: malformed font table bounds")
            return struct.unpack_from(fmt, data, offset)

        face = 0
        if data[:4] == b"ttcf":
            version, count = unpack(">II", 4)
            if version not in (0x10000, 0x20000) or not 1 <= count <= 4096:
                raise ValueError("font coverage: malformed TTC header")
            unpack(f">{count}I", 12)
            face = unpack(">I", 12)[0]
        if data[face:face + 4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
            raise ValueError("font coverage unsupported: expected TTF/OTF/TTC SFNT")
        count = unpack(">H", face + 4)[0]
        if not 1 <= count <= 4096:
            raise ValueError("font coverage: malformed SFNT directory")
        tables = {}
        for index in range(count):
            tag, _, offset, length = unpack(">4sIII", face + 12 + index * 16)
            if offset + length > len(data) or tag in tables:
                raise ValueError("font coverage: malformed SFNT table")
            tables[tag] = (offset, offset + length)
        if b"cmap" not in tables or b"maxp" not in tables:
            raise ValueError("font coverage unsupported: missing cmap/maxp")
        maxp, maxp_end = tables[b"maxp"]
        glyph_count = unpack(">H", maxp + 4, maxp_end)[0]
        cmap, cmap_end = tables[b"cmap"]
        version, count = unpack(">HH", cmap, cmap_end)
        if version != 0 or count > 4096:
            raise ValueError("font coverage: malformed cmap")
        candidates = []
        for index in range(count):
            platform, encoding, offset = unpack(">HHI", cmap + 4 + index * 8, cmap_end)
            if platform == 0 or (platform == 3 and encoding in (1, 10)):
                start = cmap + offset
                fmt = unpack(">H", start, cmap_end)[0]
                # FreeType prefers the last full-repertoire Unicode charmap,
                # then the last remaining Unicode charmap. Format 14 describes
                # variation sequences, never a standalone character mapping.
                if fmt == 14:
                    continue
                full = (platform, encoding) in ((3, 10), (0, 4), (0, 6))
                candidates.append(((full, index), start, fmt))
        if not candidates:
            raise ValueError("font coverage unsupported: no Unicode cmap")
        _, start, fmt = max(candidates)
        mapped = {}
        if fmt in (12, 13):
            _, reserved, length, _, groups = unpack(">HHIII", start, cmap_end)
            end = start + length
            if reserved or end > cmap_end or length != 16 + groups * 12:
                raise ValueError("font coverage: malformed cmap groups")
            previous = -1
            for index in range(groups):
                first, last, glyph = unpack(">III", start + 16 + index * 12, end)
                last_glyph = glyph + last - first if fmt == 12 else glyph
                if first <= previous or last < first or last > 0x10FFFF or last_glyph >= glyph_count:
                    raise ValueError("font coverage: malformed cmap group range")
                previous = last
                for code in wanted[bisect_left(wanted, first):bisect_right(wanted, last)]:
                    mapped[code] = glyph + code - first if fmt == 12 else glyph
        elif fmt == 4:
            _, length, _, seg_bytes = unpack(">HHHH", start, cmap_end)
            end = start + length
            segments = seg_bytes // 2
            if seg_bytes % 2 or not segments or end > cmap_end or length < 16 + segments * 8:
                raise ValueError("font coverage: malformed cmap segments")
            ends = start + 14
            starts = ends + 2 * segments + 2
            deltas = starts + 2 * segments
            offsets = deltas + 2 * segments
            previous = -1
            for index in range(segments):
                last = unpack(">H", ends + 2 * index, end)[0]
                first = unpack(">H", starts + 2 * index, end)[0]
                delta = unpack(">h", deltas + 2 * index, end)[0]
                pointer = offsets + 2 * index
                distance = unpack(">H", pointer, end)[0]
                if first > last or first <= previous or distance % 2:
                    raise ValueError("font coverage: malformed cmap segment range")
                previous = last
                if distance and (pointer + distance < offsets + 2 * segments or
                                 pointer + distance + 2 * (last - first + 1) > end):
                    raise ValueError("font coverage: malformed cmap glyph array")
                for code in wanted[bisect_left(wanted, first):bisect_right(wanted, last)]:
                    glyph = unpack(">H", pointer + distance + 2 * (code - first), end)[0] if distance else code
                    glyph = ((glyph + delta) & 0xFFFF) if glyph or not distance else 0
                    if glyph >= glyph_count:
                        raise ValueError("font coverage: malformed cmap glyph ID")
                    mapped[code] = glyph
        else:
            raise ValueError(f"font coverage unsupported: cmap format {fmt}")
    except (OSError, struct.error, OverflowError) as error:
        raise ValueError("font coverage: cannot read valid local font") from error
    missing = sorted(code for code in wanted if not mapped.get(code))
    if missing:
        labels = ", ".join(f"U+{code:04X}" for code in missing[:32])
        raise ValueError(f"font coverage: missing Unicode glyphs: {labels}")
    return {"font_path": str(path), "face_index": 0, "cmap_format": fmt,
            "codepoints": sorted(wanted), "status": "covered"}


def _effects(value):
    _fields(value, set(_COMPONENTS) - {"face"} | {"face_gradient"}, set(), "effects")
    result = {}
    for kind, effect in value.items():
        fields = ({"top", "bottom"} if kind == "face_gradient" else
                  {"offset", "steps", "color"} if kind == "depth" else
                  {"offset", "opacity", "color"} if kind == "highlight" else
                  {"offset", "blur", "opacity", "color"})
        _fields(effect, fields, fields, f"effects.{kind}")
        if kind == "face_gradient":
            result[kind] = {key: _rgba(effect[key]) for key in fields}
            continue
        offset = _sequence(effect["offset"], 2, f"{kind}.offset")
        normalized = {"offset": [_number(v, f"{kind}.offset", minimum=-4096, maximum=4096) for v in offset],
                      "color": _rgba(effect["color"])}
        if "blur" in fields:
            normalized["blur"] = _number(effect["blur"], f"{kind}.blur", minimum=0, maximum=256)
        if "opacity" in fields:
            normalized["opacity"] = _number(effect["opacity"], f"{kind}.opacity", minimum=0, maximum=1)
        if "steps" in fields:
            normalized["steps"] = _integer(effect["steps"], "depth.steps", minimum=1, maximum=128)
        result[kind] = normalized
    return result


def _prepare(size, rows, scale, base_dir):
    prepared, seen, coverage_cache = [], set(), {}
    for index, row in enumerate(rows):
        _fields(row, _ROW_FIELDS, _REQUIRED, f"rows[{index}]")
        identity = _identity(row["id"], seen, "row")
        text = row["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 10_000 or any(c in text for c in "\r\n\x00"):
            raise ValueError(f"Row {identity}: expected nonempty single-line text")
        if _requires_raqm(text) and not features.check_feature("raqm"):
            raise ValueError(f"Row {identity}: complex-script text requires Pillow RAQM support")
        path = _local_path(row["font_path"], base_dir=base_dir)
        font_size = _number(row["font_size"], "font_size", minimum=1 / scale,
                            maximum=min(2048, max(size) * 4, 8192 / scale))
        stroke = _number(row.get("stroke", 0), "stroke", minimum=0, maximum=font_size)
        angle = _number(row.get("angle", 0), "angle", minimum=-360, maximum=360)
        center = _sequence(row["center"], 2, "center")
        center = [_number(v, "center", minimum=0, maximum=bound) for v, bound in zip(center, size)]
        effects = _effects(row["effects"]) if "effects" in row else None
        color = _rgba(row["color"])
        if effects is None and color[3] != 255:
            raise ValueError("legacy color: expected opaque RGB")
        key = (str(path), text)
        if key not in coverage_cache:
            coverage_cache[key] = check_font_coverage(path, text)
        prepared.append({**row, "font_path": str(path), "font_size": font_size, "stroke": stroke,
                         "angle": angle, "center": center, "effects": effects,
                         "coverage": coverage_cache[key], "rgba": color})
    return prepared


def _bounds(image):
    box = image.getchannel("A").getbbox()
    return [box[0], box[1], box[2] - box[0], box[3] - box[1]] if box else None


def _shift(mask, offset, scale):
    result = Image.new("L", mask.size)
    result.paste(mask, tuple(round(v * scale) for v in offset))
    return result


def _tint(mask, color, opacity=1):
    result = Image.new("RGBA", mask.size, color[:3] + (0,))
    alpha = round(color[3] * opacity)
    result.putalpha(mask.point([round(v * alpha / 255) for v in range(256)]))
    return result


def _font_metrics(row, scale):
    try:
        font = ImageFont.truetype(row["font_path"], round(row["font_size"] * scale))
        stroke = round(row["stroke"] * scale)
        bounds = font.getbbox(row["text"], stroke_width=stroke)
    except (OSError, ValueError) as error:
        raise ValueError(f"Row {row['id']}: cannot load or measure font/text") from error
    return font, stroke, bounds


def _legacy_budget(size, rows, scale):
    base = size[0] * size[1] * (10 + scale * scale)
    for row in rows:
        if row["effects"] is not None:
            continue
        _, _, bounds = _font_metrics(row, scale)
        width = bounds[2] - bounds[0] + 12 * scale
        height = bounds[3] - bounds[1] + 12 * scale
        radians = math.radians(row["angle"])
        rotated_width = math.ceil(abs(width * math.cos(radians)) + abs(height * math.sin(radians))) + 2
        rotated_height = math.ceil(abs(width * math.sin(radians)) + abs(height * math.cos(radians))) + 2
        if base + width * height + rotated_width * rotated_height > _MAX_WORK:
            raise ValueError("lettering working pixel budget exceeded")
    if not rows and base > _MAX_WORK:
        raise ValueError("lettering working pixel budget exceeded")


def _glyph(row, scale):
    font, stroke, bounds = _font_metrics(row, scale)
    padding = 6 * scale
    size = (bounds[2] - bounds[0] + 2 * padding, bounds[3] - bounds[1] + 2 * padding)
    _size(size, "glyph patch", limit=_MAX_WORK // 12)
    radians = math.radians(row["angle"])
    rw = math.ceil(abs(size[0] * math.cos(radians)) + abs(size[1] * math.sin(radians))) + 2
    rh = math.ceil(abs(size[0] * math.sin(radians)) + abs(size[1] * math.cos(radians))) + 2
    if rw * rh * 12 > _MAX_WORK:
        raise ValueError("lettering working pixel budget exceeded")
    mask = Image.new("L", size)
    ImageDraw.Draw(mask).text((padding - bounds[0], padding - bounds[1]), row["text"], font=font,
                              fill=255, stroke_width=stroke, stroke_fill=255)
    ink = mask.getbbox()
    if ink is None:
        raise ValueError(f"Row {row['id']}: blank glyph bbox")
    mask = mask.crop(ink)
    if row["angle"]:
        mask = mask.rotate(row["angle"], Image.Resampling.BICUBIC, expand=True)
    return mask


def _effect_tiles(size, row, scale):
    glyph = _glyph(row, scale)
    effects = row["effects"]
    margin = 4 + max((max(map(abs, effect["offset"])) + 4 * effect.get("blur", 0)
                      for kind, effect in effects.items() if kind != "face_gradient"), default=0)
    padding = math.ceil(margin * scale)
    gx = round(row["center"][0] * scale - glyph.width / 2)
    gy = round(row["center"][1] * scale - glyph.height / 2)
    left = (gx - padding) // scale
    top = (gy - padding) // scale
    right = math.ceil((gx + glyph.width + padding) / scale)
    bottom = math.ceil((gy + glyph.height + padding) / scale)
    work_size = ((right - left) * scale, (bottom - top) * scale)
    pixels = work_size[0] * work_size[1]
    if 10 * size[0] * size[1] + 16 * pixels > _MAX_WORK:
        raise ValueError("lettering working pixel budget exceeded")
    if pixels * (1 + effects.get("depth", {}).get("steps", 0)) > 200_000_000:
        raise ValueError("lettering depth work budget exceeded")
    mask = Image.new("L", work_size)
    mask.paste(glyph, (gx - left * scale, gy - top * scale))
    glyph.close()
    tiles = {"glyph-mask": _tint(mask, (255, 255, 255, 255))}
    face = _tint(mask, row["rgba"])
    if "face_gradient" in effects:
        gradient = effects["face_gradient"]
        box = mask.getbbox()
        draw = ImageDraw.Draw(face)
        for y in range(box[1], box[3]):
            fraction = (y - box[1]) / max(1, box[3] - box[1] - 1)
            color = tuple(round(a + (b - a) * fraction) for a, b in zip(gradient["top"], gradient["bottom"]))
            draw.line((0, y, face.width, y), fill=color)
        face.putalpha(ImageChops.multiply(face.getchannel("A"), mask))
    tiles["face"] = face
    for kind in _COMPONENTS:
        if kind not in effects:
            continue
        effect = effects[kind]
        if kind == "depth":
            effect_mask = Image.new("L", work_size)
            for step in range(1, effect["steps"] + 1):
                shifted = _shift(mask, [v * step / effect["steps"] for v in effect["offset"]], scale)
                effect_mask = ImageChops.lighter(effect_mask, shifted)
        else:
            effect_mask = _shift(mask, effect["offset"], scale)
            if kind == "highlight":
                effect_mask = ImageChops.subtract(mask, effect_mask)
            elif effect["blur"]:
                effect_mask = effect_mask.filter(ImageFilter.GaussianBlur(effect["blur"] * scale))
        tiles[kind] = _tint(effect_mask, effect["color"], effect.get("opacity", 1))
    final_tiles = {}
    for kind, tile in tiles.items():
        final = tile.resize((right - left, bottom - top), Image.Resampling.LANCZOS) if scale != 1 else tile
        box = final.getchannel("A").getbbox()
        if box and (box[0] == 0 or box[1] == 0 or box[2] == final.width or box[3] == final.height):
            raise ValueError(f"Row {row['id']}: {kind} halo exceeds safe tile padding")
        if box and (left + box[0] < 0 or top + box[1] < 0 or
                    left + box[2] > size[0] or top + box[3] > size[1]):
            raise ValueError(f"Row {row['id']}: {kind} canvas clip (actual alpha halo outside canvas)")
        final_tiles[kind] = final
    return final_tiles, (left, top)


def render_lettering(size: tuple[int, int], rows: list[dict], *, scale: int = 6,
                     base_dir=None) -> tuple[Image.Image, list[dict], dict[str, Image.Image]]:
    """Return (RGBA canvas, row reports, same-size RGBA component canvases).

    Required row keys: id/text/font_path/font_size/center/color. Optional:
    angle=0, stroke=0, effects. Effects require all their named fields:
    face_gradient={top,bottom}; shadow/contact={offset,blur,opacity,color};
    depth={offset,steps,color}; highlight={offset,opacity,color}. Colors accept
    Pillow strings or integer RGB/RGBA arrays (legacy rows remain opaque RGB).
    Gradient overrides face color. Highlight is the inner rim mask-shift(mask).
    Offsets are canvas-axis vectors, including for rotated text. Opacity is 0..1.
    Omitted effects uses the old renderer; effects={} selects the new pipeline.
    Components include white glyph-mask (coverage independent of face alpha).
    """
    size = _size(size)
    scale = _integer(scale, "scale", minimum=1, maximum=16)
    _items(rows, "rows")
    if len(rows) > 256 or max(size) > 32768 or 10 * size[0] * size[1] > _MAX_WORK:
        raise ValueError("lettering canvas/row budget exceeded")
    prepared = _prepare(size, rows, scale, base_dir)
    _legacy_budget(size, prepared, scale)
    components = {kind: Image.new("RGBA", size) for kind in (*_COMPONENTS, "glyph-mask")}
    if all(row["effects"] is None for row in prepared):
        if 10 * size[0] * size[1] + size[0] * size[1] * scale * scale > _MAX_WORK:
            raise ValueError("lettering working pixel budget exceeded")
        legacy = [{key: value for key, value in row.items() if key in _ROW_FIELDS - {"effects"}} for row in prepared]
        result, reports = paint_text_layer(size, legacy, scale=scale)
        components["face"] = result.copy()
        components["glyph-mask"] = _tint(result.getchannel("A"), (255, 255, 255, 255))
        for row, report in zip(prepared, reports):
            single, _ = paint_text_layer(size, [{key: value for key, value in row.items()
                                               if key in _ROW_FIELDS - {"effects"}}], scale=scale)
            component_bounds = dict.fromkeys((*_COMPONENTS, "glyph-mask"))
            component_bounds["face"] = _bounds(single)
            component_bounds["glyph-mask"] = _bounds(single)
            report.update(alpha_bbox_px=_bounds(single), component_bbox_px=component_bounds,
                          font_coverage=row["coverage"], effects={})
            single.close()
        return result, reports, components
    result = Image.new("RGBA", size)
    reports = []
    for row in prepared:
        if row["effects"] is None:
            if 10 * size[0] * size[1] + size[0] * size[1] * scale * scale > _MAX_WORK:
                raise ValueError("lettering working pixel budget exceeded")
            legacy = {key: value for key, value in row.items() if key in _ROW_FIELDS - {"effects"}}
            face, records = paint_text_layer(size, [legacy], scale=scale)
            tiles, position = {"face": face, "glyph-mask": _tint(face.getchannel("A"), (255, 255, 255, 255))}, (0, 0)
            report = records[0]
        else:
            tiles, position = _effect_tiles(size, row, scale)
            report = {key: row[key] for key in ("id", "text", "font_path", "font_size", "center", "angle", "stroke")}
            report["color"] = list(row["rgba"])
        component_bounds = dict.fromkeys((*_COMPONENTS, "glyph-mask"))
        row_layer = Image.new("RGBA", size)
        for kind in (*_COMPONENTS, "glyph-mask"):
            if kind not in tiles:
                continue
            tile = tiles[kind]
            box = _bounds(tile)
            component_bounds[kind] = [box[0] + position[0], box[1] + position[1], *box[2:]] if box else None
            components[kind].alpha_composite(tile, position)
            if kind != "glyph-mask":
                row_layer.alpha_composite(tile, position)
        result.alpha_composite(row_layer)
        report.update(bbox_px=_bounds(row_layer), alpha_bbox_px=_bounds(row_layer),
                      component_bbox_px=component_bounds, font_coverage=row["coverage"],
                      effects=row["effects"] or {})
        reports.append(report)
        row_layer.close()
        for tile in tiles.values():
            tile.close()
    return result, reports, components
