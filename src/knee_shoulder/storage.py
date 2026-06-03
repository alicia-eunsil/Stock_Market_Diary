from __future__ import annotations

from pathlib import Path

import pandas as pd


def ensure_directories(paths: list[str]) -> None:
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)


def load_existing_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "turnover"])
    return pd.read_csv(path, dtype={"date": str})


def get_latest_history_date(path: Path) -> str | None:
    history = load_existing_history(path)
    if history.empty or "date" not in history.columns:
        return None
    dates = history["date"].dropna().astype(str)
    if dates.empty:
        return None
    return dates.max()


def merge_and_save_history(path: Path, incoming: pd.DataFrame) -> pd.DataFrame:
    current = load_existing_history(path)
    combined = pd.concat([current, incoming], ignore_index=True)
    combined = combined.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(path, index=False, encoding="utf-8-sig")
    return combined


def save_daily_patch(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def save_daily_signals(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.sort_values(["knee_score", "shoulder_score"], ascending=False).to_csv(path, index=False, encoding="utf-8-sig")


def load_validation_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"signal_date": str, "symbol": str})


def save_validation_history(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def load_all_signal_files(signal_dir: str) -> pd.DataFrame:
    files = sorted(Path(signal_dir).glob("*_signals.csv"))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_csv(file, dtype={"symbol": str, "date": str}) for file in files]
    combined = pd.concat(frames, ignore_index=True)
    return combined.drop_duplicates(subset=["date", "symbol"]).sort_values(["date", "symbol"]).reset_index(drop=True)


def load_intraday_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "time", "open", "high", "low", "close", "volume", "turnover"])
    return pd.read_csv(path, dtype={"date": str, "time": str})


def merge_and_save_intraday(path: Path, incoming: pd.DataFrame) -> pd.DataFrame:
    current = load_intraday_history(path)
    combined = pd.concat([current, incoming], ignore_index=True)
    combined = combined.drop_duplicates(subset=["date", "time"]).sort_values(["date", "time"]).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(path, index=False, encoding="utf-8-sig")
    return combined


def load_intraday_feature_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"date": str, "symbol": str})


def save_intraday_feature_history(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def load_export_trend_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"월별": str, "기간": str})


def save_export_trend_history(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
