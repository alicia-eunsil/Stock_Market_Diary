from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import requests


@dataclass
class ExportApiAuth:
    service_key: str
    base_url: str = "https://apis.data.go.kr/1220000/prlstMmUtPrviExpAcrs"
    endpoint: str = "getPrlstMmUtPrviExpAcrs"


_TARGET_ALIASES: dict[str, list[str]] = {
    "월별": ["yyyymm", "ym", "stdr", "month", "base", "월"],
    "기간": ["period", "priod", "term", "range", "기간"],
    "전체": ["total", "all", "sum", "overall", "전체", "totl"],
    "반도체": ["semi", "반도체"],
    "철강제품": ["steel", "iron", "철강"],
    "승용차": ["passenger", "car", "승용"],
    "석유제품": ["oil", "petroleum", "석유"],
    "무선통신기기": ["wireless", "mobile", "radio", "무선", "통신"],
    "선박": ["ship", "vessel", "선박"],
    "자동차부품": ["autopart", "auto_part", "parts", "부품", "자동차부품"],
    "컴퓨터주변기기": ["computer", "peripheral", "pc", "주변", "컴퓨터"],
    "정밀기기": ["precision", "instrument", "optical", "정밀"],
    "가전제품": ["home", "appliance", "electronics", "가전"],
}


def _normalize_key(key: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", key).lower()


def _month_to_index(month: str) -> int:
    dt = datetime.strptime(str(month), "%Y%m")
    return dt.year * 12 + (dt.month - 1)


def _index_to_month(index: int) -> str:
    year = index // 12
    month = (index % 12) + 1
    return f"{year:04d}{month:02d}"


def clamp_export_month_range(start_month: str, end_month: str, max_years: int = 10) -> tuple[str, str]:
    start = datetime.strptime(str(start_month), "%Y%m")
    end = datetime.strptime(str(end_month), "%Y%m")
    max_months = max_years * 12 - 1
    end_index = end.year * 12 + (end.month - 1)
    min_start_index = end_index - max_months
    min_start_year = min_start_index // 12
    min_start_month = (min_start_index % 12) + 1
    min_start = datetime(min_start_year, min_start_month, 1)
    if start < min_start:
        start = min_start
    return start.strftime("%Y%m"), end.strftime("%Y%m")


def latest_published_export_month(now: datetime | None = None) -> str:
    current = now or datetime.now()
    if current.day < 11:
        current = current.replace(day=1)
        if current.month == 1:
            current = current.replace(year=current.year - 1, month=12)
        else:
            current = current.replace(month=current.month - 1)
    return current.strftime("%Y%m")


def _to_int(value: str | None) -> int:
    if value is None:
        return 0
    text = str(value).strip().replace(",", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _find_value(row: dict[str, str], aliases: list[str]) -> str:
    normalized = {key: _normalize_key(key) for key in row}
    for alias in aliases:
        alias_norm = _normalize_key(alias)
        for key, key_norm in normalized.items():
            if alias_norm and alias_norm in key_norm:
                return row[key]
    return ""


def _parse_xml_items(xml_text: str) -> list[dict[str, str]]:
    root = ET.fromstring(xml_text)
    result_code = root.findtext(".//resultCode") or root.findtext(".//header/resultCode")
    if result_code and result_code not in {"00", "0"}:
        result_msg = root.findtext(".//resultMsg") or root.findtext(".//header/resultMsg") or "Unknown error"
        raise ValueError(f"Public data API error {result_code}: {result_msg}")

    items = root.findall(".//item")
    parsed: list[dict[str, str]] = []
    for item in items:
        row: dict[str, str] = {}
        for child in list(item):
            row[child.tag] = (child.text or "").strip()
        if row:
            parsed.append(row)
    return parsed


def _normalize_rows(rows: list[dict[str, str]]) -> pd.DataFrame:
    columns = [
        "월별",
        "기간",
        "전체",
        "반도체",
        "철강제품",
        "승용차",
        "석유제품",
        "무선통신기기",
        "선박",
        "자동차부품",
        "컴퓨터주변기기",
        "정밀기기",
        "가전제품",
    ]
    records: list[dict[str, object]] = []
    for row in rows:
        normalized = {
            column: _find_value(row, aliases)
            for column, aliases in _TARGET_ALIASES.items()
        }
        if not normalized["월별"] and not normalized["전체"]:
            continue

        record = {
            "월별": _to_int(normalized["월별"]),
            "기간": normalized["기간"],
            "전체": _to_int(normalized["전체"]),
            "반도체": _to_int(normalized["반도체"]),
            "철강제품": _to_int(normalized["철강제품"]),
            "승용차": _to_int(normalized["승용차"]),
            "석유제품": _to_int(normalized["석유제품"]),
            "무선통신기기": _to_int(normalized["무선통신기기"]),
            "선박": _to_int(normalized["선박"]),
            "자동차부품": _to_int(normalized["자동차부품"]),
            "컴퓨터주변기기": _to_int(normalized["컴퓨터주변기기"]),
            "정밀기기": _to_int(normalized["정밀기기"]),
            "가전제품": _to_int(normalized["가전제품"]),
        }
        records.append(record)

    frame = pd.DataFrame.from_records(records, columns=columns)
    if frame.empty:
        return frame
    frame = frame.drop_duplicates(subset=["월별", "기간"], keep="last").sort_values(["월별", "기간"]).reset_index(drop=True)
    return frame


def fetch_export_trend_history(
    auth: ExportApiAuth,
    start_month: str,
    end_month: str,
    page_no: int = 1,
    num_rows: int = 1000,
) -> pd.DataFrame:
    start_month, end_month = clamp_export_month_range(start_month, end_month)
    start_idx = _month_to_index(start_month)
    end_idx = _month_to_index(end_month)
    frames: list[pd.DataFrame] = []
    url = f"{auth.base_url.rstrip('/')}/{auth.endpoint.lstrip('/')}"

    for month_idx in range(start_idx, end_idx + 1):
        month = _index_to_month(month_idx)
        params = {
            "serviceKey": auth.service_key,
            "pageNo": page_no,
            "numOfRows": num_rows,
            "type": "xml",
            "strtYymm": month,
            "endYymm": month,
        }
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        rows = _parse_xml_items(response.text)
        if not rows:
            snippet = re.sub(r"\s+", " ", response.text)[:500]
            raise ValueError(
                f"Public data API returned no rows for month={month}. "
                f"status={response.status_code} content_type={response.headers.get('content-type', '')} "
                f"snippet={snippet}"
            )
        frames.append(_normalize_rows(rows))

    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
    frame = frame.drop_duplicates(subset=["월별", "기간"], keep="last").sort_values(["월별", "기간"]).reset_index(drop=True)
    return frame
