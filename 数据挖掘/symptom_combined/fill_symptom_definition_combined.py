#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fill missing Symptom_definition values by combining baike.duguji.cn and dayi.org.cn.

Default order:
1. Query baike.duguji.cn first.
2. Query dayi.org.cn only when the first source did not provide a usable definition.
3. Never let a later source overwrite a value filled by an earlier source.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import importlib.util
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_MINING_DIR = SCRIPT_DIR.parent
DAYI_SCRIPT = DATA_MINING_DIR / "symptom_dayi" / "fill_symptom_definition_from_dayi.py"
DUGUJI_SCRIPT = DATA_MINING_DIR / "symptom_duguji" / "fill_symptom_definition_from_duguji.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DAYI = load_module("symptom_dayi_crawler", DAYI_SCRIPT)
DUGUJI = load_module("symptom_duguji_crawler", DUGUJI_SCRIPT)


def find_root_dir(start: Path) -> Path:
    for path in [start, *start.parents]:
        if (path / "data_csv" / "Symptom_Properties.xlsx").exists():
            return path
    return start.parents[1]


ROOT_DIR = find_root_dir(SCRIPT_DIR)
DEFAULT_INPUT = ROOT_DIR / "data_csv" / "Symptom_Properties.xlsx"
DEFAULT_OUTPUT = SCRIPT_DIR / "Symptom_Properties_combined_filled.csv"
DEFAULT_LOG = SCRIPT_DIR / "symptom_combined_crawl_log.csv"
DEFAULT_UPDATED_TABLE = SCRIPT_DIR / "symptom_combined_updated_records.csv"
DEFAULT_NOT_UPDATED_TABLE = SCRIPT_DIR / "symptom_combined_not_updated_records.csv"
DEFAULT_DAYI_CACHE_DIR = SCRIPT_DIR / "dayi_cache"
DEFAULT_DUGUJI_CACHE_DIR = SCRIPT_DIR / "duguji_cache"

SOURCE_NAMES = ("duguji", "dayi")

LOG_COLUMNS = [
    "TCM_symptom_id",
    "TCM_symptom_name",
    "first_source",
    "first_status",
    "first_detail_url",
    "first_message",
    "second_source",
    "second_status",
    "second_detail_url",
    "second_message",
    "updated",
    "updated_source",
]

UPDATED_COLUMNS = [
    "TCM_symptom_id",
    "TCM_symptom_name",
    "source",
    "detail_url",
    "old_definition",
    "new_definition",
]

NOT_UPDATED_COLUMNS = [
    "TCM_symptom_id",
    "TCM_symptom_name",
    "first_source",
    "first_status",
    "second_source",
    "second_status",
    "reason",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill Symptom_definition by querying one symptom site first, then the other."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="source xlsx path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="filled CSV path")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="crawl log CSV path")
    parser.add_argument("--updated-table", type=Path, default=DEFAULT_UPDATED_TABLE)
    parser.add_argument("--not-updated-table", type=Path, default=DEFAULT_NOT_UPDATED_TABLE)
    parser.add_argument("--dayi-cache-dir", type=Path, default=DEFAULT_DAYI_CACHE_DIR)
    parser.add_argument("--duguji-cache-dir", type=Path, default=DEFAULT_DUGUJI_CACHE_DIR)
    parser.add_argument("--limit", type=int, default=0, help="max symptoms to crawl; 0 means all")
    parser.add_argument("--names", default="", help="comma-separated TCM_symptom_name values")
    parser.add_argument(
        "--source-order",
        choices=("duguji-dayi", "dayi-duguji"),
        default="duguji-dayi",
        help="query order; later source never overwrites earlier source",
    )
    parser.add_argument("--min-delay", type=float, default=1.5)
    parser.add_argument("--max-delay", type=float, default=4.0)
    parser.add_argument("--fast", action="store_true", help="use 0.2-0.8 seconds delay")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--overwrite", action="store_true", help="allow first successful source to overwrite existing values")
    parser.add_argument("--include-complete", action="store_true", help="also query rows whose definition is already complete")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--progress-interval", type=float, default=0.5)
    return parser.parse_args()


def normalize_text(value: str) -> str:
    return DAYI.normalize_text(value)


def is_blank(value: Optional[str]) -> bool:
    return DAYI.is_blank(value)


def write_table(path: Path, fieldnames: List[str], records: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def source_order(value: str) -> Tuple[str, str]:
    first, second = value.split("-", 1)
    return first, second


def iter_target_records(
    table,
    names: Iterable[str],
    limit: int,
    overwrite: bool,
    include_complete: bool,
):
    wanted_names = {normalize_text(name) for name in names if normalize_text(name)}
    yielded = 0
    for row_num, row, record in table.iter_data_rows():
        symptom_name = normalize_text(record.get("TCM_symptom_name", ""))
        definition = normalize_text(record.get("Symptom_definition", ""))
        if not symptom_name:
            continue
        if wanted_names and symptom_name not in wanted_names:
            continue
        if not overwrite and not include_complete and not is_blank(definition):
            continue
        yield row_num, row, record
        yielded += 1
        if limit and yielded >= limit:
            break


def make_client(source: str, args: argparse.Namespace):
    if source == "dayi":
        return DAYI.DayiClient(
            cache_dir=args.dayi_cache_dir,
            min_delay=args.min_delay,
            max_delay=args.max_delay,
            timeout=args.timeout,
            retries=args.retries,
            force_refresh=args.force_refresh,
        )
    if source == "duguji":
        return DUGUJI.DugujiClient(
            cache_dir=args.duguji_cache_dir,
            min_delay=args.min_delay,
            max_delay=args.max_delay,
            timeout=args.timeout,
            retries=args.retries,
            force_refresh=args.force_refresh,
        )
    raise ValueError(f"unknown source: {source}")


def crawl_source(source: str, client, symptom_name: str) -> Tuple[str, str, str, str]:
    if source == "dayi":
        return DAYI.crawl_one(client, symptom_name)
    if source == "duguji":
        return DUGUJI.crawl_one(client, symptom_name)
    raise ValueError(f"unknown source: {source}")


def should_accept_definition(definition: str, old_definition: str, overwrite: bool) -> bool:
    return bool(definition) and (overwrite or is_blank(old_definition))


def format_source_status(source: str, status: str) -> str:
    return f"{source}:{status}" if source else "skipped"


def classify_exception(exc: Exception) -> Tuple[str, str]:
    message = f"{type(exc).__name__}: {exc}"
    if any(code in message for code in ["HTTP Error 468", "HTTP Error 429", "HTTP Error 403"]):
        return "blocked", message
    if "HTTP Error 404" in message:
        return "not_found", message
    return "error", message


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
        args.updated_table = DEFAULT_UPDATED_TABLE.with_name(DEFAULT_UPDATED_TABLE.stem + "_sample.csv")
    if subset_run and args.not_updated_table == DEFAULT_NOT_UPDATED_TABLE:
        args.not_updated_table = DEFAULT_NOT_UPDATED_TABLE.with_name(
            DEFAULT_NOT_UPDATED_TABLE.stem + "_sample.csv"
        )

    table = DAYI.XlsxTable(args.input)
    required_headers = {"TCM_symptom_id", "TCM_symptom_name", "Symptom_definition"}
    missing_headers = required_headers - set(table.headers)
    if missing_headers:
        raise RuntimeError(f"missing required xlsx columns: {sorted(missing_headers)}")

    names = [item.strip() for item in args.names.split(",") if item.strip()]
    target_records = list(
        iter_target_records(
            table,
            names=names,
            limit=args.limit,
            overwrite=args.overwrite,
            include_complete=args.include_complete,
        )
    )
    skipped_complete = sum(
        1
        for _, _, record in table.iter_data_rows()
        if normalize_text(record.get("TCM_symptom_name", ""))
        and not is_blank(normalize_text(record.get("Symptom_definition", "")))
    )

    first_source, second_source = source_order(args.source_order)
    thread_local = threading.local()

    def get_client(source: str):
        attr = f"{source}_client"
        client = getattr(thread_local, attr, None)
        if client is None:
            client = make_client(source, args)
            setattr(thread_local, attr, client)
        return client

    def crawl_target(target):
        _, row, record = target
        symptom_name = normalize_text(record.get("TCM_symptom_name", ""))

        first_definition = first_detail_url = first_message = ""
        second_definition = second_detail_url = second_message = ""
        first_status = second_status = "skipped"

        try:
            first_definition, first_detail_url, first_status, first_message = crawl_source(
                first_source, get_client(first_source), symptom_name
            )
        except Exception as exc:
            first_status, first_message = classify_exception(exc)

        if first_status == "ok" and first_definition:
            return (
                row,
                record,
                first_source,
                first_definition,
                first_detail_url,
                first_status,
                first_message,
                "",
                "",
                "",
                "skipped_first_source_success",
                "",
            )

        try:
            second_definition, second_detail_url, second_status, second_message = crawl_source(
                second_source, get_client(second_source), symptom_name
            )
        except Exception as exc:
            second_status, second_message = classify_exception(exc)

        return (
            row,
            record,
            first_source,
            first_definition,
            first_detail_url,
            first_status,
            first_message,
            second_source,
            second_definition,
            second_detail_url,
            second_status,
            second_message,
        )

    progress = DAYI.ProgressPrinter(
        total=len(target_records),
        enabled=not args.no_progress and not args.verbose,
        refresh_interval=args.progress_interval,
    )
    log_records: List[Dict[str, str]] = []
    updated_records: List[Dict[str, str]] = []
    not_updated_records: List[Dict[str, str]] = []
    completed = updated_count = matched_count = 0
    def_col = table.header_to_col["Symptom_definition"]

    def handle_result(
        row,
        record,
        first_source_name: str,
        first_definition: str,
        first_detail_url: str,
        first_status: str,
        first_message: str,
        second_source_name: str,
        second_definition: str,
        second_detail_url: str,
        second_status: str,
        second_message: str,
    ) -> None:
        nonlocal completed, updated_count, matched_count
        completed += 1
        symptom_id = normalize_text(record.get("TCM_symptom_id", ""))
        symptom_name = normalize_text(record.get("TCM_symptom_name", ""))
        old_definition = normalize_text(record.get("Symptom_definition", ""))

        chosen_source = ""
        chosen_definition = ""
        chosen_detail_url = ""
        if first_status == "ok" and first_definition:
            matched_count += 1
            chosen_source = first_source_name
            chosen_definition = first_definition
            chosen_detail_url = first_detail_url
        elif second_status == "ok" and second_definition:
            matched_count += 1
            chosen_source = second_source_name
            chosen_definition = second_definition
            chosen_detail_url = second_detail_url

        updated = should_accept_definition(chosen_definition, old_definition, args.overwrite)
        if updated:
            table.set_cell_text(row, def_col, chosen_definition)
            updated_count += 1
            updated_records.append(
                {
                    "TCM_symptom_id": symptom_id,
                    "TCM_symptom_name": symptom_name,
                    "source": chosen_source,
                    "detail_url": chosen_detail_url,
                    "old_definition": old_definition,
                    "new_definition": chosen_definition,
                }
            )
        else:
            if chosen_definition and not args.overwrite and not is_blank(old_definition):
                reason = "matched definition but Symptom_definition already had value"
            else:
                reason = "; ".join(
                    item
                    for item in [
                        f"{first_source_name}: {first_message or first_status}",
                        f"{second_source_name}: {second_message or second_status}"
                        if second_source_name
                        else "",
                    ]
                    if item
                )
            not_updated_records.append(
                {
                    "TCM_symptom_id": symptom_id,
                    "TCM_symptom_name": symptom_name,
                    "first_source": first_source_name,
                    "first_status": first_status,
                    "second_source": second_source_name,
                    "second_status": second_status,
                    "reason": reason,
                }
            )

        log_records.append(
            {
                "TCM_symptom_id": symptom_id,
                "TCM_symptom_name": symptom_name,
                "first_source": first_source_name,
                "first_status": first_status,
                "first_detail_url": first_detail_url,
                "first_message": first_message,
                "second_source": second_source_name,
                "second_status": second_status,
                "second_detail_url": second_detail_url,
                "second_message": second_message,
                "updated": "yes" if updated else "no",
                "updated_source": chosen_source if updated else "",
            }
        )

        status = ",".join(
            item
            for item in [
                format_source_status(first_source_name, first_status),
                format_source_status(second_source_name, second_status) if second_source_name else "",
            ]
            if item
        )
        if args.verbose:
            print(
                f"[{completed}/{len(target_records)}] {symptom_id} {symptom_name}: "
                f"{status}; updated={'yes' if updated else 'no'}"
            )
        else:
            progress.update(completed, updated_count, symptom_name, status)

    if args.workers == 1:
        for target in target_records:
            handle_result(*crawl_target(target))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_map = {executor.submit(crawl_target, target): target[0] for target in target_records}
            for future in concurrent.futures.as_completed(future_map):
                handle_result(*future.result())

    progress.finish()
    DAYI.write_xlsx_table_csv(args.output, table)
    write_table(args.log, LOG_COLUMNS, log_records)
    write_table(args.updated_table, UPDATED_COLUMNS, updated_records)
    write_table(args.not_updated_table, NOT_UPDATED_COLUMNS, not_updated_records)
    print(
        f"Done. crawled={completed}, matched={matched_count}, updated={updated_count}, "
        f"not_updated={len(not_updated_records)}, "
        f"skipped_complete_rows={skipped_complete if not args.include_complete and not args.overwrite else 0}, "
        f"order={args.source_order}, output={args.output}, log={args.log}, "
        f"updated_table={args.updated_table}, not_updated_table={args.not_updated_table}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
