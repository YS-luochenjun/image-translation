"""Risk-oriented tests for memory-only lettering; no downloaded font fixtures."""
from copy import deepcopy
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageFont

from image_localizer.cli import available_fonts
from image_localizer.layer_ops import paint_text_layer
from image_localizer.lettering import check_font_coverage, render_lettering


def latin_font():
    for candidate in available_fonts():
        if Path(candidate).name.lower() in ("arial.ttf", "dejavusans.ttf"):
            return candidate
    for candidate in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return str(Path(ImageFont.truetype(candidate, 12).path).resolve())
        except OSError:
            continue
    raise unittest.SkipTest("No doctor-compatible local Latin font")


def row(**changes):
    return {"id": "title", "text": "Hello", "font_path": latin_font(), "font_size": 32,
            "center": [130, 80], "color": "#ce3500", "angle": 0, "stroke": .25,
            "effects": {}, **changes}


def shadow(**changes):
    return {"offset": [8, 6], "blur": 4, "opacity": .7, "color": "black", **changes}


def rect(image):
    return image.getchannel("A").getbbox()


def synthetic_font(subtable, *, platform=3, encoding=10, ttc=False):
    cmap = struct.pack(">HHHHI", 0, 1, platform, encoding, 12) + subtable
    maxp = struct.pack(">IH", 0x10000, 200)
    base = 16 if ttc else 0
    cmap_offset = base + 44
    header = b"\x00\x01\x00\x00" + struct.pack(">HHHH", 2, 0, 0, 0)
    directory = struct.pack(">4sIII", b"cmap", 0, cmap_offset, len(cmap))
    directory += struct.pack(">4sIII", b"maxp", 0, cmap_offset + len(cmap), len(maxp))
    prefix = b"ttcf" + struct.pack(">III", 0x10000, 1, 16) if ttc else b""
    return prefix + header + directory + cmap + maxp


class LetteringTests(unittest.TestCase):
    def test_no_effects_is_byte_exact_legacy_for_multiple_overlapping_rows(self):
        rows = [row(angle=13), row(id="second", center=[150, 85], color="blue", angle=-7)]
        for item in rows:
            del item["effects"]
        expected, old_report = paint_text_layer((260, 160), rows)
        output, reports, components = render_lettering((260, 160), rows)
        self.assertEqual(output.tobytes(), expected.tobytes())
        self.assertEqual(components["face"].tobytes(), expected.tobytes())
        self.assertEqual(reports[0]["bbox_px"], old_report[0]["bbox_px"])
        self.assertEqual(reports[0]["font_coverage"]["status"], "covered")

    def test_shadow_and_depth_expand_beyond_glyph_and_reports_are_actual(self):
        effects = {"shadow": shadow(), "contact": shadow(offset=[1, 2], blur=1),
                   "depth": {"offset": [7, 4], "steps": 8, "color": "#601400"},
                   "highlight": {"offset": [1, 1], "opacity": .6, "color": "white"}}
        output, reports, components = render_lettering((260, 160), [row(effects=effects)])
        glyph, halo = rect(components["glyph-mask"]), rect(components["shadow"])
        self.assertGreater(halo[2], glyph[2])
        self.assertGreater(halo[3], glyph[3])
        self.assertGreater(rect(components["depth"])[2], glyph[2])
        self.assertIsNotNone(rect(components["highlight"]))
        actual = rect(output)
        self.assertEqual(reports[0]["alpha_bbox_px"], [actual[0], actual[1], actual[2]-actual[0], actual[3]-actual[1]])
        for name, component in components.items():
            self.assertEqual((component.mode, component.size), ("RGBA", (260, 160)))
            box = rect(component)
            self.assertEqual(reports[0]["component_bbox_px"][name],
                             [box[0], box[1], box[2]-box[0], box[3]-box[1]])
        rebuilt = Image.new("RGBA", output.size)
        for name in ("shadow", "contact", "depth", "face", "highlight"):
            rebuilt.alpha_composite(components[name])
        self.assertEqual(rebuilt.tobytes(), output.tobytes())

    def test_blur_has_fading_halo_and_partial_alpha(self):
        _, _, components = render_lettering((260, 160), [row(text="H", stroke=0,
            effects={"shadow": shadow(offset=[0, 0], blur=5, opacity=1)})])
        glyph = rect(components["glyph-mask"])
        alpha = components["shadow"].getchannel("A")
        self.assertLess(rect(components["shadow"])[0], glyph[0])
        y = (glyph[1] + glyph[3]) // 2
        samples = [alpha.getpixel((glyph[0] - distance, y)) for distance in (1, 4, 9, 16)]
        self.assertGreater(samples[0], samples[1])
        self.assertGreater(samples[1], samples[2])
        self.assertGreater(samples[2], samples[3])
        self.assertEqual(samples[3], 0)

    def test_transparent_face_does_not_destroy_shadow_or_coverage_mask(self):
        output, reports, parts = render_lettering((260, 160), [row(color=[1, 2, 3, 0],
            effects={"shadow": shadow()})])
        self.assertIsNone(rect(parts["face"]))
        self.assertIsNotNone(rect(parts["glyph-mask"]))
        self.assertEqual(output.tobytes(), parts["shadow"].tobytes())
        self.assertIsNone(reports[0]["component_bbox_px"]["face"])

    def test_gradient_interpolates_rgba_only_within_glyphs(self):
        _, _, parts = render_lettering((260, 160), [row(text="H", stroke=0,
            effects={"face_gradient": {"top": [255, 0, 0, 255], "bottom": [0, 0, 255, 70]}})])
        face = parts["face"]
        glyph = rect(parts["glyph-mask"])
        x = glyph[0] + 5
        top = face.getpixel((x, glyph[1] + 5))
        bottom = face.getpixel((x, glyph[3] - 6))
        self.assertGreater(top[0], bottom[0])
        self.assertLess(top[2], bottom[2])
        self.assertGreater(top[3], bottom[3])
        self.assertEqual(face.getpixel((0, 0))[3], 0)

    def test_rotation_retains_edges_and_offsets_are_not_scaled_twice(self):
        for scale in (1, 3, 6):
            _, _, parts = render_lettering((260, 160), [row(angle=37, effects={
                "shadow": shadow(offset=[13, -9], blur=0, opacity=1)})], scale=scale)
            face, shifted = rect(parts["face"]), rect(parts["shadow"])
            self.assertEqual(tuple(b - a for a, b in zip(face, shifted)), (13, -9, 13, -9))
            self.assertLessEqual(abs((face[0] + face[2])/2 - 130), 2)
            self.assertLessEqual(abs((face[1] + face[3])/2 - 80), 4)
        flat = render_lettering((260, 160), [row(angle=0)])[2]["face"]
        rotated = render_lettering((260, 160), [row(angle=37)])[2]["face"]
        self.assertGreater(rect(rotated)[3]-rect(rotated)[1], rect(flat)[3]-rect(flat)[1])

    def test_shadow_clip_rejected_even_when_glyph_fits(self):
        render_lettering((260, 160), [row(center=[130, 30])])
        with self.assertRaisesRegex(ValueError, "shadow canvas clip"):
            render_lettering((260, 160), [row(center=[130, 30], effects={
                "shadow": shadow(offset=[0, -25], blur=6)})])
        with self.assertRaisesRegex(ValueError, "canvas clip"):
            render_lettering((260, 160), [row(center=[15, 20], angle=40)])

    def test_rotated_edges_match_larger_canvas_without_cropping(self):
        item = row(center=[90.5, 65.25], angle=-41, effects={"shadow": shadow(offset=[-9.5, 8.25])})
        small, _, small_parts = render_lettering((210, 160), [item])
        large_item = {**item, "center": [v + 40 for v in item["center"]]}
        large, _, large_parts = render_lettering((290, 240), [large_item])
        self.assertEqual(small.tobytes(), large.crop((40, 40, 250, 200)).tobytes())
        for name in small_parts:
            self.assertEqual(small_parts[name].tobytes(), large_parts[name].crop((40, 40, 250, 200)).tobytes())

    def test_shadow_clip_is_rejected_on_all_four_sides(self):
        for offset in ([100, 0], [-100, 0], [0, 65], [0, -65]):
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "shadow canvas clip"):
                render_lettering((260, 160), [row(effects={"shadow": shadow(offset=offset, blur=5)})])

    def test_multiple_effect_rows_keep_row_compositing_order(self):
        first = row(effects={"shadow": shadow(offset=[0, 0])})
        second = row(id="second", color=[0, 0, 255, 128], center=[135, 82],
                     effects={"contact": shadow(offset=[0, 0], blur=2)})
        first_layer = render_lettering((260, 160), [first])[0]
        second_layer = render_lettering((260, 160), [second])[0]
        output = render_lettering((260, 160), [first, second])[0]
        self.assertEqual(output.tobytes(), Image.alpha_composite(first_layer, second_layer).tobytes())

    def test_transparent_gradient_remains_invisible_but_keeps_glyph_mask(self):
        output, _, parts = render_lettering((260, 160), [row(effects={
            "face_gradient": {"top": "#ff000000", "bottom": "#0000ff00"}})])
        self.assertIsNone(rect(output))
        self.assertIsNone(rect(parts["face"]))
        self.assertIsNotNone(rect(parts["glyph-mask"]))

    def test_effect_input_is_not_mutated_and_relative_font_path_resolves(self):
        item = row(effects={"shadow": shadow()})
        original = deepcopy(item)
        path = Path(item["font_path"])
        item["font_path"] = path.name
        render_lettering((260, 160), [item], base_dir=path.parent)
        item["font_path"] = original["font_path"]
        self.assertEqual(item, original)

    def test_missing_chinese_glyph_rejected_with_and_without_effects(self):
        for effects in ({}, None):
            item = row(text="中文")
            if effects is None:
                del item["effects"]
            with self.assertRaisesRegex(ValueError, "missing Unicode glyphs.*U\\+4E2D"):
                render_lettering((260, 160), [item])

    def test_strict_unknown_keys_types_nonfinite_and_effect_ranges(self):
        invalid = [row(unused=True), row(effects={"unused": {}}),
                   row(effects={"shadow": shadow(unused=True)}),
                   row(effects={"face_gradient": {"top": "white"}}), row(effects=None),
                   row(font_size=True), row(angle=float("nan")), row(stroke=float("inf")),
                   row(center=[True, 80]), row(color=[1, False, 3]), row(text="a\nb")]
        for key, values in (("blur", [True, float("nan"), -1, 257]),
                            ("opacity", [False, float("inf"), -1, 1.1]),
                            ("offset", [[0, True], [float("inf"), 0], [1e100, 0]])):
            invalid.extend(row(effects={"shadow": shadow(**{key: value})}) for value in values)
        for value in (True, 1.5, 0, 129):
            invalid.append(row(effects={"depth": {"offset": [1, 1], "steps": value, "color": "black"}}))
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                render_lettering((260, 160), [item])
        for size, scale in (((260.0, 160), 6), ((True, 160), 6), ((260, 160), True), ((260, 160), 1.5)):
            with self.subTest(size=size, scale=scale), self.assertRaises(ValueError):
                render_lettering(size, [], scale=scale)
        with self.assertRaisesRegex(ValueError, "unique"):
            render_lettering((260, 160), [row(), row()])

    def test_huge_offset_rejected_before_any_image_allocation(self):
        with patch("image_localizer.lettering.Image.new", side_effect=AssertionError("allocation")):
            with self.assertRaisesRegex(ValueError, "offset"):
                render_lettering((260, 160), [row(effects={"shadow": shadow(offset=[10**1000, 0])})])
        with self.assertRaisesRegex(ValueError, "budget"):
            render_lettering((260, 160), [row(effects={"shadow": shadow(offset=[4096, 4096])})])
        with self.assertRaises(ValueError):
            render_lettering((10000, 10000), [])

    def test_empty_rows_returns_transparent_components(self):
        output, reports, parts = render_lettering((30, 20), [])
        self.assertEqual(reports, [])
        self.assertIsNone(rect(output))
        self.assertEqual(set(parts), {"face", "shadow", "contact", "depth", "highlight", "glyph-mask"})
        self.assertTrue(all(rect(value) is None for value in parts.values()))


class CoverageParserTests(unittest.TestCase):
    def check(self, data, text="A"):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.ttf"
            path.write_bytes(data)
            return check_font_coverage(path, text)

    def test_format12_supplementary_unicode_and_ttc_face_zero(self):
        subtable = struct.pack(">HHIII", 12, 0, 40, 0, 2)
        subtable += struct.pack(">III", 65, 65, 1) + struct.pack(">III", 0x20000, 0x20000, 2)
        for ttc in (False, True):
            report = self.check(synthetic_font(subtable, ttc=ttc), "A\U00020000")
            self.assertEqual(report["cmap_format"], 12)
            self.assertEqual(report["face_index"], 0)
        with self.assertRaisesRegex(ValueError, "missing"):
            self.check(synthetic_font(subtable), "B")

    def test_format13_many_to_one_and_glyph_zero_is_missing(self):
        subtable = struct.pack(">HHIII", 13, 0, 28, 0, 1) + struct.pack(">III", 65, 70, 1)
        self.assertEqual(self.check(synthetic_font(subtable), "ABC")["cmap_format"], 13)
        zero = struct.pack(">HHIII", 12, 0, 28, 0, 1) + struct.pack(">III", 65, 65, 0)
        with self.assertRaisesRegex(ValueError, "missing"):
            self.check(synthetic_font(zero))

    def test_format4_delta_and_indirect_glyph_array(self):
        # Two segments, A/B and terminal FFFF; first maps via glyph IDs.
        subtable = struct.pack(">7H", 4, 36, 0, 4, 4, 1, 0)
        subtable += struct.pack(">2H", 66, 65535) + struct.pack(">H", 0)
        subtable += struct.pack(">2H", 65, 65535) + struct.pack(">2h", 1, 1)
        subtable += struct.pack(">2H", 4, 0) + struct.pack(">2H", 1, 2)
        self.assertEqual(self.check(synthetic_font(subtable, encoding=1), "AB")["cmap_format"], 4)
        with self.assertRaisesRegex(ValueError, "missing"):
            self.check(synthetic_font(subtable, encoding=1), "C")

    def test_unsupported_cmap_variation_and_malformed_tables_are_not_passed(self):
        unsupported = struct.pack(">HHH", 6, 6, 0)
        with self.assertRaisesRegex(ValueError, "unsupported.*format 6"):
            self.check(synthetic_font(unsupported))
        with self.assertRaisesRegex(ValueError, "unsupported.*variation"):
            check_font_coverage(latin_font(), "A\ufe0f")
        malformed = struct.pack(">HHIII", 12, 0, 28, 0, 1) + struct.pack(">III", 80, 65, 1)
        for data in (b"wOFF" + b"0"*50, b"", synthetic_font(malformed), synthetic_font(unsupported)[:-3]):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.check(data)


if __name__ == "__main__":
    unittest.main()
