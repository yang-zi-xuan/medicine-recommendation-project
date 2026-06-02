#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Combine pharmacopoeia and dayi.org.cn crawlers.

Order:
1. Query the pharmacopoeia site first.
2. Query dayi.org.cn only when there are still fillable blanks.
3. Never let later dayi values overwrite values filled by the pharmacopoeia step.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import importlib.util
import json
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_MINING_DIR = SCRIPT_DIR.parent
CHP_SCRIPT = DATA_MINING_DIR / "chp" / "fill_herb_info_from_chp.py"
DAYI_SCRIPT = DATA_MINING_DIR / "dayi" / "fill_herb_info_from_dayi.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHP = load_module("chp_crawler", CHP_SCRIPT)
DAYI = load_module("dayi_crawler", DAYI_SCRIPT)


def find_root_dir(start: Path) -> Path:
    for path in [start, *start.parents]:
        if (path / "data_csv" / "HERB_herb_info_v1.csv").exists():
            return path
    return start.parents[1]


ROOT_DIR = find_root_dir(SCRIPT_DIR)
DEFAULT_INPUT = ROOT_DIR / "data_csv" / "HERB_herb_info_v1.csv"
DEFAULT_OUTPUT = SCRIPT_DIR / "HERB_herb_info_v1_combined_filled.csv"
DEFAULT_LOG = SCRIPT_DIR / "combined_crawl_log.csv"
DEFAULT_UPDATED_TABLE = SCRIPT_DIR / "combined_updated_records.csv"
DEFAULT_NOT_UPDATED_TABLE = SCRIPT_DIR / "combined_not_updated_records.csv"
DEFAULT_CHP_CACHE_DIR = SCRIPT_DIR / "chp_cache"
DEFAULT_DAYI_CACHE_DIR = SCRIPT_DIR / "dayi_cache"

CHP_FILLABLE_COLUMNS = set(CHP.FILLABLE_COLUMNS)
DAYI_FILLABLE_COLUMNS = set(DAYI.FILLABLE_COLUMNS)
COMBINED_FILLABLE_COLUMNS = sorted(CHP_FILLABLE_COLUMNS | DAYI_FILLABLE_COLUMNS)

LOG_COLUMNS = [
    "Herb_ID",
    "Herb_cn_name",
    "chp_status",
    "chp_detail_url",
    "chp_filled_fields",
    "chp_message",
    "dayi_status",
    "dayi_detail_url",
    "dayi_filled_fields",
    "dayi_message",
]

UPDATED_COLUMNS = [
    "Herb_ID",
    "Herb_cn_name",
    "chp_detail_url",
    "dayi_detail_url",
    "chp_filled_fields",
    "dayi_filled_fields",
    "filled_values",
]

NOT_UPDATED_COLUMNS = [
    "Herb_ID",
    "Herb_cn_name",
    "chp_status",
    "dayi_status",
    "reason",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill herb CSV by querying pharmacopoeia first, then dayi.org.cn."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="source CSV path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="filled CSV path")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="crawl log CSV path")
    parser.add_argument(
        "--updated-table",
        type=Path,
        default=DEFAULT_UPDATED_TABLE,
        help="CSV path for rows that were actually updated",
    )
    parser.add_argument(
        "--not-updated-table",
        type=Path,
        default=DEFAULT_NOT_UPDATED_TABLE,
        help="CSV path for rows that received no updates",
    )
    parser.add_argument(
        "--chp-cache-dir",
        type=Path,
        default=DEFAULT_CHP_CACHE_DIR,
        help="combined-run cache directory for pharmacopoeia API responses",
    )
    parser.add_argument(
        "--dayi-cache-dir",
        type=Path,
        default=DEFAULT_DAYI_CACHE_DIR,
        help="combined-run cache directory for dayi HTML responses",
    )
    parser.add_argument("--limit", type=int, default=0, help="max herbs to crawl; 0 means all")
    parser.add_argument(
        "--names",
        default="",
        help="comma-separated Herb_cn_name values to crawl; useful for smoke tests",
    )
    parser.add_argument("--book-id", type=int, default=1, help="pharmacopoeia bookId")
    parser.add_argument("--chp-page-size", type=int, default=20, help="pharmacopoeia search page size")
    parser.add_argument(
        "--chp-search-pages", type=int, default=3, help="max pharmacopoeia search pages to inspect"
    )
    parser.add_argument(
        "--min-delay", type=float, default=1.5, help="minimum delay between network requests"
    )
    parser.add_argument(
        "--max-delay", type=float, default=4.0, help="maximum delay between network requests"
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="use a faster delay preset: 0.2-0.8 seconds between network requests",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="number of concurrent crawl threads; 1 is safest, 3-5 is usually reasonable",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout seconds")
    parser.add_argument("--retries", type=int, default=2, help="retry count after first failure")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="allow pharmacopoeia to overwrite original values; dayi still will not overwrite pharmacopoeia-filled values",
    )
    parser.add_argument(
        "--include-complete",
        action="store_true",
        help="also query rows whose combined fillable columns are already complete",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="ignore cached responses and refetch",
    )
    parser.add_argument("--no-progress", action="store_true", help="disable the terminal progress bar")
    parser.add_argument("--verbose", action="store_true", help="print one status line for every crawled herb")
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=0.5,
        help="minimum seconds between progress bar refreshes",
    )
    return parser.parse_args()


def is_blank(value: Optional[str]) -> bool:
    return value is None or value.strip() == "" or value.strip().upper() == "NA"


def normalize_text(value: str) -> str:
    return CHP.normalize_text(value)


def row_has_blank(row: Dict[str, str], columns: Iterable[str]) -> bool:
    return any(is_blank(row.get(column)) for column in columns)


def iter_target_rows(
    rows: List[Dict[str, str]],
    names: Iterable[str],
    limit: int,
    overwrite: bool,
    include_complete: bool,
) -> Iterable[Tuple[int, Dict[str, str]]]:
    wanted_names = {normalize_text(name) for name in names if normalize_text(name)}
    yielded = 0
    for index, row in enumerate(rows):
        herb_name = normalize_text(row.get("Herb_cn_name", ""))
        if not herb_name or herb_name.upper() == "NA":
            continue
        if wanted_names and herb_name not in wanted_names:
            continue
        if not overwrite and not include_complete and not row_has_blank(row, COMBINED_FILLABLE_COLUMNS):
            continue
        yield index, row
        yielded += 1
        if limit and yielded >= limit:
            break


def fill_row_without_overwriting_locked(
    row: Dict[str, str],
    fields: Dict[str, str],
    overwrite: bool,
    locked_columns: Set[str],
) -> List[str]:
    filled: List[str] = []
    for column, value in fields.items():
        if column in locked_columns or column not in row or not value:
            continue
        if overwrite or is_blank(row.get(column)):
            row[column] = value
            filled.append(column)
    return filled


def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = list(reader.fieldnames or [])
        return fieldnames, list(reader)


def write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_table(path: Path, fieldnames: List[str], records: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def needs_dayi_after_chp(row: Dict[str, str], chp_fields: Dict[str, str], overwrite: bool) -> bool:
    temp_row = dict(row)
    fill_row_without_overwriting_locked(temp_row, chp_fields, overwrite=overwrite, locked_columns=set())
    return row_has_blank(temp_row, DAYI_FILLABLE_COLUMNS)


def apply_combined_fields(
    row: Dict[str, str],
    chp_fields: Dict[str, str],
    dayi_fields: Dict[str, str],
    overwrite: bool,
) -> Tuple[List[str], List[str]]:
    chp_filled = fill_row_without_overwriting_locked(
        row, chp_fields, overwrite=overwrite, locked_columns=set()
    )
    dayi_filled = fill_row_without_overwriting_locked(
        row, dayi_fields, overwrite=overwrite, locked_columns=set(chp_filled)
    )
    return chp_filled, dayi_filled


def main() -> int:
    args = parse_args()
    if args.fast:
        args.min_delay = 0.2
        args.max_delay = 0.8
        args.progress_interval = min(args.progress_interval, 0.2)
    args.workers = max(1, args.workers)

    subset_run = bool(args.limit or args.names.strip())
    if subset_run and args.output == DEFAULT_OUTPUT:
        args.output = DEFAULT_OUTPUT.with_name(DEFAULT_OUTPUT.stem + "_sample.csv")
    if subset_run and args.log == DEFAULT_LOG:
        args.log = DEFAULT_LOG.with_name(DEFAULT_LOG.stem + "_sample.csv")
    if subset_run and args.updated_table == DEFAULT_UPDATED_TABLE:
        args.updated_table = DEFAULT_UPDATED_TABLE.with_name(
            DEFAULT_UPDATED_TABLE.stem + "_sample.csv"
        )
    if subset_run and args.not_updated_table == DEFAULT_NOT_UPDATED_TABLE:
        args.not_updated_table = DEFAULT_NOT_UPDATED_TABLE.with_name(
            DEFAULT_NOT_UPDATED_TABLE.stem + "_sample.csv"
        )

    fieldnames, rows = read_csv(args.input)
    names = [item.strip() for item in args.names.split(",") if item.strip()]
    target_rows = list(
        iter_target_rows(
            rows,
            names=names,
            limit=args.limit,
            overwrite=args.overwrite,
            include_complete=args.include_complete,
        )
    )
    skipped_complete = sum(
        1
        for row in rows
        if normalize_text(row.get("Herb_cn_name", ""))
        and normalize_text(row.get("Herb_cn_name", "")).upper() != "NA"
        and not row_has_blank(row, COMBINED_FILLABLE_COLUMNS)
    )

    thread_local = threading.local()

    def get_clients():
        chp_client = getattr(thread_local, "chp_client", None)
        dayi_client = getattr(thread_local, "dayi_client", None)
        if chp_client is None:
            chp_client = CHP.ChpClient(
                cache_dir=args.chp_cache_dir,
                min_delay=args.min_delay,
                max_delay=args.max_delay,
                timeout=args.timeout,
                retries=args.retries,
                force_refresh=args.force_refresh,
            )
            thread_local.chp_client = chp_client
        if dayi_client is None:
            dayi_client = DAYI.DayiClient(
                cache_dir=args.dayi_cache_dir,
                min_delay=args.min_delay,
                max_delay=args.max_delay,
                timeout=args.timeout,
                retries=args.retries,
                force_refresh=args.force_refresh,
            )
            thread_local.dayi_client = dayi_client
        return chp_client, dayi_client

    def crawl_target(
        target: Tuple[int, Dict[str, str]]
    ) -> Tuple[
        int,
        Dict[str, str],
        Dict[str, str],
        str,
        str,
        str,
        Dict[str, str],
        str,
        str,
        str,
    ]:
        row_index, row = target
        chp_client, dayi_client = get_clients()
        chp_fields: Dict[str, str] = {}
        dayi_fields: Dict[str, str] = {}
        chp_detail_url = dayi_detail_url = ""
        chp_status = dayi_status = "skipped"
        chp_message = dayi_message = ""

        if row_has_blank(row, CHP_FILLABLE_COLUMNS) or args.overwrite or args.include_complete:
            try:
                chp_fields, chp_detail_url, chp_status, chp_message = CHP.crawl_one(
                    chp_client,
                    row,
                    book_id=args.book_id,
                    page_size=args.chp_page_size,
                    search_pages=args.chp_search_pages,
                )
            except Exception as exc:
                chp_status = "error"
                chp_message = f"{type(exc).__name__}: {exc}"
        else:
            chp_status = "skipped_no_chp_blank_fields"

        if needs_dayi_after_chp(row, chp_fields, overwrite=args.overwrite):
            try:
                dayi_fields, dayi_detail_url, dayi_status, dayi_message = DAYI.crawl_one(
                    dayi_client, row
                )
            except Exception as exc:
                dayi_status = "error"
                dayi_message = f"{type(exc).__name__}: {exc}"
        else:
            dayi_status = "skipped_no_remaining_blank_fields"

        return (
            row_index,
            row,
            chp_fields,
            chp_detail_url,
            chp_status,
            chp_message,
            dayi_fields,
            dayi_detail_url,
            dayi_status,
            dayi_message,
        )

    progress = CHP.ProgressPrinter(
        total=len(target_rows),
        enabled=not args.no_progress and not args.verbose,
        refresh_interval=args.progress_interval,
    )
    log_records: List[Dict[str, str]] = []
    updated_records: List[Dict[str, str]] = []
    not_updated_records: List[Dict[str, str]] = []
    total = matched = filled_count = 0

    def handle_result(
        completed: int,
        row: Dict[str, str],
        chp_fields: Dict[str, str],
        chp_detail_url: str,
        chp_status: str,
        chp_message: str,
        dayi_fields: Dict[str, str],
        dayi_detail_url: str,
        dayi_status: str,
        dayi_message: str,
    ) -> None:
        nonlocal total, matched, filled_count
        total += 1
        herb_id = row.get("Herb_ID", "")
        herb_name = normalize_text(row.get("Herb_cn_name", ""))
        chp_filled, dayi_filled = apply_combined_fields(
            row, chp_fields, dayi_fields, overwrite=args.overwrite
        )
        row_filled = chp_filled + dayi_filled
        if chp_status == "ok" or dayi_status == "ok":
            matched += 1
        filled_count += len(row_filled)

        if chp_status == "ok" and not chp_filled:
            chp_message = chp_message or "matched page but chp target columns already had values"
        if dayi_status == "ok" and not dayi_filled:
            dayi_message = dayi_message or "matched page but remaining dayi target columns already had values"

        log_records.append(
            {
                "Herb_ID": herb_id,
                "Herb_cn_name": herb_name,
                "chp_status": chp_status,
                "chp_detail_url": chp_detail_url,
                "chp_filled_fields": "|".join(chp_filled),
                "chp_message": chp_message,
                "dayi_status": dayi_status,
                "dayi_detail_url": dayi_detail_url,
                "dayi_filled_fields": "|".join(dayi_filled),
                "dayi_message": dayi_message,
            }
        )
        if row_filled:
            updated_records.append(
                {
                    "Herb_ID": herb_id,
                    "Herb_cn_name": herb_name,
                    "chp_detail_url": chp_detail_url,
                    "dayi_detail_url": dayi_detail_url,
                    "chp_filled_fields": "|".join(chp_filled),
                    "dayi_filled_fields": "|".join(dayi_filled),
                    "filled_values": json.dumps(
                        {field: row.get(field, "") for field in row_filled},
                        ensure_ascii=False,
                    ),
                }
            )
        else:
            not_updated_records.append(
                {
                    "Herb_ID": herb_id,
                    "Herb_cn_name": herb_name,
                    "chp_status": chp_status,
                    "dayi_status": dayi_status,
                    "reason": "; ".join(
                        item
                        for item in [
                            f"chp: {chp_message or chp_status}",
                            f"dayi: {dayi_message or dayi_status}",
                        ]
                        if item
                    ),
                }
            )

        status = f"chp={chp_status},dayi={dayi_status}"
        if args.verbose:
            print(
                f"[{completed}/{len(target_rows)}] {herb_id} {herb_name}: {status}; "
                f"filled={','.join(row_filled) if row_filled else '-'}"
            )
        else:
            progress.update(
                done=completed,
                matched=matched,
                filled_cells=filled_count,
                herb_name=herb_name,
                status=status,
            )

    completed = 0
    if args.workers == 1:
        for target in target_rows:
            (
                _,
                row,
                chp_fields,
                chp_detail_url,
                chp_status,
                chp_message,
                dayi_fields,
                dayi_detail_url,
                dayi_status,
                dayi_message,
            ) = crawl_target(target)
            completed += 1
            handle_result(
                completed,
                row,
                chp_fields,
                chp_detail_url,
                chp_status,
                chp_message,
                dayi_fields,
                dayi_detail_url,
                dayi_status,
                dayi_message,
            )
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_map = {
                executor.submit(crawl_target, target): target[0] for target in target_rows
            }
            for future in concurrent.futures.as_completed(future_map):
                (
                    _,
                    row,
                    chp_fields,
                    chp_detail_url,
                    chp_status,
                    chp_message,
                    dayi_fields,
                    dayi_detail_url,
                    dayi_status,
                    dayi_message,
                ) = future.result()
                completed += 1
                handle_result(
                    completed,
                    row,
                    chp_fields,
                    chp_detail_url,
                    chp_status,
                    chp_message,
                    dayi_fields,
                    dayi_detail_url,
                    dayi_status,
                    dayi_message,
                )

    progress.finish()
    write_csv(args.output, fieldnames, rows)
    write_table(args.log, LOG_COLUMNS, log_records)
    write_table(args.updated_table, UPDATED_COLUMNS, updated_records)
    write_table(args.not_updated_table, NOT_UPDATED_COLUMNS, not_updated_records)
    print(
        f"Done. crawled={total}, matched={matched}, filled_cells={filled_count}, "
        f"updated_rows={len(updated_records)}, not_updated_rows={len(not_updated_records)}, "
        f"skipped_complete_rows={skipped_complete if not args.include_complete and not args.overwrite else 0}, "
        f"output={args.output}, log={args.log}, "
        f"updated_table={args.updated_table}, not_updated_table={args.not_updated_table}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
