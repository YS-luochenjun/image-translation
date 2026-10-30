"""Risk-focused coverage for pure Pillow layer operations."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from image_localizer.layer_ops import compose_assets, paint_text_layer, repair_background


def font_path():
    for candidate in ("C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return str(Path(ImageFont.truetype(candidate, 12).path).resolve())
        except OSError:
            continue
    raise unittest.SkipTest("No local TrueType test font")


def row(**changes):
    return {"id": "title", "text": "Hello", "font_path": font_path(), "font_size": 18,
            "center": [80, 40], "color": "#cf0700", "angle": 3.5, "stroke": .2, **changes}


class RepairTests(unittest.TestCase):
    def test_mask_restore_and_pixels_outside_edit_remain_exact(self):
        source = Image.new("RGBA", (10, 8))
        source.putdata([(x * 13, y * 21, x + y, 50 + x + y) for y in range(8) for x in range(10)])
        before = source.tobytes()
        output, mask, report = repair_background(source, [
            {"type": "solid", "bbox": [1, 1, 5, 4], "color": "white"},
            {"type": "restore", "bbox": [2, 2, 2, 1]},
        ])
        self.assertEqual(source.tobytes(), before)
        self.assertEqual((output.mode, mask.mode), ("RGBA", "L"))
        for y in range(8):
            for x in range(10):
                changed = 1 <= x < 6 and 1 <= y < 5 and not (2 <= x < 4 and y == 2)
                self.assertEqual(mask.getpixel((x, y)), 255 if changed else 0)
                self.assertEqual(output.getpixel((x, y)), (255, 255, 255, 255) if changed else source.getpixel((x, y)))
        self.assertEqual(report[1]["bbox"], [2, 2, 2, 1])

    def test_row_donors_are_original_and_median_truncates_inclusively(self):
        source = Image.new("RGB", (5, 3), (200, 200, 200))
        for y in range(3):
            source.putpixel((0, y), (10 + y, 20, 30))
            source.putpixel((1, y), (11 + y, 21, 31))
        output, mask, _ = repair_background(source, [
            {"type": "solid", "bbox": [0, 0, 2, 3], "color": "red"},
            {"type": "sample_rows", "bbox": [3, 0, 2, 3], "donors": [[0, 0, 2, 3]],
             "row_radius": 0, "rgb_min": [10, 20, 30], "rgb_max": [13, 21, 31]},
        ])
        for y in range(3):
            self.assertEqual(output.getpixel((3, y)), (10 + y, 20, 30, 255))
        self.assertEqual(mask.getpixel((2, 1)), 0)

    def test_radius_respects_donor_vertical_extent(self):
        source = Image.new("RGB", (3, 3), "red")
        source.putpixel((0, 1), (20, 30, 40))
        output, _, _ = repair_background(source, [{"type": "sample_rows", "bbox": [2, 0, 1, 3],
            "donors": [[0, 1, 1, 1]], "row_radius": 1, "rgb_min": [0, 0, 0], "rgb_max": [255, 255, 255]}])
        self.assertEqual([output.getpixel((2, y)) for y in range(3)], [(20, 30, 40, 255)] * 3)

    def test_missing_donor_data_fails_instead_of_fallback(self):
        source = Image.new("RGB", (3, 3), "red")
        operation = {"type": "sample_rows", "bbox": [2, 0, 1, 3], "donors": [[0, 1, 1, 1]],
                     "row_radius": 0, "rgb_min": [0, 0, 0], "rgb_max": [255, 255, 255]}
        with self.assertRaisesRegex(ValueError, "no valid donor"):
            repair_background(source, [operation])
        with self.assertRaisesRegex(ValueError, "no valid donor"):
            repair_background(source, [{**operation, "donors": [[0, 0, 1, 3]], "rgb_max": [1, 1, 1]}])

    def test_reject_invalid_operations_boxes_colors_and_fields(self):
        source = Image.new("RGB", (3, 3))
        invalid = [
            {"type": "clean_plate", "bbox": [0, 0, 1, 1], "path": "x"},
            {"type": "solid", "bbox": [2, 2, 2, 2], "color": "red"},
            {"type": "solid", "bbox": [-1, 0, 1, 1], "color": "red"},
            {"type": "solid", "bbox": [0, 0, 1.0, 1], "color": "red"},
            {"type": "solid", "bbox": [0, 0, True, 1], "color": "red"},
            {"type": "solid", "bbox": [0, 0, 1, 1], "color": "#ff000080"},
            {"type": "solid", "bbox": [0, 0, 1, 1], "color": [256, 0, 0]},
            {"type": "restore", "bbox": [0, 0, 1, 1], "extra": 1},
            {"type": [], "bbox": [0, 0, 1, 1]},
        ]
        for operation in invalid:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                repair_background(source, [operation])


class TextTests(unittest.TestCase):
    def test_matches_old_burger_typeset_raster_algorithm(self):
        config = row()
        scale = 6
        font = ImageFont.truetype(config["font_path"], round(config["font_size"] * scale))
        stroke = round(config["stroke"] * scale)
        bb = font.getbbox(config["text"], stroke_width=stroke)
        temp = Image.new("RGBA", (bb[2] - bb[0] + 12 * scale, bb[3] - bb[1] + 12 * scale))
        ImageDraw.Draw(temp).text((6 * scale - bb[0], 6 * scale - bb[1]), config["text"], font=font,
                                 fill=config["color"], stroke_width=stroke, stroke_fill=config["color"])
        temp = temp.crop(temp.getbbox()).rotate(config["angle"], resample=Image.Resampling.BICUBIC, expand=True)
        reference = Image.new("RGBA", (160 * scale, 80 * scale))
        x = round(config["center"][0] * scale - temp.width / 2)
        y = round(config["center"][1] * scale - temp.height / 2)
        reference.alpha_composite(temp, (x, y))
        reference = reference.resize((160, 80), Image.Resampling.LANCZOS)
        result, records = paint_text_layer((160, 80), [config])
        self.assertEqual(result.tobytes(), reference.tobytes())
        self.assertEqual(records[0]["font_size"], 18)
        self.assertEqual(records[0]["bbox_px"], [round(v / scale, 2) for v in (x, y, temp.width, temp.height)])

    def test_protection_cuts_final_alpha_and_requires_visual_review(self):
        original, _ = paint_text_layer((160, 80), [row()])
        protected, records = paint_text_layer((160, 80), [row()], preserve_boxes=[[75, 20, 10, 40]])
        cut = records[-1]
        expected = sum(original.getchannel("A").crop((75, 20, 85, 60)).histogram()[1:])
        self.assertGreater(expected, 0)
        self.assertEqual(cut["cleared_alpha_pixels"], expected)
        self.assertTrue(cut["requires_review"])
        self.assertIn("damage glyphs", cut["warnings"][0])
        self.assertIsNone(protected.getchannel("A").crop((75, 20, 85, 60)).getbbox())
        self.assertEqual(protected.crop((0, 0, 75, 80)).tobytes(), original.crop((0, 0, 75, 80)).tobytes())

    def test_complex_scripts_use_shared_raqm_gate(self):
        with patch("image_localizer.layer_ops.features.check_feature", return_value=False):
            with self.assertRaisesRegex(ValueError, "RAQM"):
                paint_text_layer((160, 80), [row(text="مرحبا")])
            self.assertIsNotNone(paint_text_layer((160, 80), [row()])[0].getbbox())

    def test_reject_blank_nonfinite_overflow_and_unknown_fields(self):
        for changes in ({"text": " "}, {"text": "\u200b"}, {"text": "a\nb"}, {"angle": float("nan")},
                        {"center": [float("inf"), 40]}, {"center": [0, 0]}, {"font_size": 9000},
                        {"font_size": True}, {"stroke": -1}, {"unknown": True}, {"color": [1, 2, 3, 4]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                paint_text_layer((160, 80), [row(**changes)])
        for size, scale in (((10000, 10000), 1), ((2000, 2000), 6), ((10, 10), 0), ((10, 10), 1.5)):
            with self.subTest(size=size, scale=scale), self.assertRaises(ValueError):
                paint_text_layer(size, [], scale=scale)
        with self.assertRaises(ValueError):
            paint_text_layer((160, 80), [], preserve_boxes=[[159, 0, 2, 1]])


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        Image.new("RGBA", (4, 4), (255, 0, 0, 128)).save(self.root / "red.png")
        Image.new("RGBA", (4, 4), (0, 0, 255, 128)).save(self.root / "blue.png")

    def asset(self, **changes):
        return {"id": "a", "path": "red.png", "position": [1, 1], "kind": "compound", **changes}

    def test_alpha_composite_order_and_outside_pixels(self):
        result, report = compose_assets((8, 8), [self.asset(), self.asset(id="b", path="blue.png")], base_dir=self.root)
        expected = Image.alpha_composite(Image.new("RGBA", (1, 1), (255, 0, 0, 128)),
                                         Image.new("RGBA", (1, 1), (0, 0, 255, 128))).getpixel((0, 0))
        self.assertEqual(result.getpixel((2, 2)), expected)
        self.assertEqual(result.getpixel((0, 0)), (0, 0, 0, 0))
        self.assertEqual(report[0]["path"], str((self.root / "red.png").resolve()))

    def test_large_transparent_asset_can_resize_and_crop_first(self):
        image = Image.new("RGBA", (40, 20))
        image.paste((0, 255, 0, 255), (10, 5, 30, 15))
        image.save(self.root / "large.png")
        result, report = compose_assets((8, 8), [self.asset(path="large.png", crop=[10, 5, 20, 10], size=[4, 2])], base_dir=self.root)
        self.assertEqual(result.getpixel((1, 1)), (0, 255, 0, 255))
        self.assertEqual(report[0]["warnings"], [])
        _, report = compose_assets((8, 8), [self.asset(size=[4, 2])], base_dir=self.root)
        self.assertIn("aspect ratio", report[0]["warnings"][0])

    def test_reject_overflow_bad_crop_url_kind_and_unknown_fields(self):
        for changes in ({"position": [7, 7]}, {"position": [float("nan"), 1]}, {"crop": [3, 3, 2, 2]},
                        {"crop": [0, 0, 0, 1]}, {"size": [0, 1]}, {"path": "https://example.org/a.png"},
                        {"kind": "other"}, {"unused": 1}, {"size": [100000, 100000]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                compose_assets((8, 8), [self.asset(**changes)], base_dir=self.root)

    def test_reject_multiframe_source_and_asset(self):
        path = self.root / "animated.gif"
        Image.new("RGB", (3, 3), "red").save(path, save_all=True,
            append_images=[Image.new("RGB", (3, 3), "blue")], duration=50, loop=0)
        with self.assertRaisesRegex(ValueError, "multi-frame"):
            compose_assets((8, 8), [self.asset(path=path)], base_dir=self.root)
        with Image.open(path) as opened:
            with self.assertRaisesRegex(ValueError, "multi-frame"):
                repair_background(opened, [])


if __name__ == "__main__":
    unittest.main()
