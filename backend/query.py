"""
后端查询模块 - 第一阶段（完整版）

功能：
  model=0: 按中药名查询中药信息（精确匹配 + 语义检索 fallback）
  model=1: 按药方名查询药方信息（精确匹配 + 语义检索 fallback）
  model=2: 按症状关键词推荐药方（语义检索 + 多症状对齐 + 日志/置信度，供 Sankey/Timeline/Confidence UI 使用）
  model=3: 症状 → 相关症状/证候概念 → 药方 → 中草药 推理链路（含 DeepSeek LLM 自然语言解释）
"""
from __future__ import annotations

import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# ── 性能计时 ──────────────────────────────────────────────────────────────────
_timings: dict[str, float] = {}


@contextmanager
def _clock(label: str):
    t0 = time.perf_counter()
    yield
    _timings[label] = time.perf_counter() - t0


def _get_timings() -> dict[str, float]:
    return dict(_timings)

# ── 路径配置 ──────────────────────────────────────────────────────────────────
_DATA_DIR = Path(__file__).resolve().parent.parent / "data_csv"

_HERB_CSV    = _DATA_DIR / "HERB_herb_info_v1.csv"
_FORMULA_CSV = _DATA_DIR / "HERB_formula_info_v2.csv"

# ── 懒加载数据 ────────────────────────────────────────────────────────────────
_herb_df: pd.DataFrame | None = None
_formula_df: pd.DataFrame | None = None


def _get_herb_df() -> pd.DataFrame:
    global _herb_df
    if _herb_df is None:
        _herb_df = pd.read_csv(_HERB_CSV, encoding="utf-8-sig", low_memory=False)
    return _herb_df


def _get_formula_df() -> pd.DataFrame:
    global _formula_df
    if _formula_df is None:
        _formula_df = pd.read_csv(_FORMULA_CSV, encoding="utf-8-sig", low_memory=False)
    return _formula_df


# ── 统一返回格式 ───────────────────────────────────────────────────────────────
def _ok(data: Any) -> dict:
    return {"code": 200, "message": "success", "data": data}


def _fail(msg: str) -> dict:
    return {"code": 404, "message": "failure", "data": {"detail": msg}}


def _safe_str(val, default: str = "") -> str:
    """空值/NaN 安全的字符串转换。"""
    if val is None:
        return default
    try:
        if isinstance(val, float) and np.isnan(val):
            return default
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    if s in ("", "nan", "NaN", "None", "none", "null"):
        return default
    return s


def _norm_name(text: str) -> str:
    return re.sub(r"[\s_\-·,，。;；:：/\\]+", "", _safe_str(text).lower())


def _score_name_field(keyword: str, value: str, field_weight: int) -> tuple[int, str]:
    raw_kw = _safe_str(keyword).lower()
    raw_val = _safe_str(value).lower()
    norm_kw = _norm_name(keyword)
    norm_val = _norm_name(value)
    if not norm_kw or not norm_val:
        return 0, ""
    if raw_kw == raw_val or norm_kw == norm_val:
        return 100 + field_weight, "exact"
    if raw_val.startswith(raw_kw) or norm_val.startswith(norm_kw):
        return 75 + field_weight, "prefix"
    if raw_kw in raw_val or norm_kw in norm_val:
        return 45 + field_weight, "contains"
    return 0, ""


def _rank_name_matches(
    df: pd.DataFrame,
    keyword: str,
    fields: list[tuple[str, str, int]],
) -> list[tuple[pd.Series, dict[str, Any]]]:
    """Return rows ranked by exact > prefix > contains and field importance."""
    ranked: list[tuple[int, int, pd.Series, dict[str, Any]]] = []
    kw_len = len(_norm_name(keyword))
    for _, row in df.iterrows():
        best_score = 0
        best_meta: dict[str, Any] = {}
        for field_name, label, weight in fields:
            score, match_type = _score_name_field(keyword, row.get(field_name), weight)
            if score > best_score:
                best_score = score
                best_meta = {
                    "match_type": match_type,
                    "matched_field": label,
                    "matched_value": _safe_str(row.get(field_name)),
                    "match_score": score,
                }
        if best_score:
            matched_len = len(_norm_name(best_meta.get("matched_value", "")))
            length_penalty = abs(matched_len - kw_len)
            ranked.append((best_score, -length_penalty, row, best_meta))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [(row, meta) for _, _, row, meta in ranked]


def _formula_identity(item: dict) -> str:
    return _safe_str(item.get("formula_id")) or _safe_str(item.get("cn_name"))


# ── 症状关键词解析 ─────────────────────────────────────────────────────────────
_SYMPTOM_SEP = re.compile(r"[，,、；;。\s　/|\\]+")


def _split_symptoms(text: str) -> list[str]:
    """把用户输入拆成多个症状关键词。"""
    if not text:
        return []
    parts = _SYMPTOM_SEP.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _normalize_symptoms(keywords: list[str], threshold: float = 0.5) -> dict[str, list[str]]:
    """将非标准用户症状映射到标准中医术语（长关键词可分解为多个症状）。"""
    if not keywords:
        return {}
    try:
        from backend.semantic import _load_symptom_index, _get_model
        embs, meta = _load_symptom_index()
        model = _get_model()
    except Exception:
        return {kw: [kw] for kw in keywords}

    # 预收集所有标准症状名，用于自检
    known_names: set[str] = set()
    for row in meta:
        n = str(row.get("TCM_symptom_name", "")).strip()
        if n and len(n) >= 2:
            known_names.add(n)

    # ── BGE 单条标准化辅助函数（供子串匹配剩余文本兜底）─────────────────
    def _bge_normalize_one(term: str) -> str:
        """对单个短词做 BGE 标准化，返回最佳标准名；无匹配时返回原词。"""
        if len(term) < 2:
            return term
        vec = model.encode([term], normalize_embeddings=True)[0].astype(np.float32)
        best_idx = int(np.argmax(embs @ vec))
        best_score = float((embs @ vec)[best_idx])
        if best_score >= threshold:
            name = str(meta[best_idx].get("TCM_symptom_name", "")).strip()
            if name and len(name) >= 2 and name.lower() not in ("nan", "none"):
                return name
        return term

    mapping: dict[str, list[str]] = {}
    for kw in keywords:
        # 若关键词本身已是标准术语，直接保留，不重映射
        if kw in known_names:
            mapping[kw] = [kw]
            continue

        # ── 阶段 1：子串匹配分解（长关键词 ≥4 字时优先尝试）───────────────
        if len(kw) >= 4:
            sub_matches: list[str] = []
            # 按长度降序遍历已知症状名，长的优先，避免短词"吃掉"长词的字符
            sorted_names = sorted(known_names, key=len, reverse=True)
            remaining_chars: list[str | None] = list(kw)  # 逐字符标记，已匹配的位置置 None
            for name in sorted_names:
                # 在「未被消费的字符序列」中查找
                remaining_str = "".join(c for c in remaining_chars if c is not None)
                idx = remaining_str.find(name)
                if idx == -1:
                    continue
                # 映射回 remaining_chars 中的位置
                mapped_start = 0
                for pos, c in enumerate(remaining_chars):
                    if c is not None:
                        if mapped_start == idx:
                            # 标记该区间的字符为已消费
                            for offset in range(len(name)):
                                inner_pos = pos + offset
                                while inner_pos < len(remaining_chars) and remaining_chars[inner_pos] is None:
                                    inner_pos += 1
                                if inner_pos < len(remaining_chars):
                                    remaining_chars[inner_pos] = None
                            sub_matches.append(name)
                            break
                        mapped_start += 1

            matched_chars = sum(len(m) for m in sub_matches)
            # 至少匹配到 2 个独立症状，且覆盖原关键词 ≥40% 时才采纳
            if len(sub_matches) >= 2 and matched_chars >= len(kw) * 0.4:
                # ── 处理剩余未匹配字符：提取连续段交给 BGE 标准化 ──
                final_matches: list[str] = list(sub_matches)
                buf: list[str] = []
                for c in remaining_chars:
                    if c is not None:
                        buf.append(c)
                    else:
                        if len(buf) >= 2:
                            final_matches.append(_bge_normalize_one("".join(buf)))
                        buf.clear()
                if len(buf) >= 2:
                    final_matches.append(_bge_normalize_one("".join(buf)))
                mapping[kw] = final_matches
                continue
            # 若子串匹配恰好覆盖了整句且只匹配到一个（说明整句本身在库中但被自检遗漏）：
            # 直接走下面的 BGE 路径，让语义检索去确认

        # ── 阶段 2：BGE 语义分解（子串匹配未命中时的兜底）────────────────
        kw_vec = model.encode([kw], normalize_embeddings=True)[0].astype(np.float32)
        scores = embs @ kw_vec
        # 收集所有 >= threshold 的候选，按分数降序
        candidates = []
        for idx in range(len(scores)):
            s = float(scores[idx])
            if s >= threshold:
                name = str(meta[idx].get("TCM_symptom_name", "")).strip()
                if name and len(name) >= 2 and name.lower() not in ("nan", "none"):
                    candidates.append((s, name))
        candidates.sort(key=lambda x: x[0], reverse=True)

        if not candidates:
            mapping[kw] = [kw]
            continue

        # 长关键词（≥6字）可能包含多个独立症状，取 top-3 候选分解
        if len(kw) >= 6 and len(candidates) >= 2:
            multi = [c[1] for c in candidates[:3] if c[0] >= threshold]
            if len(multi) >= 2:
                mapping[kw] = multi
                continue
        mapping[kw] = [candidates[0][1]]

    return mapping


def _normalize_and_dedup(keywords: list[str]) -> tuple[list[str], dict[str, list[str]], dict[str, list[str]]]:
    """标准化 + 去重，返回 (去重后标准词列表, 标准化映射, 合并信息)。"""
    norm_list_map = _normalize_symptoms(keywords)  # {raw: [std1, std2, ...]}
    # 展平为单个列表（长关键词可能分解为多个标准词）
    normalized_all: list[str] = []
    kw_indices: list[tuple[int, str]] = []  # (original_idx, std)
    for i, kw in enumerate(keywords):
        for std in norm_list_map.get(kw, [kw]):
            normalized_all.append(std)
            kw_indices.append((i, std))

    seen: set[str] = set()
    dedup_keywords: list[str] = []
    dedup_merged: dict[str, list[str]] = {}
    for orig_idx, norm in kw_indices:
        raw = keywords[orig_idx]
        if norm not in seen:
            seen.add(norm)
            dedup_keywords.append(norm)
            dedup_merged[norm] = [raw]
        else:
            dedup_merged.setdefault(norm, [norm]).append(raw)
    dedup_merged = {k: list(dict.fromkeys(v)) for k, v in dedup_merged.items() if len(v) > 1}
    # 构建标准化映射：值为列表，支持一对多（长关键词分解为多个症状）
    norm_map: dict[str, list[str]] = {}
    for kw in keywords:
        stds = norm_list_map.get(kw, [kw])
        norm_map[kw] = stds
    return dedup_keywords, norm_map, dedup_merged


# ── 翻译增强 ─────────────────────────────────────────────────────────────────────
def _translate_herb_fields(items: list[dict]) -> None:
    """为中药条目批量翻译 Function / Indication / Properties 等英文字段。"""
    fields = ["function", "indication", "properties", "meridians", "use_part", "toxicity"]
    all_texts: list[str] = []
    for item in items:
        for f in fields:
            all_texts.append(item.get(f, "") or "")
    try:
        from backend.llm import translate_tcm
        translated = translate_tcm(all_texts)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return
    idx = 0
    for item in items:
        for f in fields:
            cn = translated[idx] if idx < len(translated) else ""
            idx += 1
            if cn:
                item[f"{f}_cn"] = cn


def _translate_formula_fields(items: list[dict]) -> None:
    """为药方条目翻译 indications_en 字段。"""
    all_texts = [item.get("indications_en", "") or "" for item in items]
    try:
        from backend.llm import translate_tcm
        translated = translate_tcm(all_texts)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return
    for item, cn in zip(items, translated):
        if cn:
            item["indications_en_cn"] = cn


# ── 行 → item 字典 ────────────────────────────────────────────────────────────
def _herb_row_to_item(row, score: float | None = None) -> dict:
    item = {
        "herb_id":     _safe_str(row.get("Herb_ID")),
        "cn_name":     _safe_str(row.get("Herb_cn_name")),
        "pinyin_name": _safe_str(row.get("Herb_pinyin_name")),
        "en_name":     _safe_str(row.get("Herb_en_name")),
        "properties":  _safe_str(row.get("Properties")),
        "meridians":   _safe_str(row.get("Meridians")),
        "use_part":    _safe_str(row.get("UsePart")),
        "function":    _safe_str(row.get("Function")),
        "indication":  _safe_str(row.get("Indication")),
        "toxicity":    _safe_str(row.get("Toxicity")),
    }
    if score is not None:
        item["semantic_score"] = float(score)
    return item


def _formula_row_to_item(row, score: float | None = None) -> dict:
    item = {
        "formula_id":     _safe_str(row.get("Formula_id")),
        "cn_name":        _safe_str(row.get("Formula_cn_name")),
        "pinyin_name":    _safe_str(row.get("Formula_pinyin_name")),
        "en_name":        _safe_str(row.get("Formula_en_name")),
        "alias":          _safe_str(row.get("Formula_alias_name")),
        "dosage_form":    _safe_str(row.get("Dosage_form")),
        "administration": _safe_str(row.get("Administration")),
        "type":           _safe_str(row.get("Type")),
        "category":       _safe_str(row.get("Category")),
        "herbs_cn":       _safe_str(row.get("Herbs_in_Chinese")),
        "syndromes_cn":   _safe_str(row.get("Syndromes_in_Chinese")),
        "indications_cn": _safe_str(row.get("Indications_in_Chinese")),
        "indications_en": _safe_str(row.get("Indications_in_English")),
        "source":         _safe_str(row.get("Source")),
    }
    if score is not None:
        item["semantic_score"] = float(score)
    return item


# ── 模式 0：中药查询 ──────────────────────────────────────────────────────────
def query_herb(name: str) -> dict:
    """model=0: 按中药中文名/拼音名/英文名查询中药信息（精确 → 语义 fallback）"""
    if not name or not name.strip():
        return _fail("中药名不能为空")
    df = _get_herb_df()
    kw = name.strip()
    hits = _rank_name_matches(
        df,
        kw,
        [
            ("Herb_cn_name", "中文名", 20),
            ("Herb_pinyin_name", "拼音名", 12),
            ("Herb_en_name", "英文名", 8),
        ],
    )
    items: list[dict] = []
    if hits:
        for row, match_meta in hits[:10]:
            item = _herb_row_to_item(row)
            item.update(match_meta)
            items.append(item)
        _translate_herb_fields(items)
        return _ok({"total": int(len(hits)), "items": items, "match_type": items[0]["match_type"]})

    # fallback：语义检索
    try:
        from backend.semantic import semantic_search_herb
        results = semantic_search_herb(kw, top_k=10)
        for row, score in results:
            items.append(_herb_row_to_item(row, score=score))
        if items:
            _translate_herb_fields(items)
            return _ok({"total": len(items), "items": items, "match_type": "semantic"})
    except Exception as e:
        return _fail(f"中药语义检索失败：{e}")

    return _fail(f"未找到中药：{kw}")


# ── 模式 1：药方查询 ──────────────────────────────────────────────────────────
def query_formula(name: str) -> dict:
    """model=1: 按药方中文名/拼音名/英文名查询药方信息（精确 → 语义 fallback）"""
    if not name or not name.strip():
        return _fail("药方名不能为空")
    df = _get_formula_df()
    kw = name.strip()
    hits = _rank_name_matches(
        df,
        kw,
        [
            ("Formula_cn_name", "中文名", 20),
            ("Formula_alias_name", "别名", 16),
            ("Formula_pinyin_name", "拼音名", 12),
            ("Formula_en_name", "英文名", 8),
        ],
    )
    items: list[dict] = []
    if hits:
        for row, match_meta in hits[:10]:
            item = _formula_row_to_item(row)
            item.update(match_meta)
            items.append(item)
        name_match_total = len(hits)

        if len(items) < 5:
            seen = {_formula_identity(item) for item in items}
            try:
                from backend.semantic import semantic_search_formula
                semantic_results = semantic_search_formula(kw, top_k=10)
                for row, score in semantic_results:
                    supplement = _formula_row_to_item(row, score=score)
                    identity = _formula_identity(supplement)
                    if not identity or identity in seen:
                        continue
                    supplement.update({
                        "match_type": "semantic_supplement",
                        "matched_field": "语义补充",
                        "matched_value": kw,
                        "match_score": float(score),
                    })
                    items.append(supplement)
                    seen.add(identity)
                    if len(items) >= 5:
                        break
            except Exception:
                # Name hits are still valid; semantic supplement is best-effort.
                pass
        _translate_formula_fields(items)
        match_type = "mixed" if any(i.get("match_type") == "semantic_supplement" for i in items) else items[0]["match_type"]
        return _ok({
            "total": len(items),
            "name_match_total": int(name_match_total),
            "items": items,
            "match_type": match_type,
            "supplemented": match_type == "mixed",
        })

    # fallback：语义检索
    try:
        from backend.semantic import semantic_search_formula
        results = semantic_search_formula(kw, top_k=10)
        for row, score in results:
            items.append(_formula_row_to_item(row, score=score))
        if items:
            _translate_formula_fields(items)
            return _ok({"total": len(items), "items": items, "match_type": "semantic"})
    except Exception as e:
        return _fail(f"药方语义检索失败：{e}")

    return _fail(f"未找到药方：{kw}")


# ── softmax 归一化 ─────────────────────────────────────────────────────────────
def _softmax_normalize(scores: list[float], temperature: float = 0.05) -> list[float]:
    if not scores:
        return []
    arr = np.asarray(scores, dtype=np.float64) / max(temperature, 1e-6)
    arr -= arr.max()
    exp = np.exp(arr)
    s = exp.sum()
    if s <= 0:
        return [1.0 / len(scores)] * len(scores)
    return (exp / s).tolist()


# ── 模式 2：症状推荐药方 ───────────────────────────────────────────────────────
def _build_symptom_alignment(symptom_keywords: list[str],
                             formula_items: list[dict]) -> dict[str, list[str]]:
    """统计每个药方匹配到的症状关键词，用于 Sankey/对齐展示。"""
    alignment: dict[str, list[str]] = {}
    for f in formula_items:
        name = f.get("cn_name") or ""
        if not name:
            continue
        haystack = " ".join([
            f.get("indications_cn", ""),
            f.get("indications_en", ""),
            f.get("syndromes_cn", ""),
            f.get("herbs_cn", ""),
        ])
        matched = []
        for kw in symptom_keywords:
            if kw and kw in haystack and kw not in matched:
                matched.append(kw)
        alignment[name] = matched
    return alignment


def _build_log_data(query_text: str,
                    symptom_keywords: list[str],
                    raw_results: list[tuple[dict, float]],
                    top5: list[dict]) -> dict:
    """构造 3 步查询日志，喂给 render_log_timeline。"""
    # 步骤 1：症状解析
    step1 = {
        "time_label": "1",
        "database_name": "症状解析（NLP）",
        "input": query_text,
        "input_fromwhere": "用户输入",
        "output_with_score": symptom_keywords or [query_text],
        "chosen_output": symptom_keywords or [query_text],
    }

    # 步骤 2：BGE 语义检索
    raw_names_with_score = [
        f"{_safe_str(r.get('Formula_cn_name'), '无名')} ({s:.2f})"
        for r, s in raw_results
    ]
    step2 = {
        "time_label": "2",
        "database_name": "HERB_formula_info_v2（BGE 语义索引）",
        "input": " / ".join(symptom_keywords) or query_text,
        "input_fromwhere": "步骤 1",
        "output_with_score": raw_names_with_score,
        "chosen_output": [f["cn_name"] for f in top5],
    }

    # 步骤 3：症状对齐 / 重排
    total_kw = max(len(symptom_keywords), 1)
    step3_out = [
        f"{f['cn_name']} (对齐 {len(f.get('matched_symptoms', []))}/{total_kw})"
        for f in top5
    ]
    step3 = {
        "time_label": "3",
        "database_name": "症状 ↔ 药方 对齐重排",
        "input": " / ".join([f["cn_name"] for f in top5]),
        "input_fromwhere": "步骤 2",
        "output_with_score": step3_out,
        "chosen_output": [f["cn_name"] for f in top5],
    }
    return {"entries": [step1, step2, step3], "total_label": 3}


def _suggest_missing_symptoms(
    formulas: list[dict],
    existing_symptoms: list[str],
    top_n: int = 5,
) -> list[dict]:
    """从 top10 药方的适应症中提取候选症状，按跨药方重叠数排序。"""
    try:
        from backend.semantic import _load_symptom_index
        _, symptom_meta = _load_symptom_index()
    except Exception:
        return []

    known_symptoms: list[str] = []
    for row in symptom_meta:
        name = str(row.get("TCM_symptom_name", "")).strip()
        if name and len(name) >= 2 and name.lower() not in ("nan", "none", ""):
            known_symptoms.append(name)

    existing_set = {s.strip() for s in existing_symptoms}

    symptom_overlap: dict[str, int] = {}
    for f in formulas:
        text = " ".join([
            f.get("indications_cn", ""),
            f.get("syndromes_cn", ""),
        ])
        seen: set[str] = set()
        for sym in known_symptoms:
            sym_stripped = sym.strip()
            if sym_stripped not in seen and sym_stripped in text and sym_stripped not in existing_set:
                seen.add(sym_stripped)
                symptom_overlap[sym_stripped] = symptom_overlap.get(sym_stripped, 0) + 1

    sorted_symptoms = sorted(symptom_overlap.items(), key=lambda x: x[1], reverse=True)
    return [
        {"symptom": s, "overlap_count": c, "total_formulas": len(formulas)}
        for s, c in sorted_symptoms[:top_n]
    ]


def query_by_symptom(symptom: str) -> dict:
    """model=2: 按症状关键词推荐药方（语义检索 + 关键词对齐 + 日志/置信度）"""
    if not symptom or not symptom.strip():
        return _fail("症状描述不能为空")

    kw = symptom.strip()
    symptom_keywords = _split_symptoms(kw)

    # 术语标准化 + 去重（如 头疼+头痛 → 只保留一个 头痛）
    dedup_keywords, norm_map, dedup_merged = _normalize_and_dedup(symptom_keywords)

    # 主路径：BGE 语义检索（用标准化+去重后的术语）
    semantic_query = " ".join(dedup_keywords) or kw
    raw_results: list[tuple[dict, float]] = []
    try:
        from backend.semantic import semantic_search_formula
        raw_results = semantic_search_formula(semantic_query, top_k=10)
    except Exception:
        raw_results = []

    # 回退路径：CSV 关键词匹配（用标准化术语）
    used_fallback = False
    if not raw_results:
        df = _get_formula_df()
        fallback_kw = "|".join(dedup_keywords) or kw
        mask = (
            df["Indications_in_Chinese"].str.contains(fallback_kw, case=False, na=False, regex=True)
            | df["Indications_in_English"].str.contains(fallback_kw, case=False, na=False, regex=True)
            | df["Syndromes_in_Chinese"].str.contains(fallback_kw, case=False, na=False, regex=True)
            | df["Syndromes_in_English"].str.contains(fallback_kw, case=False, na=False, regex=True)
        )
        hits = df[mask]
        if hits.empty:
            return _fail(f"未找到与症状相关的药方：{kw}")
        raw_results = [(row.to_dict(), 0.0) for _, row in hits.head(10).iterrows()]
        used_fallback = True

    # 转换为 item
    items: list[dict] = []
    raw_scores: list[float] = []
    for row, score in raw_results:
        item = _formula_row_to_item(row, score=score)
        items.append(item)
        raw_scores.append(float(score))

    # softmax 归一化分数（无语义分数时给均匀分布）
    if any(s > 0 for s in raw_scores):
        normalized = _softmax_normalize(raw_scores)
    else:
        normalized = [1.0 / len(items)] * len(items)
    for item, ns, rs in zip(items, normalized, raw_scores):
        item["normalized_score"] = float(round(ns, 4))
        item["raw_score"] = float(round(rs, 4))

    # 症状对齐：用标准化后的术语匹配（胃痛→胃脘痛）
    alignment = _build_symptom_alignment(dedup_keywords, items)
    for item in items:
        item["matched_symptoms"] = alignment.get(item["cn_name"], [])

    # 排序：先按对齐数（多者优先），再按 normalized_score
    items.sort(
        key=lambda x: (len(x.get("matched_symptoms", [])), x.get("normalized_score", 0.0)),
        reverse=True,
    )

    top5 = items[:5]
    top5_formulas = [
        {
            "cn_name":          f["cn_name"],
            "normalized_score": f.get("normalized_score", 0.0),
            "herbs_cn":         f["herbs_cn"],
            "indications_cn":   f["indications_cn"],
            "syndromes_cn":     f["syndromes_cn"],
            "matched_symptoms": f.get("matched_symptoms", []),
        }
        for f in top5
    ]

    log_data = _build_log_data(kw, dedup_keywords, raw_results, top5)

    # 置信度：复合乘法模型（语义质量 × 症状数因子 × 覆盖率）
    if any(s > 0 for s in raw_scores):
        top5_raw = sorted(raw_scores, reverse=True)[:5]
        # BGE cosine 在 TCM 文本上基线 ≈ 0.2，重映射：(x-0.2)/0.5 → 0~1
        raw_mean = float(np.clip(np.mean(top5_raw), 0.0, 1.0))
        semantic_quality = float(np.clip((raw_mean - 0.2) / 0.5, 0.0, 1.0))

        n_symptoms = len(dedup_keywords)
        count_factor = {1: 0.55, 2: 0.70, 3: 0.85, 4: 0.95}.get(n_symptoms, 1.0)

        all_matched = set()
        for f in top5:
            all_matched.update(f.get("matched_symptoms", []))
        n_matched = len(all_matched)
        coverage_safe = 0.45 + 0.55 * min(n_matched / 3.0, 1.0)

        confidence = float(np.clip(
            semantic_quality * count_factor * coverage_safe, 0.0, 1.0
        ))
    else:
        max_align = max((len(f.get("matched_symptoms", [])) for f in top5), default=0)
        n_symptoms = len(dedup_keywords)
        n_matched = max_align
        count_factor = {1: 0.55, 2: 0.70, 3: 0.85, 4: 0.95}.get(n_symptoms, 1.0)
        coverage_safe = 0.45 + 0.55 * min(n_matched / 3.0, 1.0)
        confidence = float(np.clip(coverage_safe * count_factor, 0.0, 1.0))

    # 症状补充建议：置信度不足时，从 top10 药方适应症中推荐候选症状
    # 排除集同时包含原始词和标准化词，防止任一形态被重复推荐
    exclude_set = list(dict.fromkeys(symptom_keywords + dedup_keywords))
    suggested_symptoms: list[dict] = []
    if confidence < 0.7:
        suggested_symptoms = _suggest_missing_symptoms(items, exclude_set)
        if suggested_symptoms:
            step4 = {
                "time_label": "4",
                "database_name": "症状补充建议（Top10 治法关联分析）",
                "input": " / ".join([f["cn_name"] for f in top5]),
                "input_fromwhere": "步骤 3",
                "output_with_score": [
                    f"{s['symptom']} (重叠 {s['overlap_count']}/{s['total_formulas']})"
                    for s in suggested_symptoms
                ],
                "chosen_output": [s["symptom"] for s in suggested_symptoms],
            }
            log_data["entries"].append(step4)
            log_data["total_label"] = 4

    _translate_formula_fields(items)
    return _ok({
        "total":              len(items),
        "items":              items,
        "top5_formulas":      top5_formulas,
        "symptom_alignment":  {k: v for k, v in alignment.items() if v},
        "all_symptoms":       symptom_keywords or [kw],
        "normalized_symptoms": dedup_keywords,
        "norm_map":           {k: v for k, v in norm_map.items() if v != [k]},
        "dedup_merged":       dedup_merged,
        "log_data":           log_data,
        "confidence":         confidence,
        "match_type":         "fallback" if used_fallback else "semantic",
        "suggested_symptoms": suggested_symptoms,
    })


# ── 统一入口（供 app.py 调用）─────────────────────────────────────────────────
def query(model: int, herb: str = "", formula: str = "") -> dict:
    """
    model=0: 中药查询（使用 herb 字段）
    model=1: 药方查询（使用 formula 字段）
    model=2: 症状推荐（herb 或 formula 字段均可作为症状关键词）
    model=3: 症状 → 相关症状/证候概念 → 药方 → 中草药 推理链路（含 LLM 解读）
    """
    _timings.clear()
    if model == 0:
        with _clock("mode0_total"):
            result = query_herb(herb)
        result["data"]["_timing"] = _get_timings()
        return result
    elif model == 1:
        with _clock("mode1_total"):
            result = query_formula(formula)
        result["data"]["_timing"] = _get_timings()
        return result
    elif model == 2:
        with _clock("mode2_total"):
            result = query_by_symptom(herb or formula)
        result["data"]["_timing"] = _get_timings()
        return result
    elif model == 3:
        keyword = (herb or formula or "").strip()
        if not keyword:
            return _fail("症状描述不能为空")

        with _clock("mode3_normalize"):
            symptom_keywords = _split_symptoms(keyword)
            dedup_keywords, norm_map, dedup_merged = _normalize_and_dedup(symptom_keywords)
            normalized_text = " ".join(dedup_keywords) or keyword

        try:
            with _clock("mode3_chain_reasoning"):
                from backend.chain_reasoning import query_chain
                chain_res = query_chain(normalized_text)
            if isinstance(chain_res, dict) and chain_res.get("error"):
                return _fail(chain_res.get("error"))

            chain_res["raw_symptom"] = keyword
            chain_res["norm_map"] = {k: v for k, v in norm_map.items() if v != [k]}
            chain_res["dedup_merged"] = dedup_merged

            with _clock("mode3_llm"):
                try:
                    from backend.llm import generate_explanation
                    chain_res["llm_explanation"] = generate_explanation(chain_res)
                except Exception as e:
                    chain_res["llm_explanation"] = f"（LLM 解释生成失败：{e}）"

            result = _ok({"chain": chain_res})
        except Exception as e:
            return _fail(f"推理链路查询失败：{e}")

        # 合并 chain_reasoning 内部计时
        if "perf" in chain_res:
            _timings.update(chain_res.pop("perf"))
        _timings["mode3_total"] = sum(_timings.values())
        result["data"]["_timing"] = _get_timings()
        return result
    else:
        return _fail(f"未知模式：{model}")
