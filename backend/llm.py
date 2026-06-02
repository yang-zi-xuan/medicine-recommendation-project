"""
LLM 模块 — 调用 DeepSeek API 将推理链路转化为自然语言解释
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests

_BASE_URL = "https://api.deepseek.com"
_MODEL = "deepseek-v4-flash"


def _load_dotenv_key() -> str:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return ""

    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "DEEPSEEK_API_KEY":
                return value.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _get_api_key() -> str:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    try:
        import streamlit as st
        key = str(st.secrets.get("DEEPSEEK_API_KEY", "")).strip()
        if key:
            return key
    except Exception:
        pass
    return _load_dotenv_key()

_SYSTEM_PROMPT = """\
你是一位经验丰富的中医师，擅长用通俗易懂的语言向患者解释病情和用药逻辑。

你会收到一份中医推理链路数据，包含：
- 患者的症状
- 根据症状匹配到的相关症状/证候概念（含相似度）
- 推荐的药方（含组成和适应症）
- 药方中的中草药（含功效和主治）
- 语义自洽性检查结果（含余弦相似度分数，范围0~1）

请将这些信息组织成一段自然、流畅、有逻辑的解释，让患者能够理解：
1. 根据症状，系统匹配到了哪些相关症状/证候概念，为什么
2. 为什么推荐这些药方，它们分别针对什么
3. 药方中的关键中草药各起什么作用
4. 整体文本链路在语义上是否自洽

要求：
- 使用中文，语言温和专业
- 草药功效和主治优先使用 function_cn / indication_cn 等中文字段，不要直接复制英文字段
- 不要使用markdown格式、标题符号
- 按照"辨证 → 选方 → 用药"的逻辑顺序组织
- 每段之间空一行
- 总长度控制在 300-500 字
- 在末尾加上免责声明：本分析仅供参考，具体诊疗请咨询专业中医师
- 不得将结果表述为疾病诊断、临床疗效验证或正式用药建议
- 根据语义自洽性分数调整语气：分数>0.5时可使用较确定的语义相关表述；0.3~0.5时宜用"可能""倾向于"等保留性措辞；分数<0.3时须明确提示证据不足\
"""


def generate_explanation(chain_result: dict[str, Any]) -> str:
    """
    调用 DeepSeek API，将推理链路数据转化为自然语言解释。

    Args:
        chain_result: query_chain() 返回的推理结果字典

    Returns:
        自然语言解释文本
    """
    api_key = _get_api_key()
    if not api_key:
        return "LLM 解读已跳过：未配置 DEEPSEEK_API_KEY。"

    consistency = chain_result.get("semantic_consistency") or {}
    val_score = consistency.get("overall_score")
    if val_score is None:
        val_score = 0.5

    user_content = json.dumps(chain_result, ensure_ascii=False, indent=2)
    user_content = (
        f"[语义自洽性分数: {float(val_score):.3f}]\n"
        f"{user_content}"
    )

    try:
        resp = requests.post(
            f"{_BASE_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": _MODEL,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.7,
                "max_tokens": 1024,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except requests.exceptions.Timeout:
        return "DeepSeek API 请求超时，请稍后重试。"
    except requests.exceptions.RequestException as e:
        return f"DeepSeek API 调用失败：{e}"
    except (KeyError, IndexError):
        return "DeepSeek API 返回格式异常，无法解析。"


# ── 翻译缓存 ───────────────────────────────────────────────────────────────
_TRANS_CACHE_PATH = Path(__file__).resolve().parent.parent / "data_csv" / "translation_cache.json"


def _load_trans_cache() -> dict:
    try:
        if _TRANS_CACHE_PATH.exists():
            with open(_TRANS_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_trans_cache(cache: dict) -> None:
    try:
        with open(_TRANS_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def translate_tcm(texts: list[str]) -> list[str]:
    """将英文中医文本批量翻译为中文（持久化缓存，分批 API 调用）。"""
    texts = [str(t).strip() for t in texts]
    if not texts:
        return []

    api_key = _get_api_key()
    if not api_key:
        return [""] * len(texts)

    cache = _load_trans_cache()
    results: list[str] = []
    pending: list[tuple[int, str]] = []

    for i, t in enumerate(texts):
        if not t or t.lower() in ("na", "none", "nan", ""):
            results.append("")
        elif t in cache:
            results.append(cache[t])
        else:
            results.append("")
            pending.append((i, t))

    if not pending:
        return results

    # 分批翻译，每批最多 15 条
    BATCH_SIZE = 15
    for batch_start in range(0, len(pending), BATCH_SIZE):
        batch = pending[batch_start:batch_start + BATCH_SIZE]
        lines = "\n".join(f"{j+1}. {t}" for j, (_, t) in enumerate(batch))
        prompt = f"""Translate each numbered TCM English text into concise Chinese:
{lines}

Rules:
- Use authentic TCM terminology (中医术语)
- Keep each translation on one line
- Output ONLY "N. Chinese text" format, one per line"""

        try:
            resp = requests.post(
                f"{_BASE_URL}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": _MODEL,
                    "messages": [
                        {"role": "system", "content": "你是一位专业的中医药翻译。将英文中医文本翻译为简洁地道的中医术语。"},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 2048,
                },
                timeout=30,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()

            for line in raw.split("\n"):
                line = line.strip()
                if not line:
                    continue
                for j, (idx, _) in enumerate(batch):
                    prefix = f"{j+1}."
                    if line.startswith(prefix):
                        cn = line[len(prefix):].strip()
                        if cn:
                            results[idx] = cn
                            cache[batch[j][1]] = cn
                        break

        except Exception:
            import traceback
            traceback.print_exc()

    _save_trans_cache(cache)
    return results
