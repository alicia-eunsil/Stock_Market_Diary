from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .indicators import add_indicators


@dataclass
class SignalThresholds:
    signal_threshold: int
    strong_threshold: int
    min_volume: int


def _score_bucket(score: int, strong_threshold: int, signal_threshold: int) -> str:
    if score >= strong_threshold:
        return "Strong"
    if score >= signal_threshold:
        return "Watch"
    return "Neutral"


def score_symbol(history: pd.DataFrame, symbol: str, name: str, thresholds: SignalThresholds) -> dict | None:
    if history.empty or len(history) < 60:
        return None

    frame = add_indicators(history)
    latest = frame.iloc[-1]
    prev = frame.iloc[-2]

    knee_score = 0
    shoulder_score = 0
    knee_reasons: list[str] = []
    shoulder_reasons: list[str] = []

    if latest["volume"] >= thresholds.min_volume:
        if pd.notna(latest["dist_from_low_20_pct"]) and latest["dist_from_low_20_pct"] <= 3:
            knee_score += 15
            knee_reasons.append("최근 20일 저점권")
        if pd.notna(latest["dist_from_high_20_pct"]) and latest["dist_from_high_20_pct"] >= -3:
            shoulder_score += 20
            shoulder_reasons.append("최근 20일 고점권")

        if latest["close"] > prev["close"]:
            knee_score += 15
            knee_reasons.append("종가 반등")
        if latest["close"] < prev["close"]:
            shoulder_score += 15
            shoulder_reasons.append("종가 약세")

        if pd.notna(latest["rsi_14"]) and pd.notna(prev["rsi_14"]) and latest["rsi_14"] > prev["rsi_14"] and latest["rsi_14"] < 45:
            knee_score += 10
            knee_reasons.append("RSI 반등")
        if pd.notna(latest["rsi_14"]) and pd.notna(prev["rsi_14"]) and latest["rsi_14"] < prev["rsi_14"] and latest["rsi_14"] > 55:
            shoulder_score += 15
            shoulder_reasons.append("RSI 약화")

        if pd.notna(latest["macd_hist"]) and pd.notna(prev["macd_hist"]) and latest["macd_hist"] > prev["macd_hist"]:
            knee_score += 15
            knee_reasons.append("MACD 개선")
        if pd.notna(latest["macd_hist"]) and pd.notna(prev["macd_hist"]) and latest["macd_hist"] < prev["macd_hist"]:
            shoulder_score += 15
            shoulder_reasons.append("MACD 둔화")

        if pd.notna(latest["vol_ratio_20"]) and latest["vol_ratio_20"] >= 1.5:
            knee_score += 20
            knee_reasons.append("거래량 증가")
            shoulder_score += 5
            shoulder_reasons.append("거래량 이상")

        if pd.notna(latest["ma_20"]) and latest["close"] >= latest["ma_20"] and prev["close"] < prev["ma_20"]:
            knee_score += 25
            knee_reasons.append("20일선 회복")
        if pd.notna(latest["ma_20"]) and latest["close"] <= latest["ma_20"] and prev["close"] > prev["ma_20"]:
            shoulder_score += 5
            shoulder_reasons.append("20일선 이탈")

        # Trend continuation bonus:
        # existing logic favored "20일선 회복" (cross-up) but under-scored already-strong leaders.
        if (
            pd.notna(latest["ma_20"])
            and pd.notna(latest.get("ma_60"))
            and latest["close"] > latest["ma_20"]
            and latest["ma_20"] > latest["ma_60"]
            and latest["close"] > prev["close"]
        ):
            knee_score += 15
            knee_reasons.append("20일선 상회 추세")

        # Momentum expansion bonus for strong daily move with meaningful volume.
        pct_change = ((latest["close"] / prev["close"]) - 1.0) * 100.0 if prev["close"] else 0.0
        if pct_change >= 3.0 and pd.notna(latest["vol_ratio_20"]) and latest["vol_ratio_20"] >= 1.1:
            knee_score += 15
            knee_reasons.append("상승 탄력 확대")

    knee_score = min(knee_score, 100)
    shoulder_score = min(shoulder_score, 100)
    signal_date = str(latest["date"])

    return {
        "date": signal_date,
        "symbol": symbol,
        "name": name,
        "close": int(latest["close"]),
        "volume": int(latest["volume"]),
        "turnover": int(latest.get("turnover", 0) or 0),
        "pct_change": round(((latest["close"] / prev["close"]) - 1.0) * 100.0, 2) if prev["close"] else 0.0,
        "vol_ratio_20": round(float(latest["vol_ratio_20"]), 2) if pd.notna(latest["vol_ratio_20"]) else None,
        "knee_score": knee_score,
        "knee_grade": _score_bucket(knee_score, thresholds.strong_threshold, thresholds.signal_threshold),
        "knee_reasons": " | ".join(knee_reasons),
        "knee_confirmed": int(pd.notna(latest["ma_20"]) and latest["close"] >= latest["ma_20"] and prev["close"] < prev["ma_20"]),
        "shoulder_score": shoulder_score,
        "shoulder_grade": _score_bucket(shoulder_score, thresholds.strong_threshold, thresholds.signal_threshold),
        "shoulder_reasons": " | ".join(shoulder_reasons),
        "shoulder_confirmed": int(pd.notna(latest["ma_20"]) and latest["close"] <= latest["ma_20"] and prev["close"] > prev["ma_20"]),
    }
