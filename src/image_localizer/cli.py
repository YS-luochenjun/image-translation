"""Local CLI; no upload handlers or hidden model calls."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from PIL import Image, __version__ as pillow_version, features

from .contracts import advisory_checks, require
from .pipeline import initialize, load_run, packet, quality, render, review_packet


def available_fonts() -> list[str]:
    roots = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts", Path("/usr/share/fonts/truetype/dejavu"), Path("/System/Library/Fonts/Supplemental")]
    names = ("msyh.ttc", "msyhbd.ttc", "arial.ttf", "arialbd.ttf", "YuGothM.ttc", "malgun.ttf", "DejaVuSans.ttf", "Arial.ttf")
    return [str(path.resolve()) for root in roots for name in names if (path := root / name).is_file()]


def doctor() -> dict:
    return {"python": sys.version.split()[0], "executable": sys.executable, "pillow": pillow_version,
            "raqm": features.check_feature("raqm"), "font_candidates": available_fonts(),
            "host_vision": "must be verified by actually reading an image in this session",
            "imagegen": {"current_tool": "host-provided; inspect current session tool schema", "reference_input": None,
                         "provider_verified": False, "note": "CLI cannot inspect host tool capabilities. Verify reference input and actual results in the current session; project does not read host credentials or invoke imagegen."},
            "render_support": "basic: axis-aligned regions; layered: sampled repair, rotated text, configurable face/contact/depth/soft shadow, cmap glyph checks, local assets; jobs: shared reviewed background and frozen inputs; no automatic OCR or visual approval"}


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
        if name in ("packet", "review-packet"):
            mode = command.add_mutually_exclusive_group()
            mode.add_argument("--full", action="store_true", help="Return the expanded packet")
            mode.add_argument("--since", help="Checkpoint from a previously read compact packet")
    for name in ('prepare-layered', 'render-layered'):
        command = commands.add_parser(name, help='Process an explicit local layered plan; no model calls')
        command.add_argument('plan', type=Path)
        command.add_argument('--output', type=Path, required=True)
        if name == 'render-layered':
            command.add_argument('--background', type=Path, required=True)
    layered_check = commands.add_parser('check-layered', help='Verify layered artifacts and actual review evidence')
    layered_check.add_argument('output', type=Path)
    job_init = commands.add_parser('init-job', help='Start an Agent-led local multi-language image job')
    job_init.add_argument('source', type=Path)
    job_init.add_argument('--targets', required=True, help='Comma-separated language identifiers')
    job_init.add_argument('--output', required=True, type=Path)
    for name, argument in [('prepare-job', 'job'), ('render-job', 'prepared'),
                           ('compare', 'spec'), ('benchmark', 'spec')]:
        command = commands.add_parser(name)
        command.add_argument(argument, type=Path)
        command.add_argument('--output', required=name in ('compare', 'benchmark'), type=Path)
        if name == 'prepare-job':
            command.add_argument('--reuse-background', type=Path, help='Prior prepared job with the identical reviewed background')
            command.add_argument('--revisioned', action='store_true',
                                 help='Use a stable output slot and reuse verified revisions with identical inputs')
            command.add_argument('--build-script', type=Path,
                                 help='Run a local per-source Python builder with the job path before preparation')
        if name == 'render-job':
            command.add_argument('--full-previews', action='store_true', help='Export standalone comparison and zoom PNGs')
    job_check = commands.add_parser('check-job', help='Check all language outputs without inventing user acceptance')
    job_check.add_argument('output', type=Path)
    font_check = commands.add_parser('font-check', help='Verify Unicode glyph mappings in a real local font')
    font_check.add_argument('font', type=Path)
    font_check.add_argument('--text', required=True)
    example = commands.add_parser("demo", help="Create and render an authored synthetic example")
    example.add_argument("--output", type=Path, default=Path("work/demo"))
    fireplay_scan = commands.add_parser('fireplay-discover', help='Prepare image requirement queries or inspect saved connector pages')
    fireplay_scan.add_argument('--receipt', type=Path, help='Actual query and summary_pages responses from the host connector')
    fireplay_register = commands.add_parser('fireplay-register', help='Register exact image requirement summary and brief receipts')
    fireplay_register.add_argument('code')
    fireplay_register.add_argument('--snapshot', type=Path, required=True)
    fireplay_refresh = commands.add_parser('fireplay-refresh-source', help='Record a new URL for the same Fireplay source ID')
    fireplay_refresh.add_argument('code')
    fireplay_refresh.add_argument('receipt', type=Path)
    fireplay_refresh.add_argument('--image', type=int, default=1)
    fireplay_reconcile = commands.add_parser('fireplay-reconcile-upload', help='Verify the original R2 object for an uncertain PUT')
    fireplay_reconcile.add_argument('code')
    fireplay_reconcile.add_argument('operation_id')
    fireplay_reuse = commands.add_parser('fireplay-reuse', help='Find historical deliveries for the exact original bytes')
    fireplay_reuse.add_argument('code')
    fireplay_reuse.add_argument('--image', type=int, default=1, help='One-based original image index')
    fireplay_reuse.add_argument('--lookup', type=Path, help='Saved official image-search and resource responses')
    reuse_plan = commands.add_parser('fireplay-reuse-plan', help='Match historical output against the frozen delivery matrix')
    reuse_plan.add_argument('code')
    reuse_plan.add_argument('state', type=Path)
    reuse_plan.add_argument('--rework', action='store_true')
    reuse_plan.add_argument('--remote-image', type=int, help='Select one planned original for Fireplay fallback')
    reuse_plan.add_argument('--lookup', type=Path, help='Saved official search, resource and panel responses')
    reuse_stage = commands.add_parser('fireplay-reuse-stage', help='Stage verified historical bytes for current visual review')
    reuse_stage.add_argument('code')
    reuse_stage.add_argument('state', type=Path)
    reuse_stage.add_argument('source_id')
    reuse_stage.add_argument('language')
    reuse_stage.add_argument('member_id')
    reuse_stage.add_argument('material_id')
    reuse_stage.add_argument('--evidence', type=Path, required=True)
    commands.add_parser('knowledge-audit', help='Check curated knowledge and list evidence awaiting manual review')
    storage_preview = commands.add_parser('storage-preview', help='Inventory exact duplicate images and fonts')
    storage_preview.add_argument('--limit', type=int, default=20)
    storage_compact = commands.add_parser('storage-compact', help='Preview or clone closed demand fonts and work images')
    storage_compact.add_argument('--receipt', type=Path, help='Execute compaction and write a new audit file')
    storage_compact.add_argument('--images-only', action='store_true')
    storage_retention = commands.add_parser('storage-retention', help='Preview or apply verified Fireplay retention')
    storage_retention.add_argument('code', nargs='?')
    storage_retention.add_argument('--apply', action='store_true')
    storage_retention.add_argument('--archive-only', action='store_true')
    storage_hold = commands.add_parser('storage-hold', help='Protect a demand or restart retention after release')
    storage_hold.add_argument('code')
    storage_hold.add_argument('reason')
    storage_hold.add_argument('--release', action='store_true')
    storage_quarantine = commands.add_parser('storage-quarantine', help='Preview or apply quarantine recovery or expiry')
    storage_quarantine.add_argument('code')
    storage_quarantine.add_argument('--purge', action='store_true')
    storage_quarantine.add_argument('--apply', action='store_true')
    artifact_stage = commands.add_parser('artifact-stage', help='Stage or reuse an exact artifact at a canonical path')
    artifact_stage.add_argument('source', type=Path)
    artifact_stage.add_argument('destination', type=Path)
    scratch_stage = commands.add_parser('scratch-stage', help='Register a model edit once under the requirement scratch directory')
    scratch_stage.add_argument('code')
    scratch_stage.add_argument('source', type=Path)
    scratch_stage.add_argument('--provider', required=True)
    scratch_stage.add_argument('--operation', required=True)
    scratch_stage.add_argument('--member', action='append', required=True)
    scratch_preview = commands.add_parser('scratch-preview', help='Build or reuse a demand contact sheet')
    scratch_preview.add_argument('code')
    scratch_preview.add_argument('spec', type=Path)
    legacy_fonts = commands.add_parser('legacy-font-compact', help='Preview or clone identical legacy font snapshots')
    legacy_fonts.add_argument('project', type=Path)
    legacy_fonts.add_argument('--receipt', type=Path, help='Execute compaction and create this new audit file')
    timing = commands.add_parser('publication-timing', help='Summarize observed upload timings from local journals')
    timing.add_argument('project', type=Path, nargs='?', default=Path(__file__).resolve().parents[2])
    transition = commands.add_parser('fireplay-transition', help='Prepare or reconcile one Fireplay status change')
    transition.add_argument('code')
    transition.add_argument('action', choices=('start', 'finish'))
    transition.add_argument('snapshot', type=Path)
    transition.add_argument('--delivery-state', type=Path)
    transition_mode = transition.add_mutually_exclusive_group()
    transition_mode.add_argument('--prepare', action='store_true')
    transition_mode.add_argument('--reconcile', action='store_true')
    for name in ('fireplay-accept', 'fireplay-status', 'fireplay-download', 'fireplay-job'):
        command = commands.add_parser(name, help='Manage a locally accepted image requirement and verified source')
        command.add_argument('code')
        if name in ('fireplay-download', 'fireplay-job'):
            command.add_argument('--image', type=int, default=1, help='One-based source image index')
        if name == 'fireplay-job':
            command.add_argument('--output', type=Path, help='Override the default per-source scratch job path')
    review_desk = commands.add_parser('workbench', aliases=['review-desk'], help='Open the local image translation workbench')
    review_desk.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    review_desk.add_argument('--port', type=int, default=8765)
    review_desk.add_argument('--open', action='store_true', help='Open the desk in the default browser')
    review_desk.add_argument('--dsh-origin', help='Exact loopback HTTP origin allowed to embed the desk')
    for name in ('material-size-plan', 'material-size-validate'):
        command = commands.add_parser(name, help='Plan or read-only validate production dimensions and byte coverage')
        command.add_argument('spec', type=Path)
    for name in ('delivery-score', 'delivery-next', 'delivery-gate'):
        command = commands.add_parser(name, help='Read-only balanced delivery score or explicit member next actions')
        command.add_argument('spec', type=Path)
        if name == 'delivery-next':
            mode = command.add_mutually_exclusive_group()
            mode.add_argument('--full', action='store_true')
            mode.add_argument('--since', help='Checkpoint from a previously read compact delivery report')
    for name in ('record-stage', 'stage-summary'):
        command = commands.add_parser(name, help='Record or summarize observed stage timings and costs')
        command.add_argument('journal', type=Path)
        if name == 'record-stage':
            command.add_argument('event', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command in ('workbench', 'review-desk'):
            from .review_desk import serve
            serve(args.root, port=args.port, open_browser=args.open, dsh_origin=args.dsh_origin)
            return 0
        if args.command.startswith('fireplay-'):
            from . import fireplay
            from .pipeline import read_json
            root = Path(__file__).resolve().parents[2]
            if args.command == 'fireplay-discover':
                result = fireplay.discover(read_json(args.receipt) if args.receipt else None)
            elif args.command == 'fireplay-register':
                result = fireplay.register(args.code, read_json(args.snapshot), root)
            elif args.command == 'fireplay-accept':
                result = fireplay.accept(args.code, root)
            elif args.command == 'fireplay-refresh-source':
                result = fireplay.refresh_source(args.code, args.image, read_json(args.receipt), root)
            elif args.command == 'fireplay-reconcile-upload':
                from .fireplay_upload_reconcile import reconcile_upload
                result = reconcile_upload(args.code, args.operation_id, root)
            elif args.command == 'fireplay-reuse':
                from .fireplay_reuse import search_history
                result = search_history(root, args.code, args.image, read_json(args.lookup) if args.lookup else None)
            elif args.command == 'fireplay-reuse-plan':
                from .fireplay_reuse import reuse_plan
                result = reuse_plan(root, args.code, args.state, rework=args.rework,
                                    lookup=read_json(args.lookup) if args.lookup else None, remote_image=args.remote_image)
            elif args.command == 'fireplay-reuse-stage':
                from .fireplay_reuse import stage_reuse
                result = stage_reuse(root, args.code, args.state, args.source_id, args.language, args.member_id,
                                     args.material_id, read_json(args.evidence))
            elif args.command == 'fireplay-status':
                result = fireplay.status(args.code, root)
            elif args.command == 'fireplay-transition':
                from .fireplay_status_transition import status_transition
                result = status_transition(root, args.code, args.action, read_json(args.snapshot),
                                           prepare=args.prepare, reconcile=args.reconcile,
                                           delivery_state=args.delivery_state)
                if args.action == 'finish' and result.get('state') == 'confirmed':
                    from .storage_retention import maintain_storage
                    try:
                        storage = maintain_storage(root, code=args.code, apply=True, archive_only=True)
                        result['local_archive'] = next(iter(storage['demands']), {'state': 'missing_closeout_receipt'})
                    except (ValueError, OSError, KeyError, TypeError) as error:
                        result['local_archive'] = {'state': 'kept_for_review', 'reason': str(error)}
            else:
                from .fireplay_download import download_source, initialize_source_job
                result = (download_source(args.code, args.image, root)
                          if args.command == 'fireplay-download'
                          else initialize_source_job(args.code, args.image, args.output, root))
        elif args.command == 'storage-preview':
            from .storage_preview import preview_storage
            result = preview_storage(Path(__file__).resolve().parents[2], args.limit)
        elif args.command == 'storage-compact':
            from .legacy_storage import compact_legacy_fonts
            result = compact_legacy_fonts(Path(__file__).resolve().parents[2], args.receipt, current=True,
                                          images_only=args.images_only)
        elif args.command == 'storage-retention':
            from .storage_retention import maintain_storage
            result = maintain_storage(Path(__file__).resolve().parents[2], code=args.code, apply=args.apply,
                                      archive_only=args.archive_only)
        elif args.command == 'storage-hold':
            from .storage_retention import retention_hold
            result = retention_hold(Path(__file__).resolve().parents[2], args.code, args.reason, release=args.release)
        elif args.command == 'storage-quarantine':
            from .storage_retention import manage_quarantine
            result = manage_quarantine(Path(__file__).resolve().parents[2], args.code, purge=args.purge, apply=args.apply)
        elif args.command == 'artifact-stage':
            from .artifact_copy import stage_artifact
            result = stage_artifact(args.source, args.destination)
        elif args.command == 'scratch-stage':
            from .scratch_artifacts import stage_model_output
            result = stage_model_output(Path(__file__).resolve().parents[2], args.code, args.source,
                                        provider=args.provider, operation=args.operation, members=args.member)
        elif args.command == 'scratch-preview':
            from .scratch_artifacts import scratch_preview
            result = scratch_preview(Path(__file__).resolve().parents[2], args.code, args.spec)
        elif args.command == 'knowledge-audit':
            from .knowledge import audit_knowledge
            result = audit_knowledge(Path(__file__).resolve().parents[2])
        elif args.command == 'legacy-font-compact':
            from .legacy_storage import compact_legacy_fonts
            result = compact_legacy_fonts(args.project, args.receipt)
        elif args.command == 'publication-timing':
            from .publication_timing import publication_timing
            result = publication_timing(args.project)
        elif args.command in ('material-size-plan', 'material-size-validate'):
            from .material_sizes import plan_sizes, validate_sizes
            result = (plan_sizes if args.command == 'material-size-plan' else validate_sizes)(args.spec)
        elif args.command == 'delivery-score':
            from .delivery_quality import assess_delivery
            from .pipeline import read_json
            result = assess_delivery(read_json(args.spec), args.spec.resolve().parent)
        elif args.command in ('delivery-next', 'delivery-gate'):
            from .delivery_autopilot import next_delivery, publication_gate
            result = (next_delivery if args.command == 'delivery-next' else publication_gate)(args.spec)
            if args.command == 'delivery-next' and not args.full:
                from .delivery_autopilot import compact_delivery
                result = compact_delivery(result, args.spec, args.since)
        elif args.command in ('record-stage', 'stage-summary'):
            from .pipeline import read_json
            from .stage_events import record_stage, summarize_stages
            result = (record_stage(args.journal, read_json(args.event)) if args.command == 'record-stage'
                      else summarize_stages(args.journal))
        elif args.command == "doctor":
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
                from .check_records import record_check
                result = record_check('check-layered', args.output, check_layered)
        elif args.command in ('init-job', 'prepare-job', 'render-job', 'check-job'):
            from .jobs import initialize_job, prepare_job, render_job, check_job
            if args.command == 'init-job':
                result = initialize_job(args.source, [value.strip() for value in args.targets.split(',')], args.output)
            elif args.command == 'prepare-job':
                output, revisioned = args.output, args.revisioned
                if output is None:
                    author = args.job.resolve().parent
                    require(author.parent.name == 'jobs' and author.parent.parent.name == 'scratch',
                            'Specify --output for a job outside the standard scratch/jobs layout')
                    output, revisioned = author.parent.parent / 'prepared' / author.name, True
                if args.build_script is None:
                    result = prepare_job(args.job, output, reuse_background=args.reuse_background, revisioned=revisioned)
                else:
                    from subprocess import PIPE, run
                    from .fireplay import _lock

                    job = args.job.resolve(strict=True)
                    script = args.build_script.resolve(strict=True)
                    require(job.is_file() and script.is_file() and script.suffix == '.py',
                            'Expected a job file and a local Python build script')
                    with _lock(job.parent, '.build.lock'):
                        built = run([sys.executable, str(script), str(job)], stdout=PIPE,
                                    encoding='utf-8', errors='replace')
                        if built.stdout:
                            print(built.stdout, end='', file=sys.stderr)
                        require(built.returncode == 0,
                                f'Source build failed (exit {built.returncode}); preparation did not run')
                        result = prepare_job(job, output, reuse_background=args.reuse_background, revisioned=revisioned)
            elif args.command == 'render-job':
                output = args.output
                if output is None:
                    prepared = args.prepared.resolve()
                    slot = prepared.parent
                    require(not args.full_previews and slot.parent.name == 'prepared'
                            and slot.parent.parent.name == 'scratch',
                            'Specify --output for previews or a prepared job outside the standard layout')
                    output = slot.parent.parent / 'rendered' / slot.name / prepared.name
                result = render_job(args.prepared, output, full_previews=args.full_previews)
            else:
                from .check_records import record_check
                result = record_check('check-job', args.output, check_job)
        elif args.command in ('compare', 'benchmark'):
            from .evaluation import build_comparison, run_benchmark
            result = (build_comparison if args.command == 'compare' else run_benchmark)(args.spec, args.output)
        elif args.command == 'font-check':
            from .lettering import check_font_coverage
            result = check_font_coverage(args.font, args.text)
        elif args.command == "packet":
            result = packet(args.run, compact=not args.full, since=args.since)
        elif args.command == "validate":
            manifest = load_run(args.run)
            result = {"status": "valid", "advisories": advisory_checks(manifest), "note": "Contract validation does not certify translation or visual quality."}
        elif args.command == "render":
            result = render(args.run, args.clean_background)
        elif args.command == "review-packet":
            result = review_packet(args.run, compact=not args.full, since=args.since)
        else:
            result = quality(args.run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command in ('material-size-plan', 'material-size-validate') and result.get('status') in ('fail', 'unsupported'):
            return 1
        if args.command in ('quality', 'check-layered', 'check-job'):
            return {"pass": 0, "pending": 2, "fail": 1}[result["status"]]
        if args.command in ('render-layered', 'render-job') and result.get('status') == 'fail':
            return 1
        if args.command == 'benchmark' and result.get('technical_expectations') == 'fail':
            return 1
        if args.command == 'compare' and result.get('technical') == 'fail':
            return 1
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
