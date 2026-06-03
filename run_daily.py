from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from src.knee_shoulder.config import load_config, load_secrets
from src.knee_shoulder.kis_client import KisAuth, fetch_daily_history, fetch_intraday_history, issue_access_token, throttle
from src.knee_shoulder.master import build_stock_master_from_excel, load_stock_master
from src.knee_shoulder.signals import SignalThresholds, score_symbol
from src.knee_shoulder.storage import (
    ensure_directories,
    get_latest_history_date,
    load_all_signal_files,
    load_intraday_feature_history,
    merge_and_save_history,
    merge_and_save_intraday,
    save_daily_patch,
    save_daily_signals,
    save_intraday_feature_history,
    save_validation_history,
)
from src.knee_shoulder.validation import build_validation_rows


def setup_logging(log_dir: str) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / f"{datetime.now():%Y-%m-%d}_run.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily knee/shoulder batch job.")
    parser.add_argument("--config", default=None, help="Path to config.json")
    parser.add_argument("--secrets", default=None, help="Path to secrets.json")
    parser.add_argument("--master-source", default=None, help="Source Excel file for stock master rebuild")
    parser.add_argument("--rebuild-master", action="store_true", help="Rebuild stock master CSV from the source Excel")
    return parser.parse_args()


def resolve_fetch_start_date(raw_path: Path, runtime: dict, end_date_dt: datetime) -> str:
    latest_stored = get_latest_history_date(raw_path)
    if not latest_stored:
        return (end_date_dt - timedelta(days=runtime["history_lookback_days"])).strftime("%Y%m%d")

    latest_dt = datetime.strptime(latest_stored, "%Y%m%d")
    start_dt = latest_dt + timedelta(days=1)
    if start_dt > end_date_dt:
        start_dt = end_date_dt
    return start_dt.strftime("%Y%m%d")


def _resolve_latest_market_date(patch_df: pd.DataFrame, fallback_date: str) -> str:
    if patch_df.empty or "date" not in patch_df.columns:
        return fallback_date
    dates = patch_df["date"].dropna().astype(str)
    if dates.empty:
        return fallback_date
    return dates.max()


def _compute_intraday_quality(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"intraday_quality_score": 0.0, "intraday_close_pos": 0.0, "intraday_trend_pct": 0.0, "intraday_vol_skew": 0.0}

    bars = frame.copy().sort_values(["date", "time"]).reset_index(drop=True)
    close = pd.to_numeric(bars["close"], errors="coerce")
    volume = pd.to_numeric(bars["volume"], errors="coerce").fillna(0)
    if close.empty or close.isna().all():
        return {"intraday_quality_score": 0.0, "intraday_close_pos": 0.0, "intraday_trend_pct": 0.0, "intraday_vol_skew": 0.0}

    day_high = close.max()
    day_low = close.min()
    day_open = close.iloc[0]
    day_close = close.iloc[-1]
    close_pos = 0.5 if day_high == day_low else float((day_close - day_low) / (day_high - day_low))
    trend_pct = 0.0 if day_open == 0 else float(((day_close / day_open) - 1.0) * 100.0)

    split_idx = max(1, len(bars) // 2)
    vol_1 = volume.iloc[:split_idx].sum()
    vol_2 = volume.iloc[split_idx:].sum()
    vol_skew = 0.0 if vol_1 == 0 else float((vol_2 / vol_1) - 1.0)

    # 0~100 quality score
    score = 50.0
    score += (close_pos - 0.5) * 50.0
    score += max(-5.0, min(5.0, trend_pct)) * 4.0
    score += max(-1.0, min(1.0, vol_skew)) * 10.0
    score = max(0.0, min(100.0, score))
    return {
        "intraday_quality_score": round(score, 2),
        "intraday_close_pos": round(close_pos * 100.0, 2),
        "intraday_trend_pct": round(trend_pct, 2),
        "intraday_vol_skew": round(vol_skew, 2),
    }


def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    paths = config["paths"]
    runtime = config["runtime"]
    validation_config = config["validation"]

    ensure_directories(
        [
            paths["raw_dir"],
            paths["patch_dir"],
            paths["signal_dir"],
            str(Path(paths["validation_file"]).parent),
            paths["log_dir"],
            paths.get("intraday_dir", "data/intraday"),
            str(Path(paths.get("intraday_feature_file", "data/intraday/intraday_features.csv")).parent),
        ]
    )
    setup_logging(paths["log_dir"])

    if args.rebuild_master:
        if not args.master_source:
            raise ValueError("--master-source is required with --rebuild-master")
        master_df = build_stock_master_from_excel(args.master_source, paths["stock_master"])
        logging.info("Rebuilt stock master with %s symbols", len(master_df))
        if not Path(paths["stock_master"]).exists():
            raise FileNotFoundError(f"Stock master was not created: {paths['stock_master']}")
        secrets_path = Path(args.secrets) if args.secrets else Path("secrets.json")
        if not secrets_path.exists():
            logging.info("Master rebuild only completed. secrets.json not found, skipping API batch run.")
            return

    secrets = load_secrets(args.secrets)

    master = load_stock_master(paths["stock_master"])
    logging.info("Loaded %s enabled symbols", len(master))

    auth = KisAuth(
        app_key=secrets["app_key"],
        app_secret=secrets["app_secret"],
        base_url=config["kis"]["base_url"],
    )
    access_token = issue_access_token(auth)

    run_at_dt = datetime.now()
    end_date_dt = run_at_dt
    end_date = end_date_dt.strftime("%Y%m%d")
    thresholds = SignalThresholds(
        signal_threshold=runtime["signal_threshold"],
        strong_threshold=runtime["strong_threshold"],
        min_volume=runtime["min_volume"],
    )

    logging.info("Run timestamp: %s", run_at_dt.isoformat(timespec="seconds"))
    logging.info("Target date: %s", end_date)

    patch_rows = []
    signal_rows = []

    for stock in master.itertuples(index=False):
        raw_path = Path(paths["raw_dir"]) / f"{stock.symbol}.csv"
        start_date = resolve_fetch_start_date(raw_path, runtime, end_date_dt)
        latest_stored = get_latest_history_date(raw_path)
        logging.info(
            "Fetching %s %s from %s to %s (latest stored: %s)",
            stock.symbol,
            stock.name,
            start_date,
            end_date,
            latest_stored or "none",
        )
        history = fetch_daily_history(auth, access_token, stock.symbol, start_date, end_date)
        throttle(runtime["request_sleep_sec"])
        if history.empty:
            logging.warning("No history for %s", stock.symbol)
            continue

        history["symbol"] = stock.symbol
        history["name"] = stock.name

        latest_row = history.iloc[[-1]].copy()
        latest_row["fetched_at"] = run_at_dt.isoformat(timespec="seconds")
        latest_row["analysis_date"] = end_date
        patch_rows.append(latest_row)

        merged = merge_and_save_history(raw_path, history.drop(columns=["symbol", "name"]))
        signal = score_symbol(merged, stock.symbol, stock.name, thresholds)
        if signal:
            signal_rows.append(signal)

    if not patch_rows:
        logging.warning("No daily rows collected.")
        return

    patch_df = pd.concat(patch_rows, ignore_index=True)
    latest_date = _resolve_latest_market_date(patch_df, end_date)
    save_daily_patch(Path(paths["patch_dir"]) / f"{latest_date}_prices.csv", patch_df)

    signals_df = pd.DataFrame(signal_rows)
    if signals_df.empty:
        logging.warning("No signals calculated.")
        return
    signals_df["analysis_date"] = latest_date
    signals_df["run_at"] = run_at_dt.isoformat(timespec="seconds")
    save_daily_signals(Path(paths["signal_dir"]) / f"{latest_date}_signals.csv", signals_df)

    # Intraday enrich: collect minute bars for top candidates and persist quality features.
    intraday_rows = []
    intraday_dir = Path(paths.get("intraday_dir", "data/intraday"))
    intraday_feature_path = Path(paths.get("intraday_feature_file", "data/intraday/intraday_features.csv"))
    top_buy = signals_df.sort_values("knee_score", ascending=False).head(20)
    top_sell = signals_df.sort_values("shoulder_score", ascending=False).head(20)
    candidate_symbols = sorted(set(top_buy["symbol"].astype(str).tolist() + top_sell["symbol"].astype(str).tolist()))
    for symbol in candidate_symbols:
        try:
            intraday = fetch_intraday_history(auth, access_token, symbol, latest_date)
            throttle(runtime["request_sleep_sec"])
        except Exception as exc:  # noqa: BLE001
            logging.warning("Intraday fetch failed for %s: %s", symbol, exc)
            continue
        if intraday.empty:
            continue
        merge_and_save_intraday(intraday_dir / f"{symbol}.csv", intraday)
        feature = _compute_intraday_quality(intraday)
        signal_row = signals_df[signals_df["symbol"].astype(str) == str(symbol)]
        name = signal_row.iloc[0]["name"] if not signal_row.empty else ""
        intraday_rows.append({"date": latest_date, "symbol": str(symbol), "name": str(name), **feature})

    if intraday_rows:
        feature_new = pd.DataFrame(intraday_rows)
        feature_old = load_intraday_feature_history(intraday_feature_path)
        feature_all = pd.concat([feature_old, feature_new], ignore_index=True)
        feature_all = feature_all.drop_duplicates(subset=["date", "symbol"]).sort_values(["date", "symbol"]).reset_index(drop=True)
        save_intraday_feature_history(intraday_feature_path, feature_all)
        logging.info("Saved intraday features: %s", len(feature_new))

    all_signals_df = load_all_signal_files(paths["signal_dir"])
    benchmark_symbol = validation_config.get("benchmark_symbol", "005930")
    new_validation = build_validation_rows(
        all_signals_df,
        paths["raw_dir"],
        validation_config["forward_days"],
        benchmark_symbol=benchmark_symbol,
    )
    validation_path = Path(paths["validation_file"])
    validation_all = new_validation.drop_duplicates(subset=["signal_date", "symbol"]).sort_values(["signal_date", "symbol"])
    save_validation_history(validation_path, validation_all)

    logging.info("Saved patch rows: %s", len(patch_df))
    logging.info("Saved signals: %s", len(signals_df))
    logging.info("Validation rows rebuilt: %s", len(new_validation))


if __name__ == "__main__":
    main()
