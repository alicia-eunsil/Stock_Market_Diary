from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.knee_shoulder.config import load_config
from src.knee_shoulder.storage import load_existing_history, load_validation_history


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
RISK_PER_TRADE_PCT = float(validation_cfg.get("risk_per_trade_pct", 1.0))
STOP_LOSS_PCT = float(validation_cfg.get("stop_loss_pct", 3.0))
TARGET_PROFIT_PCT = float(validation_cfg.get("target_profit_pct", 6.0))
ACCOUNT_SIZE_KRW = int(validation_cfg.get("account_size_krw", 10_000_000))


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
        st.markdown("**예측평가 읽는 법**")
        st.markdown("- 이 표는 오늘 후보가 아니라, **직전 거래일에 포착된 후보가 오늘 기준으로 어떻게 됐는지** 보는 영역입니다.")
        st.markdown("- `결정일` > 해당 종목이 매수/매도 후보로 결정된 날짜입니다.")
        st.markdown("- `매수점수` > 무릎 후보 점수입니다. 높을수록 매수 후보 근거가 많이 겹친 상태입니다.")
        st.markdown("- `매도점수` > 어깨 후보 점수입니다. 높을수록 매도 후보 근거가 많이 겹친 상태입니다.")
        st.markdown("- `수익률(1일)`, `수익률(3일)`, `수익률(5일)`, `수익률(10일)` > 결정일 이후 각각 1일, 3일, 5일, 10일 뒤 수익률입니다.")
        st.markdown("- 예를 들어 3월 9일에 결정된 종목이면, `수익률(1일)`은 3월 10일 데이터가 쌓일 때 채워집니다.")
        st.markdown("- `수익률(3일)`, `수익률(5일)`, `수익률(10일)`도 각각 해당 일수가 지난 뒤 배치가 다시 돌면 채워집니다.")
        st.markdown("- `매수 성공여부` > 5일 안에 `+3%` 이상 상승했는지 뜻합니다.")
        st.markdown("- `매도 성공여부` > 5일 안에 `-3%` 이하 하락했는지 뜻합니다.")
        st.markdown("- 해석할 때는 매수 후보는 수익률이 플러스인지, 매도 후보는 수익률이 마이너스인지 먼저 보면 됩니다.")


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


def render_candidate_radio_grid(title: str, options: list[str], key_prefix: str) -> str | None:
    st.markdown(f"**{title}**")
    if not options:
        st.caption("후보 없음")
        return None

    limited = options[:10]
    left_chunk = limited[:5]
    right_chunk = limited[5:10]
    left_col, right_col = st.columns(2)

    with left_col:
        left_selected = st.radio(
            f"{title} 좌측",
            options=left_chunk,
            key=f"{key_prefix}_left",
            label_visibility="collapsed",
        )
    with right_col:
        right_selected = None
        if right_chunk:
            right_selected = st.radio(
                f"{title} 우측",
                options=right_chunk,
                index=None,
                key=f"{key_prefix}_right",
                label_visibility="collapsed",
            )

    if right_selected:
        return right_selected
    if left_selected:
        return left_selected
    return limited[0]


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


def format_return(value: float | int | None) -> str:
    if pd.isna(value):
        return "-"
    return f"{float(value):+.2f}%"


def format_rate(value: float | int | None) -> str:
    if pd.isna(value):
        return "-"
    return f"{float(value):.1f}%"


def format_currency(value: float | int | None) -> str:
    if pd.isna(value):
        return "-"
    return f"{int(value):,}원"


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
    if signals.empty:
        return pd.DataFrame()

    frame = signals.copy()
    for col in ["knee_score", "shoulder_score", "vol_ratio_20", "pct_change", "close"]:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

    # Buy priority: strong knee + meaningful volume expansion + not overly extended day spike
    buy = frame[
        (frame["knee_score"] >= CANDIDATE_DISPLAY_MIN_SCORE)
        & (frame["knee_score"] > frame["shoulder_score"])
        & (frame["vol_ratio_20"] >= 1.2)
        & (frame["pct_change"] <= 12.0)
    ].copy()
    buy["action"] = "BUY"
    buy["priority_score"] = buy["knee_score"] + buy["vol_ratio_20"] * 10 - buy["pct_change"].clip(lower=0)

    # Sell priority: strong shoulder + weakness confirmation
    sell = frame[
        (frame["shoulder_score"] >= CANDIDATE_DISPLAY_MIN_SCORE)
        & (frame["shoulder_score"] > frame["knee_score"])
        & (frame["pct_change"] <= 0)
    ].copy()
    sell["action"] = "SELL"
    sell["priority_score"] = sell["shoulder_score"] + (sell["vol_ratio_20"].fillna(1.0) * 5) + abs(sell["pct_change"])

    action = pd.concat([buy, sell], ignore_index=True)
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
            "entry",
            "stop",
            "target",
            "qty",
            "est_loss",
            "est_gain",
        ]
    ]


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

if signals_df.empty:
    st.warning("No signal file found yet. Run `python3 run_daily.py` first.")
    st.stop()

header_cols = st.columns(3)
analysis_date = signal_date or "-"
header_cols[0].metric("Analysis Date", analysis_date)
header_cols[1].metric("Knee Strong", int((signals_df["knee_grade"] == "Strong").sum()))
header_cols[2].metric("Shoulder Strong", int((signals_df["shoulder_grade"] == "Strong").sum()))

st.subheader("오늘의 실행 리스트")
st.caption("아래 항목은 규칙 기반으로 추린 즉시 실행 후보입니다.")
action_df = build_action_list(signals_df, top_n=6)
if action_df.empty:
    st.info("오늘은 조건을 만족한 BUY/SELL 실행 후보가 없습니다. 관망(WATCH) 권장.")
else:
    quick_view = action_df.copy()
    quick_view["진입가"] = quick_view["entry"].map(format_currency)
    quick_view["손절가"] = quick_view["stop"].map(format_currency)
    quick_view["목표가"] = quick_view["target"].map(format_currency)
    quick_view["예상손실"] = quick_view["est_loss"].map(format_currency)
    quick_view["예상이익"] = quick_view["est_gain"].map(format_currency)
    quick_view["권장수량"] = quick_view["qty"].map(lambda v: f"{int(v):,}주")
    quick_view = quick_view.rename(
        columns={
            "action": "액션",
            "symbol": "종목코드",
            "name": "종목명",
            "knee_score": "매수점수",
            "shoulder_score": "매도점수",
            "pct_change": "전일대비등락률",
            "vol_ratio_20": "거래량비율",
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
    knee_selected = render_candidate_radio_grid("Knee Candidate", knee_options, "knee_candidate_radio")
with selector_col2:
    shoulder_selected = render_candidate_radio_grid("Shoulder Candidate", shoulder_options, "shoulder_candidate_radio")

selected_option = None
if knee_selected:
    selected_option = knee_selected
elif shoulder_selected:
    selected_option = shoulder_selected
else:
    st.info("현재 상세 보기로 선택할 후보 종목이 없습니다.")
    st.stop()

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

validation_header_col, validation_help_col = st.columns([20, 1])
with validation_header_col:
    st.subheader("예측평가")
with validation_help_col:
    render_validation_help()
if not validation_df.empty:
    eval_view = prepare_evaluation_frame(validation_df, analysis_date)

    if eval_view.empty:
        st.info("아직 표시할 예측평가 데이터가 없습니다.")
    else:
        completed_view = eval_view[eval_view["ret_5d"].notna()].copy()
        if completed_view.empty:
            st.info("5일 평가가 완료된 후보가 아직 없습니다.")
        else:
            knee_summary = summarize_side(completed_view, "knee_score", "knee_success", "up")
            shoulder_summary = summarize_side(completed_view, "shoulder_score", "shoulder_success", "down")

            summary_col1, summary_col2 = st.columns(2)
            with summary_col1:
                render_side_summary("Knee 매수 후보 성과", knee_summary, "+3% 성공률", "5일 플러스 비율")
            with summary_col2:
                render_side_summary("Shoulder 경고 후보 성과", shoulder_summary, "-3% 성공률", "5일 하락 비율")

            st.markdown("**전략 품질 지표 (5일 기준)**")
            quality_col1, quality_col2 = st.columns(2)
            knee_candidates = completed_view[completed_view["knee_score"] >= CANDIDATE_DISPLAY_MIN_SCORE].copy()
            shoulder_candidates = completed_view[completed_view["shoulder_score"] >= CANDIDATE_DISPLAY_MIN_SCORE].copy()
            knee_quality = calculate_strategy_metrics(knee_candidates, "knee")
            shoulder_quality = calculate_strategy_metrics(shoulder_candidates, "shoulder")

            with quality_col1:
                st.markdown("`Knee 전략`")
                q1 = st.columns(4)
                q1[0].metric("승률", format_rate(knee_quality["win_rate"]))
                q1[1].metric("기대값", format_return(knee_quality["expectancy"]))
                q1[2].metric("손익비", "-" if knee_quality["payoff"] is None else f"{knee_quality['payoff']:.2f}")
                q1[3].metric("MDD", format_return(knee_quality["max_drawdown"]))
            with quality_col2:
                st.markdown("`Shoulder 전략`")
                q2 = st.columns(4)
                q2[0].metric("승률", format_rate(shoulder_quality["win_rate"]))
                q2[1].metric("기대값", format_return(shoulder_quality["expectancy"]))
                q2[2].metric("손익비", "-" if shoulder_quality["payoff"] is None else f"{shoulder_quality['payoff']:.2f}")
                q2[3].metric("MDD", format_return(shoulder_quality["max_drawdown"]))

            st.markdown("**시장국면별 성과**")
            regime_tab1, regime_tab2 = st.tabs(["Knee", "Shoulder"])
            with regime_tab1:
                knee_regime = build_regime_summary(completed_view, "knee")
                if knee_regime.empty:
                    st.info("Knee 국면별 데이터가 아직 부족합니다.")
                else:
                    st.dataframe(knee_regime, use_container_width=True, hide_index=True)
            with regime_tab2:
                shoulder_regime = build_regime_summary(completed_view, "shoulder")
                if shoulder_regime.empty:
                    st.info("Shoulder 국면별 데이터가 아직 부족합니다.")
                else:
                    st.dataframe(shoulder_regime, use_container_width=True, hide_index=True)

            st.markdown("**최근 완료 결과**")
            tab_knee, tab_shoulder, tab_raw = st.tabs(["Knee", "Shoulder", "상세"])
            with tab_knee:
                recent_knee = format_recent_evaluation(completed_view, "knee")
                if recent_knee.empty:
                    st.info("최근 완료된 Knee 평가가 없습니다.")
                else:
                    st.dataframe(recent_knee, use_container_width=True, hide_index=True)
            with tab_shoulder:
                recent_shoulder = format_recent_evaluation(completed_view, "shoulder")
                if recent_shoulder.empty:
                    st.info("최근 완료된 Shoulder 평가가 없습니다.")
                else:
                    st.dataframe(recent_shoulder, use_container_width=True, hide_index=True)
            with tab_raw:
                raw_view = eval_view[
                    (eval_view["knee_score"] >= CANDIDATE_DISPLAY_MIN_SCORE)
                    | (eval_view["shoulder_score"] >= CANDIDATE_DISPLAY_MIN_SCORE)
                ].copy()
                recent_dates = sorted(raw_view["signal_date"].astype(str).unique())[-5:]
                raw_view = raw_view[raw_view["signal_date"].astype(str).isin(recent_dates)].copy()
                raw_view = raw_view.sort_values(["signal_date", "knee_score", "shoulder_score"], ascending=[False, False, False])
                st.dataframe(format_validation_view(raw_view), use_container_width=True, hide_index=True)
else:
    st.info("예측평가 데이터가 아직 없습니다.")

st.markdown(
    """
    <div style="text-align:center; color:#6b7280; font-size:12px; margin-top:48px; padding-bottom:16px;">
        -created by alicia-
    </div>
    """,
    unsafe_allow_html=True,
)
