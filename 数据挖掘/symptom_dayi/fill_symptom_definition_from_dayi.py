#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fill missing Symptom_definition values in Symptom_Properties.xlsx from dayi.org.cn.

This script uses only Python standard library modules.  It reads and writes xlsx
files by editing the workbook XML inside the zip archive.
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
import zipfile
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ET.register_namespace("", NS_MAIN)


def find_root_dir(start: Path) -> Path:
    for path in [start, *start.parents]:
        if (path / "data_csv" / "Symptom_Properties.xlsx").exists():
            return path
    return start.parents[1]


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = find_root_dir(SCRIPT_DIR)
DEFAULT_INPUT = ROOT_DIR / "data_csv" / "Symptom_Properties.xlsx"
DEFAULT_OUTPUT = SCRIPT_DIR / "Symptom_Properties_dayi_filled.csv"
DEFAULT_LOG = SCRIPT_DIR / "symptom_dayi_crawl_log.csv"
DEFAULT_UPDATED_TABLE = SCRIPT_DIR / "symptom_dayi_updated_records.csv"
DEFAULT_NOT_UPDATED_TABLE = SCRIPT_DIR / "symptom_dayi_not_updated_records.csv"
DEFAULT_CACHE_DIR = SCRIPT_DIR / "symptom_dayi_cache"

BASE_URL = "https://www.dayi.org.cn"
SEARCH_URL = BASE_URL + "/search?keyword={keyword}"

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
        description="Fill Symptom_definition in Symptom_Properties.xlsx from dayi.org.cn."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="source xlsx path")
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
    parser.add_argument("--limit", type=int, default=0, help="max symptoms to crawl; 0 means all")
    parser.add_argument(
        "--names",
        default="",
        help="comma-separated TCM_symptom_name values to crawl; useful for smoke tests",
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
        help="overwrite existing non-empty definitions; default only fills empty/NA",
    )
    parser.add_argument(
        "--include-complete",
        action="store_true",
        help="also query rows whose Symptom_definition is already complete",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="ignore cached HTML and refetch",
    )
    parser.add_argument("--no-progress", action="store_true", help="disable the terminal progress bar")
    parser.add_argument("--verbose", action="store_true", help="print one status line for every crawled symptom")
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=0.5,
        help="minimum seconds between progress bar refreshes",
    )
    return parser.parse_args()


def ns(tag: str) -> str:
    return f"{{{NS_MAIN}}}{tag}"


def col_to_idx(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    number = 0
    for ch in letters:
        number = number * 26 + ord(ch.upper()) - 64
    return number - 1


def idx_to_col(index: int) -> str:
    index += 1
    chars: List[str] = []
    while index:
        index, rem = divmod(index - 1, 26)
        chars.append(chr(65 + rem))
    return "".join(reversed(chars))


def normalize_text(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\u3000", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def is_blank(value: Optional[str]) -> bool:
    return value is None or value.strip() == "" or value.strip().upper() == "NA"


def strip_html(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    lines = [normalize_text(line) for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def title_plain(value: str) -> str:
    return normalize_text(strip_html(value)).replace(" ", "")


class XlsxTable:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.zip_bytes: Dict[str, bytes] = {}
        self.sheet_path = ""
        self.shared_strings: List[str] = []
        self.sheet_root: ET.Element
        self.rows: List[ET.Element] = []
        self.headers: List[str] = []
        self.header_to_col: Dict[str, int] = {}
        self._load()

    def _load_shared_strings(self) -> None:
        data = self.zip_bytes.get("xl/sharedStrings.xml")
        if not data:
            return
        root = ET.fromstring(data)
        for si in root.findall(ns("si")):
            texts = [t.text or "" for t in si.findall(f".//{ns('t')}")]
            self.shared_strings.append("".join(texts))

    def _load_sheet_path(self) -> str:
        wb = ET.fromstring(self.zip_bytes["xl/workbook.xml"])
        sheet = wb.find(f"{ns('sheets')}/{ns('sheet')}")
        if sheet is None:
            raise RuntimeError("workbook has no sheet")
        rel_id = sheet.attrib[f"{{{NS_REL}}}id"]
        rels_root = ET.fromstring(self.zip_bytes["xl/_rels/workbook.xml.rels"])
        rid_to_target = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels_root}
        target = rid_to_target[rel_id]
        return "xl/" + target.lstrip("/") if not target.startswith("xl/") else target

    def _load(self) -> None:
        with zipfile.ZipFile(self.path, "r") as zin:
            self.zip_bytes = {name: zin.read(name) for name in zin.namelist()}
        self._load_shared_strings()
        self.sheet_path = self._load_sheet_path()
        self.sheet_root = ET.fromstring(self.zip_bytes[self.sheet_path])
        sheet_data = self.sheet_root.find(ns("sheetData"))
        if sheet_data is None:
            raise RuntimeError("sheet has no sheetData")
        self.rows = sheet_data.findall(ns("row"))
        if not self.rows:
            raise RuntimeError("sheet has no rows")
        self.headers = self.row_values(self.rows[0])
        self.header_to_col = {header: idx for idx, header in enumerate(self.headers)}

    def cell_value(self, cell: ET.Element) -> str:
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            texts = [t.text or "" for t in cell.findall(f".//{ns('t')}")]
            return "".join(texts)
        value = cell.find(ns("v"))
        if value is None:
            return ""
        raw = value.text or ""
        if cell_type == "s" and raw.isdigit():
            idx = int(raw)
            return self.shared_strings[idx] if idx < len(self.shared_strings) else raw
        return raw

    def row_map(self, row: ET.Element) -> Dict[int, str]:
        values: Dict[int, str] = {}
        for cell in row.findall(ns("c")):
            ref = cell.attrib.get("r", "")
            if ref:
                values[col_to_idx(ref)] = self.cell_value(cell)
        return values

    def row_values(self, row: ET.Element) -> List[str]:
        values = self.row_map(row)
        if not values:
            return []
        return [values.get(i, "") for i in range(max(values) + 1)]

    def get_cell(self, row: ET.Element, col_idx: int, create: bool = False) -> Optional[ET.Element]:
        row_num = int(row.attrib["r"])
        ref = f"{idx_to_col(col_idx)}{row_num}"
        cells = row.findall(ns("c"))
        for cell in cells:
            if cell.attrib.get("r") == ref:
                return cell
        if not create:
            return None
        new_cell = ET.Element(ns("c"), {"r": ref})
        insert_at = len(row)
        for idx, cell in enumerate(cells):
            if col_to_idx(cell.attrib.get("r", "")) > col_idx:
                insert_at = list(row).index(cell)
                break
        row.insert(insert_at, new_cell)
        return new_cell

    def set_cell_text(self, row: ET.Element, col_idx: int, value: str) -> None:
        cell = self.get_cell(row, col_idx, create=True)
        if cell is None:
            raise RuntimeError("failed to create xlsx cell")
        cell.attrib["t"] = "inlineStr"
        for child in list(cell):
            cell.remove(child)
        inline = ET.SubElement(cell, ns("is"))
        text = ET.SubElement(inline, ns("t"))
        text.text = value

    def iter_data_rows(self) -> Iterable[Tuple[int, ET.Element, Dict[str, str]]]:
        for row in self.rows[1:]:
            values = self.row_map(row)
            record = {
                header: values.get(idx, "")
                for header, idx in self.header_to_col.items()
            }
            yield int(row.attrib["r"]), row, record

    def save(self, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        sheet_bytes = ET.tostring(self.sheet_root, encoding="utf-8", xml_declaration=True)
        with zipfile.ZipFile(self.path, "r") as zin, zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED
        ) as zout:
            for item in zin.infolist():
                data = sheet_bytes if item.filename == self.sheet_path else zin.read(item.filename)
                zout.writestr(item, data)


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
                    text = self._decode_response(
                        response.read(), response.headers.get("Content-Encoding")
                    )
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


def looks_blocked(text: str) -> bool:
    checks = ["验证码", "访问过于频繁", "安全验证", "403 Forbidden", "Too Many Requests"]
    return any(item in text for item in checks)


def parse_search_result(html_text: str, symptom_name: str) -> Optional[str]:
    pattern = re.compile(
        r'<a\s+href="(?P<href>/symptom/\d+\.html)"[^>]*>(?P<title>.*?)</a>\s*'
        r'<span[^>]*>\s*-\s*症状\s*</span>',
        re.S,
    )
    wanted = normalize_text(symptom_name).replace(" ", "")
    for match in pattern.finditer(html_text):
        title = title_plain(match.group("title"))
        if title == wanted:
            return urllib.parse.urljoin(BASE_URL, match.group("href"))
    return None


def extract_definition_from_detail(html_text: str, symptom_name: str) -> str:
    meta = re.search(
        r'<meta[^>]+name="description"[^>]+content="([^"]+)"',
        html_text,
        re.I | re.S,
    )
    if meta:
        value = normalize_text(html.unescape(meta.group(1)))
        if value and symptom_name in value:
            return value

    h1 = re.search(r"<h1[^>]*>.*?</h1>(?P<after>.*?)<div", html_text, re.I | re.S)
    if h1:
        text = strip_html(h1.group("after"))
        first_line = text.splitlines()[0] if text else ""
        if first_line and symptom_name in first_line:
            return normalize_text(first_line)

    plain = strip_html(html_text)
    for line in plain.splitlines():
        line = normalize_text(line)
        if line.startswith(symptom_name) and len(line) >= len(symptom_name) + 10:
            return line
    return ""


def crawl_one(client: DayiClient, symptom_name: str) -> Tuple[str, str, str, str]:
    search_url = SEARCH_URL.format(keyword=urllib.parse.quote(symptom_name))
    search_html, _ = client.get(search_url, referer=BASE_URL + "/")
    if looks_blocked(search_html):
        return "", "", "blocked", "search page may be anti-crawler verification"

    detail_url = parse_search_result(search_html, symptom_name)
    if not detail_url:
        return "", "", "not_found", "no exact symptom result in search page"

    detail_html, _ = client.get(detail_url, referer=search_url)
    if looks_blocked(detail_html):
        return "", detail_url, "blocked", "detail page may be anti-crawler verification"

    definition = extract_definition_from_detail(detail_html, symptom_name)
    if not definition:
        return "", detail_url, "no_definition", "detail page parsed but no definition found"
    return definition, detail_url, "ok", ""


def iter_target_records(
    table: XlsxTable,
    names: Iterable[str],
    limit: int,
    overwrite: bool,
    include_complete: bool,
) -> Iterable[Tuple[int, ET.Element, Dict[str, str]]]:
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


def write_xlsx_table_csv(path: Path, table: XlsxTable) -> None:
    records = [record for _, _, record in table.iter_data_rows()]
    write_table(path, table.headers, records)


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
    def __init__(self, total: int, enabled: bool = True, width: int = 24, refresh_interval: float = 0.5) -> None:
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

    def update(self, done: int, updated: int, symptom_name: str, status: str) -> None:
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
        line = (
            f"[{bar}] {done}/{self.total} left:{remaining} "
            f"{speed:.2f}/s ETA:{format_duration(eta)} "
            f"updated:{updated} {self._shorten(symptom_name, 8)}:{status}"
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

    table = XlsxTable(args.input)
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

    thread_local = threading.local()

    def get_client() -> DayiClient:
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

    def crawl_target(target: Tuple[int, ET.Element, Dict[str, str]]) -> Tuple[int, ET.Element, Dict[str, str], str, str, str, str]:
        row_num, row, record = target
        symptom_name = normalize_text(record.get("TCM_symptom_name", ""))
        try:
            definition, detail_url, status, message = crawl_one(get_client(), symptom_name)
            return row_num, row, record, definition, detail_url, status, message
        except Exception as exc:
            return row_num, row, record, "", "", "error", f"{type(exc).__name__}: {exc}"

    progress = ProgressPrinter(
        total=len(target_records),
        enabled=not args.no_progress and not args.verbose,
        refresh_interval=args.progress_interval,
    )
    log_records: List[Dict[str, str]] = []
    updated_records: List[Dict[str, str]] = []
    not_updated_records: List[Dict[str, str]] = []
    completed = updated_count = 0
    def_col = table.header_to_col["Symptom_definition"]

    def handle_result(
        row: ET.Element,
        record: Dict[str, str],
        definition: str,
        detail_url: str,
        status: str,
        message: str,
    ) -> None:
        nonlocal updated_count
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
            print(
                f"[{completed}/{len(target_records)}] {symptom_id} {symptom_name}: "
                f"{status}; updated={'yes' if should_update else 'no'}"
            )
        else:
            progress.update(completed, updated_count, symptom_name, status)

    if args.workers == 1:
        for target in target_records:
            row_num, row, record, definition, detail_url, status, message = crawl_target(target)
            completed += 1
            handle_result(row, record, definition, detail_url, status, message)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_map = {executor.submit(crawl_target, target): target[0] for target in target_records}
            for future in concurrent.futures.as_completed(future_map):
                row_num, row, record, definition, detail_url, status, message = future.result()
                completed += 1
                handle_result(row, record, definition, detail_url, status, message)

    progress.finish()
    write_xlsx_table_csv(args.output, table)
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
