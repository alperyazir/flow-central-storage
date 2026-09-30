"""Update a published book's files in place from inside the API container.

Same code path as ``POST /books/{id}/update-files``, without the HTTP upload,
for books too large to send through the proxy. Dry run unless ``--apply``.

    docker cp Glory_Trio_3.zip <api-container>:/tmp/
    docker exec <api-container> python -m app.scripts.update_book_files \\
        --book-id 349 --zip /tmp/Glory_Trio_3.zip            # dry run
    docker exec <api-container> python -m app.scripts.update_book_files \\
        --book-id 349 --zip /tmp/Glory_Trio_3.zip --apply    # write
"""

from __future__ import annotations

import argparse
import json
import sys


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update a book's files without touching its AI data")
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument("--zip", dest="archive", required=True, help="Path to the book archive")
    parser.add_argument("--apply", action="store_true", help="Write the changes (default: dry run)")
    parser.add_argument("--prune", action="store_true", help="Delete stored files the archive no longer has")
    parser.add_argument("--bump-version", action="store_true", help="Bump content_version after writing")
    parser.add_argument("--notify", action="store_true", help="Fire book.updated after writing")
    parser.add_argument("--regenerate-bundles", action="store_true", help="Rebuild the standalone app bundles")
    parser.add_argument("--full", action="store_true", help="Print every file path, not just the counts")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    from app.routers.books import run_book_files_update
    from app.services.storage import UploadError

    try:
        report = run_book_files_update(
            args.book_id,
            args.archive,
            prune=args.prune,
            dry_run=not args.apply,
            bump_version=args.bump_version,
            notify=args.notify,
            regenerate_bundles=args.regenerate_bundles,
        )
    except (LookupError, UploadError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not args.full:
        for key in ("unchanged",):
            report[key] = f"<{len(report[key])} paths, --full to list>"
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
