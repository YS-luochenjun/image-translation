"""Behavior tests for image localization rendering."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from image_localizer.rendering import render_image


FONT = Path("C:/Windows/Fonts/arial.ttf")
CJK_FONT = Path("C:/Windows/Fonts/msyh.ttc")


@unittest.skipUnless(FONT.is_file(), "Windows Arial font is required")
class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.png"
        Image.new("RGB", (320, 220), "#123456").save(self.source)
        self.output = self.root / "output"
        self.manifest = {
            "canvas": {"width": 320, "height": 220},
            "regions": [{
                "id": "title", "action": "translate", "source_text": "original",
                "target_text": "Hello world", "bbox": [30, 40, 250, 120],
                "style": {"font_path": str(FONT), "min_font_size": 14,
                          "max_font_size": 40, "color": "#000000", "align": "center",
                          "padding": 4, "line_spacing": 4, "stroke_width": 0,
                          "stroke_fill": "#ffffff"},
                "repair": {"mode": "solid", "color": "#ffffff"},
            }],
            "protected_regions": [],
        }

    def render(self, **kwargs):
        return render_image(self.source, self.manifest, self.output, **kwargs)

    def report(self, result):
        return json.loads(Path(result["layout"]).read_text(encoding="utf-8"))

    def assert_fails_without_output(self, message):
        with self.assertRaisesRegex(ValueError, message):
            self.render()
        self.assertFalse(self.output.exists())

    def test_outputs_and_layout_are_complete_and_manifest_is_unchanged(self):
        original = copy.deepcopy(self.manifest)
        result = self.render()
        self.assertEqual(set(path.name for path in self.output.iterdir()),
                         {"translated.png", "text-layer.png", "comparison.png", "layout.json"})
        report = self.report(result)["regions"][0]
        self.assertEqual(report["bbox"], [30, 40, 250, 120])
        self.assertEqual(report["fontsize"], 40)
        self.assertEqual(report["font_path"], str(FONT))
        self.assertEqual(self.manifest, original)
        with Image.open(result["comparison"]) as comparison:
            self.assertEqual(comparison.size, (640, 220))

    def test_solid_and_text_only_change_pixels_inside_bbox(self):
        result = self.render()
        with Image.open(self.source) as original, Image.open(result["translated"]) as translated:
            difference = ImageChops.difference(original.convert("RGB"), translated.convert("RGB"))
            self.assertEqual(difference.getbbox(), (30, 40, 280, 160))
        with Image.open(result["text_layer"]) as layer:
            self.assertEqual(layer.mode, "RGBA")
            self.assertEqual(layer.getpixel((0, 0))[3], 0)
            left, top, right, bottom = layer.getchannel("A").getbbox()
            self.assertGreaterEqual(left, 34)
            self.assertGreaterEqual(top, 44)
            self.assertLessEqual(right, 276)
            self.assertLessEqual(bottom, 156)

    def test_clean_plate_pastes_only_matching_rectangle(self):
        clean_path = self.root / "clean.png"
        clean = Image.new("RGB", (320, 220), "#fa0203")
        clean.putpixel((30, 40), (7, 8, 9))
        clean.save(clean_path)
        self.manifest["regions"][0]["repair"] = {"mode": "clean_plate"}
        result = self.render(clean_background=clean_path)
        with Image.open(result["translated"]) as rendered:
            self.assertEqual(rendered.getpixel((0, 0)), (18, 52, 86, 255))
            self.assertEqual(rendered.getpixel((300, 200)), (18, 52, 86, 255))
            self.assertEqual(rendered.getpixel((30, 40)), (7, 8, 9, 255))
            self.assertEqual(rendered.getpixel((31, 40)), (250, 2, 3, 255))
        with Image.open(clean_path) as unchanged:
            self.assertEqual(unchanged.getpixel((0, 0)), (250, 2, 3))

    def test_long_english_text_wraps_on_words_and_reduces_font(self):
        self.manifest["regions"][0]["target_text"] = "A fairly long sentence containing several English words " * 3
        result = self.render()
        report = self.report(result)["regions"][0]
        self.assertLess(report["fontsize"], 40)
        self.assertGreater(len(report["lines"]), 1)
        self.assertEqual(" ".join(report["lines"]),
                         self.manifest["regions"][0]["target_text"].rstrip())

    def test_long_unbroken_word_is_split_without_losing_characters(self):
        text = "Supercalifragilisticexpialidocious" * 2
        self.manifest["regions"][0]["target_text"] = text
        result = self.render()
        lines = self.report(result)["regions"][0]["lines"]
        self.assertGreater(len(lines), 1)
        self.assertEqual("".join(lines), text)

    def test_manual_line_breaks_and_empty_lines_are_preserved(self):
        self.manifest["regions"][0]["target_text"] = "First\n\nLast"
        result = self.render()
        self.assertEqual(self.report(result)["regions"][0]["lines"], ["First", "", "Last"])

    @unittest.skipUnless(CJK_FONT.is_file(), "Windows Microsoft YaHei is required")
    def test_cjk_wraps_without_basic_closing_punctuation_at_line_start(self):
        region = self.manifest["regions"][0]
        region["target_text"] = "这是中文，换行测试。欢迎使用！继续测试，标点符号。"
        region["bbox"] = [30, 20, 130, 180]
        region["style"].update(font_path=str(CJK_FONT), min_font_size=22, max_font_size=22)
        result = self.render()
        lines = self.report(result)["regions"][0]["lines"]
        self.assertGreater(len(lines), 1)
        self.assertEqual("".join(lines), region["target_text"])
        self.assertTrue(all(line[0] not in "，。！？、；：）》】」』" for line in lines))

    def test_minimum_font_overflow_is_rejected_before_output_creation(self):
        self.manifest["regions"][0]["target_text"] = "Long text " * 100
        self.manifest["regions"][0]["bbox"] = [30, 40, 30, 20]
        self.assert_fails_without_output("overflow")

    def test_protected_overlap_is_rejected_but_touching_edge_is_allowed(self):
        self.manifest["protected_regions"] = [{"id": "logo", "bbox": [279, 50, 10, 10]}]
        self.assert_fails_without_output("protected")
        self.manifest["protected_regions"][0]["bbox"] = [280, 50, 10, 10]
        self.render()

    def test_preserve_region_needs_no_font_and_does_not_change_pixels(self):
        self.manifest["regions"][0]["action"] = "preserve"
        del self.manifest["regions"][0]["style"]
        result = self.render()
        with Image.open(result["translated"]) as rendered, Image.open(self.source) as source:
            self.assertIsNone(ImageChops.difference(rendered.convert("RGB"), source).getbbox())
        with Image.open(result["text_layer"]) as layer:
            self.assertIsNone(layer.getchannel("A").getbbox())

    def test_translated_region_cannot_overlap_preserved_region(self):
        self.manifest["regions"].append({
            "id": "brand", "action": "preserve", "bbox": [40, 50, 30, 30],
        })
        self.assert_fails_without_output("protected")

    def test_single_glyph_wider_than_available_space_is_rejected(self):
        self.manifest["regions"][0]["target_text"] = "W"
        self.manifest["regions"][0]["bbox"] = [30, 40, 9, 120]
        self.assert_fails_without_output("overflow")

    def test_source_dimensions_must_match_canvas(self):
        self.manifest["canvas"]["width"] = 300
        self.assert_fails_without_output("dimensions")

    def test_clean_background_dimensions_must_match_source(self):
        clean = self.root / "clean.png"
        Image.new("RGB", (10, 10)).save(clean)
        with self.assertRaisesRegex(ValueError, "dimensions"):
            self.render(clean_background=clean)
        self.assertFalse(self.output.exists())

    def test_missing_font_is_rejected(self):
        self.manifest["regions"][0]["style"]["font_path"] = str(self.root / "missing.ttf")
        self.assert_fails_without_output("missing font")

    def test_missing_unicode_glyph_is_rejected_before_output(self):
        self.manifest['regions'][0]['target_text'] = 'Hello中'
        self.assert_fails_without_output('missing Unicode glyphs')

    def test_clean_plate_requires_clean_background(self):
        self.manifest["regions"][0]["repair"] = {"mode": "clean_plate"}
        self.assert_fails_without_output("requires clean_background")

    def test_solid_requires_explicit_color(self):
        self.manifest["regions"][0]["repair"] = {"mode": "solid"}
        self.assert_fails_without_output("explicit color")

    def test_complex_script_without_raqm_is_rejected(self):
        with patch("image_localizer.rendering.features.check_feature", return_value=False):
            for text in ("مرحبا بالعالم", "नमस्ते", "สวัสดี", "ᠮᠣᠩᠭᠣᠯ"):
                with self.subTest(text=text):
                    self.manifest["regions"][0]["target_text"] = text
                    self.assert_fails_without_output("RAQM")

    def test_existing_output_directory_is_never_overwritten(self):
        self.output.mkdir()
        marker = self.output / "translated.png"
        marker.write_bytes(b"keep me")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.render()
        self.assertEqual(marker.read_bytes(), b"keep me")

    def test_input_file_cannot_be_used_as_output_directory(self):
        before = self.source.read_bytes()
        with self.assertRaises(ValueError):
            render_image(self.source, self.manifest, self.source)
        self.assertEqual(self.source.read_bytes(), before)

    def test_failure_in_later_region_leaves_no_partial_output(self):
        failing = copy.deepcopy(self.manifest["regions"][0])
        failing.update(id="later", target_text="overflow " * 100, bbox=[0, 0, 30, 20])
        self.manifest["regions"].append(failing)
        self.assert_fails_without_output("overflow")

    def test_stroke_and_alignment_stay_within_padding(self):
        for align in ("left", "center", "right"):
            with self.subTest(align=align):
                region = copy.deepcopy(self.manifest["regions"][0])
                region["style"].update(align=align, stroke_width=3)
                manifest = {**self.manifest, "regions": [region]}
                result = render_image(self.source, manifest, self.root / align)
                with Image.open(result["text_layer"]) as layer:
                    left, top, right, bottom = layer.getchannel("A").getbbox()
                    self.assertGreaterEqual(left, 34)
                    self.assertLessEqual(right, 276)
                    self.assertGreaterEqual(top, 44)
                    self.assertLessEqual(bottom, 156)

    def test_comparison_thumbnail_is_at_most_1800_pixels_wide(self):
        Image.new("RGB", (1600, 400)).save(self.source)
        self.manifest["canvas"] = {"width": 1600, "height": 400}
        result = self.render()
        with Image.open(result["comparison"]) as comparison:
            self.assertEqual(comparison.width, 1800)
            self.assertEqual(comparison.height, 225)


if __name__ == "__main__":
    unittest.main()
