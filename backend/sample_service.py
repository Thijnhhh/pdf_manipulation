"""
backend/sample_service.py
─────────────────────────────────────────────────────────────
Service quản lý dữ liệu mẫu (Sample / Demo Data) và giả lập (Mock)
kết quả BigQuery/Sheets khi chạy ở chế độ Demo/Offline mà không cần
Google Service Account.
─────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import os
from typing import Any

from backend.constants import get_sample_data_dir

# Ánh xạ từ ID module trên Frontend sang tên thư mục con trong sample_data/
MODULE_DIR_MAP = {
    "merger": "pfl",
    "merger_rfid": "rfid_ht",
    "merger_trim": "trim",
    "merger_pfl_thermal": "pfl_thermal",
    "merger_rfid_sb": "rfid_sb_uq",
    "PFL": "pfl",
    "RFID HT": "rfid_ht",
    "Trim": "trim",
    "PFL THERMAL": "pfl_thermal",
    "RFID SB": "rfid_sb_uq",
}


def get_module_sample_dir(module_id: str, custom_dir: str = "") -> str:
    """Trả về đường dẫn tuyệt đối tới thư mục sample của module tương ứng."""
    if custom_dir and os.path.isdir(custom_dir):
        return os.path.normpath(custom_dir)

    sub_dir = MODULE_DIR_MAP.get(module_id, "pfl")
    return os.path.normpath(os.path.join(get_sample_data_dir(), sub_dir))


def load_sample_metadata(module_id: str, custom_dir: str = "") -> dict[str, Any]:
    """
    Đọc file metadata.json từ thư mục sample, tự động tạo các thư mục
    soview, layout, output nếu chưa có và trả về dữ liệu mẫu hoàn chỉnh.
    """
    target_dir = get_module_sample_dir(module_id, custom_dir)
    soview_dir = os.path.join(target_dir, "soview")
    layout_dir = os.path.join(target_dir, "layout")
    output_dir = os.path.join(target_dir, "output")

    os.makedirs(soview_dir, exist_ok=True)
    os.makedirs(layout_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    metadata_path = os.path.join(target_dir, "metadata.json")
    if not os.path.exists(metadata_path):
        return {
            "success": False,
            "error": f"Không tìm thấy file metadata.json tại: {metadata_path}",
            "sample_dir": target_dir,
        }

    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return {"success": False, "error": f"Lỗi đọc file metadata.json: {exc}"}

    sols_raw = data.get("sols", [])
    sols: list[str] = []
    sol_entries: list[dict] = []
    job_map: dict[str, str] = {}

    for item in sols_raw:
        if isinstance(item, str) and item.strip():
            sols.append(item.strip())
        elif isinstance(item, dict):
            s_code = str(item.get("sol", "")).strip()
            if s_code:
                sols.append(s_code)
                sol_entries.append(item)
                if item.get("job"):
                    job_map[s_code] = str(item.get("job")).strip()

    return {
        "success": True,
        "module_id": module_id,
        "sample_dir": target_dir,
        "soview_folder": soview_dir,
        "layout_folder": layout_dir,
        "output_folder": output_dir,
        "sols": sols,
        "sol_entries": sol_entries,
        "job_map": job_map,
        "raw_metadata": data,
    }


def get_mock_bigquery_job(sols: list[str], sample_dir: str = "") -> list[dict]:
    """Giả lập kết quả trả về của BigQuery get_Job từ file metadata.json."""
    meta = load_sample_metadata("merger", custom_dir=sample_dir)
    if not meta.get("success"):
        return []

    records = []
    for item in meta.get("raw_metadata", {}).get("sols", []):
        if isinstance(item, dict):
            sol = str(item.get("sol", "")).strip()
            job = str(item.get("job", "")).strip()
            if sol and job:
                records.append({"SOL": sol, "CUSTOMER_JOB": job})
    return records


def get_mock_bigquery_te(sols: list[str], sample_dir: str = "") -> list[dict]:
    """Giả lập kết quả trả về của BigQuery get_TE từ file metadata.json."""
    meta = load_sample_metadata("merger_trim", custom_dir=sample_dir)
    if not meta.get("success"):
        return []

    records = []
    for item in meta.get("raw_metadata", {}).get("sols", []):
        if isinstance(item, dict):
            sol = str(item.get("sol", "")).strip()
            ordered_item = item.get("ordered_item", "")
            list_te = item.get("list_te", [])
            if sol:
                records.append({
                    "SOL": sol,
                    "ORDERED_ITEM": ordered_item,
                    "list_TE": ", ".join(list_te) if isinstance(list_te, list) else str(list_te),
                })
    return records
