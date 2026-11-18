"""Command line: python3 -m pipeline <command> ...

Exit codes: 0 success, 2 invalid input, 3 pipeline failure, 130 cancelled.
"""
import argparse
import json
import signal
import sys

from .attempt import Attempt, create_attempt
from .bundle import compare_attempts, finalize
from .common import REPO_ROOT, PipelineError
from .job import load_job

_INPUT_ERRORS = {"invalid_job", "job_not_found", "invalid_attempt_id", "attempt_exists"}


def _print(value):
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


def _sigterm(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m pipeline", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate-job", help="validate a job file and print the normalized job")
    p.add_argument("job")
    p = sub.add_parser("prepare", help="create a new attempt directory ready for rendering")
    p.add_argument("job")
    p.add_argument("--attempt-id")
    p.add_argument("--root", default=str(REPO_ROOT), help="project root (default: this repository)")
    p = sub.add_parser("render-synthetic", help="render frames with the synthetic stand-in renderer")
    p.add_argument("attempt")
    p = sub.add_parser("finalize", help="validate, encode, check and publish a rendered attempt")
    p.add_argument("attempt")
    p.add_argument("--ffmpeg", default="ffmpeg")
    p.add_argument("--ffprobe", default="ffprobe")
    p = sub.add_parser("cancel", help="mark a non-terminal attempt failed")
    p.add_argument("attempt")
    p.add_argument("--reason", default="cancelled by user")
    p = sub.add_parser("status", help="print an attempt's state record")
    p.add_argument("attempt")
    p = sub.add_parser("compare", help="compare two completed attempts")
    p.add_argument("first")
    p.add_argument("second")
    p = sub.add_parser("verify-index", help="check the debug frame-index strip of every frame")
    p.add_argument("attempt")
    p = sub.add_parser("verify-chart", help="check the debug chart values in frame 0")
    p.add_argument("attempt")
    args = parser.parse_args(argv)

    signal.signal(signal.SIGTERM, _sigterm)
    try:
        if args.command == "validate-job":
            _print(load_job(args.job))
        elif args.command == "prepare":
            attempt = create_attempt(load_job(args.job), args.root, args.attempt_id)
            _print({"attempt": str(attempt.path), "state": attempt.state()["state"]})
        elif args.command == "render-synthetic":
            from .synthetic import render
            _print({"attempt": args.attempt, "frames_written": render(args.attempt)})
        elif args.command == "finalize":
            result = finalize(args.attempt, args.ffmpeg, args.ffprobe)
            _print({"bundle": result["bundle"], "video_sha256": result["manifest"]["video"]["sha256"],
                    "loop_seam": result["manifest"]["validation"]["loop_seam"]["verdict"],
                    "not_verified": result["manifest"]["not_verified"]})
        elif args.command == "cancel":
            changed = Attempt(args.attempt).fail("cancelled", args.reason)
            _print({"attempt": args.attempt, "changed": changed,
                    "state": Attempt(args.attempt).state()["state"]})
        elif args.command == "status":
            _print(Attempt(args.attempt).state())
        elif args.command == "compare":
            _print(compare_attempts(args.first, args.second))
        elif args.command in ("verify-index", "verify-chart"):
            from .verify import verify_chart, verify_index
            result = (verify_index if args.command == "verify-index" else verify_chart)(args.attempt)
            _print(result)
            return 0 if result["ok"] else 3
    except KeyboardInterrupt:
        print("error: cancelled", file=sys.stderr)
        return 130
    except PipelineError as error:
        print(f"error: {error}", file=sys.stderr)
        if error.code == "cancelled":
            return 130
        return 2 if error.code in _INPUT_ERRORS else 3
    return 0
