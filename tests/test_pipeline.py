"""Regression checks for evidence freshness and honest review state."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from image_localizer.cli import available_fonts
from image_localizer.contracts import ContractError, advisory_checks, validate_manifest
from image_localizer.pipeline import (
    current_render, initialize, load_run, quality, read_json, render, review_packet, write_json,
)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "input.png"
        Image.new("RGB", (400, 200), "white").save(self.source)
        self.run = self.root / "run"
        initialize(self.source, "en-US", self.run)
        self.manifest = read_json(self.run / "manifest.json")
        self.manifest["analysis"] = {"complete": True, "observations": ["Authored test source; text placement fixture."], "unresolved": []}
        self.manifest["regions"] = [{
            "id": "r1", "action": "translate", "source_text": "Save 20%", "target_text": "Save 20%",
            "confidence": 1, "notes": "Known test text.", "bbox": [20, 20, 300, 100],
            "style": {"font_path": available_fonts()[0], "min_font_size": 16, "max_font_size": 30, "color": "#111111"},
            "repair": {"mode": "solid", "color": "#ffffff"},
        }]
        self.save()

    def save(self):
        write_json(self.run / "manifest.json", self.manifest)

    def render_fixture(self):
        render(self.run)
        output, _ = current_render(self.run)
        return output

    def fixture_review(self, output):
        review_packet(self.run)
        review = read_json(output / "review-template.json")
        review.update(reviewer="unit-test fixture, not actual review", global_notes="Synthetic test contract only.",
                      canvas_preserved=True, protected_content_preserved=True)
        for check in review["checks"]:
            check.update(semantic=True, completeness=True, legibility=True, restoration=True, style=True,
                         notes="Fixture assertions for contract behavior; no production approval.")
        write_json(output / "review.json", review)
        return review

    def test_missing_review_stays_pending(self):
        self.render_fixture()
        self.assertEqual(quality(self.run)["status"], "pending")

    def test_complete_fixture_review_passes_contract(self):
        output = self.render_fixture()
        self.fixture_review(output)
        self.assertEqual(quality(self.run)["status"], "pass")
        self.assertEqual(quality(self.run)["user_acceptance"], "not_recorded")

    def test_manifest_edit_invalidates_review(self):
        output = self.render_fixture()
        self.fixture_review(output)
        self.manifest["regions"][0]["target_text"] = "New translation"
        self.save()
        self.assertEqual(quality(self.run)["status"], "fail")

    def test_image_edit_invalidates_review(self):
        output = self.render_fixture()
        self.fixture_review(output)
        Image.new("RGB", (400, 200), "black").save(output / "translated.png")
        self.assertEqual(quality(self.run)["status"], "fail")

    def test_partial_review_cannot_pass(self):
        output = self.render_fixture()
        review = self.fixture_review(output)
        review["checks"][0]["legibility"] = None
        write_json(output / "review.json", review)
        self.assertEqual(quality(self.run)["status"], "pending")

    def test_major_issue_cannot_be_offset_by_true_checks(self):
        output = self.render_fixture()
        review = self.fixture_review(output)
        review["issues"] = [{"region_id": "r1", "severity": "major", "note": "Known wrong term"}]
        write_json(output / "review.json", review)
        self.assertEqual(quality(self.run)["status"], "fail")

    def test_missing_region_review_cannot_pass(self):
        output = self.render_fixture()
        review = self.fixture_review(output)
        review["checks"] = []
        write_json(output / "review.json", review)
        self.assertEqual(quality(self.run)["status"], "fail")

    def test_empty_inventory_needs_explicit_no_text_evidence(self):
        self.manifest["regions"] = []
        with self.assertRaises(ContractError):
            validate_manifest(self.manifest)
        self.manifest["analysis"]["no_text_verified"] = True
        validate_manifest(self.manifest)

    def test_uncertain_reading_is_blocked_until_verified(self):
        self.manifest["regions"][0]["confidence"] = 0.6
        with self.assertRaises(ContractError):
            validate_manifest(self.manifest)
        self.manifest["regions"][0]["recognition_verified"] = True
        validate_manifest(self.manifest)

    def test_unresolved_analysis_blocks_render(self):
        self.manifest["analysis"]["unresolved"] = ["Unreadable price"]
        self.save()
        with self.assertRaises(ContractError):
            render(self.run)

    def test_region_overlap_is_rejected(self):
        self.manifest["protected_regions"] = [{"id": "product", "bbox": [50, 50, 10, 10]}]
        with self.assertRaises(ContractError):
            validate_manifest(self.manifest)

    def test_numeric_change_requires_advisory_acknowledgement(self):
        self.manifest["regions"][0]["target_text"] = "Save 30%"
        self.save()
        self.assertEqual(advisory_checks(self.manifest)[0]["kind"], "numbers")
        output = self.render_fixture()
        review = self.fixture_review(output)
        self.assertEqual(quality(self.run)["status"], "pending")
        review["advisories_reviewed"] = True
        write_json(output / "review.json", review)
        self.assertEqual(quality(self.run)["status"], "pass")

    def test_run_is_never_overwritten(self):
        with self.assertRaises(ContractError):
            initialize(self.source, "zh-CN", self.run)

    def test_new_render_preserves_earlier_version(self):
        first = self.render_fixture()
        self.fixture_review(first)
        self.assertEqual(quality(self.run)["status"], "pass")
        second = self.render_fixture()
        self.assertNotEqual(first, second)
        self.assertTrue((first / "translated.png").is_file())
        self.assertEqual(read_json(self.run / "quality.json")["status"], "pending")
        self.assertEqual(read_json(first / "quality.json")["status"], "pass")

    def test_corrupted_source_is_rejected(self):
        Image.new("RGB", (400, 200), "black").save(self.run / "source.png")
        with self.assertRaises(ContractError):
            load_run(self.run)

    def test_missing_background_never_silently_uses_solid_fill(self):
        self.manifest["regions"][0]["repair"] = {"mode": "clean_plate"}
        self.save()
        with self.assertRaisesRegex(ValueError, "clean_background"):
            render(self.run)

    def test_nan_confidence_is_rejected(self):
        self.manifest["regions"][0]["confidence"] = float("nan")
        with self.assertRaises(ContractError):
            validate_manifest(self.manifest)

    def test_fractional_stroke_is_rejected(self):
        self.manifest["regions"][0]["style"]["stroke_width"] = 1.5
        with self.assertRaises(ContractError):
            validate_manifest(self.manifest)

    def test_failed_gate_replaces_old_pass_report(self):
        output = self.render_fixture()
        self.fixture_review(output)
        self.assertEqual(quality(self.run)["status"], "pass")
        self.manifest["regions"][0]["target_text"] = "Changed"
        self.save()
        self.assertEqual(quality(self.run)["status"], "fail")
        self.assertEqual(read_json(output / "quality.json")["status"], "fail")
        self.assertEqual(read_json(self.run / "quality.json")["status"], "fail")

    def test_modified_auxiliary_artifacts_invalidate_quality(self):
        for filename in ("text-layer.png", "comparison.png"):
            with self.subTest(filename=filename):
                output = self.render_fixture()
                self.fixture_review(output)
                Image.new("RGBA", (400, 200), "red").save(output / filename)
                self.assertEqual(quality(self.run)["status"], "fail")

    def test_la_alpha_survives_normalization(self):
        source = self.root / "gray-alpha.png"
        Image.new("LA", (5, 5), (120, 0)).save(source)
        alpha_run = self.root / "alpha-run"
        initialize(source, "en", alpha_run)
        with Image.open(alpha_run / "source.png") as normalized:
            self.assertEqual(normalized.mode, "RGBA")
            self.assertEqual(normalized.getpixel((0, 0))[3], 0)

    def test_preserve_also_requires_low_confidence_verification(self):
        region = self.manifest["regions"][0]
        region.update(action="preserve", confidence=0.1)
        with self.assertRaises(ContractError):
            validate_manifest(self.manifest)
        region["recognition_verified"] = True
        validate_manifest(self.manifest)


if __name__ == "__main__":
    unittest.main()
