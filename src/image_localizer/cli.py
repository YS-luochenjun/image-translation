"""Local CLI; no upload handlers or hidden model calls."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from PIL import Image, __version__ as pillow_version, features

from .contracts import advisory_checks
from .pipeline import initialize, load_run, packet, quality, render, review_packet


def available_fonts() -> list[str]:
    roots = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts", Path("/usr/share/fonts/truetype/dejavu"), Path("/System/Library/Fonts/Supplemental")]
    names = ("msyh.ttc", "msyhbd.ttc", "arial.ttf", "arialbd.ttf", "YuGothM.ttc", "malgun.ttf", "DejaVuSans.ttf", "Arial.ttf")
    return [str(path.resolve()) for root in roots for name in names if (path := root / name).is_file()]


def doctor() -> dict:
    return {"python": sys.version.split()[0], "executable": sys.executable, "pillow": pillow_version,
            "raqm": features.check_feature("raqm"), "font_candidates": available_fonts(),
            "host_vision": "must be verified by actually reading an image in this session",
            "imagegen": {"current_tool": "text-to-image only", "reference_input": False,
                         "provider_verified": False, "note": "Project does not read host credentials or invoke imagegen."},
            "render_support": "basic: axis-aligned regions; layered: sampled repair, rotated text, local assets, protected-pixel checks; complex scripts need RAQM; no automatic OCR"}


def demo(output: Path) -> dict:
    from PIL import ImageDraw, ImageFont
    from .pipeline import read_json, write_json
    fonts = available_fonts()
    latin = next((font for font in fonts if Path(font).name.lower() in ("arial.ttf", "dejavusans.ttf")), None)
    chinese = next((font for font in fonts if Path(font).name.lower() == "msyh.ttc"), None)
    if not latin:
        raise ValueError("Demo requires Arial or DejaVuSans; see doctor")
    if output.exists():
        raise ValueError("Demo directory already exists; choose another --output")
    output.mkdir(parents=True)
    image = Image.new("RGB", (1200, 800), "#f3eee6")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((760, 85, 1110, 715), radius=65, fill="#d9e4da")
    draw.ellipse((790, 200, 1080, 490), fill="#749483")
    draw.rounded_rectangle((855, 300, 1020, 610), radius=24, fill="#f9f6ee")
    draw.rounded_rectangle((904, 260, 973, 315), radius=8, fill="#334c43")
    draw.text((70, 60), "STUDIO / 01", font=ImageFont.truetype(latin, 22), fill="#334c43")
    specs = [
        ("title", "Everyday essentials", "日常好物" if chinese else "Les essentiels", [70, 200, 640, 125], 58, "#263d34"),
        ("body", "Made for your daily ritual.", "为每一天的生活仪式而生。" if chinese else "Pour votre rituel quotidien.", [70, 345, 640, 90], 30, "#51675b"),
        ("offer", "Save 20% today", "今日优惠 20%" if chinese else "Economisez 20%", [70, 475, 640, 95], 42, "#263d34"),
        ("cta", "Shop now", "即刻选购" if chinese else "Acheter", [70, 625, 400, 85], 30, "#f3eee6"),
    ]
    draw.rounded_rectangle((70, 625, 470, 710), radius=20, fill="#263d34")
    clean_background = output / "authored-clean-background.png"
    image.save(clean_background)
    for _, text, _, bbox, size, fill in specs:
        draw.text((bbox[0] + 16, bbox[1] + 12), text, font=ImageFont.truetype(latin, size), fill=fill)
    source = output / "example-source.png"
    image.save(source)
    run = output / "run"
    initialize(source, "zh-CN" if chinese else "fr-FR", run)
    manifest = read_json(run / "manifest.json")
    manifest["analysis"] = {"complete": True, "observations": ["Synthetic flat-color product poster; four text regions, brand and product illustration preserved. Demo copy is authored, not OCR output."], "unresolved": []}
    manifest["protected_regions"] = [{"id": "product", "bbox": [760, 85, 350, 630]}]
    manifest["regions"] = [
        {"id": region_id, "action": "translate", "source_text": text, "target_text": target, "bbox": bbox,
         "confidence": 1.0, "notes": "Synthetic example; known source text and flat background.",
         "style": {"font_path": chinese or latin, "min_font_size": 20, "max_font_size": size,
                   "color": fill, "align": "left", "padding": 16, "line_spacing": 6},
         "repair": {"mode": "clean_plate"} if region_id == "cta" else {"mode": "solid", "color": "#f3eee6"}}
        for region_id, text, target, bbox, size, fill in specs
    ] + [{"id": "brand", "action": "preserve", "source_text": "STUDIO / 01", "bbox": [70, 60, 250, 40],
          "confidence": 1.0, "notes": "Synthetic brand label preserved by design."}]
    write_json(run / "manifest.json", manifest)
    packet(run)
    rendered = render(run, clean_background)
    review = review_packet(run)
    report = quality(run)
    return {"synthetic_demo": True, "run": str(run.resolve()), "render": rendered, "review": review, "quality": report,
            "note": "No OCR/API/generation call. Review remains pending until actual inspection."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local single-image translation workspace")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Inspect local dependencies and honest capability boundaries")
    init = commands.add_parser("init", help="Normalize a local source and create an unreviewed task")
    init.add_argument("source", type=Path)
    init.add_argument("--target", required=True)
    init.add_argument("--run", type=Path, required=True)
    for name in ("packet", "validate", "render", "review-packet", "quality"):
        command = commands.add_parser(name)
        command.add_argument("run", type=Path)
        if name == "render":
            command.add_argument("--clean-background", type=Path)
    for name in ('prepare-layered', 'render-layered'):
        command = commands.add_parser(name, help='Process an explicit local layered plan; no model calls')
        command.add_argument('plan', type=Path)
        command.add_argument('--output', type=Path, required=True)
        if name == 'render-layered':
            command.add_argument('--background', type=Path, required=True)
    layered_check = commands.add_parser('check-layered', help='Verify layered artifacts and actual review evidence')
    layered_check.add_argument('output', type=Path)
    example = commands.add_parser("demo", help="Create and render an authored synthetic example")
    example.add_argument("--output", type=Path, default=Path("work/demo"))
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor()
        elif args.command == "init":
            result = initialize(args.source, args.target, args.run)
        elif args.command == "demo":
            result = demo(args.output)
        elif args.command in ('prepare-layered', 'render-layered', 'check-layered'):
            from .layered import prepare_layered, render_layered, check_layered
            if args.command == 'prepare-layered':
                result = prepare_layered(args.plan, args.output)
            elif args.command == 'render-layered':
                result = render_layered(args.plan, args.background, args.output)
            else:
                result = check_layered(args.output)
        elif args.command == "packet":
            result = packet(args.run)
        elif args.command == "validate":
            manifest = load_run(args.run)
            result = {"status": "valid", "advisories": advisory_checks(manifest), "note": "Contract validation does not certify translation or visual quality."}
        elif args.command == "render":
            result = render(args.run, args.clean_background)
        elif args.command == "review-packet":
            result = review_packet(args.run)
        else:
            result = quality(args.run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command in ('quality', 'check-layered'):
            return {"pass": 0, "pending": 2, "fail": 1}[result["status"]]
        if args.command == 'render-layered' and result.get('status') == 'fail':
            return 1
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
