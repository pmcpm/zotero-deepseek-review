#!/usr/bin/env python3
"""Auditable literature-review workflow. Writes task-owned files; no desktop input."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from model_client import ModelClient

SKILL = Path(__file__).resolve().parents[1]
IDS = re.compile(r"\[\[(R\d{3,})\]\]")
FORBIDDEN = ["不表明", "不代表", "未使用", "不作为", "不能说明", "并不意味着", "不能证明", "并非", "不等于", "仅仅", "需要强调", "值得注意"]
REQUIRED = ["title", "audience", "word_limit", "reference_target", "language_ratio", "citation_style", "hide_doi", "bilingual", "date_range", "source_scope", "supplement_dir", "output_docx", "zotero.collection_name"]
BIB_FIELDS = ["itemType", "title", "creators", "date", "publicationTitle", "volume", "issue", "pages", "DOI", "url", "language"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    raw = value.encode("utf8") if isinstance(value, str) else canonical(value).encode("utf8")
    return hashlib.sha256(raw).hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path: Path, value: Any, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x" if exclusive else "w", encoding="utf8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def dotted(obj: dict, key: str) -> Any:
    for part in key.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def init_task(root: Path, supplied: dict | None = None) -> dict:
    if (root / "task.json").exists():
        raise ValueError("Task already exists; edit its configuration and resume")
    task = {
        "schema_version": 1, "task_id": uuid.uuid4().hex, "title": None,
        "scope": "related_work", "audience": None, "context": "",
        "word_limit": None, "word_count_basis": "word", "reference_target": None,
        "language_ratio": None, "citation_style": None, "hide_doi": None, "bilingual": None,
        "date_range": None, "source_scope": None,
        "zotero": {"library_id": None, "collection_key": None, "collection_name": None},
        "supplement_dir": None, "output_docx": None, "outline": [], "author_materials": [],
        "forbidden_expressions": FORBIDDEN.copy(), "model": "deepseek-v4-flash",
        "model_api": {"provider": "cstcloud"}, "max_revisions": 2,
        "formatting": {"paper": "A4", "margin_cm": 2.54, "body_font": "宋体", "body_pt": 12,
                       "heading_font": "黑体", "line_spacing": 1.5, "first_line_characters": 2, "color": "000000"},
    }
    task.update(supplied or {})
    # Reject inline credentials/invalid transports before persisting configuration.
    ModelClient(task, validate_only=True)
    save(root / "task.json", task, exclusive=True)
    save(root / "evidence.json", [], exclusive=True)
    save(root / "supplements.json", [], exclusive=True)
    save(root / "state.json", {"phase": "clarification", "created_at": now()}, exclusive=True)
    return check_task(task)


def check_task(task: dict) -> dict:
    missing = [name for name in REQUIRED if dotted(task, name) in (None, "", [])]
    issues = []
    if task.get("model"):
        try:
            ModelClient(task, validate_only=True)
        except ValueError as error:
            issues.append(str(error))
    if task.get("word_limit") is not None and (type(task["word_limit"]) is not int or task["word_limit"] <= 0):
        issues.append("word_limit must be a positive integer")
    if type(task.get("max_revisions", 2)) is not int or task.get("max_revisions", 2) not in (0, 1, 2):
        issues.append("max_revisions must be 0, 1 or 2; additional calls require a new user instruction")
    for name in ("hide_doi", "bilingual"):
        if task.get(name) is not None and type(task[name]) is not bool:
            issues.append(name + " must be a boolean")
    if task.get("word_count_basis", "word") not in ("word", "characters_without_whitespace"):
        issues.append("Unsupported word_count_basis")
    return {"ready": not missing and not issues, "missing": missing, "issues": issues}


def normalize_title(text: str) -> str:
    return "".join(c.lower() for c in unicodedata.normalize("NFKC", text or "") if c.isalnum())


def normalize_doi(text: str) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", (text or "").strip(), flags=re.I).rstrip(" .").lower()


def normalize_metadata(data: dict) -> dict:
    result = {f: copy.deepcopy(data.get(f, "")) for f in BIB_FIELDS}
    result["DOI"] = normalize_doi(result["DOI"])
    result["creators"] = [{k: str(v).strip() for k, v in c.items() if k in ("creatorType", "firstName", "lastName", "name", "fieldMode")} for c in data.get("creators", [])]
    for key, value in result.items():
        if isinstance(value, str):
            result[key] = unicodedata.normalize("NFKC", value).strip()
    return result


def metadata_snapshot(cards: list[dict]) -> str:
    return digest([{"reference_id": c.get("reference_id"), "zotero_key": c.get("zotero_key"), "metadata": normalize_metadata(c.get("metadata", {}))} for c in cards])


def assign_ids(cards: list[dict]) -> list[dict]:
    used = {c["reference_id"] for c in cards if c.get("reference_id")}
    if len(used) != sum(bool(c.get("reference_id")) for c in cards):
        raise ValueError("Duplicate stable reference ID")
    if any(not re.fullmatch(r"R\d{3,}", rid) for rid in used):
        raise ValueError("Invalid reference ID")
    next_id = max([int(rid[1:]) for rid in used] + [0]) + 1
    for card in cards:
        if not card.get("reference_id"):
            card["reference_id"] = f"R{next_id:03d}"
            next_id += 1
    return cards


def audit_metadata(cards: list[dict], readback: list[dict] | None = None, collection_key: str | None = None) -> dict:
    issues: list[dict] = []
    if not cards:
        issues.append({"problem": "No selected literature"})
    duplicates = []
    seen_doi: dict[str, str] = {}
    seen_title: dict[str, str] = {}
    for card in cards:
        rid = card.get("reference_id", "?")
        data = card.get("metadata", {})
        for field in ("itemType", "title", "creators", "date"):
            if not data.get(field):
                issues.append({"reference_id": rid, "field": field, "problem": "missing"})
        if data.get("itemType") == "journalArticle" and not data.get("publicationTitle"):
            issues.append({"reference_id": rid, "field": "publicationTitle", "problem": "missing"})
        # Pages may be an article number. Do not invent pagination/issue/DOI.
        doi = normalize_doi(data.get("DOI", ""))
        if doi and not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
            issues.append({"reference_id": rid, "field": "DOI", "problem": "invalid syntax"})
        verification = card.get("verification", {})
        if not verification.get("metadata_checked") or not card.get("sources"):
            issues.append({"reference_id": rid, "problem": "Codex source-based metadata verification required"})
        title = normalize_title(data.get("title", ""))
        for index, key, kind in ((seen_doi, doi, "DOI"), (seen_title, title, "title")):
            if key:
                if key in index:
                    duplicates.append({"a": index[key], "b": rid, "basis": kind})
                else:
                    index[key] = rid
    if readback is not None:
        by_key = {x.get("key", x.get("data", {}).get("key")): x.get("data", x) for x in readback}
        for card in cards:
            key = card.get("zotero_key")
            actual = by_key.get(key)
            if not key or actual is None:
                issues.append({"reference_id": card.get("reference_id"), "problem": "missing Zotero readback"})
                continue
            expected = normalize_metadata(card["metadata"])
            observed = normalize_metadata(actual)
            for field in BIB_FIELDS:
                if expected[field] != observed[field]:
                    issues.append({"reference_id": card["reference_id"], "field": field, "problem": "readback mismatch", "expected": expected[field], "observed": observed[field]})
            if collection_key and collection_key not in actual.get("collections", []):
                issues.append({"reference_id": card["reference_id"], "field": "collections", "problem": "target collection missing"})
            for attachment_key in card.get("attachment_keys", []):
                attachment = by_key.get(attachment_key)
                if not attachment or attachment.get("parentItem") != key:
                    issues.append({"reference_id": card["reference_id"], "field": "attachment_keys", "problem": "attachment parent mismatch", "key": attachment_key})
    return {"passed": not issues and not duplicates, "issues": issues, "duplicates": duplicates, "stage": "post_import" if readback is not None else "pre_import", "metadata_snapshot_hash": metadata_snapshot(cards), "collection_key": collection_key}


def audit_evidence(cards: list[dict]) -> list[dict]:
    issues = []
    for card in cards:
        if not card.get("required", True):
            continue
        verify = card.get("verification", {})
        if card.get("requires_fulltext", True) and not (verify.get("fulltext_checked") and verify.get("evidence_location")):
            issues.append({"reference_id": card.get("reference_id"), "problem": "full text and source location verification required"})
        if not card.get("supported_claims"):
            issues.append({"reference_id": card.get("reference_id"), "problem": "supported_claims required"})
    return issues


def supplement_status(root: Path) -> dict:
    task = load(root / "task.json")
    entries = load(root / "supplements.json")
    base = Path(task["supplement_dir"]).resolve()
    for entry in entries:
        filename = entry.get("expected_filename", "")
        path = (base / filename).resolve()
        if not filename or path.parent != base:
            raise ValueError("Supplement filename must be a simple filename within supplement_dir")
        if not path.is_file():
            entry["status"] = "waiting_file"
            continue
        payload = path.read_bytes()
        if path.suffix.lower() == ".pdf" and not payload[:1024].lstrip().startswith(b"%PDF-"):
            entry["status"] = "invalid_file"
            continue
        sha = hashlib.sha256(payload).hexdigest()
        if entry.get("sha256") and entry["sha256"] != sha:
            entry["status"] = "changed_requires_review"
        elif entry.get("status") != "fulltext_verified":
            entry["status"] = "received_requires_fulltext_review"
        entry.update({"sha256": sha, "path": str(path), "size": len(payload)})
    save(root / "supplements.json", entries)
    pending = [x for x in entries if x["status"] != "fulltext_verified"]
    return {"ready": not pending, "pending": pending}


def build_messages(task: dict, cards: list[dict], correction: dict | None = None) -> list[dict]:
    check = check_task(task)
    if not check["ready"]:
        raise ValueError("Clarification required: " + canonical(check))
    metadata = audit_metadata(cards)
    evidence_issues = audit_evidence(cards)
    if not metadata["passed"] or evidence_issues:
        raise ValueError("Literature verification required: " + canonical({"metadata": metadata, "evidence": evidence_issues}))
    if not task.get("outline"):
        raise ValueError("Codex must prepare a task-specific argument outline before writing")
    system = "你是中文学术相关工作与文献综述的独立写作者。正文全部由你撰写；调用方负责来源核对、写作任务组织、排版和引文转换。"
    rules = {
        "内容": "按研究路径比较和综合，围绕提供的行文逻辑组织连续学术正文。具体研究发现逐项对应证据卡及证据位置。",
        "引用": "使用稳定标识 [[R001]] 等；组合引用相邻书写。只使用给定 reference_id，每篇文献（除明确标记 required=false）至少有一处确切论点对应。输出省去文末参考文献列表，交由 Word Zotero 插件生成。",
        "长度": {"hard_limit": task["word_limit"], "basis": task.get("word_count_basis", "word"), "rule": "含小节标题，排除引文标识和参考文献。Word 口径按中文字符与英文词估算，返回前自行压缩到上限内。"},
        "字符": "所有连字符、词间连接及范围连接统一使用 ASCII '-' (U+002D)。",
        "语言": {"forbidden": task.get("forbidden_expressions", FORBIDDEN), "rule": "使用直接、精确的正面表述，删除冗余防御性表达。"},
        "作者材料": "本文具体方法、实验结果及创新点仅取自 author_materials；作者材料为空时以已有研究综合与研究需求结束。",
        "输出": "只输出小节标题和中文正文，标题单独成行，段间空行。省去模型自述、写作说明、字数报告、代码围栏及检查过程。",
        "自查": "返回前完成证据对应、引用覆盖、篇幅、禁用表达和连接符检查。",
    }
    exposed_task = {k: task.get(k) for k in ("title", "scope", "audience", "context", "reference_target", "language_ratio", "date_range", "source_scope", "outline", "author_materials")}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "固定写作规范：\n" + json.dumps(rules, ensure_ascii=False, indent=2) + "\n当前任务与论证结构：\n" + json.dumps(exposed_task, ensure_ascii=False, indent=2) + "\n核验文献证据卡：\n" + json.dumps(cards, ensure_ascii=False, indent=2)}]
    if correction:
        messages.extend([{"role": "assistant", "content": correction["body"]}, {"role": "user", "content": "请依据以下核查结果完整修正正文，继续遵守全部原始要求。保持已有证据范围与引用标识。只返回修正后的完整正文。\n" + json.dumps(correction["issues"], ensure_ascii=False, indent=2)}])
    return messages


def count_words(text: str, task: dict, count_file: Path | None = None) -> dict:
    plain = IDS.sub("", text)
    if task.get("word_count_basis") == "characters_without_whitespace":
        return {"count": len(re.sub(r"\s", "", plain)), "basis": "characters_without_whitespace", "exact": True}
    if count_file:
        result = load(count_file)
        if result.get("body_sha256") != digest(text):
            raise ValueError("Word count does not match this immutable body hash")
        if result.get("exact") is not True or result.get("basis") != "word":
            raise ValueError("Exact Word count required")
        if type(result.get("count")) is not int or result["count"] <= 0:
            raise ValueError("Word count must be a positive integer")
        return result
    return {"count": len(re.findall(r"[\u3400-\u9fff]|[A-Za-z0-9]+(?:[-&.][A-Za-z0-9]+)*", plain)), "basis": "word_estimate", "exact": False}


def audit_body(body: str, task: dict, cards: list[dict], count: dict | None = None) -> dict:
    ids = IDS.findall(body)
    expected = {c["reference_id"] for c in cards if c.get("required", True)}
    known = {c["reference_id"] for c in cards}
    count = count or count_words(body, task)
    issues = []
    if not body.strip():
        issues.append({"type": "empty_body"})
    if count["count"] > task["word_limit"]:
        issues.append({"type": "over_length", "count": count["count"], "limit": task["word_limit"], "exact": count["exact"]})
    for name, values in (("unknown_reference", sorted(set(ids) - known)), ("missing_reference", sorted(expected - set(ids)))):
        if values:
            issues.append({"type": name, "reference_ids": values})
    malformed = re.findall(r"\[\[(.*?)\]\]", body)
    bad = [x for x in malformed if not re.fullmatch(r"R\d{3,}", x)]
    if bad:
        issues.append({"type": "malformed_reference", "values": bad})
    leftover = IDS.sub("", body)
    if "[[" in leftover or "]]" in leftover:
        issues.append({"type": "malformed_reference_delimiter", "problem": "Unterminated, nested or stray citation delimiters remain"})
    banned = [s for s in task.get("forbidden_expressions", FORBIDDEN) if s in body]
    if banned:
        issues.append({"type": "forbidden_expression", "values": banned})
    hyphens = sorted(set(re.findall(r"[\u2010-\u2015\u2212\ufe63\uff0d]", body)))
    if hyphens:
        issues.append({"type": "non_ascii_hyphen", "values": hyphens})
    return {"body_sha256": digest(body), "count": count, "unique_references": len(set(ids)), "issues": issues,
            "mechanical_passed": not issues, "exact_count_pending": not count["exact"], "semantic_review_required": True, "content_modified": False}



def next_attempt(root: Path) -> int:
    return len(list((root / "writing").glob("attempt-*"))) if (root / "writing").exists() else 0


def latest_attempt(root: Path) -> Path:
    dirs = sorted((root / "writing").glob("attempt-*"))
    if not dirs:
        raise ValueError("No model response yet")
    return dirs[-1]


def write_draft(root: Path, mock: Path | None = None, correction: dict | None = None) -> dict:
    task, cards = load(root / "task.json"), load(root / "evidence.json")
    task_check = check_task(task)
    if not task_check["ready"]:
        raise ValueError("Clarification required: " + canonical(task_check))
    post_path = root / "metadata-post-import.json"
    if not post_path.is_file():
        raise ValueError("Post-import Zotero readback audit required before writing")
    post = load(post_path)
    if not post.get("passed") or post.get("metadata_snapshot_hash") != metadata_snapshot(cards):
        raise ValueError("Post-import audit is failed or stale; re-read Zotero and audit current metadata")
    if not task.get("zotero", {}).get("collection_key") or post.get("collection_key") != task["zotero"]["collection_key"]:
        raise ValueError("Post-import audit must be bound to the current target Zotero collection")
    if (root / "selected.json").exists():
        old_selection = load(root / "selected.json")
        save(root / "writing" / f"selection-history-{uuid.uuid4().hex}.json", old_selection, exclusive=True)
        save(root / "selected.json", {"state": "superseded", "output_document_allowed": False, "updated_at": now()})
    supplements = supplement_status(root)
    if not supplements["ready"]:
        save(root / "state.json", {"phase": "waiting_fulltext", "pending": supplements["pending"], "updated_at": now()})
        return {"phase": "waiting_fulltext", "pending": supplements["pending"]}
    client = ModelClient(task, mock)
    models = client.models(root / "writing/discovery")
    while True:
        index = next_attempt(root)
        if index > task.get("max_revisions", 2):
            state = {"phase": "needs_user_decision", "reason": "revision_limit", "updated_at": now()}
            save(root / "state.json", state)
            return state
        messages = build_messages(task, cards, correction)
        attempt = root / "writing" / f"attempt-{index:02d}"
        attempt.mkdir(parents=True, exist_ok=False)
        save(attempt / "task.json", task, exclusive=True)
        save(attempt / "evidence.json", cards, exclusive=True)
        save(attempt / "messages.json", messages, exclusive=True)
        (attempt / "prompt.txt").write_text("\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages), encoding="utf8")
        save(attempt / "request.json", {**client.request_metadata(), "messages_sha256": digest(messages), "available_models": models, "created_at": now(), "simulation": mock is not None}, exclusive=True)
        try:
            raw = client.chat(attempt, index)
        except Exception as error:
            save(root / "state.json", {"phase": "needs_user_decision", "reason": "inference_failure", "attempt": index, "error": str(error), "updated_at": now()})
            raise
        # Save the raw response internally. Never print reasoning_content or expose it as a deliverable.
        save(attempt / "raw.json", raw, exclusive=True)
        body = raw.get("choices", [{}])[0].get("message", {}).get("content")
        if not isinstance(body, str) or not body.strip():
            raise ValueError("Model returned empty final content; original response retained")
        (attempt / "body.txt").write_bytes(body.encode("utf8"))
        report = audit_body(body, task, cards)
        if raw.get("choices", [{}])[0].get("finish_reason") == "length":
            report["issues"].append({"type": "truncated_response", "problem": "Generation stopped at model output length limit"})
            report["mechanical_passed"] = False
        save(attempt / "audit-initial.json", report, exclusive=True)
        save(attempt / "manifest.json", {"body_sha256": digest(body), "raw_sha256": digest(raw), "messages_sha256": digest(messages), "task_sha256": digest(task), "evidence_sha256": digest(cards), "usage": raw.get("usage"), "finish_reason": raw.get("choices", [{}])[0].get("finish_reason"), "simulation": mock is not None}, exclusive=True)
        if report["issues"]:
            correction = {"body": body, "issues": report["issues"]}
            continue
        state = {"phase": "awaiting_exact_count_and_semantic_review", "attempt": index, "body_sha256": digest(body), "updated_at": now()}
        save(root / "state.json", state)
        return state


def review_draft(root: Path, review: Path, count_file: Path | None = None, mock: Path | None = None) -> dict:
    task, cards = load(root / "task.json"), load(root / "evidence.json")
    attempt = latest_attempt(root)
    body = (attempt / "body.txt").read_bytes().decode("utf8")
    manifest = load(attempt / "manifest.json")
    if not check_task(task)["ready"]:
        raise ValueError("Current task requires clarification")
    if manifest.get("task_sha256") != digest(task) or manifest.get("evidence_sha256") != digest(cards):
        raise ValueError("Draft task or evidence snapshot is stale; regenerate against current verified materials")
    post = load(root / "metadata-post-import.json")
    if not post.get("passed") or post.get("metadata_snapshot_hash") != metadata_snapshot(cards) or post.get("collection_key") != task.get("zotero", {}).get("collection_key"):
        raise ValueError("Current Zotero post-import readback audit required before selection")
    if not supplement_status(root)["ready"]:
        raise ValueError("Pending or changed full text must be reviewed before selection")
    if digest(body) != manifest["body_sha256"]:
        raise ValueError("Immutable model body changed")
    semantic = load(review)
    if semantic.get("body_sha256") != digest(body) or type(semantic.get("passed")) is not bool or not isinstance(semantic.get("issues", []), list):
        raise ValueError("Semantic review must identify the current body hash, passed flag and issue list")
    report = audit_body(body, task, cards, count_words(body, task, count_file))
    if manifest.get("finish_reason") == "length":
        report["issues"].append({"type": "truncated_response"})
        report["mechanical_passed"] = False
    stamp = uuid.uuid4().hex[:12]
    save(attempt / f"audit-reviewed-{stamp}.json", report, exclusive=True)
    save(attempt / f"semantic-review-{stamp}.json", semantic, exclusive=True)
    issues = report["issues"] + semantic.get("issues", [])
    if not semantic["passed"] and not semantic.get("issues"):
        raise ValueError("Failed semantic review must provide evidence-grounded correction issues")
    if issues:
        return write_draft(root, mock, {"body": body, "issues": issues})
    if report["exact_count_pending"]:
        raise ValueError("Exact Word count required before selecting a final draft")
    selected = {"attempt": int(attempt.name.split("-")[1]), "body_path": str(attempt / "body.txt"), "body_sha256": digest(body), "task_sha256": digest(task), "evidence_sha256": digest(cards), "word_count": report["count"], "semantic_review": str(attempt / f"semantic-review-{stamp}.json"), "simulation": manifest["simulation"], "output_document_allowed": not manifest["simulation"], "selected_at": now()}
    save(root / "selected.json", selected)
    save(root / "state.json", {"phase": "ready_for_word", "selected_body_sha256": digest(body), "simulation": manifest["simulation"], "updated_at": now()})
    return selected


def load_selected(root: Path, allow_simulation: bool = False) -> dict:
    """Public Word-stage gate. Return an approved, current and immutable selection."""
    root = Path(root).resolve()
    selected = load(root / "selected.json")
    task, cards, state = load(root / "task.json"), load(root / "evidence.json"), load(root / "state.json")
    if state.get("phase") != "ready_for_word":
        raise ValueError("Task is not ready for Word delivery")
    if not allow_simulation and (selected.get("simulation") or not selected.get("output_document_allowed")):
        raise ValueError("Simulation cannot authorize a production Word document")
    if selected.get("task_sha256") != digest(task) or selected.get("evidence_sha256") != digest(cards):
        raise ValueError("Selected draft is stale after task or evidence changes")
    if not check_task(task)["ready"]:
        raise ValueError("Current task requires clarification")
    if not supplement_status(root)["ready"]:
        raise ValueError("Pending or changed full text must be reviewed before Word delivery")
    body_path = Path(selected["body_path"]).resolve()
    review_path = Path(selected["semantic_review"]).resolve()
    if not body_path.is_relative_to(root) or not review_path.is_relative_to(root):
        raise ValueError("Selected artifacts must belong to this task")
    body = body_path.read_bytes().decode("utf8")
    if digest(body) != selected.get("body_sha256") or state.get("selected_body_sha256") != digest(body):
        raise ValueError("Selected model body changed")
    attempt = body_path.parent
    manifest, raw, messages = load(attempt / "manifest.json"), load(attempt / "raw.json"), load(attempt / "messages.json")
    if (manifest.get("body_sha256") != digest(body) or manifest.get("raw_sha256") != digest(raw)
            or manifest.get("messages_sha256") != digest(messages)
            or manifest.get("task_sha256") != digest(task) or manifest.get("evidence_sha256") != digest(cards)
            or digest(load(attempt / "task.json")) != digest(task) or digest(load(attempt / "evidence.json")) != digest(cards)
            or raw.get("choices", [{}])[0].get("message", {}).get("content") != body):
        raise ValueError("Original response or request provenance changed")
    semantic = load(review_path)
    if semantic.get("body_sha256") != digest(body) or semantic.get("passed") is not True or semantic.get("issues"):
        raise ValueError("Current model body requires a passing Codex semantic review")
    count = selected.get("word_count", {})
    if count.get("exact") is not True or type(count.get("count")) is not int or not 0 < count["count"] <= task["word_limit"]:
        raise ValueError("Current model body requires an exact count within the task limit")
    report = audit_body(body, task, cards, count)
    if not report["mechanical_passed"] or manifest.get("finish_reason") == "length":
        raise ValueError("Current model body failed final mechanical audit")
    post = load(root / "metadata-post-import.json")
    if not post.get("passed") or post.get("metadata_snapshot_hash") != metadata_snapshot(cards) or post.get("collection_key") != task.get("zotero", {}).get("collection_key"):
        raise ValueError("Current Zotero readback audit is required")
    return selected


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("init", "check", "assign-ids", "metadata-audit", "supplements", "prompt", "write", "review", "audit"):
        q = sub.add_parser(command)
        q.add_argument("task_dir", type=Path)
        if command == "init": q.add_argument("--config", type=Path)
        if command == "metadata-audit": q.add_argument("--readback", type=Path)
        if command in ("write", "review"): q.add_argument("--mock-responses", type=Path)
        if command in ("review", "audit"): q.add_argument("--word-count", type=Path)
        if command == "review": q.add_argument("--semantic-review", required=True, type=Path)
    a = p.parse_args(); root = a.task_dir.resolve()
    if a.command == "init": result = init_task(root, load(a.config) if a.config else None)
    elif a.command == "check": result = check_task(load(root / "task.json"))
    elif a.command == "assign-ids":
        cards = assign_ids(load(root / "evidence.json")); save(root / "evidence.json", cards); result = {"reference_ids": [c["reference_id"] for c in cards]}
    elif a.command == "metadata-audit":
        task = load(root / "task.json")
        if a.readback and not task.get("zotero", {}).get("collection_key"):
            raise ValueError("Resolve the target Zotero collection key before post-import readback audit")
        result = audit_metadata(load(root / "evidence.json"), load(a.readback) if a.readback else None, task.get("zotero", {}).get("collection_key"))
        save(root / ("metadata-post-import.json" if a.readback else "metadata-pre-import.json"), result)
    elif a.command == "supplements": result = supplement_status(root)
    elif a.command == "prompt":
        messages = build_messages(load(root / "task.json"), load(root / "evidence.json")); save(root / "preview-messages.json", messages); (root / "preview-prompt.txt").write_text("\n\n".join(m["content"] for m in messages), encoding="utf8"); result = {"messages_sha256": digest(messages), "path": str(root / "preview-prompt.txt")}
    elif a.command == "write": result = write_draft(root, a.mock_responses)
    elif a.command == "review": result = review_draft(root, a.semantic_review, a.word_count, a.mock_responses)
    else:
        attempt = latest_attempt(root); body = (attempt / "body.txt").read_bytes().decode("utf8"); task = load(root / "task.json"); result = audit_body(body, task, load(root / "evidence.json"), count_words(body, task, a.word_count)); save(root / "latest-audit.json", result)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    try: main()
    except (ValueError, RuntimeError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True), file=sys.stderr)
        sys.exit(1)
