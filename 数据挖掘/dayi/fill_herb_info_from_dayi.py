#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fill missing HERB_herb_info_v1.csv fields from dayi.org.cn cmedical pages.

The script is intentionally conservative:
- exact Chinese-name matching only;
- existing non-empty values are not overwritten by default;
- no concurrent crawling;
- browser-like headers, cookies, cache, random delays, and retry backoff.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import gzip
import hashlib
import html
import http.cookiejar
import json
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


def find_root_dir(start: Path) -> Path:
    for path in [start, *start.parents]:
        if (path / "data_csv" / "HERB_herb_info_v1.csv").exists():
            return path
    return start.parents[1]


ROOT_DIR = find_root_dir(Path(__file__).resolve().parent)
DEFAULT_INPUT = ROOT_DIR / "data_csv" / "HERB_herb_info_v1.csv"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "HERB_herb_info_v1_dayi_filled.csv"
DEFAULT_LOG = Path(__file__).resolve().parent / "dayi_crawl_log.csv"
DEFAULT_UPDATED_TABLE = Path(__file__).resolve().parent / "dayi_updated_records.csv"
DEFAULT_NOT_UPDATED_TABLE = Path(__file__).resolve().parent / "dayi_not_updated_records.csv"
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / "dayi_cache"

BASE_URL = "https://www.dayi.org.cn"
SEARCH_URL = BASE_URL + "/search?keyword={keyword}&type=cmedical"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

SHORT_FIELD_MAP = {
    "latinName": "Herb_latin_name",
    "toxicity": "Toxicity",
    "cmedicalType": "Therapeutic_cn_class",
    "drugSite": "UsePart",
    "drug_site": "UsePart",
}

LONG_FIELD_MAP = {
    "func": "Function",
    "mainAttend": "Indication",
}

LOG_COLUMNS = [
    "Herb_ID",
    "Herb_cn_name",
    "status",
    "detail_url",
    "filled_fields",
    "message",
]

UPDATED_COLUMNS = [
    "Herb_ID",
    "Herb_cn_name",
    "detail_url",
    "filled_fields",
    "filled_values",
]

NOT_UPDATED_COLUMNS = [
    "Herb_ID",
    "Herb_cn_name",
    "status",
    "detail_url",
    "reason",
]

FILLABLE_COLUMNS = sorted(
    {
        "Herb_latin_name",
        "Properties",
        "Meridians",
        "UsePart",
        "Function",
        "Indication",
        "Toxicity",
        "Therapeutic_cn_class",
    }
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crawl dayi.org.cn cmedical pages and fill missing herb CSV fields."
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
        "--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="HTML cache directory"
    )
    parser.add_argument("--limit", type=int, default=0, help="max herbs to crawl; 0 means all")
    parser.add_argument(
        "--names",
        default="",
        help="comma-separated Herb_cn_name values to crawl; useful for smoke tests",
    )
    parser.add_argument(
        "--min-delay", type=float, default=1.5, help="minimum delay between HTTP requests"
    )
    parser.add_argument(
        "--max-delay", type=float, default=4.0, help="maximum delay between HTTP requests"
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
        help="overwrite existing non-empty CSV values; default only fills empty/NA",
    )
    parser.add_argument(
        "--include-complete",
        action="store_true",
        help="also query rows whose fillable columns are already complete",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="ignore cached HTML and refetch pages",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="disable the terminal progress bar",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print one status line for every crawled herb",
    )
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
    value = html.unescape(value)
    value = value.replace("\u3000", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def strip_html(value: str) -> str:
    value = value.replace("\\/", "/")
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    lines = [normalize_text(line) for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def decode_js_string(raw: str) -> str:
    if raw == "null":
        return ""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw.strip('"')
    return strip_html(str(value))


def clean_result_title(value: str) -> str:
    value = re.sub(r"(?i)</?em[^>]*>", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    return normalize_text(value)


def split_properties_and_meridians(remark: str) -> Tuple[str, str]:
    remark = normalize_text(remark)
    if not remark:
        return "", ""
    parts = re.split(r"[；;]", remark, maxsplit=1)
    if len(parts) == 2:
        return normalize_text(parts[0]), normalize_text(parts[1])
    match = re.search(r"(归.+?经)$", remark)
    if match:
        return normalize_text(remark[: match.start()]), normalize_text(match.group(1))
    return remark, ""


def looks_blocked(text: str) -> bool:
    checks = ["验证码", "访问过于频繁", "安全验证", "403 Forbidden", "Too Many Requests"]
    return any(item in text for item in checks)


class DayiClient:
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
        return self.cache_dir / f"{digest}.html"

    def _headers(self, referer: str) -> Dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Referer": referer,
            "Upgrade-Insecure-Requests": "1",
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
                    raw = response.read()
                    text = self._decode_response(raw, response.headers.get("Content-Encoding"))
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


def parse_search_result(html_text: str, herb_name: str) -> Optional[str]:
    pattern = re.compile(
        r'<a\s+href="(?P<href>/cmedical/\d+\.html)"[^>]*>(?P<title>.*?)</a>\s*'
        r'<span[^>]*>\s*-\s*中药材\s*</span>',
        re.S,
    )
    wanted = normalize_text(herb_name).replace(" ", "")
    for match in pattern.finditer(html_text):
        title = clean_result_title(match.group("title")).replace(" ", "")
        if title == wanted:
            return urllib.parse.urljoin(BASE_URL, match.group("href"))
    return None


def extract_js_assignments(html_text: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    pattern = re.compile(r"\bk\.([A-Za-z_][A-Za-z0-9_]*)=(\"(?:\\.|[^\"\\])*\"|null)")
    for key, raw_value in pattern.findall(html_text):
        value = decode_js_string(raw_value)
        if value:
            fields[key] = value
    return fields


def extract_page_fields(html_text: str) -> Dict[str, str]:
    js_fields = extract_js_assignments(html_text)
    extracted: Dict[str, str] = {}

    for source_key, target_key in SHORT_FIELD_MAP.items():
        if js_fields.get(source_key):
            extracted[target_key] = js_fields[source_key]

    for source_key, target_key in LONG_FIELD_MAP.items():
        if js_fields.get(source_key):
            extracted[target_key] = js_fields[source_key]

    remark = js_fields.get("remark", "")
    properties, meridians = split_properties_and_meridians(remark)
    if properties:
        extracted["Properties"] = properties
    if meridians:
        extracted["Meridians"] = meridians

    return extracted


def has_fillable_blank(row: Dict[str, str]) -> bool:
    return any(is_blank(row.get(column)) for column in FILLABLE_COLUMNS)


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
        if not overwrite and not include_complete and not has_fillable_blank(row):
            continue
        yield index, row
        yielded += 1
        if limit and yielded >= limit:
            break


def fill_row(row: Dict[str, str], fields: Dict[str, str], overwrite: bool) -> List[str]:
    filled: List[str] = []
    for column, value in fields.items():
        if column not in row or not value:
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


def write_log(path: Path, records: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=LOG_COLUMNS)
        writer.writeheader()
        writer.writerows(records)


def write_table(path: Path, fieldnames: List[str], records: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def format_duration(seconds: float) -> str:
    if seconds < 0 or seconds == float("inf"):
        return "--:--"
    seconds = int(seconds)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


class ProgressPrinter:
    def __init__(
        self,
        total: int,
        enabled: bool = True,
        width: int = 24,
        refresh_interval: float = 0.5,
    ) -> None:
        self.total = max(0, total)
        self.enabled = enabled
        self.width = width
        self.refresh_interval = max(0.0, refresh_interval)
        self.started_at = time.time()
        self.last_len = 0
        self.last_render_at = 0.0

    @staticmethod
    def _shorten(value: str, limit: int) -> str:
        value = value.strip()
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 1)] + "~"

    def update(
        self,
        done: int,
        matched: int,
        filled_cells: int,
        herb_name: str,
        status: str,
    ) -> None:
        if not self.enabled:
            return
        now = time.time()
        if done < self.total and now - self.last_render_at < self.refresh_interval:
            return
        elapsed = max(now - self.started_at, 0.001)
        speed = done / elapsed
        remaining = max(self.total - done, 0)
        eta = remaining / speed if speed > 0 else float("inf")
        ratio = (done / self.total) if self.total else 1.0
        bar_done = int(self.width * ratio)
        bar = "#" * bar_done + "-" * (self.width - bar_done)
        herb_display = self._shorten(herb_name, 8)
        line = (
            f"[{bar}] {done}/{self.total} left:{remaining} "
            f"{speed:.2f}/s ETA:{format_duration(eta)} "
            f"ok:{matched} cells:{filled_cells} {herb_display}:{status}"
        )
        columns = shutil.get_terminal_size((100, 20)).columns
        max_len = max(40, min(columns - 1, 120))
        if len(line) > max_len:
            line = line[: max_len - 1] + "~"
        padding = " " * max(self.last_len - len(line), 0)
        print("\r" + line + padding, end="", flush=True)
        self.last_len = len(line)
        self.last_render_at = now

    def finish(self) -> None:
        if self.enabled:
            print()
            self.last_len = 0


def crawl_one(
    client: DayiClient,
    row: Dict[str, str],
) -> Tuple[Dict[str, str], str, str, str]:
    herb_name = normalize_text(row.get("Herb_cn_name", ""))
    search_url = SEARCH_URL.format(keyword=urllib.parse.quote(herb_name))

    search_html, _ = client.get(search_url, referer=BASE_URL + "/")
    if looks_blocked(search_html):
        return {}, "", "blocked", "search page may be anti-crawler verification"

    detail_url = parse_search_result(search_html, herb_name)
    if not detail_url:
        return {}, "", "not_found", "no exact cmedical result in search page"

    detail_html, _ = client.get(detail_url, referer=search_url)
    if looks_blocked(detail_html):
        return {}, detail_url, "blocked", "detail page may be anti-crawler verification"

    fields = extract_page_fields(detail_html)
    if not fields:
        return {}, detail_url, "no_fields", "detail page parsed but no mapped fields found"
    return fields, detail_url, "ok", ""


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
    thread_local = threading.local()

    def get_thread_client() -> DayiClient:
        client = getattr(thread_local, "client", None)
        if client is None:
            client = DayiClient(
                cache_dir=args.cache_dir,
                min_delay=args.min_delay,
                max_delay=args.max_delay,
                timeout=args.timeout,
                retries=args.retries,
                force_refresh=args.force_refresh,
            )
            thread_local.client = client
        return client

    def crawl_target(
        target: Tuple[int, Dict[str, str]]
    ) -> Tuple[int, Dict[str, str], Dict[str, str], str, str, str]:
        row_index, row = target
        try:
            fields, detail_url, status, message = crawl_one(get_thread_client(), row)
            return row_index, row, fields, detail_url, status, message
        except Exception as exc:  # keep a full crawl moving even when one row fails
            return row_index, row, {}, "", "error", f"{type(exc).__name__}: {exc}"

    log_records: List[Dict[str, str]] = []
    updated_records: List[Dict[str, str]] = []
    not_updated_records: List[Dict[str, str]] = []
    total = success = filled_count = 0
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
        and not has_fillable_blank(row)
    )
    progress = ProgressPrinter(
        total=len(target_rows),
        enabled=not args.no_progress and not args.verbose,
        refresh_interval=args.progress_interval,
    )

    def handle_result(
        completed: int,
        row: Dict[str, str],
        fields: Dict[str, str],
        detail_url: str,
        status: str,
        message: str,
    ) -> None:
        nonlocal total, success, filled_count
        total += 1
        herb_id = row.get("Herb_ID", "")
        herb_name = normalize_text(row.get("Herb_cn_name", ""))
        filled_fields: List[str] = []

        if status == "ok":
            filled_fields = fill_row(row, fields, overwrite=args.overwrite)
            success += 1
            filled_count += len(filled_fields)
            if not filled_fields:
                status = "ok_no_empty_fields"
                message = "matched page but target columns already had values"

        log_records.append(
            {
                "Herb_ID": herb_id,
                "Herb_cn_name": herb_name,
                "status": status,
                "detail_url": detail_url,
                "filled_fields": "|".join(filled_fields),
                "message": message,
            }
        )
        if filled_fields:
            updated_records.append(
                {
                    "Herb_ID": herb_id,
                    "Herb_cn_name": herb_name,
                    "detail_url": detail_url,
                    "filled_fields": "|".join(filled_fields),
                    "filled_values": json.dumps(
                        {field: row.get(field, "") for field in filled_fields},
                        ensure_ascii=False,
                    ),
                }
            )
        else:
            not_updated_records.append(
                {
                    "Herb_ID": herb_id,
                    "Herb_cn_name": herb_name,
                    "status": status,
                    "detail_url": detail_url,
                    "reason": message or status,
                }
        )
        if args.verbose:
            print(
                f"[{completed}/{len(target_rows)}] {herb_id} {herb_name}: {status}; "
                f"filled={','.join(filled_fields) if filled_fields else '-'}"
            )
        else:
            progress.update(
                done=completed,
                matched=success,
                filled_cells=filled_count,
                herb_name=herb_name,
                status=status,
            )

    completed = 0
    if args.workers == 1:
        for target in target_rows:
            _, row, fields, detail_url, status, message = crawl_target(target)
            completed += 1
            handle_result(completed, row, fields, detail_url, status, message)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_map = {
                executor.submit(crawl_target, target): target[0] for target in target_rows
            }
            for future in concurrent.futures.as_completed(future_map):
                _, row, fields, detail_url, status, message = future.result()
                completed += 1
                handle_result(completed, row, fields, detail_url, status, message)

    progress.finish()
    write_csv(args.output, fieldnames, rows)
    write_log(args.log, log_records)
    write_table(args.updated_table, UPDATED_COLUMNS, updated_records)
    write_table(args.not_updated_table, NOT_UPDATED_COLUMNS, not_updated_records)
    print(
        f"Done. crawled={total}, matched={success}, filled_cells={filled_count}, "
        f"updated_rows={len(updated_records)}, not_updated_rows={len(not_updated_records)}, "
        f"skipped_complete_rows={skipped_complete if not args.include_complete and not args.overwrite else 0}, "
        f"output={args.output}, log={args.log}, "
        f"updated_table={args.updated_table}, not_updated_table={args.not_updated_table}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
