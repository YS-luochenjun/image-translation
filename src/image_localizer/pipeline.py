"""Versioned local work packets and evidence-bound quality gates."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps

from .production_timing import bind_timing_output, timed_production
from .contracts import ContractError, advisory_checks, require, validate_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    require(isinstance(data, dict), f"Expected JSON object: {path.name}")
    return data


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def initialize(source: Path, target: str, run: Path) -> dict:
    require(source.is_file(), "Source image does not exist")
    require(bool(target.strip()), "Target language is required")
    require(not run.exists(), "Run directory already exists; choose a new ID")
    with Image.open(source) as image:
        require(getattr(image, "n_frames", 1) == 1, "Animated/multi-page inputs are unsupported; select one frame first")
        normalized = ImageOps.exif_transpose(image)
        if normalized.mode not in ("RGB", "RGBA"):
            normalized = normalized.convert("RGBA" if "A" in normalized.getbands() or "transparency" in normalized.info else "RGB")
        normalized.load()
        require(normalized.width * normalized.height <= 50_000_000, "Image exceeds 50 megapixel local limit")
        normalized.info.clear()
    run.mkdir(parents=True)
    source_copy = run / "source.png"
    normalized.save(source_copy)
    manifest = {
        "schema_version": 1,
        "source": {"path": "source.png", "sha256": sha256(source_copy), "original_sha256": sha256(source)},
        "canvas": {"width": normalized.width, "height": normalized.height},
        "target_language": target,
        "analysis": {"complete": False, "observations": []},
        "regions": [], "protected_regions": [], "glossary": [],
    }
    write_json(run / "manifest.json", manifest)
    return {"run": str(run.resolve()), "source": str(source_copy.resolve()), "next": "packet; view actual source; fill manifest.json"}


def load_run(run: Path, *, ready: bool = True) -> dict:
    manifest = read_json(run / "manifest.json")
    validate_manifest(manifest, ready=ready)
    require(manifest["source"].get("path") == "source.png", "Source must reference the run's immutable source.png")
    require(sha256(run / "source.png") == manifest["source"]["sha256"], "Source changed; create a new run")
    with Image.open(run / "source.png") as image:
        require(image.size == (manifest["canvas"]["width"], manifest["canvas"]["height"]), "Manifest canvas differs from actual source")
    return manifest


def packet(run: Path) -> dict:
    from .knowledge import load_knowledge

    manifest = load_run(run, ready=False)
    knowledge = load_knowledge(PROJECT_ROOT, source_sha256=manifest['source']['sha256'],
                               tags=manifest['analysis'].get('content_tags'))
    prompts = [PROJECT_ROOT / "agent" / "prompts" / name for name in ("analyze.md", "translate.md", "repair.md")]
    result = {
        "stage": "analyze-and-translate", "source": str((run / "source.png").resolve()),
        "source_sha256": manifest["source"]["sha256"], "manifest": manifest,
        "coordinate_system": "EXIF-normalized source.png pixels, bbox=[x,y,width,height]",
        "prompts": {path.name: path.read_text(encoding="utf-8") for path in prompts},
        "knowledge": knowledge,
        "workflow_stages": [
            {"id": "analyze-style", "output": "source text, semantic constraints, style and composition plan"},
            {"id": "clean-background", "output": "text-free background; inspect it before lettering"},
            {"id": "target-lettering", "output": "independent transparent target-language layer"},
            {"id": "composite", "output": "candidate composite and layout records"},
            {"id": "adapt-review", "output": "whole-image and regional review bound to current artifacts"},
        ],
        "capabilities": {"host_vision": "agent must actually view source", "builtin_imagegen": "host-provided; verify reference-image inputs against current session tool schema and actual results", "local_compositor": True,
                         "basic_cli": "axis-aligned rectangular solid/clean_plate only",
                         "enhanced_layers": "prepare-layered/render-layered/check-layered: explicit sampled repair, rotated text and local assets; Agent supplies semantics and actual review",
                         "layered_guide": str(PROJECT_ROOT / 'docs' / 'layered-tools.md')},
        "warning": "Packet generation performs no recognition, translation, visual stage review or model call.",
    }
    write_json(run / "packet.json", result)
    return result


@timed_production(input_name='run')
def render(run: Path, clean_background: Path | None = None) -> dict:
    from .rendering import render_image
    manifest = load_run(run)
    require(not manifest["analysis"].get("unresolved"), "Resolve analysis uncertainties before rendering")
    for region in manifest["regions"]:
        if region["action"] == "translate":
            font = Path(region["style"]["font_path"])
            require(font.is_absolute(), "font_path must be absolute (use doctor to locate fonts)")
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    output = run / "renders" / version
    bind_timing_output(output)
    manifest_hash = sha256(run / "manifest.json")
    source_hash = sha256(run / "source.png")
    background_hash = sha256(clean_background) if clean_background else None
    font_hashes = {region["style"]["font_path"]: sha256(Path(region["style"]["font_path"]))
                   for region in manifest["regions"] if region["action"] == "translate"}
    paths = render_image(run / "source.png", manifest, output, clean_background)
    require(all(sha256(Path(path)) == digest for path, digest in font_hashes.items()), "Font changed during render")
    require(sha256(run / "manifest.json") == manifest_hash and sha256(run / "source.png") == source_hash,
            "Input changed during render; rerender required")
    if clean_background:
        require(sha256(clean_background) == background_hash, "Clean background changed during render")
    receipt = {
        "version": version, "manifest_sha256": manifest_hash,
        "source_sha256": source_hash,
        "clean_background_sha256": background_hash,
        "font_sha256": font_hashes,
        "output_sha256": sha256(output / "translated.png"),
        "layout_sha256": sha256(output / "layout.json"),
        "artifacts": paths,
        "artifact_sha256": {name: sha256(output / name) for name in ("translated.png", "layout.json", "text-layer.png", "comparison.png")},
        "advisories": advisory_checks(manifest),
        "status": "needs_review",
    }
    write_json(output / "receipt.json", receipt)
    write_json(run / "current.json", {"version": version, "receipt_sha256": sha256(output / "receipt.json")})
    save_quality(run, output, {"status": "pending", "technical": "pass", "review_kind": None,
                              "output": str((output / "translated.png").resolve()), "user_acceptance": "not_recorded",
                              "reasons": ["New render requires actual review"]})
    return {"version": version, "output_dir": str(output.resolve()), "status": "needs_review", "advisories": receipt["advisories"]}


def render_directory(run: Path) -> Path:
    pointer = read_json(run / "current.json")
    version = pointer.get("version", "")
    require(isinstance(version, str) and version and Path(version).name == version and "/" not in version and "\\" not in version and version not in (".", ".."), "Invalid render version")
    return run / "renders" / version


def current_render(run: Path) -> tuple[Path, dict]:
    output = render_directory(run)
    pointer = read_json(run / "current.json")
    require(sha256(output / "receipt.json") == pointer.get("receipt_sha256"), "Render receipt changed")
    receipt = read_json(output / "receipt.json")
    require(sha256(run / "manifest.json") == receipt.get("manifest_sha256"), "Manifest changed after render; rerender required")
    require(sha256(run / "source.png") == receipt.get("source_sha256"), "Source changed after render")
    require(sha256(output / "translated.png") == receipt.get("output_sha256"), "Rendered image changed; regenerate and review")
    require(sha256(output / "layout.json") == receipt.get("layout_sha256"), "Layout evidence changed")
    artifact_hashes = receipt.get("artifact_sha256", {})
    for name in ("translated.png", "layout.json", "text-layer.png", "comparison.png"):
        require(sha256(output / name) == artifact_hashes.get(name), f"Artifact changed or missing evidence: {name}")
    return output, receipt


def review_packet(run: Path) -> dict:
    manifest = load_run(run)
    output, receipt = current_render(run)
    template = {
        "manifest_sha256": receipt["manifest_sha256"], "output_sha256": receipt["output_sha256"],
        "reviewer": "", "review_kind": "self", "canvas_preserved": None, "protected_content_preserved": None,
        "checks": [{"id": item["id"], "semantic": None, "completeness": None, "legibility": None,
                    "restoration": None, "style": None, "notes": ""} for item in manifest["regions"]],
        "issues": [], "global_notes": "",
    }
    write_json(output / "review-template.json", template)
    return {"source": str((run / "source.png").resolve()), "output": str((output / "translated.png").resolve()),
            "manifest_path": str((run / "manifest.json").resolve()),
            "receipt_path": str((output / "receipt.json").resolve()),
            "layout_path": str((output / "layout.json").resolve()),
            "comparison": str((output / "comparison.png").resolve()),
            "instructions": (PROJECT_ROOT / "agent/prompts/review.md").read_text(encoding="utf-8"),
            "template": str((output / "review-template.json").resolve()),
            "save_review_as": str((output / "review.json").resolve()), "advisories": receipt["advisories"]}


def save_quality(run: Path, output: Path | None, result: dict) -> dict:
    try:
        if run.is_dir():
            write_json(run / "quality.json", result)
        if output is not None and output.is_dir():
            write_json(output / "quality.json", result)
    except OSError as error:
        result.setdefault("reasons", []).append(f"Could not persist quality report: {error}")
        result["status"] = "fail"
    return result


def quality(run: Path) -> dict:
    output = None
    try:
        if (run / "current.json").is_file():
            output = render_directory(run)
        manifest = load_run(run)
        output, receipt = current_render(run)
        with Image.open(output / "translated.png") as image:
            require(image.size == (manifest["canvas"]["width"], manifest["canvas"]["height"]), "Output dimensions changed")
        result = {"status": "pending", "technical": "pass", "review_kind": None,
                  "output": str((output / "translated.png").resolve()), "user_acceptance": "not_recorded", "reasons": []}
        review_path = output / "review.json"
        if not review_path.exists():
            result["reasons"].append("Actual visual/semantic review is missing")
        else:
            review = read_json(review_path)
            require(review.get("manifest_sha256") == receipt["manifest_sha256"] and review.get("output_sha256") == receipt["output_sha256"], "Review belongs to a different manifest or image")
            require(isinstance(review.get("reviewer"), str) and bool(review["reviewer"].strip()), "Named reviewer required")
            require(review.get("review_kind") in ("self", "independent"), "Invalid review kind")
            result["review_kind"] = review["review_kind"]
            require(isinstance(review.get("global_notes"), str) and bool(review["global_notes"].strip()), "Actual global observations required")
            checks = review.get("checks")
            require(isinstance(checks, list) and all(isinstance(item, dict) for item in checks), "Review checks required")
            ids = [item.get("id") for item in checks]
            require(len(ids) == len(set(ids)) and set(ids) == {item["id"] for item in manifest["regions"]}, "Review must cover every region exactly once")
            values = [review.get("canvas_preserved"), review.get("protected_content_preserved")]
            for check in checks:
                require(isinstance(check.get("notes"), str) and bool(check["notes"].strip()), "Per-region observations required")
                values.extend(check.get(field) for field in ("semantic", "completeness", "legibility", "restoration", "style"))
            require(all(value is None or type(value) is bool for value in values), "Review verdicts must be boolean or null")
            issues = review.get("issues")
            require(isinstance(issues, list), "Review issues required")
            for issue in issues:
                require(isinstance(issue, dict) and issue.get("severity") in ("minor", "major", "critical") and isinstance(issue.get("note"), str) and bool(issue["note"].strip()), "Invalid review issue")
                require(issue.get("region_id") is None or issue["region_id"] in ids, "Unknown issue region ID")
            unresolved_advisories = receipt.get("advisories") and not review.get("advisories_reviewed") is True
            if False in values or any(issue["severity"] in ("major", "critical") for issue in issues):
                result.update(status="fail", reasons=["Review identifies a blocking defect"])
            elif None in values or unresolved_advisories:
                result["reasons"].append("Review is incomplete or numeric/terminology advisories remain unreviewed")
            else:
                result["status"] = "pass"
            result["issues"] = issues
        return save_quality(run, output, result)
    except (ContractError, OSError, ValueError, KeyError, TypeError) as error:
        return save_quality(run, output, {"status": "fail", "technical": "fail", "reasons": [str(error)], "user_acceptance": "not_recorded"})
