"""
逻辑链路推理模块（模式 3）
提供 症状→相关症状/证候概念→药方→中草药 的完整推理链路

基于 phase1 的 CSV 数据 + BGE 向量检索，无需 PostgreSQL。
数据来源：
  - Symptom_Properties.xlsx  → 症状/证候概念表
  - HERB_formula_info_v2.csv → 药方表
  - HERB_herb_info_v1.csv    → 中草药表
"""
from __future__ import annotations

import math
import re
import time
from typing import Any

_perf: dict[str, float] = {}

import numpy as np


# ── 语义检索接口（懒导入，避免循环引用）──────────────────────────────────────

def _search_symptom(query: str, top_k: int = 10):
    from backend.semantic import semantic_search_symptom
    return semantic_search_symptom(query, top_k)


def _search_formula(query: str, top_k: int = 10):
    from backend.semantic import semantic_search_formula
    return semantic_search_formula(query, top_k)


def _search_herb(query: str, top_k: int = 10):
    from backend.semantic import semantic_search_herb
    return semantic_search_herb(query, top_k)


def _encode(text: str) -> np.ndarray:
    from backend.semantic import encode_text
    return encode_text(text)


# ── 归一化：softmax with temperature ─────────────────────────────────────────

def _softmax_normalize(scores: list[float], temperature: float = 0.05) -> list[float]:
    """对 cosine similarity 列表做 softmax 归一化，结果之和为 1，温度越低差距越大。"""
    arr = np.array(scores, dtype=np.float64) / temperature
    arr -= arr.max()
    exp = np.exp(arr)
    return (exp / exp.sum()).tolist()


# ── 工具函数 ───────────────────────────────────────────────────────────────────

def _is_valid(val) -> bool:
    if val is None:
        return False
    if isinstance(val, str) and val.strip() in ("", "None", "nan", "NaN", "none", "null"):
        return False
    if isinstance(val, float) and math.isnan(val):
        return False
    return True


def _safe(val, default: str = "") -> str:
    return str(val) if _is_valid(val) else default


def _parse_herb_names(herbs_text: str) -> list[str]:
    if not _is_valid(herbs_text):
        return []
    text = herbs_text.replace("，", ",").replace("、", ",").replace("；", ",").replace(";", ",")
    herbs = [re.sub(r"\([^)]*\)", "", h).strip() for h in text.split(",")]
    return [h for h in herbs if h and len(h) >= 1]


# ── 推理链路核心 ───────────────────────────────────────────────────────────────

def _step1_symptom_to_concept(symptom: str) -> list[dict[str, Any]]:
    """步骤1：症状 → 相关症状/证候概念（语义检索 Symptom_Properties）"""
    results = _search_symptom(symptom, top_k=10)
    concepts = []
    for row, score in results:
        name = _safe(row.get("TCM_symptom_name"))
        if not name:
            continue
        concepts.append({
            "name": name,
            "similarity": float(score),
            "raw_similarity": float(score),
            "definition": _safe(row.get("Symptom_definition"), "无定义"),
            "property": _safe(row.get("Symptom_property"), "无属性"),
            "locus": _safe(row.get("Symptom_locus"), ""),
        })
    if concepts:
        raw_scores = [d["similarity"] for d in concepts]
        normed = _softmax_normalize(raw_scores)
        for d, s in zip(concepts, normed):
            d["similarity"] = round(s, 4)
    return concepts


def _step2_concept_to_formula(concepts: list[dict], symptom: str) -> list[dict[str, Any]]:
    """步骤2：相关概念 → 药方（用概念名+症状作为查询，语义检索药方表）"""
    concept_names = [d["name"] for d in concepts[:3]]
    query_text = " ".join(concept_names) + " " + symptom
    results = _search_formula(query_text, top_k=10)

    formulas = []
    seen = set()
    for row, score in results:
        name = _safe(row.get("Formula_cn_name"))
        if not name or name in seen:
            continue
        seen.add(name)
        formulas.append({
            "name": name,
            "similarity": float(score),
            "raw_similarity": float(score),
            "herbs": _safe(row.get("Herbs_in_Chinese"), "未知成分"),
            "indication": _safe(row.get("Indications_in_Chinese"),
                                _safe(row.get("Syndromes_in_Chinese"), "无适应症")),
            "syndromes": _safe(row.get("Syndromes_in_Chinese"), ""),
            "_raw": row,
        })
    if formulas:
        raw_scores = [f["similarity"] for f in formulas]
        normed = _softmax_normalize(raw_scores)
        for f, s in zip(formulas, normed):
            f["similarity"] = round(s, 4)
    return formulas


def _step3_formula_to_herb(formulas: list[dict]) -> list[dict[str, Any]]:
    """步骤3：药方 → 中草药（从 top3 药方提取草药名，逐个在 herb 表中语义检索）"""
    all_herb_names: list[str] = []
    for f in formulas[:3]:
        all_herb_names.extend(_parse_herb_names(f["herbs"]))

    unique_herbs = list(dict.fromkeys(all_herb_names))
    if not unique_herbs:
        return []

    # 批量查询：用合并文本做一次大范围检索
    combined = " ".join(unique_herbs)
    batch_results = _search_herb(combined, top_k=min(len(unique_herbs) * 2, 30))

    # 名称匹配
    herbs: list[dict[str, Any]] = []
    matched: set[str] = set()

    for herb_name in unique_herbs:
        for row, score in batch_results:
            cn = _safe(row.get("Herb_cn_name"))
            if not cn:
                continue
            if herb_name in cn or cn in herb_name:
                if cn not in matched:
                    matched.add(cn)
                    herbs.append({
                        "name": cn,
                        "similarity": float(score),
                        "raw_similarity": float(score),
                        "function": _safe(row.get("Function"), "无功效"),
                        "indication": _safe(row.get("Indication"), "无主治"),
                        "category": _safe(row.get("Therapeutic_cn_class"),
                                          _safe(row.get("Properties"), "未知分类")),
                    })
                break

    # 未匹配的逐个检索
    for herb_name in unique_herbs:
        if any(herb_name in h["name"] or h["name"] in herb_name for h in herbs):
            continue
        per_results = _search_herb(herb_name, top_k=3)
        for row, score in per_results:
            cn = _safe(row.get("Herb_cn_name"))
            if cn and (herb_name in cn or cn in herb_name) and cn not in matched:
                matched.add(cn)
                herbs.append({
                    "name": cn,
                    "similarity": float(score),
                    "raw_similarity": float(score),
                    "function": _safe(row.get("Function"), "无功效"),
                    "indication": _safe(row.get("Indication"), "无主治"),
                    "category": _safe(row.get("Therapeutic_cn_class"),
                                      _safe(row.get("Properties"), "未知分类")),
                })
                break

    if herbs:
        raw_scores = [h["similarity"] for h in herbs]
        normed = _softmax_normalize(raw_scores)
        for h, s in zip(herbs, normed):
            h["similarity"] = round(s, 4)
    return herbs


def _score_level(score: float | None) -> str:
    if score is None:
        return "unavailable"
    if score >= 0.50:
        return "high"
    if score >= 0.35:
        return "medium"
    if score >= 0.25:
        return "low"
    return "weak"


def _semantic_pair_score(symptom: str, text: str) -> float | None:
    text = _safe(text).strip()
    if not text:
        return None
    s_vec = _encode(symptom)
    t_vec = _encode(text)
    return round(float(np.dot(s_vec, t_vec)), 4)


def _semantic_consistency_check(
    symptom: str,
    concepts: list[dict],
    formulas: list[dict],
    herbs: list[dict],
) -> dict[str, Any]:
    """检查推荐链路文本语义是否自洽，不代表医学诊断或临床疗效。"""
    concept_text = " ".join(
        f"{c.get('name', '')} {c.get('definition', '')} {c.get('property', '')}"
        for c in concepts[:5]
    )
    formula_text = " ".join(
        f"{f.get('name', '')} {f.get('syndromes', '')} {f.get('indication', '')}"
        for f in formulas[:5]
    )
    herb_text = " ".join(
        f"{h.get('function_cn') or h.get('function', '')} {h.get('indication_cn') or h.get('indication', '')}"
        for h in herbs
        if (h.get("function_cn") or h.get("function") or h.get("indication_cn") or h.get("indication"))
    )

    component_scores = {
        "symptom_to_concepts": _semantic_pair_score(symptom, concept_text),
        "symptom_to_formulas": _semantic_pair_score(symptom, formula_text),
        "symptom_to_herbs": _semantic_pair_score(symptom, herb_text),
    }
    available = [s for s in component_scores.values() if s is not None]
    overall = round(float(np.mean(available)), 4) if available else None
    level = _score_level(overall)

    level_text = {
        "high": "语义一致性较高",
        "medium": "语义一致性中等",
        "low": "语义一致性较低",
        "weak": "语义一致性较弱，建议人工复核",
        "unavailable": "缺少可计算的语义证据",
    }[level]

    score_text = "不可用" if overall is None else f"{overall:.3f}"
    summary = (
        f"语义自洽性检查：总分 {score_text}，{level_text}。"
        "该分数仅表示症状描述、相关概念、药方主治和中草药功效文本在向量空间中的相关性，"
        "不代表医学诊断、临床疗效或用药建议。"
    )

    return {
        "method": "embedding_semantic_consistency",
        "overall_score": overall,
        "level": level,
        "level_text": level_text,
        "component_scores": component_scores,
        "thresholds": {"low": 0.25, "medium": 0.35, "high": 0.50},
        "evidence": {
            "symptom": symptom,
            "concept_text": concept_text[:300],
            "formula_text": formula_text[:300],
            "herb_effect_text": herb_text[:300],
        },
        "claim_scope": "仅表示文本语义一致性，不代表医学诊断、临床疗效或用药建议。",
        "summary": summary,
    }


def _build_chain_text(symptom: str, concepts: list[dict], formulas: list[dict],
                      herbs: list[dict], consistency: dict[str, Any]) -> str:
    """构建推理链路的文本描述"""
    parts = [f"症状: {symptom}\n"]

    parts.append("相关症状/证候概念:")
    for i, d in enumerate(concepts[:5], 1):
        parts.append(
            f"  {i}. {d['name']} (相似度: {d['similarity']:.3f})\n"
            f"     定义: {d['definition']}\n"
            f"     属性: {d['property']}"
        )

    parts.append("\n推荐药方:")
    for i, f in enumerate(formulas[:5], 1):
        parts.append(
            f"  {i}. {f['name']} (相似度: {f['similarity']:.3f})\n"
            f"     成分: {f['herbs']}\n"
            f"     适应症: {f['indication']}"
        )

    parts.append("\n组成中草药:")
    if herbs:
        for i, h in enumerate(herbs, 1):
            func_cn = h.get("function_cn", "") or h.get("function", "")
            ind_cn  = h.get("indication_cn", "") or h.get("indication", "")
            cat_cn  = h.get("category_cn", "") or h.get("category", "")
            parts.append(
                f"  {i}. {h['name']} (相似度: {h['similarity']:.3f})\n"
                f"     功效: {func_cn}\n"
                f"     主治: {ind_cn}\n"
                f"     分类: {cat_cn}"
            )
    else:
        parts.append("  无匹配中草药")

    parts.append(f"\n语义自洽性检查结果:\n{consistency.get('summary', '')}")
    return "\n".join(parts)


# ── 统一入口 ───────────────────────────────────────────────────────────────────

def query_chain(symptom: str, **_kwargs) -> dict[str, Any]:
    """
    执行症状→相关症状/证候概念→药方→中草药推理链路。

    使用 CSV 数据 + BGE 向量检索，全部本地计算，无需数据库。

    Args:
        symptom: 用户输入的症状描述

    Returns:
        统一结构的推理结果字典
    """
    if not symptom or not symptom.strip():
        return {"error": "症状描述不能为空"}

    symptom = symptom.strip()
    _perf.clear()

    try:
        t0 = time.perf_counter()
        concepts = _step1_symptom_to_concept(symptom)
        _perf["step1_symptom_to_concept"] = time.perf_counter() - t0
        if not concepts:
            return {"error": "未找到相关症状/证候概念"}

        t0 = time.perf_counter()
        formulas = _step2_concept_to_formula(concepts, symptom)
        _perf["step2_concept_to_formula"] = time.perf_counter() - t0
        if not formulas:
            return {"error": "未找到相关药方"}

        t0 = time.perf_counter()
        herbs = _step3_formula_to_herb(formulas)
        _perf["step3_formula_to_herb"] = time.perf_counter() - t0

        # 翻译草药功效主治为中英双语（三字段合并为一次 API 调用）
        t0 = time.perf_counter()
        fields = ("function", "indication", "category")
        all_texts = [h.get(f, "") or "" for h in herbs for f in fields]
        try:
            from backend.llm import translate_tcm
            translated = translate_tcm(all_texts)
        except Exception:
            import traceback
            traceback.print_exc()
            translated = [""] * len(all_texts)
        _perf["translation"] = time.perf_counter() - t0
        idx = 0
        for h in herbs:
            for f in fields:
                cn = translated[idx] if idx < len(translated) else ""
                idx += 1
                if cn:
                    h[f"{f}_cn"] = cn

        t0 = time.perf_counter()
        consistency = _semantic_consistency_check(symptom, concepts, formulas, herbs)
        _perf["step4_semantic_consistency"] = time.perf_counter() - t0

        chain_text = _build_chain_text(symptom, concepts, formulas, herbs, consistency)

        return {
            "chain_result": chain_text,
            "symptom": symptom,
            "concepts": [
                {"name": d["name"], "similarity": d["similarity"],
                 "raw_similarity": d.get("raw_similarity"),
                 "definition": d["definition"], "property": d["property"],
                 "locus": d.get("locus", "")}
                for d in concepts[:5]
            ],
            "diseases": [
                {"name": d["name"], "similarity": d["similarity"],
                 "raw_similarity": d.get("raw_similarity"),
                 "definition": d["definition"], "property": d["property"],
                 "locus": d.get("locus", "")}
                for d in concepts[:5]
            ],
            "formulas": [
                {"name": f["name"], "similarity": f["similarity"],
                 "raw_similarity": f.get("raw_similarity"),
                 "herbs": f["herbs"], "indication": f["indication"]}
                for f in formulas[:5]
            ],
            "herbs": [
                {"name": h["name"], "similarity": h["similarity"],
                 "raw_similarity": h.get("raw_similarity"),
                 "function": h["function"], "indication": h["indication"],
                 "category": h.get("category", ""),
                 "function_cn": h.get("function_cn", ""),
                 "indication_cn": h.get("indication_cn", ""),
                 "category_cn": h.get("category_cn", "")}
                for h in herbs
            ],
            "semantic_consistency": consistency,
            "closed_loop_validation": consistency.get("summary", ""),
            "is_mock": False,
            "perf": dict(_perf),
        }
    except Exception as e:
        return {"error": str(e)}
