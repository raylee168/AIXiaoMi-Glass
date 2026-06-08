import json
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pymysql

from app.config import get_settings
from app.db import fetch_all, fetch_one


STAGES = [
    {"id": "upload", "label": "照片上传"},
    {"id": "event", "label": "上传事件"},
    {"id": "preprocess", "label": "照片预处理"},
    {"id": "decision", "label": "智能判断"},
    {"id": "billing_hold", "label": "额度冻结"},
    {"id": "generation", "label": "相册生成"},
    {"id": "push", "label": "结果推送"},
    {"id": "settlement", "label": "结算扣费"},
    {"id": "cleanup", "label": "文件清理"},
]


def list_flows(limit: int = 30, user_id: str | None = None) -> dict:
    settings = get_settings()
    params: list[Any] = []
    where = ""
    if user_id:
        where = "WHERE user_id = %s"
        params.append(user_id)
    params.append(max(1, min(limit, 100)))

    rows = _safe_fetch_all(
        settings.core_album_db,
        f"""
        SELECT upload_batch_id, user_id, source_channel, upload_type, photo_count,
               status, created_at, updated_at, completed_at
        FROM upload_batches
        {where}
        ORDER BY created_at DESC
        LIMIT %s
        """,
        tuple(params),
    )

    items = []
    for row in rows:
        try:
            detail = get_flow(row["upload_batch_id"], include_logs=False)
            current_stage = detail["current_stage"]
            progress = detail["progress"]
            overall_status = detail["overall_status"]
        except Exception:
            current_stage = {"id": "upload", "label": "照片上传", "status": "running"}
            progress = 0
            overall_status = "running"

        items.append(
            _clean(
                {
                    "flow_id": row["upload_batch_id"],
                    "user_id": row["user_id"],
                    "source_channel": row.get("source_channel"),
                    "upload_type": row.get("upload_type"),
                    "photo_count": row.get("photo_count"),
                    "status": row.get("status"),
                    "created_at": row.get("created_at"),
                    "completed_at": row.get("completed_at"),
                    "overall_status": overall_status,
                    "current_stage": current_stage,
                    "progress": progress,
                }
            )
        )

    return {"scenario": "moments_album", "items": items}


def get_flow(upload_batch_id: str, include_logs: bool = True) -> dict:
    settings = get_settings()
    batch = fetch_one(
        settings.core_album_db,
        """
        SELECT upload_batch_id, user_id, source_channel, upload_type, photo_count,
               status, created_at, updated_at, completed_at
        FROM upload_batches
        WHERE upload_batch_id = %s
        """,
        (upload_batch_id,),
    )
    if not batch:
        raise ValueError("flow_not_found")

    photos = _safe_fetch_all(
        settings.core_album_db,
        """
        SELECT photo_id, user_id, upload_batch_id, original_filename, file_size,
               width, height, uploaded_at, expire_at, preprocess_status,
               smart_reject_count, smart_reject_status, used_in_generation,
               cleanup_status, cleaned_at
        FROM photo_files
        WHERE upload_batch_id = %s
        ORDER BY uploaded_at
        """,
        (upload_batch_id,),
    )
    photo_ids = [row["photo_id"] for row in photos]

    event = _safe_fetch_one(
        settings.core_album_db,
        """
        SELECT event_id, event_type, user_id, source_server, payload_json, status,
               retry_count, next_run_at, processed_at, error_message, created_at, updated_at
        FROM plugin_events
        WHERE event_id = %s
        """,
        (f"evt_{upload_batch_id}",),
    )

    preprocess_rows = _fetch_by_ids(
        settings.core_album_db,
        "photo_preprocess_results",
        "photo_id",
        photo_ids,
        """
        photo_id, user_id, quality_score, sharpness_score, brightness_score,
        colorfulness_score, is_blurry, is_screenshot, scene_tags_json,
        mood_hint, system_reject_level, system_reject_reason, processed_at
        """,
    )
    preprocess = _by_key(preprocess_rows, "photo_id")

    decision_photos = _fetch_decision_photos(settings.core_album_db, photo_ids)
    decision_ids = sorted({row["decision_job_id"] for row in decision_photos})
    decisions = _fetch_by_ids(
        settings.core_album_db,
        "album_decision_jobs",
        "decision_job_id",
        decision_ids,
        """
        decision_job_id, user_id, decision_window_start, decision_window_end,
        photo_count, usable_photo_count, summary_json, status, decision_result,
        decision_reason, confidence, template_matches_json, suggested_album_count,
        created_generation_count, processed_at, created_at, updated_at
        """,
    )

    generation_tasks = _fetch_generation_tasks(settings.core_album_db, decision_ids)
    generation_ids = [row["generation_task_id"] for row in generation_tasks]
    results = _fetch_by_ids(
        settings.core_album_db,
        "album_generation_results",
        "generation_task_id",
        generation_ids,
        """
        result_id, generation_task_id, user_id, album_title, copy_text,
        image_path, thumbnail_path, file_size, has_watermark, expire_at,
        cleanup_status, created_at, updated_at
        """,
    )
    costs = _fetch_by_ids(
        settings.core_album_db,
        "album_cost_items",
        "generation_task_id",
        generation_ids,
        """
        generation_task_id, user_id, cost_type, cost_name, provider, model_name,
        usage_json, charged_tokens, visible_to_user, created_at
        """,
    )
    pushes = _fetch_by_ids(
        settings.core_album_db,
        "album_push_tasks",
        "generation_task_id",
        generation_ids,
        """
        push_task_id, user_id, generation_task_id, result_id, push_channel,
        status, message_id, pushed_at, retry_count, error_message, created_at, updated_at
        """,
    )
    cleanups = _fetch_cleanups(settings.core_album_db, generation_ids, upload_batch_id)

    hold_ids = [row.get("account_hold_id") for row in generation_tasks if row.get("account_hold_id")]
    holds = _fetch_by_ids(
        settings.account_db,
        "account_token_holds",
        "hold_id",
        hold_ids,
        """
        hold_id, user_id, biz_type, biz_task_id, estimated_tokens,
        max_frozen_tokens, actual_consumed_tokens, released_tokens,
        status, frozen_at, settled_at, released_at, created_at, updated_at
        """,
    )
    transactions = _fetch_transactions(settings.account_db, generation_ids)
    wallet = _safe_fetch_one(
        settings.account_db,
        """
        SELECT user_id, balance_tokens, frozen_tokens, total_recharged_tokens,
               total_consumed_tokens, updated_at
        FROM account_wallets
        WHERE user_id = %s
        """,
        (batch["user_id"],),
    )

    normalized_decisions = [_json_normalize(row) for row in decisions]
    normalized_tasks = [_json_normalize(row) for row in generation_tasks]
    normalized_costs = [_json_normalize(row) for row in costs]
    normalized_cleanups = [_json_normalize(row) for row in cleanups]
    stages = _build_stages(
        batch=batch,
        photos=photos,
        event=event,
        preprocess=preprocess,
        decisions=normalized_decisions,
        generation_tasks=normalized_tasks,
        holds=holds,
        results=results,
        pushes=pushes,
        transactions=transactions,
        cleanups=normalized_cleanups,
    )

    completed = sum(1 for stage in stages if stage["status"] == "done")
    failed = any(stage["status"] == "failed" for stage in stages)
    current = next((stage for stage in stages if stage["status"] in {"failed", "running", "waiting"}), stages[-1])

    return _clean(
        {
            "scenario": "moments_album",
            "flow_id": upload_batch_id,
            "overall_status": "failed" if failed else ("done" if completed == len(stages) else "running"),
            "current_stage": current,
            "progress": round(completed / len(stages), 4),
            "refreshed_at": datetime.now(timezone.utc),
            "batch": batch,
            "wallet": wallet,
            "stages": stages,
            "photos": _merge_photo_preprocess(photos, preprocess),
            "event": _json_normalize(event),
            "decisions": normalized_decisions,
            "decision_photos": decision_photos,
            "generation_tasks": normalized_tasks,
            "results": results,
            "costs": normalized_costs,
            "pushes": pushes,
            "holds": holds,
            "transactions": transactions,
            "cleanups": normalized_cleanups,
            "logs": recent_logs(upload_batch_id=upload_batch_id, user_id=batch["user_id"], limit=80) if include_logs else [],
        }
    )


def recent_logs(upload_batch_id: str | None = None, user_id: str | None = None, limit: int | None = None) -> list[dict]:
    settings = get_settings()
    line_limit = max(1, min(limit or settings.log_line_limit, 500))
    units = [
        "aixiaomi-gateway.service",
        "aixiaomi-friendalbum.service",
        "aixiaomi-agent.service",
        "aixiaomi-account.service",
        "aixiaomi-llmproxy.service",
    ]
    patterns = [item for item in [upload_batch_id, user_id] if item]
    logs: list[dict] = []

    for unit in units:
        try:
            proc = subprocess.run(
                ["journalctl", "-u", unit, "-n", str(line_limit), "--no-pager", "-o", "short-iso"],
                capture_output=True,
                text=True,
                timeout=5,
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            continue
        if proc.returncode not in (0, 1):
            continue
        for line in proc.stdout.splitlines():
            if patterns and not any(pattern in line for pattern in patterns):
                continue
            logs.append({"unit": unit, "line": _mask_line(line)})

    return logs[-line_limit:]


def _fetch_decision_photos(database: str, photo_ids: list[str]) -> list[dict]:
    return _fetch_in(
        database,
        """
        SELECT decision_job_id, photo_id, user_id, photo_role, reject_reason,
               is_main_candidate, score, created_at
        FROM album_decision_job_photos
        WHERE photo_id IN ({placeholders})
        ORDER BY created_at
        """,
        photo_ids,
    )


def _fetch_generation_tasks(database: str, decision_ids: list[str]) -> list[dict]:
    return _fetch_in(
        database,
        """
        SELECT generation_task_id, user_id, decision_job_id, template_id, album_index,
               status, photo_ids_json, estimated_token_cost, max_frozen_tokens,
               frozen_token_amount, actual_token_cost, account_hold_id, has_watermark,
               result_dir, result_album_path, started_at, finished_at, retry_count,
               error_message, created_at, updated_at
        FROM album_generation_tasks
        WHERE decision_job_id IN ({placeholders})
        ORDER BY created_at, album_index
        """,
        decision_ids,
    )


def _fetch_cleanups(database: str, generation_ids: list[str], upload_batch_id: str) -> list[dict]:
    rows = _fetch_by_ids(
        database,
        "album_cleanup_tasks",
        "generation_task_id",
        generation_ids,
        """
        cleanup_task_id, user_id, generation_task_id, upload_batch_id,
        cleanup_scope_json, expire_at, status, cleaned_file_count,
        failed_file_count, cleaned_at, error_message, created_at, updated_at
        """,
    )
    if rows:
        return rows
    return _safe_fetch_all(
        database,
        """
        SELECT cleanup_task_id, user_id, generation_task_id, upload_batch_id,
               cleanup_scope_json, expire_at, status, cleaned_file_count,
               failed_file_count, cleaned_at, error_message, created_at, updated_at
        FROM album_cleanup_tasks
        WHERE upload_batch_id = %s
        ORDER BY created_at
        """,
        (upload_batch_id,),
    )


def _fetch_transactions(database: str, generation_ids: list[str]) -> list[dict]:
    return _fetch_in(
        database,
        """
        SELECT transaction_id, user_id, transaction_type, amount_tokens,
               balance_after, related_hold_id, related_biz_task_id,
               description, created_at
        FROM account_token_transactions
        WHERE related_biz_task_id IN ({placeholders})
        ORDER BY created_at
        """,
        generation_ids,
    )


def _fetch_in(database: str, sql_template: str, values: list[Any]) -> list[dict]:
    if not values:
        return []
    placeholders = ",".join(["%s"] * len(values))
    return _safe_fetch_all(database, sql_template.format(placeholders=placeholders), tuple(values))


def _fetch_by_ids(database: str, table: str, key: str, values: list[Any], fields: str) -> list[dict]:
    if not values:
        return []
    placeholders = ",".join(["%s"] * len(values))
    sql = f"SELECT {fields} FROM {table} WHERE {key} IN ({placeholders})"
    return _safe_fetch_all(database, sql, tuple(values))


def _safe_fetch_all(database: str, sql: str, params: tuple | dict | None = None) -> list[dict]:
    try:
        return fetch_all(database, sql, params)
    except pymysql.MySQLError:
        return []


def _safe_fetch_one(database: str, sql: str, params: tuple | dict | None = None) -> dict | None:
    rows = _safe_fetch_all(database, sql, params)
    return rows[0] if rows else None


def _build_stages(
    batch: dict,
    photos: list[dict],
    event: dict | None,
    preprocess: dict[str, dict],
    decisions: list[dict],
    generation_tasks: list[dict],
    holds: list[dict],
    results: list[dict],
    pushes: list[dict],
    transactions: list[dict],
    cleanups: list[dict],
) -> list[dict]:
    expected_count = int(batch.get("photo_count") or len(photos) or 0)
    uploaded_count = len(photos)
    preprocess_count = len(preprocess)
    failed_preprocess = any(row.get("preprocess_status") == "failed" for row in photos)

    successful_generations = [row for row in generation_tasks if row.get("status") == "success"]
    failed_generation = any(row.get("status") == "failed" for row in generation_tasks)
    successful_pushes = [row for row in pushes if row.get("status") == "success"]
    failed_push = any(row.get("status") == "failed" for row in pushes)
    frozen_holds = [row for row in holds if row.get("status") in {"frozen", "settled"}]
    settled_holds = [row for row in holds if row.get("status") == "settled"]
    cleanup_done = [row for row in cleanups if row.get("status") in {"success", "done"}]
    cleanup_failed = any(row.get("status") == "failed" for row in cleanups)

    decision_done = any(row.get("status") == "success" for row in decisions) or any(
        row.get("decision_result") in {"should_generate", "skip"} for row in decisions
    )
    should_generate = any(row.get("decision_result") == "should_generate" for row in decisions)

    stage_map = {
        "upload": {
            "status": _stage_status(batch.get("status") == "completed", running=uploaded_count > 0),
            "summary": f"{uploaded_count}/{expected_count or uploaded_count} 张照片已入库",
            "time": batch.get("completed_at") or batch.get("updated_at") or batch.get("created_at"),
        },
        "event": {
            "status": _stage_status(
                bool(event and event.get("status") in {"success", "processed"}),
                failed=bool(event and event.get("status") == "failed"),
                running=bool(event),
            ),
            "summary": event.get("status") if event else "等待上传完成后创建事件",
            "time": (event or {}).get("processed_at") or (event or {}).get("updated_at"),
        },
        "preprocess": {
            "status": _stage_status(preprocess_count >= uploaded_count and uploaded_count > 0, failed=failed_preprocess, running=preprocess_count > 0),
            "summary": f"{preprocess_count}/{uploaded_count} 张照片已完成预处理",
            "time": _latest_time(list(preprocess.values()), "processed_at"),
        },
        "decision": {
            "status": _stage_status(decision_done, running=bool(decisions)),
            "summary": _latest_decision_summary(decisions),
            "time": _latest_time(decisions, "processed_at", "updated_at", "created_at"),
        },
        "billing_hold": {
            "status": _stage_status(bool(frozen_holds) or (decision_done and not should_generate), running=bool(generation_tasks)),
            "summary": f"{len(frozen_holds)}/{len(generation_tasks)} 个生成任务已冻结额度",
            "time": _latest_time(holds, "frozen_at", "updated_at", "created_at"),
        },
        "generation": {
            "status": _stage_status(
                (bool(generation_tasks) and len(successful_generations) == len(generation_tasks)) or (decision_done and not should_generate),
                failed=failed_generation,
                running=bool(generation_tasks),
            ),
            "summary": f"{len(successful_generations)}/{len(generation_tasks)} 个相册生成成功，{len(results)} 个结果",
            "time": _latest_time(generation_tasks, "finished_at", "updated_at", "created_at"),
        },
        "push": {
            "status": _stage_status(
                bool(pushes) and len(successful_pushes) == len(pushes),
                failed=failed_push,
                running=bool(pushes),
            ),
            "summary": f"{len(successful_pushes)}/{len(pushes)} 条推送成功",
            "time": _latest_time(pushes, "pushed_at", "updated_at", "created_at"),
        },
        "settlement": {
            "status": _stage_status(bool(settled_holds), running=bool(transactions)),
            "summary": f"{sum(int(row.get('actual_consumed_tokens') or 0) for row in settled_holds):,} Token 已结算",
            "time": _latest_time(holds, "settled_at", "updated_at"),
        },
        "cleanup": {
            "status": _stage_status(bool(cleanup_done), failed=cleanup_failed, running=bool(cleanups)),
            "summary": f"{len(cleanup_done)}/{len(cleanups)} 个清理任务完成",
            "time": _latest_time(cleanups, "cleaned_at", "updated_at", "created_at"),
        },
    }
    return [_clean({**stage, **stage_map[stage["id"]]}) for stage in STAGES]


def _stage_status(done: bool, failed: bool = False, running: bool = False) -> str:
    if failed:
        return "failed"
    if done:
        return "done"
    if running:
        return "running"
    return "waiting"


def _latest_decision_summary(decisions: list[dict]) -> str:
    if not decisions:
        return "等待照片达到触发条件"
    latest = decisions[-1]
    result = latest.get("decision_result") or latest.get("status") or "-"
    reason = latest.get("decision_reason") or ""
    return f"{result} {reason}".strip()


def _merge_photo_preprocess(photos: list[dict], preprocess: dict[str, dict]) -> list[dict]:
    merged = []
    for photo in photos:
        row = dict(photo)
        row["preprocess"] = _json_normalize(preprocess.get(photo["photo_id"]))
        merged.append(row)
    return merged


def _json_normalize(row: dict | None) -> dict | None:
    if not row:
        return row
    normalized = dict(row)
    for key, value in list(normalized.items()):
        if key.endswith("_json") or key == "payload_json":
            normalized[key] = _json_value(value)
    return normalized


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _by_key(rows: list[dict], key: str) -> dict[str, dict]:
    return {row[key]: row for row in rows}


def _latest_time(rows: list[dict], *fields: str) -> Any:
    values = []
    for row in rows:
        for field in fields:
            value = row.get(field)
            if value:
                values.append(value)
    return max(values) if values else None


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == int(value) else float(value)
    return value


def _mask_line(line: str) -> str:
    for marker in ("sk-", "github_pat_"):
        pos = line.find(marker)
        if pos < 0:
            continue
        end = pos + len(marker)
        while end < len(line) and (line[end].isalnum() or line[end] in "-_"):
            end += 1
        line = line[: pos + len(marker)] + "***MASKED***" + line[end:]
    return line
