#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fill missing Symptom_definition values by calling an AI API (DeepSeek by default).

For each symptom whose definition is empty, the script:
1. Builds a prompt from TCM_symptom_name, Symptom_locus, Symptom_property and Type.
2. Calls the AI API.
3. Writes the returned definition into the row.
4. Outputs a filled CSV + crawl log + updated / not-updated tables.

Default input: data_csv/Symptom_Properties_combined_filled.csv
Default API:  DeepSeek (deepseek-chat)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import random
import shutil
import threading
import time
import urllib.error
import urllib.request
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


# Only import utility functions from the dayi script (no XlsxTable dependency)
_COMMON = load_module("symptom_dayi_common", DAYI_SYMPTOM_SCRIPT)


def find_root_dir(start: Path) -> Path:
    for path in [start, *start.parents]:
        if (path / "data_csv" / "Symptom_Properties_combined_filled.csv").exists():
            return path
    return start.parents[1]


ROOT_DIR = find_root_dir(SCRIPT_DIR)
DEFAULT_INPUT = ROOT_DIR / "data_csv" / "Symptom_Properties_combined_filled.csv"
DEFAULT_OUTPUT = SCRIPT_DIR / "Symptom_Properties_ai_filled.csv"
DEFAULT_LOG = SCRIPT_DIR / "symptom_ai_crawl_log.csv"
DEFAULT_UPDATED_TABLE = SCRIPT_DIR / "symptom_ai_updated_records.csv"
DEFAULT_NOT_UPDATED_TABLE = SCRIPT_DIR / "symptom_ai_not_updated_records.csv"
DEFAULT_CACHE_DIR = SCRIPT_DIR / "ai_cache"

LOG_COLUMNS = [
    "TCM_symptom_id",
    "TCM_symptom_name",
    "status",
    "updated",
    "message",
]

UPDATED_COLUMNS = [
    "TCM_symptom_id",
    "TCM_symptom_name",
    "old_definition",
    "new_definition",
]

NOT_UPDATED_COLUMNS = [
    "TCM_symptom_id",
    "TCM_symptom_name",
    "status",
    "reason",
]


# ---------------------------------------------------------------------------
# Shared utilities (local copy to avoid XlsxTable import)
# ---------------------------------------------------------------------------

def normalize_text(value: str) -> str:
    return _COMMON.normalize_text(value)


def is_blank(value: Optional[str]) -> bool:
    return _COMMON.is_blank(value)


def write_table(path: Path, fieldnames: List[str], records: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def read_csv(path: Path):
    """Read a CSV file and return (fieldnames, rows)."""
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


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


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

PROMPT_ONTOLOGICAL = """你是一位资深中医专家。请为以下中医症状撰写一段专业、准确的定义。

症状名称：{symptom_name}
病位：{symptom_locus}
病性/病势：{symptom_property}

要求：
1. 定义长度控制在 50-200 字
2. 应包含该症状的主要临床表现
3. 如有可能，简要说明其病因病机
4. 语言使用中医专业术语，简洁规范
5. 仅输出定义文本，不要包含"定义："等前缀或其他解释"""

PROMPT_SYNONYMOUS = """你是一位资深中医专家。请用一句话解释以下中医症状术语的含义。

症状名称：{symptom_name}
病位：{symptom_locus}
病性/病势：{symptom_property}

要求：
1. 用 20-80 字简短解释该术语的临床含义
2. 语言使用中医专业术语
3. 仅输出解释文本，不要包含前缀或其他内容"""


def build_prompt(symptom_name: str, symptom_locus: str, symptom_property: str, symptom_type: str) -> str:
    locus = normalize_text(symptom_locus) or "（无）"
    prop = normalize_text(symptom_property) or "（无）"

    if symptom_type == "Synonymous terms":
        template = PROMPT_SYNONYMOUS
    else:
        template = PROMPT_ONTOLOGICAL

    return template.format(symptom_name=symptom_name, symptom_locus=locus, symptom_property=prop)


# ---------------------------------------------------------------------------
# AI client
# ---------------------------------------------------------------------------

class AIClient:
    """
    AI API client.  Defaults to DeepSeek (deepseek-chat).

    The API contract is OpenAI-compatible chat completions:
        POST {api_url}
        Headers: Authorization: Bearer {api_key}
        Body: {"model": "...", "messages": [{"role": "user", "content": "..."}]}
        Response: {"choices": [{"message": {"content": "..."}}]}
    """

    def __init__(
        self,
        cache_dir: Path,
        timeout: float,
        retries: int,
        force_refresh: bool,
        api_key: str = "",
        api_url: str = "https://api.deepseek.com/v1/chat/completions",
        model: str = "deepseek-chat",
    ) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.retries = retries
        self.force_refresh = force_refresh
        self.api_key = api_key
        self.api_url = api_url
        self.model = model

    def _cache_path(self, prompt: str) -> Path:
        digest = hashlib.sha1(prompt.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, prompt: str) -> Optional[str]:
        cache_path = self._cache_path(prompt)
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                return data.get("definition", "")
            except (json.JSONDecodeError, KeyError):
                return None
        return None

    def _write_cache(self, prompt: str, definition: str) -> None:
        cache_path = self._cache_path(prompt)
        tmp = cache_path.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(
            json.dumps({"definition": definition, "prompt": prompt}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(cache_path)

    def _call_api(self, prompt: str) -> Tuple[str, str]:
        if not self.api_key or not self.api_url:
            return "", "no API credentials configured (use --api-key and --api-url)"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = json.dumps({
            "model": self.model or "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 512,
        }).encode("utf-8")

        last_error = ""
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.api_url, data=body, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"].strip()
                return content, ""
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.retries:
                    time.sleep((2 ** attempt) + random.uniform(0.5, 1.5))
            except (KeyError, IndexError, json.JSONDecodeError) as exc:
                return "", f"unexpected response format: {exc}"

        return "", last_error

    def generate_definition(
        self,
        symptom_name: str,
        symptom_locus: str = "",
        symptom_property: str = "",
        symptom_type: str = "",
    ) -> Tuple[str, str, str]:
        """
        Returns (definition, status, message) where status is one of:
            "ok"            — definition successfully generated
            "no_definition" — API returned empty content
            "error"         — API call or parsing failed
        """
        prompt = build_prompt(symptom_name, symptom_locus, symptom_property, symptom_type)

        if not self.force_refresh:
            cached = self._read_cache(prompt)
            if cached is not None:
                if cached.strip():
                    return cached.strip(), "ok", ""
                return "", "no_definition", "cached empty result"

        definition, error = self._call_api(prompt)

        if error:
            return "", "error", error
        if not definition or not definition.strip():
            return "", "no_definition", "API returned empty content"

        self._write_cache(prompt, definition)
        return definition.strip(), "ok", ""


# ---------------------------------------------------------------------------
# Target iteration (CSV variant — yields (index, row_dict))
# ---------------------------------------------------------------------------

def iter_target_rows(
    rows: List[Dict[str, str]],
    names: Iterable[str],
    limit: int,
    overwrite: bool,
    include_complete: bool,
):
    wanted_names = {normalize_text(name) for name in names if normalize_text(name)}
    yielded = 0
    for idx, row in enumerate(rows):
        symptom_name = normalize_text(row.get("TCM_symptom_name", ""))
        definition = normalize_text(row.get("Symptom_definition", ""))
        if not symptom_name:
            continue
        if wanted_names and symptom_name not in wanted_names:
            continue
        if not overwrite and not include_complete and not is_blank(definition):
            continue
        yield idx, row
        yielded += 1
        if limit and yielded >= limit:
            break


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill Symptom_definition in a CSV via AI API (DeepSeek by default)."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="source CSV path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="filled CSV path")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="crawl log CSV path")
    parser.add_argument("--updated-table", type=Path, default=DEFAULT_UPDATED_TABLE)
    parser.add_argument("--not-updated-table", type=Path, default=DEFAULT_NOT_UPDATED_TABLE)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="API response cache directory")
    parser.add_argument("--limit", type=int, default=0, help="max symptoms to process; 0 means all")
    parser.add_argument("--names", default="", help="comma-separated TCM_symptom_name values")
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between API calls")
    parser.add_argument("--workers", type=int, default=1, help="number of concurrent threads")
    parser.add_argument("--timeout", type=float, default=60.0, help="API HTTP timeout seconds")
    parser.add_argument("--retries", type=int, default=2, help="retry count after first failure")
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing non-empty definitions")
    parser.add_argument("--include-complete", action="store_true", help="also process rows whose definition is already complete")
    parser.add_argument("--force-refresh", action="store_true", help="ignore cached responses and re-call API")
    parser.add_argument("--no-progress", action="store_true", help="disable the terminal progress bar")
    parser.add_argument("--verbose", action="store_true", help="print one status line for every processed symptom")
    parser.add_argument("--progress-interval", type=float, default=0.5, help="minimum seconds between progress bar refreshes")

    parser.add_argument(
        "--api-key",
        default=os.getenv("DEEPSEEK_API_KEY", ""),
        help="AI API key (defaults to DEEPSEEK_API_KEY environment variable)",
    )
    parser.add_argument("--api-url", default="https://api.deepseek.com/v1/chat/completions", help="AI API endpoint URL")
    parser.add_argument("--api-model", default="deepseek-chat", help="AI model name")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()
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

    # Read CSV
    fieldnames, rows = read_csv(args.input)
    required_headers = {"TCM_symptom_id", "TCM_symptom_name", "Symptom_definition"}
    missing_headers = required_headers - set(fieldnames)
    if missing_headers:
        raise RuntimeError(f"missing required CSV columns: {sorted(missing_headers)}")

    # Select target rows
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
        if normalize_text(row.get("TCM_symptom_name", ""))
        and not is_blank(normalize_text(row.get("Symptom_definition", "")))
    )

    thread_local = threading.local()

    def get_client() -> AIClient:
        client = getattr(thread_local, "client", None)
        if client is None:
            client = AIClient(
                cache_dir=args.cache_dir,
                timeout=args.timeout,
                retries=args.retries,
                force_refresh=args.force_refresh,
                api_key=args.api_key,
                api_url=args.api_url,
                model=args.api_model,
            )
            thread_local.client = client
        return client

    def crawl_target(target):
        idx, row = target
        symptom_name = normalize_text(row.get("TCM_symptom_name", ""))
        symptom_locus = normalize_text(row.get("Symptom_locus", ""))
        symptom_property = normalize_text(row.get("Symptom_property", ""))
        symptom_type = normalize_text(row.get("Type", ""))

        try:
            definition, status, message = get_client().generate_definition(
                symptom_name=symptom_name,
                symptom_locus=symptom_locus,
                symptom_property=symptom_property,
                symptom_type=symptom_type,
            )
            return idx, row, definition, status, message
        except Exception as exc:
            return idx, row, "", "error", f"{type(exc).__name__}: {exc}"

    progress = ProgressPrinter(
        total=len(target_rows),
        enabled=not args.no_progress and not args.verbose,
        refresh_interval=args.progress_interval,
    )
    log_records: List[Dict[str, str]] = []
    updated_records: List[Dict[str, str]] = []
    not_updated_records: List[Dict[str, str]] = []
    completed = updated_count = 0

    def handle_result(
        idx: int,
        row: Dict[str, str],
        definition: str,
        status: str,
        message: str,
    ) -> None:
        nonlocal completed, updated_count
        completed += 1
        symptom_id = normalize_text(row.get("TCM_symptom_id", ""))
        symptom_name = normalize_text(row.get("TCM_symptom_name", ""))
        old_definition = normalize_text(row.get("Symptom_definition", ""))

        should_update = status == "ok" and definition and (args.overwrite or is_blank(old_definition))
        if should_update:
            row["Symptom_definition"] = definition
            updated_count += 1
            updated_records.append(
                {
                    "TCM_symptom_id": symptom_id,
                    "TCM_symptom_name": symptom_name,
                    "old_definition": old_definition,
                    "new_definition": definition,
                }
            )
        else:
            if status == "ok":
                message = message or "matched but Symptom_definition already had value"
            not_updated_records.append(
                {
                    "TCM_symptom_id": symptom_id,
                    "TCM_symptom_name": symptom_name,
                    "status": status,
                    "reason": message or status,
                }
            )
        log_records.append(
            {
                "TCM_symptom_id": symptom_id,
                "TCM_symptom_name": symptom_name,
                "status": status,
                "updated": "yes" if should_update else "no",
                "message": message,
            }
        )
        if args.verbose:
            print(
                f"[{completed}/{len(target_rows)}] {symptom_id} {symptom_name}: "
                f"{status}; updated={'yes' if should_update else 'no'}"
            )
        else:
            progress.update(completed, updated_count, symptom_name, status)

    if args.workers == 1:
        for target in target_rows:
            if completed > 0 and args.delay > 0:
                time.sleep(args.delay)
            handle_result(*crawl_target(target))
    else:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_map = {executor.submit(crawl_target, target): target[0] for target in target_rows}
            for future in concurrent.futures.as_completed(future_map):
                handle_result(*future.result())

    progress.finish()

    # Write outputs
    write_csv(args.output, fieldnames, rows)
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
