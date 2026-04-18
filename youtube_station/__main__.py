from __future__ import annotations

import argparse
import json
import time

from .config import load_config
from .runner import StationRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Autonomous AI YouTube streaming station")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("plan", "render", "run"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--config", required=True)
        sub.add_argument("--count", type=int, default=1)
        sub.add_argument("--dry-run", action="store_true")
        sub.add_argument("--once", action="store_true")
        sub.add_argument("--stream", action="store_true")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    runner = StationRunner(config=config)

    if args.command == "plan":
        segments = runner.planner.plan_segments(count=args.count)
        print(json.dumps([segment.to_dict() for segment in segments], indent=2))
        return

    if args.command == "render":
        result = runner.run_once(dry_run=args.dry_run, publish=False)
        print(json.dumps({"video_path": str(result.video_path), "metadata_path": str(result.metadata_path)}, indent=2))
        return

    if args.command == "run":
        while True:
            result = runner.run_once(dry_run=args.dry_run, publish=args.stream)
            print(json.dumps({"video_path": str(result.video_path), "metadata_path": str(result.metadata_path)}, indent=2))
            if args.once:
                return
            time.sleep(max(config.generation.segment_duration_seconds, 5))


if __name__ == "__main__":
    main()
