"""Render validated image-localization manifests using Pillow only."""

from __future__ import annotations

import io
import json
import re
import unicodedata
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont, features


_CLOSING_PUNCTUATION = frozenset("，。！？、；：）》】」』〕〉〗〙〛’”％‰…,.!?;:%)]}")
_COMPLEX_RANGES = (
    (0x0590, 0x08FF),  # Hebrew, Arabic, Syriac and related scripts.
    (0x0900, 0x0DFF),  # Indic scripts.
    (0x0E00, 0x0FFF),  # Thai, Lao and Tibetan.
    (0x1000, 0x109F),  # Myanmar.
    (0x1700, 0x18AF),  # Philippine scripts, Khmer and Mongolian.
    (0x1900, 0x1CFF),  # Limbu, Tai scripts, Balinese and related scripts.
    (0xA800, 0xA8FF),  # Syloti Nagri, Phags-pa and Saurashtra.
    (0xA980, 0xA9DF),  # Javanese and Myanmar Extended-B.
    (0xAA00, 0xAA7F),  # Cham and Myanmar Extended-A.
    (0xAAE0, 0xAAFF),  # Meetei Mayek extensions.
    (0xABC0, 0xABFF),  # Meetei Mayek.
    (0x11000, 0x11FFF),  # Supplementary Brahmic scripts.
    (0xFB1D, 0xFDFF),  # Hebrew/Arabic presentation forms.
    (0xFE70, 0xFEFF),
)


def _requires_raqm(text: str) -> bool:
    return any(
        start <= ord(character) <= end
        for character in text
        for start, end in _COMPLEX_RANGES
    )


def _is_cjk(character: str) -> bool:
    code = ord(character)
    return (
        0x2E80 <= code <= 0xA4CF
        or 0xAC00 <= code <= 0xD7AF
        or 0xF900 <= code <= 0xFAFF
        or 0xFE30 <= code <= 0xFE4F
        or 0xFF00 <= code <= 0xFFEF
        or 0x20000 <= code <= 0x323AF
    )


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    current = ""
    for character in text:
        if _is_cjk(character) or character in _CLOSING_PUNCTUATION:
            if current:
                tokens.extend(re.findall(r"\s+|\S+", current))
                current = ""
            tokens.append(character)
        else:
            current += character
    if current:
        tokens.extend(re.findall(r"\s+|\S+", current))
    return tokens


def _bounds(font: ImageFont.FreeTypeFont, text: str, stroke: int) -> tuple[int, int, int, int]:
    return font.getbbox(text, stroke_width=stroke)


def _width(font: ImageFont.FreeTypeFont, text: str, stroke: int) -> int:
    left, _, right, _ = _bounds(font, text, stroke)
    return right - left


def _clusters(text: str) -> list[str]:
    """Keep combining marks and variation selectors with their base character."""
    clusters: list[str] = []
    for character in text:
        if clusters and (unicodedata.combining(character) or character in "\ufe0e\ufe0f"):
            clusters[-1] += character
        else:
            clusters.append(character)
    return clusters


def _wrap_paragraph(text: str, font: ImageFont.FreeTypeFont, width: int, stroke: int) -> list[str] | None:
    lines: list[str] = []
    current = ""
    for token in _tokens(text):
        if token.isspace():
            if current:
                current += token
            continue
        candidate = current + token
        if _width(font, candidate, stroke) <= width:
            current = candidate
            continue
        current = current.rstrip()
        if token[0] in _CLOSING_PUNCTUATION and current:
            # Move a preceding glyph together with punctuation onto the next line.
            prefix = _clusters(current)
            tail = prefix.pop()
            while prefix and tail[0] in _CLOSING_PUNCTUATION:
                tail = prefix.pop() + tail
            if not prefix or _width(font, tail + token, stroke) > width:
                return None
            lines.append("".join(prefix).rstrip())
            current = tail + token
            continue
        if current:
            lines.append(current)
            current = ""
        if _width(font, token, stroke) <= width:
            current = token
            continue
        for cluster in _clusters(token):
            if _width(font, cluster, stroke) > width:
                return None
            if current and _width(font, current + cluster, stroke) > width:
                lines.append(current)
                current = ""
            current += cluster
    lines.append(current.rstrip())
    return lines


def _layout_region(region: dict) -> tuple[dict, Image.Image]:
    style = region.get("style", {})
    font_path = Path(style.get("font_path", ""))
    if not font_path.is_file():
        raise ValueError(f"Region {region['id']}: missing font: {font_path}")
    text = region.get("target_text", "").replace("\r\n", "\n").replace("\r", "\n")
    if _requires_raqm(text) and not features.check_feature("raqm"):
        raise ValueError(f"Region {region['id']}: complex-script text requires Pillow RAQM support")
    _, _, box_width, box_height = region["bbox"]
    padding = style.get("padding", 4)
    available_width = box_width - 2 * padding
    available_height = box_height - 2 * padding
    stroke = style.get("stroke_width", 0)
    spacing = style.get("line_spacing", 4)
    minimum = style.get("min_font_size", 14)
    maximum = style.get("max_font_size", 64)
    if available_width <= 0 or available_height <= 0:
        raise ValueError(f"Region {region['id']}: text overflow: padding leaves no drawing area")
    for fontsize in range(maximum, minimum - 1, -1):
        try:
            font = ImageFont.truetype(str(font_path), fontsize)
        except OSError as error:
            raise ValueError(f"Region {region['id']}: cannot load font: {font_path}") from error
        lines: list[str] = []
        for paragraph in text.split("\n"):
            wrapped = _wrap_paragraph(paragraph, font, available_width, stroke)
            if wrapped is None:
                break
            lines.extend(wrapped)
        else:
            bounds = [_bounds(font, line, stroke) for line in lines]
            reference = _bounds(font, "Ag", stroke)
            baseline_height = max(1, reference[3] - reference[1])
            heights = [max(baseline_height, box[3] - box[1]) for box in bounds]
            total_height = sum(heights) + spacing * (len(lines) - 1)
            if total_height > available_height:
                continue
            patch = Image.new("RGBA", (box_width, box_height))
            draw = ImageDraw.Draw(patch)
            y = padding + (available_height - total_height) // 2
            for line, box, height in zip(lines, bounds, heights):
                line_width = box[2] - box[0]
                align = style.get("align", "center")
                offset = {"left": 0, "center": (available_width - line_width) // 2,
                          "right": available_width - line_width}[align]
                draw.text(
                    (padding + offset - box[0], y - box[1]), line, font=font,
                    fill=style.get("color", "#000000"), stroke_width=stroke,
                    stroke_fill=style.get("stroke_fill", "#000000"),
                )
                y += height + spacing
            report = {
                "id": region["id"], "action": "translate", "fontsize": fontsize,
                "lines": lines, "bbox": list(region["bbox"]), "font_path": str(font_path),
            }
            return report, patch
    raise ValueError(f"Region {region['id']}: text overflow at minimum font size {minimum}")


def _rectangle(bbox: list[int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    x, y, width, height = bbox
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > size[0] or y + height > size[1]:
        raise ValueError(f"Region bbox outside canvas: {bbox}")
    return x, y, x + width, y + height


def _intersects(first: tuple, second: tuple) -> bool:
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _comparison(source: Image.Image, translated: Image.Image) -> Image.Image:
    comparison = Image.new("RGB", (source.width * 2, source.height), "white")
    comparison.paste(source, (0, 0), source.getchannel("A"))
    comparison.paste(translated, (source.width, 0), translated.getchannel("A"))
    comparison.thumbnail((1800, max(1, source.height)), Image.Resampling.LANCZOS)
    return comparison


def render_image(
    source_path: Path,
    manifest: dict,
    output_dir: Path,
    clean_background: Path | None = None,
) -> dict:
    """Render all translations and return paths to the four generated artifacts.

    The caller supplies a validated manifest and a nonexistent output directory.
    All layout and rendering checks finish before the directory is created.
    """
    source_path = Path(source_path)
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise ValueError(f"Output directory already exists: {output_dir}")
    filenames = {"translated": "translated.png", "text_layer": "text-layer.png",
                 "comparison": "comparison.png", "layout": "layout.json"}
    paths = {key: output_dir / name for key, name in filenames.items()}
    inputs = {source_path.resolve()}
    if clean_background is not None:
        clean_background = Path(clean_background)
        inputs.add(clean_background.resolve())
    if any(path.resolve() in inputs for path in paths.values()):
        raise ValueError("Output must not overwrite source or clean background")
    with Image.open(source_path) as opened:
        source = opened.convert("RGBA")
    canvas = manifest["canvas"]
    expected_size = (canvas["width"], canvas["height"])
    if source.size != expected_size:
        raise ValueError(f"Source image dimensions {source.size} do not match canvas {expected_size}")
    clean = None
    if clean_background is not None:
        with Image.open(clean_background) as opened:
            clean = opened.convert("RGBA")
        if clean.size != source.size:
            raise ValueError("Clean background dimensions do not match source image")
    protected = [_rectangle(item["bbox"], source.size) for item in manifest.get("protected_regions", [])]
    protected.extend(
        _rectangle(region["bbox"], source.size)
        for region in manifest["regions"] if region["action"] == "preserve"
    )
    prepared = []
    reports = []
    for region in manifest["regions"]:
        rectangle = _rectangle(region["bbox"], source.size)
        if region["action"] == "preserve":
            reports.append({"id": region["id"], "action": "preserve", "bbox": list(region["bbox"]),
                            "fontsize": None, "lines": [], "font_path": None})
            continue
        if any(_intersects(rectangle, item) for item in protected):
            raise ValueError(f"Region {region['id']}: translated bbox intersects a protected region")
        repair = region["repair"]
        if repair["mode"] == "clean_plate":
            if clean is None:
                raise ValueError(f"Region {region['id']}: clean_plate requires clean_background")
            background = clean.crop(rectangle)
        elif repair["mode"] == "solid":
            if not repair.get("color"):
                raise ValueError(f"Region {region['id']}: solid repair requires an explicit color")
            background = Image.new("RGBA", (rectangle[2] - rectangle[0], rectangle[3] - rectangle[1]),
                                   ImageColor.getcolor(repair["color"], "RGBA"))
        else:
            raise ValueError(f"Unknown repair mode: {repair['mode']}")
        report, text_patch = _layout_region(region)
        reports.append(report)
        prepared.append((rectangle, background, text_patch))
    translated = source.copy()
    text_layer = Image.new("RGBA", source.size)
    for rectangle, background, text_patch in prepared:
        position = rectangle[:2]
        translated.paste(Image.alpha_composite(background, text_patch), position)
        text_layer.alpha_composite(text_patch, position)
    layout = {"canvas": dict(canvas), "regions": reports}
    contents = {
        "translated": _png_bytes(translated), "text_layer": _png_bytes(text_layer),
        "comparison": _png_bytes(_comparison(source, translated)),
        "layout": json.dumps(layout, ensure_ascii=False, indent=2).encode("utf-8"),
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    for key, content in contents.items():
        paths[key].write_bytes(content)
    return {key: str(path) for key, path in paths.items()}
