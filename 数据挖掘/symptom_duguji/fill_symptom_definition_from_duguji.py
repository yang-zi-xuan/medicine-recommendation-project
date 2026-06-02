#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fill missing Symptom_definition values in Symptom_Properties.xlsx from baike.duguji.cn.

The site exposes MediaWiki raw text with ?action=raw, which is more stable than
parsing rendered HTML pages.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import gzip
import hashlib
import html
import http.cookiejar
import importlib.util
import random
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_MINING_DIR = SCRIPT_DIR.parent
DAYI_SYMPTOM_SCRIPT = DATA_MINING_DIR / "symptom_dayi" / "fill_symptom_definition_from_dayi.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMMON = load_module("symptom_dayi_common", DAYI_SYMPTOM_SCRIPT)


def find_root_dir(start: Path) -> Path:
    for path in [start, *start.parents]:
        if (path / "data_csv" / "Symptom_Properties.xlsx").exists():
            return path
    return start.parents[1]


ROOT_DIR = find_root_dir(SCRIPT_DIR)
DEFAULT_INPUT = ROOT_DIR / "data_csv" / "Symptom_Properties.xlsx"
DEFAULT_OUTPUT = SCRIPT_DIR / "Symptom_Properties_duguji_filled.csv"
DEFAULT_LOG = SCRIPT_DIR / "symptom_duguji_crawl_log.csv"
DEFAULT_UPDATED_TABLE = SCRIPT_DIR / "symptom_duguji_updated_records.csv"
DEFAULT_NOT_UPDATED_TABLE = SCRIPT_DIR / "symptom_duguji_not_updated_records.csv"
DEFAULT_CACHE_DIR = SCRIPT_DIR / "symptom_duguji_cache"

BASE_URL = "https://baike.duguji.cn"
RAW_URL = BASE_URL + "/baike/w/{title}?action=raw"
PAGE_URL = BASE_URL + "/baike/w/{title}"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

LOG_COLUMNS = [
    "TCM_symptom_id",
    "TCM_symptom_name",
    "status",
    "detail_url",
    "updated",
    "message",
]

UPDATED_COLUMNS = [
    "TCM_symptom_id",
    "TCM_symptom_name",
    "detail_url",
    "old_definition",
    "new_definition",
]

NOT_UPDATED_COLUMNS = [
    "TCM_symptom_id",
    "TCM_symptom_name",
    "status",
    "detail_url",
    "reason",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill Symptom_definition in Symptom_Properties.xlsx from baike.duguji.cn."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="source xlsx path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="filled CSV path")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="crawl log CSV path")
    parser.add_argument("--updated-table", type=Path, default=DEFAULT_UPDATED_TABLE)
    parser.add_argument("--not-updated-table", type=Path, default=DEFAULT_NOT_UPDATED_TABLE)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="raw page cache directory")
    parser.add_argument("--limit", type=int, default=0, help="max symptoms to crawl; 0 means all")
    parser.add_argument("--names", default="", help="comma-separated TCM_symptom_name values")
    parser.add_argument("--min-delay", type=float, default=1.0)
    parser.add_argument("--max-delay", type=float, default=3.0)
    parser.add_argument("--fast", action="store_true", help="use 0.2-0.8 seconds delay")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--include-complete", action="store_true")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--progress-interval", type=float, default=0.5)
    return parser.parse_args()


def normalize_text(value: str) -> str:
    return COMMON.normalize_text(value)


def is_blank(value: Optional[str]) -> bool:
    return COMMON.is_blank(value)


def strip_wiki_markup(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"\{\{[^{}]*\}\}", "", value)
    value = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", value)
    value = re.sub(r"'{2,5}", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    return normalize_text(value)


def clean_raw_lines(raw_text: str) -> List[str]:
    lines: List[str] = []
    for raw_line in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = strip_wiki_markup(raw_line)
        if not line:
            continue
        if line.startswith("#REDIRECT") or line.startswith("分类:"):
            continue
        lines.append(line)
    return lines


def extract_definition(raw_text: str, symptom_name: str) -> str:
    lines = clean_raw_lines(raw_text)
    if not lines:
        return ""

    headings = {f"什么是{symptom_name}", f"{symptom_name}是什么", "基本概述", "概述", "简介"}
    for line in lines[:5]:
        if line == symptom_name:
            continue
        if line.startswith(symptom_name) and len(line) >= len(symptom_name) + 8:
            return line

    for idx, line in enumerate(lines):
        if line in headings:
            for candidate in lines[idx + 1 :]:
                if candidate in headings or len(candidate) < 8:
                    continue
                if symptom_name in candidate or "症状" in candidate or "指" in candidate:
                    return candidate

    for line in lines:
        if line == symptom_name:
            continue
        if line.startswith(symptom_name) and len(line) >= len(symptom_name) + 8:
            return line

    for line in lines:
        if line not in headings and line != symptom_name and len(line) >= 12:
            return line
    return ""


class DugujiClient:
    def __init__(
        self,
        cache_dir: Path,
        min_delay: float,
        max_delay: float,
        timeout: float,
        retries: int,
        force_refresh: bool,
    ) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_delay = max(0.0, min_delay)
        self.max_delay = max(self.min_delay, max_delay)
        self.timeout = timeout
        self.retries = retries
        self.force_refresh = force_refresh
        self.last_request_at = 0.0
        self.user_agent = random.choice(USER_AGENTS)
        cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.txt"

    def _headers(self, referer: str) -> Dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/plain,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Referer": referer,
        }

    def _sleep_if_needed(self) -> None:
        if self.max_delay <= 0:
            return
        elapsed = time.time() - self.last_request_at
        wait_for = random.uniform(self.min_delay, self.max_delay)
        if elapsed < wait_for:
            time.sleep(wait_for - elapsed)

    @staticmethod
    def _decode_response(data: bytes, encoding: Optional[str]) -> str:
        if encoding == "gzip":
            data = gzip.decompress(data)
        elif encoding == "deflate":
            try:
                data = zlib.decompress(data)
            except zlib.error:
                data = zlib.decompress(data, -zlib.MAX_WBITS)
        return data.decode("utf-8", errors="replace")

    def get(self, url: str, referer: str) -> Tuple[str, bool]:
        cache_path = self._cache_path(url)
        if cache_path.exists() and not self.force_refresh:
            return cache_path.read_text(encoding="utf-8", errors="replace"), True

        last_error = ""
        for attempt in range(self.retries + 1):
            self._sleep_if_needed()
            req = urllib.request.Request(url, headers=self._headers(referer))
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    text = self._decode_response(response.read(), response.headers.get("Content-Encoding"))
                self.last_request_at = time.time()
                tmp_path = cache_path.with_suffix(f".{threading.get_ident()}.tmp")
                tmp_path.write_text(text, encoding="utf-8")
                tmp_path.replace(cache_path)
                return text, False
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
                self.last_request_at = time.time()
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.retries:
                    time.sleep((2**attempt) + random.uniform(0.5, 1.5))
        raise RuntimeError(last_error)


def crawl_one(client: DugujiClient, symptom_name: str) -> Tuple[str, str, str, str]:
    encoded = urllib.parse.quote(symptom_name, safe="")
    raw_url = RAW_URL.format(title=encoded)
    page_url = PAGE_URL.format(title=encoded)
    try:
        raw_text, _ = client.get(raw_url, referer=BASE_URL + "/baike/")
    except RuntimeError as exc:
        message = str(exc)
        if "HTTP Error 404" in message or "HTTPError: HTTP Error 404" in message:
            return "", page_url, "not_found", "HTTP 404: page does not exist"
        raise

    if "您可以新建这个页面" in raw_text or "没有此页面" in raw_text:
        return "", page_url, "not_found", "page does not exist"
    definition = extract_definition(raw_text, symptom_name)
    if not definition:
        return "", page_url, "no_definition", "raw page parsed but no definition found"
    return definition, page_url, "ok", ""


def iter_target_records(
    table,
    names: Iterable[str],
    limit: int,
    overwrite: bool,
    include_complete: bool,
) -> Iterable[Tuple[int, object, Dict[str, str]]]:
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


def write_table(path: Path, fieldnames: List[str], records: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


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
        args.not_updated_table = DEFAULT_NOT_UPDATED_TABLE.with_name(DEFAULT_NOT_UPDATED_TABLE.stem + "_sample.csv")

    table = COMMON.XlsxTable(args.input)
    required_headers = {"TCM_symptom_id", "TCM_symptom_name", "Symptom_definition"}
    missing_headers = required_headers - set(table.headers)
    if missing_headers:
        raise RuntimeError(f"missing required xlsx columns: {sorted(missing_headers)}")

    names = [item.strip() for item in args.names.split(",") if item.strip()]
    target_records = list(iter_target_records(table, names, args.limit, args.overwrite, args.include_complete))
    skipped_complete = sum(
        1
        for _, _, record in table.iter_data_rows()
        if normalize_text(record.get("TCM_symptom_name", ""))
        and not is_blank(normalize_text(record.get("Symptom_definition", "")))
    )

    thread_local = threading.local()

    def get_client() -> DugujiClient:
        client = getattr(thread_local, "client", None)
        if client is None:
            client = DugujiClient(
                cache_dir=args.cache_dir,
                min_delay=args.min_delay,
                max_delay=args.max_delay,
                timeout=args.timeout,
                retries=args.retries,
                force_refresh=args.force_refresh,
            )
            thread_local.client = client
        return client

    def crawl_target(target):
        row_num, row, record = target
        symptom_name = normalize_text(record.get("TCM_symptom_name", ""))
        try:
            definition, detail_url, status, message = crawl_one(get_client(), symptom_name)
            return row, record, definition, detail_url, status, message
        except Exception as exc:
            return row, record, "", "", "error", f"{type(exc).__name__}: {exc}"

    progress = COMMON.ProgressPrinter(
        total=len(target_records),
        enabled=not args.no_progress and not args.verbose,
        refresh_interval=args.progress_interval,
    )
    log_records: List[Dict[str, str]] = []
    updated_records: List[Dict[str, str]] = []
    not_updated_records: List[Dict[str, str]] = []
    completed = updated_count = 0
    def_col = table.header_to_col["Symptom_definition"]

    def handle_result(row, record, definition: str, detail_url: str, status: str, message: str) -> None:
        nonlocal completed, updated_count
        completed += 1
        symptom_id = normalize_text(record.get("TCM_symptom_id", ""))
        symptom_name = normalize_text(record.get("TCM_symptom_name", ""))
        old_definition = normalize_text(record.get("Symptom_definition", ""))
        should_update = status == "ok" and definition and (args.overwrite or is_blank(old_definition))
        if should_update:
            table.set_cell_text(row, def_col, definition)
            updated_count += 1
            updated_records.append(
                {
                    "TCM_symptom_id": symptom_id,
                    "TCM_symptom_name": symptom_name,
                    "detail_url": detail_url,
                    "old_definition": old_definition,
                    "new_definition": definition,
                }
            )
        else:
            if status == "ok":
                message = message or "matched page but Symptom_definition already had value"
            not_updated_records.append(
                {
                    "TCM_symptom_id": symptom_id,
                    "TCM_symptom_name": symptom_name,
                    "status": status,
                    "detail_url": detail_url,
                    "reason": message or status,
                }
            )
        log_records.append(
            {
                "TCM_symptom_id": symptom_id,
                "TCM_symptom_name": symptom_name,
                "status": status,
                "detail_url": detail_url,
                "updated": "yes" if should_update else "no",
                "message": message,
            }
        )
        if args.verbose:
            print(f"[{completed}/{len(target_records)}] {symptom_id} {symptom_name}: {status}; updated={'yes' if should_update else 'no'}")
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
    COMMON.write_xlsx_table_csv(args.output, table)
    write_table(args.log, LOG_COLUMNS, log_records)
    write_table(args.updated_table, UPDATED_COLUMNS, updated_records)
    write_table(args.not_updated_table, NOT_UPDATED_COLUMNS, not_updated_records)
    print(
        f"Done. crawled={completed}, updated={updated_count}, "
        f"not_updated={len(not_updated_records)}, "
        f"skipped_complete_rows={skipped_complete if not args.include_complete and not args.overwrite else 0}, "
        f"output={args.output}, log={args.log}, "
        f"updated_table={args.updated_table}, not_updated_table={args.not_updated_table}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
