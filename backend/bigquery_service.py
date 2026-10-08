"""
backend/bigquery_service.py
─────────────────────────────────────────────────────────────
Dedicated service for executing and validating Google BigQuery
stored procedure calls:
  • fetch_bigquery_procedure: generic procedure caller for filters ARRAY<STRUCT<so INT64, line INT64>>
  • fetch_bigquery_te: executes get_TE (PDF Merger (Trim))
  • fetch_bigquery_job: executes get_Job (PFL)
  • validate_pfl_job_data: validates customer_job 8-digit codes and generates warning messages
─────────────────────────────────────────────────────────────
"""

import re
from google.cloud import bigquery
from google.oauth2 import service_account

from backend.constants import (
    get_credentials_path,
    BQ_PROC_GET_TE,
    BQ_PROC_GET_JOB,
    JOB_CODE_LENGTH,
)


def fetch_bigquery_procedure(procedure_name: str, pairs: list, tracing_enabled: bool = False) -> list:
    """
    Generic execution of stored procedures expecting filters ARRAY<STRUCT<so INT64, line INT64>>.
    Shared by PDF Merger (Trim) (get_TE) and PFL (get_Job).
    """
    if not pairs:
        return []

    creds = service_account.Credentials.from_service_account_file(get_credentials_path())
    client = bigquery.Client(credentials=creds, project=creds.project_id)

    structs_sql = ",\n        ".join(
        [f"STRUCT({so} AS so, {line} AS line)" for so, line in pairs]
    )
    call_sql = f"""
    CALL {procedure_name}([
        {structs_sql}
    ]);
    """

    try:
        query_job = client.query(call_sql)
        results = query_job.result()
        records = [dict(row) for row in results]

        if tracing_enabled:
            print(f"\n{'='*70}\n[BigQuery TRACE] {procedure_name} ({len(records)} rows returned):")
            for r in records:
                print(f"  {r}")
            print(f"{'='*70}\n", flush=True)

        return records
    finally:
        client.close()


def fetch_bigquery_te(pairs: list, tracing_enabled: bool = False) -> list:
    """
    Call BigQuery stored procedure get_TE with (so_int, line_int) pairs.
    Returns list of dicts: [{SOL, ORDERED_ITEM, list_TE}, ...]
    """
    return fetch_bigquery_procedure(BQ_PROC_GET_TE, pairs, tracing_enabled=tracing_enabled)


def fetch_bigquery_job(pairs: list, tracing_enabled: bool = False) -> list:
    """
    Call BigQuery stored procedure get_Job with (so_int, line_int) pairs.
    Returns list of dicts: [{SOL, CUSTOMER_JOB}, ...]
    """
    return fetch_bigquery_procedure(BQ_PROC_GET_JOB, pairs, tracing_enabled=tracing_enabled)


def validate_pfl_job_data(records: list[dict], flagged_sols: list[str]) -> tuple[dict[str, str], list[str]]:
    """
    Build job_map and validate 8-digit customer_job codes.
    Returns:
        job_map: { sol: "12345678" }
        warnings: list of human-readable warnings for missing or non-8-digit jobs.
    """
    job_map: dict[str, str] = {}
    warnings: list[str] = []

    bq_by_sol: dict[str, dict] = {
        str(row.get("SOL", "")).strip(): row for row in records if row.get("SOL")
    }

    job_pattern = re.compile(rf"^\d{{{JOB_CODE_LENGTH}}}$")

    for sol in flagged_sols:
        row = bq_by_sol.get(sol)
        if not row:
            warnings.append(f"SOL {sol}: BigQuery không trả về dữ liệu (không tìm thấy record).")
            continue

        raw_job = row.get("CUSTOMER_JOB")
        if raw_job is None:
            warnings.append(f"SOL {sol}: Cột CUSTOMER_JOB bị rỗng (NULL) trong BigQuery.")
            continue

        job_str = str(raw_job).strip()
        if not job_pattern.fullmatch(job_str):
            warnings.append(f"SOL {sol}: Số job '{job_str}' không đúng định dạng {JOB_CODE_LENGTH} chữ số.")
            continue

        job_map[sol] = job_str

    return job_map, warnings
