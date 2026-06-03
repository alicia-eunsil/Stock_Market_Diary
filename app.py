from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.knee_shoulder.config import load_config
try:
    from src.knee_shoulder.export_data import ExportApiAuth, fetch_export_trend_history
except ImportError:  # pragma: no cover - fail soft on partial deployments
    ExportApiAuth = None
    fetch_export_trend_history = None

from src.knee_shoulder.storage import (
    load_all_signal_files,
    load_existing_history,
    load_intraday_feature_history,
    load_validation_history,
)

try:
    from src.knee_shoulder.storage import load_export_trend_history, save_export_trend_history
except ImportError:  # pragma: no cover - fail soft on partial deployments
    load_export_trend_history = None
    save_export_trend_history = None


st.set_page_config(page_title="Knee Shoulder Monitor", page_icon="🌻", layout="wide")


def require_access_code() -> None:
    expected_code = os.getenv("ACCESS_CODE") or st.secrets.get("ACCESS_CODE")
    if not expected_code:
        st.error("ACCESS_CODE 환경변수가 설정되지 않았습니다.")
        st.stop()

    if st.session_state.get("access_granted"):
        return

    st.title("Knee/Shoulder Stock Monitor")
    st.caption("접속코드를 입력해야 대시보드를 볼 수 있습니다.")
    entered_code = st.text_input("접속코드", type="password")
    if st.button("확인", type="primary"):
        if entered_code == expected_code:
            st.session_state["access_granted"] = True
            st.rerun()
        else:
            st.error("접속코드가 올바르지 않습니다.")
    st.stop()


require_access_code()

st.title("Knee/Shoulder Stock Monitor")
st.caption("Daily close-based reversal monitoring dashboard for Korean stocks.")

config = load_config()
paths = config["paths"]
CANDIDATE_DISPLAY_MIN_SCORE = config["runtime"]["signal_threshold"]
CANDIDATE_TABLE_HEIGHT = 245
validation_cfg = config.get("validation", {})
risk_cfg = config.get("risk_control", {})
export_cfg = config.get("export_data", {})
RISK_PER_TRADE_PCT = float(validation_cfg.get("risk_per_trade_pct", 1.0))
STOP_LOSS_PCT = float(validation_cfg.get("stop_loss_pct", 3.0))
TARGET_PROFIT_PCT = float(validation_cfg.get("target_profit_pct", 6.0))
ACCOUNT_SIZE_KRW = int(validation_cfg.get("account_size_krw", 10_000_000))
ROUNDTRIP_COST_BPS = float(risk_cfg.get("roundtrip_cost_bps", 25))
MAX_MDD_STOP_PCT = float(risk_cfg.get("max_mdd_stop_pct", -8.0))
OOS_TEST_DAYS = int(risk_cfg.get("oos_test_days", 20))
OOS_TRAIN_DAYS = int(risk_cfg.get("oos_train_days", 60))
MIN_TURNOVER_REBOUND = float(config["runtime"].get("min_turnover_rebound", 150_000_000_000))
MIN_TURNOVER_TREND = float(config["runtime"].get("min_turnover_trend", 300_000_000_000))
LEADER_SYMBOLS = {
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "009150",  # 삼성전기
    "042700",  # 한미반도체
    "373220",  # LG에너지솔루션
    "035420",  # NAVER
    "035720",  # 카카오
}
EXPORT_TREND_PATH = Path(paths.get("export_trend_file", "data/external/export_trends.csv"))


def render_candidate_help(title: str, score_label: str, reasons_label: str) -> None:
    with st.popover("!"):
        st.markdown(f"**{title} 읽는 법**")
        if "Knee" in title:
            st.markdown("- `symbol` > 종목코드")
            st.markdown("- `name` > 종목명")
            st.markdown("- `close` > 전일 종가. 매수 후보가 잡힌 기준 가격입니다. 이후 이 가격 위에서 버티는지 보는 기준점입니다.")
            st.markdown("- `pct_change` > 전일대비등락률. 플러스면 당일 반등 힘이 붙었는지, 너무 급등했는지도 같이 봅니다.")
            st.markdown("- `knee_score` > 평가점수. 높을수록 매수 후보 조건이 많이 겹친 상태입니다.")
            st.markdown("- `knee_grade` > 평가등급. `Strong`는 우선 확인할 강한 매수 후보, `Watch`는 관찰할 매수 후보입니다.")
            st.markdown("- `vol_ratio_20` > 거래량. 1보다 크면 평소보다 거래가 많이 붙은 날입니다. 반등과 함께 높으면 의미가 커집니다.")
            st.markdown("- `knee_reasons` > 평가근거. 왜 이 종목이 매수 후보로 잡혔는지 핵심 이유를 보여줍니다.")
        else:
            st.markdown("- `symbol` > 종목코드")
            st.markdown("- `name` > 종목명")
            st.markdown("- `close` > 전일 종가. 매도 후보가 포착된 기준 가격입니다. 이후 이 가격 아래로 더 밀리는지 보는 기준점입니다.")
            st.markdown("- `pct_change` > 전일대비등락률. 마이너스로 꺾였는지, 고점권에서 약세 전환이 시작됐는지 볼 때 중요합니다.")
            st.markdown("- `shoulder_score` > 평가점수. 높을수록 매도 후보 조건이 많이 겹친 상태입니다.")
            st.markdown("- `shoulder_grade` > 평가등급. `Strong`는 우선 경계할 강한 매도 후보, `Watch`는 관찰할 후보입니다.")
            st.markdown("- `vol_ratio_20` > 거래량. 꺾이는 날 이 값이 높으면 단순 눌림보다 실제 매도 물량이 나왔는지 의심해볼 수 있습니다.")
            st.markdown("- `shoulder_reasons` > 평가근거. 왜 이 종목이 매도 후보로 잡혔는지 핵심 이유를 보여줍니다.")

        if "Knee" in title:
            st.markdown("**해석 예시**")
            st.markdown("- `pct_change`가 플러스이고 `vol_ratio_20`도 높으면, 그냥 잠깐 튄 반등보다 실제 매수세가 붙는 매수 후보로 해석할 수 있습니다.")
            st.markdown("- `knee_score`가 70 이상으로 높고 `Strong`면, 여러 매수 조건이 한 번에 나온 상태라 우선순위를 높게 둘 수 있습니다.")
            st.markdown("- `knee_reasons`에 `최근 20일 저점권`, `종가 반등`, `MACD 개선`이 함께 있으면, 하락 흐름이 약해지며 매수 시도가 나오는 상황으로 해석합니다.")
            st.markdown("- `close`는 이후 손절선이나 추세 유지 여부를 볼 때 기준 가격으로 사용할 수 있습니다.")
        else:
            st.markdown("**해석 예시**")
            st.markdown("- `pct_change`가 마이너스로 꺾이고 `vol_ratio_20`도 높으면, 단순 쉬어감보다 매도 물량이 강하게 나온 것으로 해석할 수 있습니다.")
            st.markdown("- `shoulder_score`가 70 이상으로 높고 `Strong`면, 고점권 약세 전환 조건이 많이 겹친 상태입니다.")
            st.markdown("- `shoulder_reasons`에 `고점권`, `종가 약세`, `MACD 둔화`가 함께 있으면, 상승 힘이 식으면서 꺾이기 시작하는 구간으로 볼 수 있습니다.")
            st.markdown("- `close`는 이후 추가 하락 여부를 볼 때 기준 가격으로 사용할 수 있습니다.")


def render_validation_help() -> None:
    with st.popover("!"):
        st.markdown("**품목별 수출 추이 읽는 법**")
        st.markdown("- 이 영역은 API에서 받아온 관세청 `수출 주요품목별 10일 단위 잠정치 통계`를 보여줍니다.")
        st.markdown("- 단위는 모두 **천달러**입니다.")
        st.markdown("- `01~10`, `01~20`, `01~31`은 각각 월초, 중순, 월말 누적치입니다.")
        st.markdown("- `전체`는 총수출액이고, 각 품목 행은 품목별 수출액입니다.")
        st.markdown("- `전월대비`는 직전 달 대비 증감률, `전년동월대비`는 같은 달 작년과 비교한 증감률입니다.")
        st.markdown("- `계절성지수`는 월별 평균이 전체 평균 대비 얼마나 강한지 보는 보조 지표입니다.")
        st.markdown("- 이 데이터는 종목 자체를 찍는 값이 아니라, **산업 환경이 좋아지는지** 보는 상위 필터로 쓰는 게 맞습니다.")


def load_latest_signals(signal_dir: str) -> tuple[pd.DataFrame, str | None]:
    files = sorted(Path(signal_dir).glob("*_signals.csv"))
    if not files:
        return pd.DataFrame(), None
    latest = files[-1]
    return pd.read_csv(latest, dtype={"symbol": str}), latest.stem.replace("_signals", "")


def prepare_history_for_chart(history: pd.DataFrame) -> pd.DataFrame:
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"].astype(str), format="%Y%m%d", errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return frame


def render_candidate_radio_grid(title: str, options: list[str], key_prefix: str, source_name: str) -> str | None:
    st.markdown(f"**{title}**")
    if not options:
        st.caption("후보 없음")
        return None

    limited = options[:10]

    def _on_change() -> None:
        st.session_state["detail_source_selector"] = source_name

    selected = st.radio(
        f"{title} 선택",
        options=limited,
        key=f"{key_prefix}_single",
        on_change=_on_change,
        label_visibility="collapsed",
    )
    return selected


def format_candidate_view(frame: pd.DataFrame, score_column: str, reasons_column: str) -> pd.DataFrame:
    view = frame[
        ["symbol", "name", "close", "pct_change", score_column, f"{score_column.split('_')[0]}_grade", "vol_ratio_20", reasons_column]
    ].copy()
    view["close"] = pd.to_numeric(view["close"], errors="coerce").map(lambda v: f"{int(v):,}" if pd.notna(v) else "")
    view = view.rename(
        columns={
            "symbol": "종목코드",
            "name": "종목명",
            "close": "전일 종가",
            "pct_change": "전일대비등락률",
            score_column: "평가점수",
            f"{score_column.split('_')[0]}_grade": "평가등급",
            "vol_ratio_20": "거래량",
            reasons_column: "평가근거",
        }
    )
    return view


def candidate_column_config() -> dict:
    return {
        "종목코드": st.column_config.TextColumn("종목코드", width="small"),
        "종목명": st.column_config.TextColumn("종목명", width="small"),
        "전일 종가": st.column_config.TextColumn("전일 종가", width="small"),
        "전일대비등락률": st.column_config.TextColumn("전일대비등락률", width="small"),
        "평가점수": st.column_config.NumberColumn("평가점수", width="small"),
        "평가등급": st.column_config.TextColumn("평가등급", width="small"),
        "거래량": st.column_config.NumberColumn("거래량", width="small"),
        "평가근거": st.column_config.TextColumn("평가근거", width="large"),
    }


def format_validation_view(frame: pd.DataFrame) -> pd.DataFrame:
    view = frame.copy()
    rename_map = {
        "signal_date": "결정일",
        "symbol": "종목코드",
        "name": "종목명",
        "knee_score": "매수점수",
        "shoulder_score": "매도점수",
        "ret_1d": "수익률(1일)",
        "ret_3d": "수익률(3일)",
        "ret_5d": "수익률(5일)",
        "ret_10d": "수익률(10일)",
        "knee_success": "매수 성공여부",
        "shoulder_success": "매도 성공여부",
    }
    existing_map = {key: value for key, value in rename_map.items() if key in view.columns}
    return view.rename(columns=existing_map)


def load_or_refresh_export_history(force_refresh: bool = False) -> pd.DataFrame:
    if load_export_trend_history is None or save_export_trend_history is None or ExportApiAuth is None or fetch_export_trend_history is None:
        return pd.DataFrame()

    frame = load_export_trend_history(EXPORT_TREND_PATH)
    if not frame.empty and not force_refresh:
        return frame

    service_key = (
        os.getenv("DATA_GO_KR_SERVICE_KEY")
        or st.secrets.get("data_go_service_key")
        or st.secrets.get("DATA_GO_KR_SERVICE_KEY")
    )
    if not service_key:
        return frame

    export_auth = ExportApiAuth(
        service_key=service_key,
        base_url=str(export_cfg.get("base_url", "https://apis.data.go.kr/1220000/prlstMmUtPrviExpAcrs")),
        endpoint=str(export_cfg.get("endpoint", "getPrlstMmUtPrviExpAcrs")),
    )
    try:
        fetched = fetch_export_trend_history(
            export_auth,
            start_month=str(export_cfg.get("start_month", "201601")),
            end_month=pd.Timestamp.now().strftime("%Y%m"),
            num_rows=int(export_cfg.get("num_rows", 1000)),
        )
    except Exception:  # noqa: BLE001
        return frame

    if fetched.empty:
        return frame

    if frame.empty:
        save_export_trend_history(EXPORT_TREND_PATH, fetched)
        return fetched

    combined = pd.concat([frame, fetched], ignore_index=True)
    combined = combined.drop_duplicates(subset=["월별", "기간"], keep="last").sort_values(["월별", "기간"]).reset_index(drop=True)
    save_export_trend_history(EXPORT_TREND_PATH, combined)
    return combined


def prepare_export_frame(frame: pd.DataFrame) -> pd.DataFrame:
    view = frame.copy()
    if view.empty:
        return view

    view["월별"] = pd.to_numeric(view["월별"], errors="coerce")
    view = view.dropna(subset=["월별"]).copy()
    view["월별"] = view["월별"].astype(int)
    if "기간" not in view.columns:
        view["기간"] = "01~31"
    view["기간"] = view["기간"].astype(str)

    numeric_cols = [col for col in view.columns if col not in {"월별", "기간"}]
    for col in numeric_cols:
        view[col] = pd.to_numeric(view[col], errors="coerce")

    return view.sort_values(["기간", "월별"]).reset_index(drop=True)


def latest_period_summary(frame: pd.DataFrame) -> tuple[dict[str, float], pd.DataFrame, pd.DataFrame]:
    if frame.empty:
        return {}, pd.DataFrame(), pd.DataFrame()

    latest = frame.iloc[-1]
    prev = frame.iloc[-2] if len(frame) > 1 else None
    prev_year = frame[frame["월별"] == int(latest["월별"]) - 100]
    prev_year_row = prev_year.iloc[0] if not prev_year.empty else None

    summary: dict[str, float] = {
        "월별": int(latest["월별"]),
        "전체": float(latest["전체"]),
        "전월대비": ((float(latest["전체"]) / float(prev["전체"]) - 1.0) * 100.0) if prev is not None and float(prev["전체"]) else float("nan"),
        "전년동월대비": ((float(latest["전체"]) / float(prev_year_row["전체"]) - 1.0) * 100.0)
        if prev_year_row is not None and float(prev_year_row["전체"])
        else float("nan"),
    }

    item_cols = [col for col in frame.columns if col not in {"월별", "기간", "전체"}]
    item_rows = []
    for col in item_cols:
        latest_val = float(latest[col]) if pd.notna(latest[col]) else float("nan")
        share = (latest_val / summary["전체"] * 100.0) if summary["전체"] else float("nan")
        mom = ((latest_val / float(prev[col]) - 1.0) * 100.0) if prev is not None and float(prev[col]) else float("nan")
        yoy = ((latest_val / float(prev_year_row[col]) - 1.0) * 100.0) if prev_year_row is not None and float(prev_year_row[col]) else float("nan")
        item_rows.append(
            {
                "품목": col,
                "최신값(천달러)": latest_val,
                "비중(%)": share,
                "전월대비(%)": mom,
                "전년동월대비(%)": yoy,
            }
        )

    item_summary = pd.DataFrame(item_rows).sort_values("최신값(천달러)", ascending=False).reset_index(drop=True)

    trend = frame[["월별", "전체"]].copy()
    trend["월별표시"] = trend["월별"].astype(str)
    return summary, item_summary, trend


def seasonality_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    view = frame.copy()
    view["month"] = view["월별"].astype(str).str[-2:].astype(int)
    period = view[view["기간"] == "01~31"].copy()
    if period.empty:
        period = view.copy()
    cols = [col for col in period.columns if col not in {"월별", "기간", "month"}]
    summary = period.groupby("month")[cols].mean(numeric_only=True).reset_index()
    if "전체" not in summary.columns:
        return pd.DataFrame()
    total_mean = summary["전체"].mean()
    summary["계절성지수(전체)"] = (summary["전체"] / total_mean * 100.0) if total_mean else float("nan")
    return summary[["month", "전체", "계절성지수(전체)"]]


def format_return(value: float | int | None) -> str:
    if pd.isna(value):
        return "-"
    return f"{float(value):+.2f}%"


def format_export_amount(value: float | int | None) -> str:
    if pd.isna(value):
        return "-"
    return f"{int(float(value)):,}천달러"


def format_rate(value: float | int | None) -> str:
    if pd.isna(value):
        return "-"
    return f"{float(value):.1f}%"


def format_currency(value: float | int | None) -> str:
    if pd.isna(value):
        return "-"
    return f"{int(value):,}원"


def styled_return_html(value: float | int | None) -> str:
    if pd.isna(value):
        return "<span style='color:#6b7280;'>-</span>"
    v = float(value)
    color = "#059669" if v > 0 else ("#dc2626" if v < 0 else "#6b7280")
    sign = "+" if v > 0 else ""
    return f"<span style='color:{color}; font-weight:700;'>{sign}{v:.2f}%</span>"


def styled_rate_html(value: float | int | None) -> str:
    if pd.isna(value):
        return "<span style='color:#6b7280;'>-</span>"
    v = float(value)
    color = "#059669" if v >= 50 else "#dc2626"
    return f"<span style='color:{color}; font-weight:700;'>{v:.1f}%</span>"


def prepare_evaluation_frame(validation: pd.DataFrame, analysis_date: str) -> pd.DataFrame:
    frame = validation[validation["signal_date"].astype(str) < analysis_date].copy()
    if frame.empty:
        return frame
    for column in [
        "knee_score",
        "shoulder_score",
        "ret_1d",
        "ret_3d",
        "ret_5d",
        "ret_10d",
        "max_up_5d",
        "max_dd_5d",
        "benchmark_ret_5d",
    ]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def calculate_strategy_metrics(candidates: pd.DataFrame, side: str) -> dict:
    if candidates.empty:
        return {
            "count": 0,
            "win_rate": None,
            "avg_return": None,
            "avg_win": None,
            "avg_loss": None,
            "payoff": None,
            "expectancy": None,
            "max_drawdown": None,
        }

    strategy_ret = candidates["ret_5d"].astype(float)
    if side == "shoulder":
        strategy_ret = -strategy_ret
    wins = strategy_ret[strategy_ret > 0]
    losses = strategy_ret[strategy_ret <= 0]
    avg_win = wins.mean() if not wins.empty else None
    avg_loss = losses.mean() if not losses.empty else None
    payoff = None
    if avg_win is not None and avg_loss is not None and avg_loss != 0:
        payoff = float(avg_win) / abs(float(avg_loss))

    win_rate = (strategy_ret > 0).mean() * 100
    expectancy = strategy_ret.mean()

    equity = (1.0 + strategy_ret / 100.0).cumprod()
    rolling_peak = equity.cummax()
    drawdown = ((equity / rolling_peak) - 1.0) * 100.0
    max_drawdown = drawdown.min() if not drawdown.empty else None

    return {
        "count": len(candidates),
        "win_rate": win_rate,
        "avg_return": strategy_ret.mean(),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff": payoff,
        "expectancy": expectancy,
        "max_drawdown": max_drawdown,
    }


def build_regime_summary(frame: pd.DataFrame, side: str) -> pd.DataFrame:
    score_column = f"{side}_score"
    filtered = frame[(frame[score_column] >= CANDIDATE_DISPLAY_MIN_SCORE) & frame["ret_5d"].notna()].copy()
    if filtered.empty or "market_regime" not in filtered.columns:
        return pd.DataFrame()

    def summarize(group: pd.DataFrame) -> pd.Series:
        metrics = calculate_strategy_metrics(group, side)
        return pd.Series(
            {
                "건수": metrics["count"],
                "전략평균(5일)": metrics["avg_return"],
                "승률": metrics["win_rate"],
                "기대값": metrics["expectancy"],
                "손익비": metrics["payoff"],
                "최대낙폭(MDD)": metrics["max_drawdown"],
            }
        )

    regime = filtered.groupby("market_regime", dropna=False).apply(summarize).reset_index()
    return regime.rename(columns={"market_regime": "시장국면"}).sort_values("건수", ascending=False)


def build_trade_plan(selected_row: pd.Series) -> dict:
    close_price = float(selected_row["close"])
    knee_score = float(selected_row.get("knee_score", 0))
    shoulder_score = float(selected_row.get("shoulder_score", 0))
    is_long = knee_score >= CANDIDATE_DISPLAY_MIN_SCORE and knee_score >= shoulder_score
    direction = "LONG" if is_long else "SHORT"

    if is_long:
        stop = close_price * (1.0 - STOP_LOSS_PCT / 100.0)
        target = close_price * (1.0 + TARGET_PROFIT_PCT / 100.0)
    else:
        stop = close_price * (1.0 + STOP_LOSS_PCT / 100.0)
        target = close_price * (1.0 - TARGET_PROFIT_PCT / 100.0)

    risk_per_share = abs(close_price - stop)
    risk_budget = ACCOUNT_SIZE_KRW * (RISK_PER_TRADE_PCT / 100.0)
    qty = int(risk_budget // risk_per_share) if risk_per_share > 0 else 0
    est_loss = qty * risk_per_share
    est_gain = qty * abs(target - close_price)

    return {
        "direction": direction,
        "entry": close_price,
        "stop": stop,
        "target": target,
        "risk_budget": risk_budget,
        "qty": qty,
        "est_loss": est_loss,
        "est_gain": est_gain,
    }


def build_action_list(signals: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    return build_action_list_with_calibration(signals, top_n=top_n, calibration=None, current_regime="Unknown")


def classify_pattern(row: pd.Series, action: str) -> str:
    pct = float(row.get("pct_change", 0.0) or 0.0)
    vol = float(row.get("vol_ratio_20", 1.0) or 1.0)
    knee = float(row.get("knee_score", 0.0) or 0.0)
    reasons = str(row.get("knee_reasons", ""))

    if action == "SELL":
        return "RiskOff"
    if "20일선 회복" in reasons and vol >= 1.3:
        return "Rebound"
    if pct >= 8.0 and vol >= 1.2:
        return "Breakout"
    if knee >= 45 and pct >= 0 and vol >= 1.0:
        return "Continuation"
    return "Rebound"


def build_action_list_with_calibration(
    signals: pd.DataFrame,
    top_n: int = 5,
    calibration: dict | None = None,
    current_regime: str = "Unknown",
    strategy_profile: str = "rebound",
) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame()

    has_calibration = calibration is not None
    calibration = calibration or {}
    action_multiplier = calibration.get("action_multiplier", {"BUY": 1.0, "SELL": 1.0})
    action_expectancy = calibration.get("action_expectancy", {"BUY": 0.0, "SELL": 0.0})
    regime_expectancy = calibration.get("regime_expectancy", {})
    pattern_expectancy = calibration.get("pattern_expectancy", {})
    pattern_count = calibration.get("pattern_count", {})
    pattern_active = calibration.get("pattern_active", {})

    frame = signals.copy()
    for col in ["knee_score", "shoulder_score", "vol_ratio_20", "pct_change", "close", "intraday_quality_score", "turnover"]:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if "symbol" in frame.columns:
        frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    if "turnover" not in frame.columns:
        frame["turnover"] = 0.0
    frame["turnover"] = frame["turnover"].fillna(0.0)
    if "intraday_quality_score" not in frame.columns:
        frame["intraday_quality_score"] = 50.0
    frame["intraday_quality_score"] = frame["intraday_quality_score"].fillna(50.0)

    if strategy_profile == "trend":
        # Trend profile: allow strong momentum names and relax knee threshold.
        buy = frame[
            (
                (
                    (frame["knee_score"] >= 45)
                    & (frame["knee_score"] > frame["shoulder_score"])
                    & (frame["vol_ratio_20"] >= 1.0)
                    & (frame["pct_change"] >= 0)
                )
                | (
                    frame["symbol"].isin(LEADER_SYMBOLS)
                    & (frame["knee_score"] >= 30)
                    & (frame["pct_change"] >= 0)
                    & (frame["vol_ratio_20"] >= 1.0)
                )
            )
            & (frame["turnover"] >= MIN_TURNOVER_TREND)
        ].copy()
    else:
        # Rebound profile: conservative pullback/reversal entry.
        buy = frame[
            (frame["knee_score"] >= CANDIDATE_DISPLAY_MIN_SCORE)
            & (frame["knee_score"] > frame["shoulder_score"])
            & (frame["vol_ratio_20"] >= 1.2)
            & (frame["pct_change"] <= 12.0)
            & (frame["turnover"] >= MIN_TURNOVER_REBOUND)
        ].copy()
    buy["action"] = "BUY"
    if strategy_profile == "trend":
        buy["priority_score_raw"] = (
            buy["knee_score"] + buy["vol_ratio_20"] * 12 + buy["pct_change"].clip(lower=0) * 0.8 + buy["intraday_quality_score"] * 0.20
        )
    else:
        buy["priority_score_raw"] = (
            buy["knee_score"] + buy["vol_ratio_20"] * 10 - buy["pct_change"].clip(lower=0) + buy["intraday_quality_score"] * 0.10
        )

    if strategy_profile == "trend":
        # Trend profile sell: allow earlier weakness detection.
        sell = frame[
            (frame["shoulder_score"] >= 55)
            & (frame["shoulder_score"] > frame["knee_score"])
            & (frame["pct_change"] <= 0)
            & (frame["turnover"] >= MIN_TURNOVER_TREND)
        ].copy()
    else:
        # Rebound profile sell: stricter risk-off signal.
        sell = frame[
            (frame["shoulder_score"] >= CANDIDATE_DISPLAY_MIN_SCORE)
            & (frame["shoulder_score"] > frame["knee_score"])
            & (frame["pct_change"] <= 0)
            & (frame["turnover"] >= MIN_TURNOVER_REBOUND)
        ].copy()
    sell["action"] = "SELL"
    sell["priority_score_raw"] = (
        sell["shoulder_score"] + (sell["vol_ratio_20"].fillna(1.0) * 5) + abs(sell["pct_change"]) + (100 - sell["intraday_quality_score"]) * 0.15
    )

    action = pd.concat([buy, sell], ignore_index=True)
    if action.empty:
        return pd.DataFrame()

    action["strategy_profile"] = strategy_profile
    action["is_leader"] = action["symbol"].astype(str).isin(LEADER_SYMBOLS)
    action["pattern"] = action.apply(lambda row: classify_pattern(row, str(row["action"])), axis=1)

    # Performance-linked calibration:
    # 1) action-level multiplier from recent realized outcomes
    # 2) market-regime expectancy bonus
    action["action_multiplier"] = action["action"].map(lambda x: float(action_multiplier.get(x, 1.0)))
    action["action_expectancy"] = action["action"].map(lambda x: float(action_expectancy.get(x, 0.0)))
    action["regime_bonus"] = action["action"].map(
        lambda x: float(regime_expectancy.get((current_regime, x), 0.0))
    )
    action["pattern_expectancy"] = action.apply(
        lambda row: float(pattern_expectancy.get((str(row["action"]), str(row["pattern"])), 0.0)),
        axis=1,
    )
    action["pattern_count"] = action.apply(
        lambda row: int(pattern_count.get((str(row["action"]), str(row["pattern"])), 0)),
        axis=1,
    )
    action["pattern_active"] = action.apply(
        lambda row: bool(pattern_active.get((str(row["action"]), str(row["pattern"])), True)),
        axis=1,
    )
    action["expected_return_adj"] = action["action_expectancy"] + action["regime_bonus"]
    action["expected_return_final"] = action["expected_return_adj"] + action["pattern_expectancy"] * 0.5
    action["priority_score"] = action["priority_score_raw"] * action["action_multiplier"] + action["regime_bonus"] * 8.0

    # Gate applies only when calibrated recommendation mode is active.
    # Historical recommendation reconstruction should not be blocked here.
    if has_calibration:
        standard_gate = (
            (action["pattern_active"])
            & (action["pattern_count"] >= 8)
            & (action["action_expectancy"] > 0)
            & (action["expected_return_adj"] > 0)
            & (action["expected_return_final"] > 0)
        )
        # Leader names share the same core profitability gate,
        # but use slightly relaxed sample condition to avoid structural exclusion.
        leader_gate = (
            (action["is_leader"])
            & (action["pattern_count"] >= 4)
            & (action["action_expectancy"] > 0)
            & (action["expected_return_adj"] > 0)
            & (action["expected_return_final"] > 0)
        )
        action = action[(standard_gate | leader_gate)].copy()
        if action.empty:
            return pd.DataFrame()

    action = action.sort_values("priority_score", ascending=False).head(top_n).reset_index(drop=True)

    plans = []
    for row in action.itertuples(index=False):
        plan = build_trade_plan(pd.Series(row._asdict()))
        if row.action == "SELL":
            # Force SELL direction in card for short-side action rows.
            plan["direction"] = "SHORT"
        plans.append(plan)

    action["direction"] = [p["direction"] for p in plans]
    action["entry"] = [p["entry"] for p in plans]
    action["stop"] = [p["stop"] for p in plans]
    action["target"] = [p["target"] for p in plans]
    action["qty"] = [p["qty"] for p in plans]
    action["est_loss"] = [p["est_loss"] for p in plans]
    action["est_gain"] = [p["est_gain"] for p in plans]

    return action[
        [
            "action",
            "symbol",
            "name",
            "knee_score",
            "shoulder_score",
            "pct_change",
            "vol_ratio_20",
            "intraday_quality_score",
            "entry",
            "stop",
            "target",
            "qty",
            "est_loss",
            "est_gain",
            "action_multiplier",
            "action_expectancy",
            "regime_bonus",
            "expected_return_adj",
            "pattern",
            "pattern_expectancy",
            "pattern_count",
            "expected_return_final",
            "strategy_profile",
            "is_leader",
        ]
    ]


def build_historical_recommendations(signals_all: pd.DataFrame, top_n: int = 6) -> pd.DataFrame:
    if signals_all.empty:
        return pd.DataFrame()
    if "date" not in signals_all.columns:
        return pd.DataFrame()

    results = []
    for signal_date, day_frame in signals_all.groupby(signals_all["date"].astype(str), sort=True):
        day_actions = build_action_list(day_frame, top_n=top_n)
        if day_actions.empty:
            continue
        day_actions = day_actions.copy()
        day_actions["signal_date"] = str(signal_date)
        results.append(day_actions)

    if not results:
        return pd.DataFrame()
    return pd.concat(results, ignore_index=True)


def build_recommendation_calibration(merged_rec: pd.DataFrame) -> dict:
    if merged_rec.empty:
        return {
            "action_multiplier": {"BUY": 1.0, "SELL": 1.0},
            "action_expectancy": {"BUY": 0.0, "SELL": 0.0},
            "regime_expectancy": {},
            "pattern_expectancy": {},
            "pattern_count": {},
            "pattern_active": {},
        }
    if "signal_date" not in merged_rec.columns:
        return {
            "action_multiplier": {"BUY": 1.0, "SELL": 1.0},
            "action_expectancy": {"BUY": 0.0, "SELL": 0.0},
            "regime_expectancy": {},
            "pattern_expectancy": {},
            "pattern_count": {},
            "pattern_active": {},
        }

    # Use recent window so recommendations adapt to regime shift.
    recent_dates = sorted(merged_rec["signal_date"].astype(str).unique())[-20:]
    recent = merged_rec[merged_rec["signal_date"].astype(str).isin(set(recent_dates))].copy()
    if recent.empty:
        return {
            "action_multiplier": {"BUY": 1.0, "SELL": 1.0},
            "action_expectancy": {"BUY": 0.0, "SELL": 0.0},
            "regime_expectancy": {},
            "pattern_expectancy": {},
            "pattern_count": {},
            "pattern_active": {},
        }

    ret_col = _select_return_column(recent, 10) or _select_return_column(recent, 5)
    if not ret_col:
        return {
            "action_multiplier": {"BUY": 1.0, "SELL": 1.0},
            "action_expectancy": {"BUY": 0.0, "SELL": 0.0},
            "regime_expectancy": {},
            "pattern_expectancy": {},
            "pattern_count": {},
            "pattern_active": {},
        }

    recent = recent[recent[ret_col].notna()].copy()
    if recent.empty:
        return {
            "action_multiplier": {"BUY": 1.0, "SELL": 1.0},
            "action_expectancy": {"BUY": 0.0, "SELL": 0.0},
            "regime_expectancy": {},
            "pattern_expectancy": {},
            "pattern_count": {},
            "pattern_active": {},
        }

    recent["strategy_ret"] = recent[ret_col].astype(float)
    recent.loc[recent["action"] == "SELL", "strategy_ret"] = -recent.loc[recent["action"] == "SELL", "strategy_ret"]
    recent["pattern"] = recent.apply(lambda row: classify_pattern(row, str(row["action"])), axis=1)

    action_multiplier = {"BUY": 1.0, "SELL": 1.0}
    action_expectancy = {"BUY": 0.0, "SELL": 0.0}
    for action_name, group in recent.groupby("action", sort=False):
        win_rate = (group["strategy_ret"] > 0).mean() * 100
        expectancy = group["strategy_ret"].mean()
        action_expectancy[action_name] = float(expectancy)
        # bounded multiplier to avoid unstable jumps
        mult = 1.0 + ((win_rate - 50.0) / 200.0) + (expectancy / 20.0)
        mult = max(0.75, min(1.25, float(mult)))
        action_multiplier[action_name] = mult

    regime_expectancy: dict[tuple[str, str], float] = {}
    if "market_regime" in recent.columns:
        for (regime, action_name), group in recent.groupby(["market_regime", "action"], dropna=False):
            regime_expectancy[(str(regime), action_name)] = float(group["strategy_ret"].mean())

    pattern_expectancy: dict[tuple[str, str], float] = {}
    pattern_count: dict[tuple[str, str], int] = {}
    pattern_active: dict[tuple[str, str], bool] = {}
    for (action_name, pattern_name), group in recent.groupby(["action", "pattern"], dropna=False):
        key = (str(action_name), str(pattern_name))
        exp = float(group["strategy_ret"].mean())
        cnt = int(len(group))
        pattern_expectancy[key] = exp
        pattern_count[key] = cnt
        pattern_active[key] = cnt >= 8 and exp > 0

    return {
        "action_multiplier": action_multiplier,
        "action_expectancy": action_expectancy,
        "regime_expectancy": regime_expectancy,
        "pattern_expectancy": pattern_expectancy,
        "pattern_count": pattern_count,
        "pattern_active": pattern_active,
    }


def _select_return_column(frame: pd.DataFrame, preferred_horizon_days: int) -> str | None:
    preferred = f"ret_{preferred_horizon_days}d"
    if preferred in frame.columns:
        return preferred
    fallback = [col for col in ["ret_20d", "ret_10d", "ret_5d", "ret_3d", "ret_1d"] if col in frame.columns]
    if not fallback:
        return None
    return fallback[0]


def summarize_recommendation_performance(frame: pd.DataFrame, horizon_days: int, lookback_dates: int) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()

    ret_col = _select_return_column(frame, horizon_days)
    if not ret_col:
        return pd.DataFrame()

    dates = sorted(frame["signal_date"].astype(str).unique())
    target_dates = set(dates[-lookback_dates:])
    scope = frame[frame["signal_date"].astype(str).isin(target_dates)].copy()
    scope = scope[scope[ret_col].notna()].copy()
    if scope.empty:
        return pd.DataFrame()

    # BUY는 수익률 그대로, SELL은 하락시 이익이므로 부호 반전.
    scope["strategy_ret"] = scope[ret_col].astype(float)
    scope.loc[scope["action"] == "SELL", "strategy_ret"] = -scope.loc[scope["action"] == "SELL", "strategy_ret"]

    out = []
    for action, group in scope.groupby("action", sort=False):
        ret = group["strategy_ret"]
        out.append(
            {
                "구분": action,
                "평가건수": len(group),
                "평균수익률": ret.mean(),
                "중앙값": ret.median(),
                "승률": (ret > 0).mean() * 100,
                "총합수익률": ret.sum(),
            }
        )

    total_ret = scope["strategy_ret"]
    out.append(
        {
            "구분": "TOTAL",
            "평가건수": len(scope),
            "평균수익률": total_ret.mean(),
            "중앙값": total_ret.median(),
            "승률": (total_ret > 0).mean() * 100,
            "총합수익률": total_ret.sum(),
        }
    )
    summary = pd.DataFrame(out)
    return summary


def build_recommendation_detail(frame: pd.DataFrame, horizon_days: int, lookback_dates: int) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    ret_col = _select_return_column(frame, horizon_days)
    if not ret_col:
        return pd.DataFrame()

    dates = sorted(frame["signal_date"].astype(str).unique())
    target_dates = set(dates[-lookback_dates:])
    scope = frame[frame["signal_date"].astype(str).isin(target_dates)].copy()
    scope = scope[scope[ret_col].notna()].copy()
    if scope.empty:
        return pd.DataFrame()

    scope["수익률"] = scope[ret_col].astype(float)
    scope["전략수익률"] = scope["수익률"]
    scope.loc[scope["action"] == "SELL", "전략수익률"] = -scope.loc[scope["action"] == "SELL", "수익률"]

    view = scope[
        [
            "signal_date",
            "action",
            "symbol",
            "name",
            "knee_score",
            "shoulder_score",
            "수익률",
            "전략수익률",
        ]
    ].copy()
    view = view.rename(
        columns={
            "signal_date": "추천일",
            "action": "액션",
            "symbol": "종목코드",
            "name": "종목명",
            "knee_score": "매수점수",
            "shoulder_score": "매도점수",
        }
    )
    return view.sort_values(["추천일", "전략수익률"], ascending=[False, False]).reset_index(drop=True)


def compute_strategy_series(frame: pd.DataFrame, horizon_days: int) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    ret_col = _select_return_column(frame, horizon_days)
    if not ret_col:
        return pd.DataFrame()
    series = frame[frame[ret_col].notna()].copy()
    if series.empty:
        return pd.DataFrame()
    series["raw_strategy_ret"] = series[ret_col].astype(float)
    series.loc[series["action"] == "SELL", "raw_strategy_ret"] = -series.loc[series["action"] == "SELL", "raw_strategy_ret"]
    cost_pct = ROUNDTRIP_COST_BPS / 100.0
    series["strategy_ret_net"] = series["raw_strategy_ret"] - cost_pct
    if "benchmark_ret_5d" in series.columns and horizon_days == 5:
        series["benchmark_ret"] = pd.to_numeric(series["benchmark_ret_5d"], errors="coerce")
    else:
        series["benchmark_ret"] = pd.NA
    series["excess_ret"] = series["strategy_ret_net"] - pd.to_numeric(series["benchmark_ret"], errors="coerce")
    return series


def _calc_mdd(ret_series: pd.Series) -> float | None:
    if ret_series.empty:
        return None
    equity = (1.0 + ret_series / 100.0).cumprod()
    peak = equity.cummax()
    dd = ((equity / peak) - 1.0) * 100.0
    return float(dd.min()) if not dd.empty else None


def build_trust_validation_report(frame: pd.DataFrame) -> dict:
    series = compute_strategy_series(frame, 5)
    if series.empty:
        return {
            "status": "insufficient",
            "reason": "평가 데이터 부족",
            "train_avg": None,
            "oos_avg": None,
            "oos_excess_avg": None,
            "oos_mdd": None,
            "oos_count": 0,
        }

    all_dates = sorted(series["signal_date"].astype(str).unique())
    test_dates = set(all_dates[-OOS_TEST_DAYS:])
    train_dates = set(all_dates[-(OOS_TEST_DAYS + OOS_TRAIN_DAYS) : -OOS_TEST_DAYS]) if len(all_dates) > OOS_TEST_DAYS else set()
    test_df = series[series["signal_date"].astype(str).isin(test_dates)].copy()
    train_df = series[series["signal_date"].astype(str).isin(train_dates)].copy()
    if test_df.empty or train_df.empty:
        return {
            "status": "insufficient",
            "reason": "워크포워드 구간 부족",
            "train_avg": None,
            "oos_avg": None,
            "oos_excess_avg": None,
            "oos_mdd": None,
            "oos_count": len(test_df),
        }

    train_avg = float(train_df["strategy_ret_net"].mean())
    oos_avg = float(test_df["strategy_ret_net"].mean())
    oos_mdd = _calc_mdd(test_df["strategy_ret_net"])
    oos_excess_avg = float(test_df["excess_ret"].mean()) if test_df["excess_ret"].notna().any() else None

    pass_mdd = oos_mdd is not None and oos_mdd >= MAX_MDD_STOP_PCT
    pass_oos = oos_avg > 0
    pass_excess = True if oos_excess_avg is None else oos_excess_avg > 0
    status = "pass" if (pass_mdd and pass_oos and pass_excess) else "fail"

    fail_reasons = []
    if not pass_oos:
        fail_reasons.append("OOS 평균수익률<=0")
    if not pass_excess:
        fail_reasons.append("벤치마크 초과수익<=0")
    if not pass_mdd:
        fail_reasons.append("MDD 한도 초과")
    reason = ", ".join(fail_reasons) if fail_reasons else "검증 통과"

    return {
        "status": status,
        "reason": reason,
        "train_avg": train_avg,
        "oos_avg": oos_avg,
        "oos_excess_avg": oos_excess_avg,
        "oos_mdd": oos_mdd,
        "oos_count": len(test_df),
    }


def summarize_side(frame: pd.DataFrame, score_column: str, success_column: str, direction: str) -> dict:
    candidates = frame[(frame[score_column] >= CANDIDATE_DISPLAY_MIN_SCORE) & frame["ret_5d"].notna()].copy()
    if candidates.empty:
        return {"count": 0, "avg_5d": None, "median_5d": None, "success_rate": None, "direction_rate": None}

    returns = candidates["ret_5d"].astype(float)
    if direction == "up":
        direction_rate = (returns > 0).mean() * 100
    else:
        direction_rate = (returns < 0).mean() * 100

    return {
        "count": len(candidates),
        "avg_5d": returns.mean(),
        "median_5d": returns.median(),
        "success_rate": candidates[success_column].astype(float).mean() * 100,
        "direction_rate": direction_rate,
    }


def render_side_summary(title: str, summary: dict, success_label: str, direction_label: str) -> None:
    st.markdown(f"**{title}**")
    cols = st.columns(4)
    cols[0].metric("평가완료", f"{summary['count']}건")
    cols[1].metric("5일 평균", format_return(summary["avg_5d"]))
    cols[2].metric("5일 중앙값", format_return(summary["median_5d"]))
    cols[3].metric(success_label, format_rate(summary["success_rate"]))
    st.caption(f"{direction_label}: {format_rate(summary['direction_rate'])}")


def format_recent_evaluation(frame: pd.DataFrame, side: str) -> pd.DataFrame:
    score_column = f"{side}_score"
    success_column = f"{side}_success"
    filtered = frame[(frame[score_column] >= CANDIDATE_DISPLAY_MIN_SCORE) & frame["ret_5d"].notna()].copy()
    if filtered.empty:
        return filtered
    filtered = filtered.sort_values(["signal_date", score_column], ascending=[False, False]).head(20)
    filtered["5일 수익률"] = filtered["ret_5d"].map(format_return)
    filtered["결과"] = filtered[success_column].map(lambda value: "성공" if int(value) == 1 else "미달")
    return filtered[["signal_date", "symbol", "name", score_column, "ret_1d", "ret_3d", "5일 수익률", "ret_10d", "결과"]].rename(
        columns={
            "signal_date": "결정일",
            "symbol": "종목코드",
            "name": "종목명",
            score_column: "점수",
            "ret_1d": "1일",
            "ret_3d": "3일",
            "ret_10d": "10일",
        }
    )


signals_df, signal_date = load_latest_signals(paths["signal_dir"])
validation_df = load_validation_history(Path(paths["validation_file"]))
signals_history_df = load_all_signal_files(paths["signal_dir"])
intraday_feature_path = Path(paths.get("intraday_feature_file", "data/intraday/intraday_features.csv"))
intraday_features_df = load_intraday_feature_history(intraday_feature_path)

if signals_df.empty:
    st.warning("No signal file found yet. Run `python3 run_daily.py` first.")
    st.stop()

header_cols = st.columns(3)
analysis_date = signal_date or "-"
header_cols[0].metric("Analysis Date", analysis_date)
header_cols[1].metric("Knee Strong", int((signals_df["knee_grade"] == "Strong").sum()))
header_cols[2].metric("Shoulder Strong", int((signals_df["shoulder_grade"] == "Strong").sum()))

if not intraday_features_df.empty and signal_date:
    day_features = intraday_features_df[intraday_features_df["date"].astype(str) == str(signal_date)].copy()
    if not day_features.empty:
        signals_df = signals_df.merge(
            day_features[["symbol", "intraday_quality_score", "intraday_close_pos", "intraday_trend_pct", "intraday_vol_skew"]],
            how="left",
            on="symbol",
        )
    else:
        signals_df["intraday_quality_score"] = 50.0
else:
    signals_df["intraday_quality_score"] = 50.0

eval_view_for_reco = pd.DataFrame()
if not validation_df.empty:
    eval_view_for_reco = prepare_evaluation_frame(validation_df, analysis_date)

recommendation_calibration = {"action_multiplier": {"BUY": 1.0, "SELL": 1.0}, "regime_expectancy": {}}
current_regime = "Unknown"
trust_report = {
    "status": "insufficient",
    "reason": "평가 데이터 부족",
    "train_avg": None,
    "oos_avg": None,
    "oos_excess_avg": None,
    "oos_mdd": None,
    "oos_count": 0,
}
if not eval_view_for_reco.empty:
    rec_history_for_cal = build_historical_recommendations(signals_history_df, top_n=6)
    if not rec_history_for_cal.empty:
        merged_for_cal = rec_history_for_cal.merge(
            eval_view_for_reco,
            how="left",
            on=["signal_date", "symbol", "name"],
            suffixes=("_rec", ""),
        )
        recommendation_calibration = build_recommendation_calibration(merged_for_cal)
        trust_report = build_trust_validation_report(merged_for_cal)
    if "market_regime" in eval_view_for_reco.columns:
        known_regime = eval_view_for_reco.dropna(subset=["market_regime"]).sort_values("signal_date")
        if not known_regime.empty:
            current_regime = str(known_regime.iloc[-1]["market_regime"])

st.subheader("오늘의 실행 리스트")
st.caption("단일 통합 로직으로 반등/추세 패턴을 함께 평가하고, 수익성 게이트를 통과한 종목만 추천합니다.")
st.caption("실행 후보는 `패턴 표본수>=8`, `패턴기대값>0`, `최근기대값>0`, `최종기대수익률>0`을 모두 만족한 종목만 표시됩니다.")
rebound_df = build_action_list_with_calibration(
    signals_df,
    top_n=6,
    calibration=recommendation_calibration,
    current_regime=current_regime,
    strategy_profile="rebound",
)
trend_df = build_action_list_with_calibration(
    signals_df,
    top_n=6,
    calibration=recommendation_calibration,
    current_regime=current_regime,
    strategy_profile="trend",
)
action_df = pd.concat([rebound_df, trend_df], ignore_index=True)
if not action_df.empty:
    action_df = action_df.sort_values(["expected_return_final", "priority_score"], ascending=False)
    action_df = action_df.drop_duplicates(subset=["symbol"], keep="first").head(8).reset_index(drop=True)

if trust_report["status"] == "fail":
    action_df = pd.DataFrame()

st.markdown("**신뢰 검증**")
if trust_report["status"] == "pass":
    st.success(
        f"검증 통과 | OOS {trust_report['oos_count']}건 | "
        f"OOS 평균 {format_return(trust_report['oos_avg'])} | "
        f"초과수익 {format_return(trust_report['oos_excess_avg']) if trust_report['oos_excess_avg'] is not None else '-'} | "
        f"MDD {format_return(trust_report['oos_mdd'])}"
    )
elif trust_report["status"] == "fail":
    st.error(
        f"자동 실행 중지 | {trust_report['reason']} | "
        f"OOS 평균 {format_return(trust_report['oos_avg'])} | "
        f"초과수익 {format_return(trust_report['oos_excess_avg']) if trust_report['oos_excess_avg'] is not None else '-'} | "
        f"MDD {format_return(trust_report['oos_mdd'])}"
    )
else:
    st.warning(f"검증 보류 | {trust_report['reason']}")


def render_action_table(action_df: pd.DataFrame, empty_msg: str) -> None:
    if action_df.empty:
        st.info(empty_msg)
        return

    quick_view = action_df.copy()
    quick_view["진입가"] = quick_view["entry"].map(format_currency)
    quick_view["손절가"] = quick_view["stop"].map(format_currency)
    quick_view["목표가"] = quick_view["target"].map(format_currency)
    quick_view["예상손실"] = quick_view["est_loss"].map(format_currency)
    quick_view["예상이익"] = quick_view["est_gain"].map(format_currency)
    quick_view["권장수량"] = quick_view["qty"].map(lambda v: f"{int(v):,}주")
    quick_view["최근기대값"] = quick_view["action_expectancy"].map(format_return)
    quick_view["패턴기대값"] = quick_view["pattern_expectancy"].map(format_return)
    quick_view["최종기대수익률"] = quick_view["expected_return_final"].map(format_return)
    quick_view["패턴표본수"] = quick_view["pattern_count"].map(lambda v: f"{int(v)}")
    quick_view = quick_view.rename(
        columns={
            "action": "액션",
            "symbol": "종목코드",
            "name": "종목명",
            "knee_score": "매수점수",
            "shoulder_score": "매도점수",
            "pct_change": "전일대비등락률",
            "vol_ratio_20": "거래량비율",
            "intraday_quality_score": "분봉품질점수",
            "strategy_profile": "전략",
            "pattern": "패턴",
            "action_multiplier": "성과보정",
            "regime_bonus": "국면보정",
        }
    )
    st.dataframe(
        quick_view[
            [
                "액션",
                "종목코드",
                "종목명",
                "매수점수",
                "매도점수",
                "전일대비등락률",
                "거래량비율",
                "분봉품질점수",
                "전략",
                "패턴",
                "패턴표본수",
                "성과보정",
                "최근기대값",
                "패턴기대값",
                "국면보정",
                "최종기대수익률",
                "진입가",
                "손절가",
                "목표가",
                "권장수량",
                "예상손실",
                "예상이익",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        height=260,
    )

render_action_table(action_df, "통합 조건을 만족한 BUY/SELL 실행 후보가 없습니다.")

m_buy = recommendation_calibration.get("action_multiplier", {}).get("BUY", 1.0)
m_sell = recommendation_calibration.get("action_multiplier", {}).get("SELL", 1.0)
st.caption(
    f"최근 성과 보정계수: BUY x{m_buy:.2f}, SELL x{m_sell:.2f} | 현재 추정 시장국면: {current_regime}"
)
pattern_active_map = recommendation_calibration.get("pattern_active", {})
if pattern_active_map:
    active_labels = [f"{a}:{p}" for (a, p), is_on in pattern_active_map.items() if is_on]
    inactive_labels = [f"{a}:{p}" for (a, p), is_on in pattern_active_map.items() if not is_on]
    if active_labels:
        st.caption(f"활성 패턴: {', '.join(active_labels)}")
    if inactive_labels:
        st.caption(f"비활성 패턴: {', '.join(inactive_labels)}")

knee_view = signals_df[signals_df["knee_score"] >= CANDIDATE_DISPLAY_MIN_SCORE].copy().sort_values(
    ["knee_score", "pct_change"], ascending=[False, False]
)
shoulder_view = signals_df[signals_df["shoulder_score"] >= CANDIDATE_DISPLAY_MIN_SCORE].copy().sort_values(
    ["shoulder_score", "pct_change"], ascending=[False, True]
)

knee_header_col, knee_help_col = st.columns([20, 1])
with knee_header_col:
    st.subheader("Knee Candidates")
with knee_help_col:
    render_candidate_help("Knee Candidates", "knee_score", "knee_reasons")
st.dataframe(
    format_candidate_view(knee_view, "knee_score", "knee_reasons"),
    use_container_width=True,
    hide_index=True,
    height=CANDIDATE_TABLE_HEIGHT,
    column_config=candidate_column_config(),
)

shoulder_header_col, shoulder_help_col = st.columns([20, 1])
with shoulder_header_col:
    st.subheader("Shoulder Candidates")
with shoulder_help_col:
    render_candidate_help("Shoulder Candidates", "shoulder_score", "shoulder_reasons")
st.dataframe(
    format_candidate_view(shoulder_view, "shoulder_score", "shoulder_reasons"),
    use_container_width=True,
    hide_index=True,
    height=CANDIDATE_TABLE_HEIGHT,
    column_config=candidate_column_config(),
)

st.subheader("종목 상세")
st.caption("후보 종목만 선택할 수 있습니다. Knee / Shoulder를 나눠서 고르세요.")

knee_options = (knee_view["symbol"] + " | " + knee_view["name"]).tolist()
shoulder_options = (shoulder_view["symbol"] + " | " + shoulder_view["name"]).tolist()

selector_col1, selector_col2 = st.columns(2)
with selector_col1:
    knee_selected = render_candidate_radio_grid("Knee Candidate", knee_options, "knee_candidate_radio", "Knee")
with selector_col2:
    shoulder_selected = render_candidate_radio_grid("Shoulder Candidate", shoulder_options, "shoulder_candidate_radio", "Shoulder")

available_sources = []
if knee_selected:
    available_sources.append("Knee")
if shoulder_selected:
    available_sources.append("Shoulder")

if not available_sources:
    st.info("현재 상세 보기로 선택할 후보 종목이 없습니다.")
    st.stop()

if "detail_source_selector" not in st.session_state or st.session_state["detail_source_selector"] not in available_sources:
    st.session_state["detail_source_selector"] = available_sources[0]

detail_source = st.radio("상세 기준", options=available_sources, horizontal=True, key="detail_source_selector")
selected_option = knee_selected if detail_source == "Knee" else shoulder_selected

selected_symbol = selected_option.split(" | ", 1)[0]
selected_row = signals_df[signals_df["symbol"] == selected_symbol].iloc[0]
history = load_existing_history(Path(paths["raw_dir"]) / f"{selected_symbol}.csv")

st.markdown("**매매카드 (규칙 기반 제안)**")
trade_plan = build_trade_plan(selected_row)
card_cols = st.columns(5)
card_cols[0].metric("방향", trade_plan["direction"])
card_cols[1].metric("진입가", format_currency(trade_plan["entry"]))
card_cols[2].metric("손절가", format_currency(trade_plan["stop"]))
card_cols[3].metric("목표가", format_currency(trade_plan["target"]))
card_cols[4].metric("권장수량", f"{trade_plan['qty']:,}주")
st.caption(
    f"계좌 {ACCOUNT_SIZE_KRW:,}원, 1회 리스크 {RISK_PER_TRADE_PCT:.1f}% 기준 | "
    f"예상손실 {int(trade_plan['est_loss']):,}원 | 예상이익 {int(trade_plan['est_gain']):,}원"
)

if not history.empty:
    history = prepare_history_for_chart(history)
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=history["date"], y=history["close"], mode="lines", name="Close"))
    if "ma_20" in history.columns:
        figure.add_trace(go.Scatter(x=history["date"], y=history["ma_20"], mode="lines", name="MA20"))
    figure.update_layout(
        height=420,
        margin=dict(l=20, r=20, t=20, b=20),
        xaxis=dict(
            title="Date",
            tickformat="%Y-%m-%d",
            type="date",
        ),
        yaxis=dict(
            title="Close",
            tickformat=",d",
        ),
    )
    st.plotly_chart(figure, use_container_width=True)

export_header_col, export_help_col, export_refresh_col = st.columns([20, 1, 2])
with export_header_col:
    st.subheader("품목별 수출 추이")
with export_help_col:
    render_validation_help()
with export_refresh_col:
    refresh_clicked = st.button("API 새로고침", key="export_refresh_btn", use_container_width=True)

export_df = load_or_refresh_export_history(force_refresh=refresh_clicked)
export_df = prepare_export_frame(export_df)

if export_df.empty:
    st.info("수출 추이 데이터가 아직 없습니다. `DATA_GO_KR_SERVICE_KEY`를 설정하고 `python3 run_daily.py`를 실행하세요.")
else:
    period_order = [period for period in ["01~10", "01~20", "01~31"] if period in set(export_df["기간"].astype(str))]
    if not period_order:
        period_order = sorted(export_df["기간"].astype(str).unique().tolist())

    export_tabs = st.tabs(period_order)
    for tab, period in zip(export_tabs, period_order):
        with tab:
            period_frame = export_df[export_df["기간"].astype(str) == str(period)].copy()
            if period_frame.empty:
                st.info("이 기간의 데이터가 없습니다.")
                continue

            summary, item_summary, trend_view = latest_period_summary(period_frame)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("최신 전체(천달러)", format_export_amount(summary.get("전체")))
            c2.metric("전월대비", format_return(summary.get("전월대비")))
            c3.metric("전년동월대비", format_return(summary.get("전년동월대비")))
            c4.metric("관측월수", f"{len(period_frame)}개")

            trend_fig = go.Figure()
            trend_fig.add_trace(
                go.Scatter(
                    x=trend_view["월별표시"],
                    y=trend_view["전체"],
                    mode="lines+markers",
                    name="전체",
                    line=dict(width=2),
                )
            )
            trend_fig.update_layout(
                height=300,
                margin=dict(l=10, r=10, t=10, b=10),
                yaxis_title="천달러",
                xaxis_title="월별",
            )
            st.plotly_chart(trend_fig, use_container_width=True)

            st.markdown("**최신월 품목 요약**")
            if not item_summary.empty:
                item_view = item_summary.copy()
                item_view["최신값(천달러)"] = item_view["최신값(천달러)"].map(lambda v: f"{int(v):,}" if pd.notna(v) else "-")
                item_view["비중(%)"] = item_view["비중(%)"].map(format_rate)
                item_view["전월대비(%)"] = item_view["전월대비(%)"].map(format_return)
                item_view["전년동월대비(%)"] = item_view["전년동월대비(%)"].map(format_return)
                st.dataframe(item_view.head(10), use_container_width=True, hide_index=True)

            if str(period) == "01~31":
                seasonality = seasonality_summary(period_frame)
                if not seasonality.empty:
                    st.markdown("**계절성 지수(전체)**")
                    season_fig = go.Figure()
                    season_fig.add_trace(
                        go.Bar(
                            x=seasonality["month"].astype(str),
                            y=seasonality["계절성지수(전체)"],
                            name="계절성 지수",
                        )
                    )
                    season_fig.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="지수", xaxis_title="월")
                    st.plotly_chart(season_fig, use_container_width=True)

st.markdown(
    """
    <div style="text-align:center; color:#6b7280; font-size:12px; margin-top:48px; padding-bottom:16px;">
        -created by alicia-
    </div>
    """,
    unsafe_allow_html=True,
)
