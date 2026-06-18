from __future__ import annotations

import os
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from bs4 import BeautifulSoup
from requests.utils import quote

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except Exception:  # noqa: BLE001
    firebase_admin = None
    credentials = None
    firestore = None

try:
    import yfinance as yf
except Exception:  # noqa: BLE001
    yf = None

st.set_page_config(page_title="Stock Market Dashboard", page_icon="🏖️", layout="wide")


GLOBAL_INDEX_TICKERS = {
    "NASDAQ": "^IXIC",
}
NAVER_INDEX_SYMBOLS = {
    "KOSPI": "KOSPI",
    "KOSDAQ": "KOSDAQ",
}
TREASURY_TICKER = "^TNX"
COMMENT_COLUMNS = ["date", "session", "comment", "created_at"]
PORTFOLIO_COLUMNS = ["symbol", "name", "avg_buy_price", "quantity", "memo", "updated_at"]
APP_VERSION = "2026-06-17-firebase-storage"


class FirebaseStorageError(RuntimeError):
    pass


def load_config(config_path: str = "config.json") -> dict:
    path = Path(config_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def get_runtime_secret(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    try:
        value = st.secrets.get(name, default)
    except Exception:  # noqa: BLE001
        return default
    return value if value else default


def get_firebase_service_account() -> dict | None:
    raw = get_runtime_secret("FIREBASE_SERVICE_ACCOUNT_JSON")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FirebaseStorageError("FIREBASE_SERVICE_ACCOUNT_JSON 형식이 올바른 JSON이 아닙니다.") from exc

    try:
        firebase_secret = st.secrets.get("firebase", None)
    except Exception:  # noqa: BLE001
        firebase_secret = None
    if firebase_secret:
        return dict(firebase_secret)

    return None


def get_firebase_collection_prefix() -> str:
    return get_runtime_secret("FIREBASE_COLLECTION_PREFIX", "stock_diary") or "stock_diary"


@st.cache_resource(show_spinner=False)
def get_firestore_client():
    if firebase_admin is None or credentials is None or firestore is None:
        raise FirebaseStorageError("firebase-admin 패키지가 설치되어 있지 않습니다.")

    service_account = get_firebase_service_account()
    if not service_account:
        return None

    try:
        app = firebase_admin.get_app("stock-diary")
    except ValueError:
        cred = credentials.Certificate(service_account)
        app = firebase_admin.initialize_app(cred, name="stock-diary")
    return firestore.client(app=app)


def get_firestore_collection(name: str):
    client = get_firestore_client()
    if client is None:
        return None
    return client.collection(get_firebase_collection_prefix()).document(name).collection("items")


def prepare_firestore_record(row: dict) -> dict:
    clean = {}
    for key, value in row.items():
        if pd.isna(value):
            clean[key] = ""
        elif hasattr(value, "item"):
            clean[key] = value.item()
        else:
            clean[key] = value
    return clean


def require_access_code() -> None:
    expected_code = os.getenv("ACCESS_CODE") or st.secrets.get("ACCESS_CODE", None)
    if not expected_code:
        return

    if st.session_state.get("access_granted"):
        return

    st.title("Stock Market Dashboard")
    entered_code = st.text_input("접속코드", type="password")
    if st.button("확인", type="primary"):
        if entered_code == expected_code:
            st.session_state["access_granted"] = True
            st.rerun()
        else:
            st.error("접속코드가 올바르지 않습니다.")
    st.stop()


def normalize_symbol(value: str) -> str:
    return "".join(ch for ch in str(value).strip() if ch.isdigit()).zfill(6)


def parse_symbol_list(raw: str) -> list[str]:
    tokens = raw.replace(",", "\n").replace(" ", "\n").splitlines()
    symbols = [normalize_symbol(token) for token in tokens if normalize_symbol(token) != "000000"]
    return list(dict.fromkeys(symbols))


def format_krw(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{int(value):,}원"


def format_market_cap(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    trillion = float(value) / 1_0000_0000_0000
    if trillion >= 1:
        return f"{trillion:.1f}조원"
    billion = float(value) / 1_0000_0000
    return f"{billion:.0f}억원"


def format_number(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):,.0f}"


def format_pct(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):+.2f}%"


def color_for_change(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "#6b7280"
    if float(value) > 0:
        return "#dc2626"
    if float(value) < 0:
        return "#2563eb"
    return "#6b7280"


def load_local_master() -> pd.DataFrame:
    path = Path("data/master/stocks_kr.csv")
    if not path.exists():
        return pd.DataFrame(columns=["symbol", "name", "market"])
    frame = pd.read_csv(path, dtype={"symbol": str})
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    if "enabled" in frame.columns:
        frame = frame[pd.to_numeric(frame["enabled"], errors="coerce").fillna(1).astype(int) == 1]
    if "market" not in frame.columns:
        frame["market"] = "KR"
    return frame[["symbol", "name", "market"]].drop_duplicates("symbol").reset_index(drop=True)


def load_local_top_symbols(limit: int) -> pd.DataFrame:
    master = load_local_master().head(limit).copy()
    if master.empty:
        return master
    master["rank"] = range(1, len(master) + 1)
    master["close"] = pd.NA
    master["market_cap"] = pd.NA
    master["volume"] = pd.NA
    master["turnover"] = pd.NA
    return master[["rank", "symbol", "name", "market", "close", "market_cap", "volume", "turnover"]]


def parse_int(value: str) -> int | None:
    cleaned = str(value).replace(",", "").replace("%", "").strip()
    if not cleaned or cleaned in {"N/A", "-"}:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def load_naver_market_cap(limit: int) -> tuple[pd.DataFrame, str | None]:
    rows = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for market_code, market_name in [("0", "KOSPI"), ("1", "KOSDAQ")]:
        for page in range(1, 4):
            url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={market_code}&page={page}"
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            response.encoding = "euc-kr"
            soup = BeautifulSoup(response.text, "html.parser")
            for row in soup.select("table.type_2 tr"):
                link = row.select_one("a.tltle[href*='code=']")
                cells = [cell.get_text(strip=True) for cell in row.find_all("td")]
                if not link or len(cells) < 7:
                    continue
                href = link.get("href", "")
                symbol = href.split("code=")[-1].split("&")[0].strip().zfill(6)
                close = parse_int(cells[2])
                market_cap_100m = parse_int(cells[6])
                volume = parse_int(cells[9]) if len(cells) > 9 else None
                if not symbol or market_cap_100m is None:
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "name": link.get_text(strip=True),
                        "market": market_name,
                        "close": close,
                        "market_cap": market_cap_100m * 100_000_000,
                        "volume": volume,
                        "turnover": pd.NA,
                    }
                )
    if not rows:
        return pd.DataFrame(), None
    frame = pd.DataFrame(rows).drop_duplicates("symbol")
    frame = frame.sort_values("market_cap", ascending=False).head(limit).reset_index(drop=True)
    frame["rank"] = frame.index + 1
    return frame[["rank", "symbol", "name", "market", "close", "market_cap", "volume", "turnover"]], datetime.now().strftime("%Y%m%d")


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def load_top_market_cap(limit: int, cache_version: str) -> tuple[pd.DataFrame, str | None, str | None]:
    try:
        naver, base_date = load_naver_market_cap(limit)
        if not naver.empty:
            return naver, base_date, None
    except Exception as naver_exc:  # noqa: BLE001
        naver_error = str(naver_exc)
    else:
        naver_error = "네이버 시가총액 데이터 없음"

    fallback = load_local_top_symbols(limit)
    if fallback.empty:
        return pd.DataFrame(), None, f"네이버 시가총액 조회 실패: {naver_error}"
    return fallback, None, f"네이버 시가총액 조회 실패로 로컬 종목 마스터를 사용합니다: {naver_error}"


def load_naver_chart(symbol: str, count: int, label: str | None = None) -> pd.DataFrame:
    url = "https://fchart.stock.naver.com/sise.nhn"
    response = requests.get(
        url,
        params={"symbol": symbol, "timeframe": "day", "count": count, "requestType": "0"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    response.raise_for_status()
    text = response.content.decode("euc-kr", errors="ignore")
    rows = []
    for raw in re.findall(r'<item\s+data="([^"]+)"\s*/?>', text):
        parts = raw.split("|")
        if len(parts) < 6:
            continue
        rows.append(
            {
                "date": pd.to_datetime(parts[0], format="%Y%m%d", errors="coerce"),
                "open": float(parts[1]),
                "high": float(parts[2]),
                "low": float(parts[3]),
                "close": float(parts[4]),
                "volume": float(parts[5]),
            }
        )
    frame = pd.DataFrame(rows).dropna(subset=["date"])
    if frame.empty:
        return frame
    if label is not None:
        frame["label"] = label
    return frame.sort_values("date").reset_index(drop=True)


@st.cache_data(ttl=60 * 30, show_spinner=False)
def load_stock_history(symbols: tuple[str, ...], days: int, cache_version: str) -> tuple[pd.DataFrame, dict[str, str], str | None]:
    if not symbols:
        return pd.DataFrame(), {}, None

    master = load_local_master()
    master_names = dict(zip(master["symbol"], master["name"], strict=False)) if not master.empty else {}
    meta, _, _ = load_top_market_cap(max(80, len(symbols)), cache_version)
    meta_names = dict(zip(meta["symbol"], meta["name"], strict=False)) if not meta.empty else {}
    name_map = {**master_names, **meta_names}

    naver_frames = []
    naver_errors = []
    for symbol in symbols:
        try:
            hist = load_naver_chart(symbol, days)
        except Exception as exc:  # noqa: BLE001
            naver_errors.append(f"{symbol}: {exc}")
            continue
        if hist.empty:
            naver_errors.append(f"{symbol}: 데이터 없음")
            continue
        hist["symbol"] = symbol
        hist["name"] = name_map.get(symbol, symbol)
        hist["change_pct"] = hist["close"].pct_change() * 100.0
        naver_frames.append(hist[["date", "symbol", "name", "open", "high", "low", "close", "volume", "change_pct"]])

    if naver_frames:
        combined = pd.concat(naver_frames, ignore_index=True)
        warning = "; ".join(naver_errors[:5]) if naver_errors else None
        return combined.reset_index(drop=True), name_map, warning

    local_history = load_local_stock_history(symbols, days)
    if not local_history.empty:
        local_names = dict(zip(local_history["symbol"], local_history["name"], strict=False))
        return local_history, local_names, f"네이버 종가 조회 실패로 로컬 CSV 데이터를 사용합니다: {'; '.join(naver_errors[:5])}"
    return pd.DataFrame(), name_map, f"종가 데이터를 가져오지 못했습니다: {'; '.join(naver_errors[:5])}"


def load_local_stock_history(symbols: tuple[str, ...], days: int) -> pd.DataFrame:
    master = load_local_master()
    name_map = dict(zip(master["symbol"], master["name"], strict=False)) if not master.empty else {}
    frames = []
    for symbol in symbols:
        path = Path("data/raw") / f"{symbol}.csv"
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, dtype={"date": str})
        except Exception:  # noqa: BLE001
            continue
        if frame.empty or "close" not in frame.columns:
            continue
        frame["date"] = pd.to_datetime(frame["date"].astype(str), format="%Y%m%d", errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values("date").tail(days).copy()
        frame["symbol"] = symbol
        frame["name"] = name_map.get(symbol, symbol)
        for column in ["open", "high", "low", "close", "volume"]:
            if column not in frame.columns:
                frame[column] = pd.NA
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["change_pct"] = frame["close"].pct_change() * 100.0
        frames.append(frame[["date", "symbol", "name", "open", "high", "low", "close", "volume", "change_pct"]])
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_yahoo_chart_history(label: str, ticker: str, period: str) -> pd.DataFrame:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}"
    response = requests.get(
        url,
        params={"range": period, "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    result = (data.get("chart", {}).get("result") or [None])[0]
    if not result:
        return pd.DataFrame()

    timestamps = result.get("timestamp") or []
    quote_data = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote_data.get("close") or []
    if not timestamps or not closes:
        return pd.DataFrame()

    frame = pd.DataFrame({"date": pd.to_datetime(timestamps, unit="s"), "close": closes})
    frame = frame.dropna(subset=["close"]).copy()
    if frame.empty:
        return frame
    frame["date"] = frame["date"].dt.tz_localize(None)
    frame["label"] = label
    return frame[["date", "close", "label"]].sort_values("date").reset_index(drop=True)


@st.cache_data(ttl=60 * 15, show_spinner=False)
def load_yfinance_history(tickers: dict[str, str], period: str, cache_version: str) -> tuple[pd.DataFrame, str | None]:
    frames = []
    errors = []
    for label, ticker in tickers.items():
        try:
            direct = load_yahoo_chart_history(label, ticker, period)
        except Exception as exc:  # noqa: BLE001
            direct = pd.DataFrame()
            direct_error = str(exc)
        else:
            direct_error = ""

        if not direct.empty:
            frames.append(direct)
            continue

        if yf is None:
            errors.append(f"{label}: Yahoo Chart API 실패, yfinance 미설치 ({direct_error})")
            continue

        try:
            data = yf.download(ticker, period=period, auto_adjust=False, progress=False, threads=False)
        except Exception as exc:  # noqa: BLE001
            detail = f"{direct_error}; {exc}" if direct_error else str(exc)
            errors.append(f"{label}: {detail}")
            continue
        if data.empty:
            detail = f" ({direct_error})" if direct_error else ""
            errors.append(f"{label}: 데이터 없음{detail}")
            continue
        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        frame = close.reset_index()
        frame.columns = ["date", "close"]
        frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None)
        frame["label"] = label
        frames.append(frame)

    if not frames:
        return pd.DataFrame(), "; ".join(errors)
    return pd.concat(frames, ignore_index=True), "; ".join(errors) if errors else None


@st.cache_data(ttl=60 * 15, show_spinner=False)
def load_index_history(period: str, cache_version: str) -> tuple[pd.DataFrame, str | None]:
    frames = []
    errors = []
    for label, symbol in NAVER_INDEX_SYMBOLS.items():
        try:
            frame = load_naver_chart(symbol, 180, label=label)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")
            continue
        if frame.empty:
            errors.append(f"{label}: 데이터 없음")
            continue
        frames.append(frame[["date", "close", "label"]])

    global_history, global_warning = load_yfinance_history(GLOBAL_INDEX_TICKERS, period, cache_version)
    if not global_history.empty:
        frames.append(global_history)
    elif global_warning:
        errors.append(global_warning)

    if not frames:
        return pd.DataFrame(), "; ".join(errors)
    return pd.concat(frames, ignore_index=True), "; ".join(errors) if errors else None


def latest_change_table(history: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for label, frame in history.groupby("label", sort=False):
        frame = frame.sort_values("date").copy()
        latest = frame.iloc[-1]
        prev = frame.iloc[-2] if len(frame) > 1 else None
        change = None
        if prev is not None and float(prev["close"]) != 0:
            change = (float(latest["close"]) / float(prev["close"]) - 1.0) * 100.0
        rows.append({"구분": label, "기준일": latest["date"].date(), "종가": float(latest["close"]), "전일대비": change})
    return pd.DataFrame(rows)


def stock_summary_table(history: pd.DataFrame, meta: pd.DataFrame, selected_symbols: list[str]) -> pd.DataFrame:
    meta_map = meta.set_index("symbol").to_dict("index") if not meta.empty else {}
    rows = []
    for symbol in selected_symbols:
        frame = history[history["symbol"] == symbol].sort_values("date")
        if frame.empty:
            continue
        latest = frame.iloc[-1]
        prev = frame.iloc[-2] if len(frame) > 1 else None
        d1 = None if prev is None or float(prev["close"]) == 0 else (float(latest["close"]) / float(prev["close"]) - 1.0) * 100.0
        d5 = period_return(frame, 5)
        d20 = period_return(frame, 20)
        cap = meta_map.get(symbol, {}).get("market_cap")
        rank = meta_map.get(symbol, {}).get("rank")
        market = meta_map.get(symbol, {}).get("market", "")
        rows.append(
            {
                "순위": int(rank) if pd.notna(rank) else None,
                "종목코드": symbol,
                "종목명": str(latest["name"]),
                "시장": market,
                "기준일": latest["date"].date(),
                "종가": float(latest["close"]),
                "1일": d1,
                "5일": d5,
                "20일": d20,
                "시가총액": cap,
            }
        )
    return pd.DataFrame(rows)


def period_return(frame: pd.DataFrame, periods: int) -> float | None:
    frame = frame.sort_values("date")
    if len(frame) <= periods:
        return None
    latest = float(frame.iloc[-1]["close"])
    base = float(frame.iloc[-periods - 1]["close"])
    if base == 0:
        return None
    return (latest / base - 1.0) * 100.0


def line_chart(frame: pd.DataFrame, x: str, y: str, group: str, title: str, normalize: bool = False) -> go.Figure:
    fig = go.Figure()
    if frame.empty:
        fig.update_layout(title=title)
        return fig

    for label, item in frame.groupby(group, sort=False):
        item = item.sort_values(x).copy()
        values = pd.to_numeric(item[y], errors="coerce")
        if normalize and not values.empty and values.iloc[0] != 0:
            values = values / values.iloc[0] * 100.0
        fig.add_trace(go.Scatter(x=item[x], y=values, mode="lines", name=str(label), line={"width": 2}))

    y_title = "Indexed 100" if normalize else "Price"
    fig.update_layout(
        title=title,
        height=420,
        margin={"l": 20, "r": 20, "t": 50, "b": 20},
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
        yaxis_title=y_title,
    )
    return fig


def load_comments(path: Path) -> pd.DataFrame:
    try:
        collection = get_firestore_collection("comments")
    except FirebaseStorageError as error:
        st.warning(str(error))
        collection = None
    if collection is not None:
        try:
            rows = [document.to_dict() or {} for document in collection.stream()]
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Firebase에서 코멘트를 읽지 못했습니다: {exc}")
            rows = []
        frame = pd.DataFrame(rows, columns=COMMENT_COLUMNS)
        for column in COMMENT_COLUMNS:
            if column not in frame.columns:
                frame[column] = ""
        return frame[COMMENT_COLUMNS]

    if not path.exists():
        return pd.DataFrame(columns=COMMENT_COLUMNS)
    return pd.read_csv(path, dtype=str)


def save_comment(path: Path, target_date, session: str, comment: str) -> None:
    current = load_comments(path)
    row = pd.DataFrame(
        [
            {
                "date": str(target_date),
                "session": session,
                "comment": comment.strip(),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        ]
    )
    updated = pd.concat([current, row], ignore_index=True)
    try:
        collection = get_firestore_collection("comments")
        if collection is not None:
            record = prepare_firestore_record(row.iloc[0].to_dict())
            document_id = re.sub(r"[^0-9A-Za-z_-]", "_", f"{record['created_at']}_{record['session']}")
            collection.document(document_id).set(record)
            return
    except FirebaseStorageError as error:
        st.error(str(error))
        st.stop()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Firebase에 코멘트를 저장하지 못했습니다: {exc}")
        st.stop()

    path.parent.mkdir(parents=True, exist_ok=True)
    updated.to_csv(path, index=False, encoding="utf-8-sig")


def load_firebase_portfolio() -> pd.DataFrame | None:
    try:
        collection = get_firestore_collection("portfolio")
    except FirebaseStorageError as error:
        st.warning(str(error))
        return None
    if collection is None:
        return None

    rows = []
    try:
        for document in collection.stream():
            row = document.to_dict() or {}
            row["symbol"] = row.get("symbol") or document.id
            rows.append(row)
    except Exception as exc:  # noqa: BLE001
        raise FirebaseStorageError(f"Firebase에서 포트폴리오를 읽지 못했습니다: {exc}") from exc

    return pd.DataFrame(rows, columns=PORTFOLIO_COLUMNS)


def save_firebase_portfolio(frame: pd.DataFrame) -> bool:
    try:
        collection = get_firestore_collection("portfolio")
    except FirebaseStorageError as error:
        st.error(str(error))
        st.stop()
    if collection is None:
        return False

    try:
        existing_symbols = {document.id for document in collection.stream()}
        incoming_symbols = set(frame["symbol"].astype(str))
        batch = collection._client.batch()
        for symbol in existing_symbols - incoming_symbols:
            batch.delete(collection.document(symbol))
        for row in frame.to_dict("records"):
            symbol = normalize_symbol(row["symbol"])
            batch.set(collection.document(symbol), prepare_firestore_record({**row, "symbol": symbol}))
        batch.commit()
    except Exception as exc:  # noqa: BLE001
        raise FirebaseStorageError(f"Firebase에 포트폴리오를 저장하지 못했습니다: {exc}") from exc
    return True


def load_portfolio(path: Path) -> pd.DataFrame:
    try:
        firebase_frame = load_firebase_portfolio()
    except FirebaseStorageError as error:
        st.warning(str(error))
        firebase_frame = None
    if firebase_frame is not None:
        frame = firebase_frame
    else:
        frame = None

    if frame is None:
        if not path.exists():
            return pd.DataFrame(columns=PORTFOLIO_COLUMNS)
        frame = pd.read_csv(path, dtype={"symbol": str})

    for column in PORTFOLIO_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    frame["avg_buy_price"] = pd.to_numeric(frame["avg_buy_price"], errors="coerce")
    frame["quantity"] = pd.to_numeric(frame["quantity"], errors="coerce")
    return frame[PORTFOLIO_COLUMNS].dropna(subset=["symbol"]).reset_index(drop=True)


def save_portfolio(path: Path, frame: pd.DataFrame) -> None:
    clean = frame.copy()
    for column in PORTFOLIO_COLUMNS:
        if column not in clean.columns:
            clean[column] = ""
    clean["symbol"] = clean["symbol"].astype(str).map(normalize_symbol)
    clean["avg_buy_price"] = pd.to_numeric(clean["avg_buy_price"], errors="coerce")
    clean["quantity"] = pd.to_numeric(clean["quantity"], errors="coerce")
    clean = clean[(clean["symbol"] != "000000") & clean["avg_buy_price"].notna() & clean["quantity"].notna()].copy()
    clean["name"] = clean["name"].fillna("")
    clean["memo"] = clean["memo"].fillna("")
    clean["updated_at"] = datetime.now().isoformat(timespec="seconds")
    clean = clean[PORTFOLIO_COLUMNS]

    try:
        if save_firebase_portfolio(clean):
            return
    except FirebaseStorageError as error:
        st.error(str(error))
        st.stop()

    st.error("Firebase 영구 저장 설정이 없어 포트폴리오를 저장하지 않았습니다.")
    st.stop()


def build_portfolio_view(portfolio: pd.DataFrame, stock_history: pd.DataFrame, name_map: dict[str, str]) -> pd.DataFrame:
    if portfolio.empty:
        return pd.DataFrame()

    latest_rows = []
    if not stock_history.empty:
        latest_rows = (
            stock_history.sort_values("date")
            .groupby("symbol", as_index=False)
            .tail(1)[["symbol", "name", "date", "close"]]
            .to_dict("records")
        )
    latest_map = {str(row["symbol"]).zfill(6): row for row in latest_rows}

    rows = []
    for row in portfolio.itertuples(index=False):
        symbol = normalize_symbol(row.symbol)
        avg_buy_price = float(row.avg_buy_price)
        quantity = float(row.quantity)
        latest = latest_map.get(symbol, {})
        current_price = latest.get("close")
        current_value = float(current_price) * quantity if current_price is not None and pd.notna(current_price) else None
        buy_value = avg_buy_price * quantity
        profit = current_value - buy_value if current_value is not None else None
        profit_pct = ((float(current_price) / avg_buy_price) - 1.0) * 100.0 if current_price is not None and avg_buy_price else None
        rows.append(
            {
                "종목코드": symbol,
                "종목명": row.name or latest.get("name") or name_map.get(symbol, symbol),
                "매입가격": avg_buy_price,
                "보유수": quantity,
                "현재가": current_price,
                "수익률": profit_pct,
                "평가손익": profit,
                "매입금액": buy_value,
                "평가금액": current_value,
                "-10% 가격": float(current_price) * 0.9 if current_price is not None and pd.notna(current_price) else None,
                "기준일": latest.get("date"),
                "메모": row.memo,
            }
        )
    return pd.DataFrame(rows)


def build_portfolio_trend(portfolio: pd.DataFrame, stock_history: pd.DataFrame, name_map: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    if portfolio.empty or stock_history.empty:
        return pd.DataFrame(), pd.DataFrame()

    rows = []
    for holding in portfolio.to_dict("records"):
        symbol = normalize_symbol(holding.get("symbol", ""))
        avg_buy_price = float(holding.get("avg_buy_price", 0))
        quantity = float(holding.get("quantity", 0))
        buy_value = avg_buy_price * quantity
        if buy_value <= 0:
            continue

        history = stock_history[stock_history["symbol"] == symbol].sort_values("date").copy()
        if history.empty:
            continue

        holding_name = "" if pd.isna(holding.get("name", "")) else str(holding.get("name", "")).strip()
        history_name = str(history["name"].dropna().iloc[-1]) if history["name"].notna().any() else ""
        name = holding_name or history_name or name_map.get(symbol, symbol)
        history["종목코드"] = symbol
        history["종목명"] = name
        history["매입금액"] = buy_value
        history["평가금액"] = pd.to_numeric(history["close"], errors="coerce") * quantity
        history["평가손익"] = history["평가금액"] - buy_value
        history["수익률"] = (history["평가손익"] / buy_value) * 100.0
        rows.append(history[["date", "종목코드", "종목명", "매입금액", "평가금액", "평가손익", "수익률"]])

    if not rows:
        return pd.DataFrame(), pd.DataFrame()

    per_stock = pd.concat(rows, ignore_index=True).dropna(subset=["평가금액", "수익률"])
    total = (
        per_stock.groupby("date", as_index=False)[["매입금액", "평가금액"]]
        .sum()
        .sort_values("date")
        .reset_index(drop=True)
    )
    total["평가손익"] = total["평가금액"] - total["매입금액"]
    total["수익률"] = (total["평가손익"] / total["매입금액"]) * 100.0
    total["구분"] = "포트폴리오 합계"
    return total, per_stock


def portfolio_trend_chart(frame: pd.DataFrame, group: str, title: str) -> go.Figure:
    fig = go.Figure()
    if frame.empty:
        return fig

    for label, group_frame in frame.groupby(group, sort=False):
        group_frame = group_frame.sort_values("date")
        fig.add_trace(
            go.Scatter(
                x=group_frame["date"],
                y=group_frame["수익률"],
                mode="lines+markers",
                name=str(label),
                hovertemplate="%{x|%Y-%m-%d}<br>수익률 %{y:+.2f}%<extra>%{fullData.name}</extra>",
            )
        )

    fig.add_hline(y=0, line_color="#9ca3af", line_dash="dot")
    fig.update_layout(
        title=title,
        template="plotly_white",
        height=360,
        margin={"l": 20, "r": 20, "t": 50, "b": 20},
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
        yaxis_title="수익률 (%)",
    )
    return fig


def style_portfolio_display(display: pd.DataFrame, source: pd.DataFrame) -> pd.io.formats.style.Styler:
    def cell_style(row: pd.Series) -> list[str]:
        styles = [""] * len(row)
        original = source.loc[row.name]
        for column in ["수익률", "평가손익"]:
            value = original.get(column)
            if value is None or pd.isna(value) or column not in row.index:
                continue
            color = "#dc2626" if float(value) > 0 else ("#2563eb" if float(value) < 0 else "#6b7280")
            styles[row.index.get_loc(column)] = f"color: {color}; font-weight: 700;"
        return styles

    return display.style.apply(cell_style, axis=1)


def inject_metric_delta_color_overrides() -> None:
    st.markdown(
        """
        <style>
        div[data-testid="stMetricDelta"] *[style*="rgb(9, 171, 59)"] {
            color: #2563eb !important;
            fill: #2563eb !important;
        }

        ::-webkit-scrollbar {
            width: 16px;
            height: 16px;
        }

        ::-webkit-scrollbar-track {
            background: #e5e7eb;
            border-radius: 8px;
        }

        ::-webkit-scrollbar-thumb {
            background: #6b7280;
            border-radius: 8px;
            border: 3px solid #e5e7eb;
        }

        ::-webkit-scrollbar-thumb:hover {
            background: #374151;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_portfolio_panel(portfolio_path: Path, stock_history: pd.DataFrame, name_map: dict[str, str]) -> None:
    st.subheader("내 보유 종목")
    firebase_storage_enabled = False
    try:
        firebase_storage_enabled = get_firestore_client() is not None
    except FirebaseStorageError as error:
        st.error(str(error))

    if firebase_storage_enabled:
        st.caption("저장 위치: Firebase Firestore")
    else:
        st.error("Firebase 영구 저장 설정이 없습니다. 포트폴리오를 저장할 수 없습니다.")
        st.caption("Streamlit secrets에 Firebase 서비스 계정 정보를 설정한 뒤 다시 저장하세요.")

    portfolio = load_portfolio(portfolio_path)

    with st.form("portfolio_add_form", clear_on_submit=True):
        cols = st.columns([1, 1.3, 1, 1, 2])
        symbol = cols[0].text_input("종목코드", placeholder="005930")
        name = cols[1].text_input("종목명", placeholder="삼성전자")
        avg_buy_price = cols[2].number_input("매입가격", min_value=0.0, step=100.0)
        quantity = cols[3].number_input("보유수", min_value=0.0, step=1.0)
        memo = cols[4].text_input("메모", placeholder="계좌/전략 등")
        submitted = st.form_submit_button("보유 종목 저장", type="primary", disabled=not firebase_storage_enabled)
        if submitted:
            normalized = normalize_symbol(symbol)
            if normalized == "000000" or avg_buy_price <= 0 or quantity <= 0:
                st.warning("종목코드, 매입가격, 보유수를 확인하세요.")
            else:
                new_row = pd.DataFrame(
                    [
                        {
                            "symbol": normalized,
                            "name": name.strip() or name_map.get(normalized, normalized),
                            "avg_buy_price": avg_buy_price,
                            "quantity": quantity,
                            "memo": memo.strip(),
                            "updated_at": datetime.now().isoformat(timespec="seconds"),
                        }
                    ]
                )
                updated = pd.concat([portfolio[portfolio["symbol"] != normalized], new_row], ignore_index=True)
                save_portfolio(portfolio_path, updated)
                st.success("보유 종목을 저장했습니다.")
                st.rerun()

    if portfolio.empty:
        st.caption("저장된 보유 종목이 없습니다.")
        return

    view = build_portfolio_view(portfolio, stock_history, name_map)
    if view.empty:
        st.caption("보유 종목을 불러오지 못했습니다.")
        return

    total_buy = pd.to_numeric(view["매입금액"], errors="coerce").sum()
    total_value = pd.to_numeric(view["평가금액"], errors="coerce").sum()
    total_profit = total_value - total_buy if total_buy else None
    total_profit_pct = (total_profit / total_buy * 100.0) if total_buy else None
    cols = st.columns(4)
    cols[0].metric("총 매입금액", format_krw(total_buy))
    cols[1].metric("총 평가금액", format_krw(total_value))
    cols[2].metric("총 평가손익", format_krw(total_profit), format_pct(total_profit_pct), delta_color="inverse")
    cols[3].metric("보유 종목 수", f"{len(view)}개")

    display = view.copy()
    for column in ["매입가격", "현재가", "평가손익", "매입금액", "평가금액", "-10% 가격"]:
        display[column] = display[column].map(format_krw)
    display["보유수"] = display["보유수"].map(format_number)
    display["수익률"] = display["수익률"].map(format_pct)
    if "기준일" in display.columns:
        display["기준일"] = pd.to_datetime(display["기준일"], errors="coerce").dt.date
    st.caption("표 안에서 정렬하고 가로/세로로 스크롤할 수 있습니다.")
    st.dataframe(style_portfolio_display(display, view), width="stretch", hide_index=True, height=360)

    total_trend, stock_trend = build_portfolio_trend(portfolio, stock_history, name_map)
    if not total_trend.empty and not stock_trend.empty:
        st.subheader("수익률 추이")
        trend_cols = st.columns([1, 1])
        with trend_cols[0]:
            st.plotly_chart(portfolio_trend_chart(total_trend, "구분", "포트폴리오 합계 수익률"), width="stretch")
        with trend_cols[1]:
            st.plotly_chart(portfolio_trend_chart(stock_trend, "종목명", "종목별 수익률"), width="stretch")
    else:
        st.caption("수익률 추이를 계산할 종가 데이터가 없습니다.")

    delete_labels = dict(zip(view["종목코드"], view["종목명"], strict=False))
    delete_symbol = st.selectbox(
        "삭제할 보유 종목",
        options=[""] + view["종목코드"].tolist(),
        format_func=lambda value: "선택 안 함" if not value else f"{delete_labels.get(value, value)} ({value})",
    )
    if st.button("선택 종목 삭제") and delete_symbol:
        updated = portfolio[portfolio["symbol"] != delete_symbol].copy()
        save_portfolio(portfolio_path, updated)
        st.success("삭제했습니다.")
        st.rerun()


def render_market_metric_card(label: str, value: str, delta: float | None, date_value) -> None:
    if delta is None or pd.isna(delta):
        delta_text = "-"
        delta_color = "#6b7280"
    else:
        delta_text = format_pct(delta)
        delta_color = "#dc2626" if float(delta) > 0 else ("#2563eb" if float(delta) < 0 else "#6b7280")
    date_text = "-" if date_value is None or pd.isna(date_value) else str(date_value)
    st.markdown(
        f"""
        <div style="border:1px solid #e5e7eb; border-radius:8px; padding:12px 14px; min-height:118px;">
            <div style="font-size:13px; color:#6b7280; font-weight:700;">{label}</div>
            <div style="font-size:25px; color:#111827; font-weight:800; margin-top:8px;">{value}</div>
            <div style="font-size:14px; color:{delta_color}; font-weight:800; margin-top:6px;">전일대비 {delta_text}</div>
            <div style="font-size:12px; color:#6b7280; margin-top:8px;">기준일: {date_text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_comment_panel(comment_path: Path) -> None:
    st.subheader("아침/저녁 코멘트")
    comments = load_comments(comment_path)

    with st.form("comment_form", clear_on_submit=True):
        cols = st.columns([1, 1, 4])
        target_date = cols[0].date_input("날짜", value=datetime.now().date())
        session = cols[1].segmented_control("구분", options=["아침", "저녁"], default="아침")
        comment = st.text_area("주식변동 코멘트", height=110, placeholder="시장 흐름, 주요 종목 변동, 내일 확인할 포인트를 기록")
        submitted = st.form_submit_button("저장", type="primary")
        if submitted:
            if comment.strip():
                save_comment(comment_path, target_date, str(session), comment)
                st.success("코멘트를 저장했습니다.")
                st.rerun()
            else:
                st.warning("코멘트를 입력하세요.")

    if comments.empty:
        st.caption("저장된 코멘트가 없습니다.")
        return

    view = comments.sort_values("created_at", ascending=False).head(30).rename(
        columns={"date": "날짜", "session": "구분", "comment": "코멘트", "created_at": "작성시각"}
    )
    st.caption("표 안에서 정렬하고 스크롤할 수 있습니다.")
    st.dataframe(view, width="stretch", hide_index=True, height=320)


def main() -> None:
    require_access_code()
    if st.session_state.get("app_cache_version") != APP_VERSION:
        st.cache_data.clear()
        st.session_state["app_cache_version"] = APP_VERSION
    inject_metric_delta_color_overrides()

    config = load_config()
    dashboard_cfg = config.get("stock_dashboard", {})
    paths = config.get("paths", {})
    comment_path = Path(paths.get("comment_file", "data/comments/market_comments.csv"))
    portfolio_path = Path(paths.get("portfolio_file", "data/portfolio/holdings.csv"))
    portfolio_df = load_portfolio(portfolio_path)

    st.title("Stock Market Dashboard")

    with st.sidebar:
        st.header("설정")
        st.caption(f"버전: {APP_VERSION}")
        limit = st.slider("시가총액 상위 종목 수", 10, 80, int(dashboard_cfg.get("market_cap_limit", 40)), step=5)
        history_days = st.slider("종가 조회 기간", 30, 260, int(dashboard_cfg.get("history_days", 120)), step=10)
        normalize = st.toggle("그래프를 100 기준으로 보기", value=True)
        default_watchlist = "\n".join(dashboard_cfg.get("default_watchlist", []))
        raw_watchlist = st.text_area("내 관심종목", value=default_watchlist, height=150)
        refresh = st.button("데이터 새로고침")
        if refresh:
            st.cache_data.clear()
            st.rerun()

    with st.spinner("시장 데이터를 불러오는 중입니다."):
        top_meta, cap_date, cap_error = load_top_market_cap(limit, APP_VERSION)

    custom_symbols = parse_symbol_list(raw_watchlist)
    portfolio_symbols = portfolio_df["symbol"].astype(str).str.zfill(6).tolist() if not portfolio_df.empty else []
    top_symbols = top_meta["symbol"].astype(str).tolist() if not top_meta.empty else []
    selected_symbols = list(dict.fromkeys(top_symbols + custom_symbols + portfolio_symbols))

    if cap_error:
        st.warning(cap_error)
    if not selected_symbols:
        st.error("조회할 종목이 없습니다. 관심종목을 입력하거나 네이버 금융 데이터 연결을 확인하세요.")
        st.stop()

    with st.spinner("종목별 일별 종가를 불러오는 중입니다."):
        stock_history, symbol_names, stock_warning = load_stock_history(tuple(selected_symbols), history_days, APP_VERSION)
    if stock_warning:
        st.warning(stock_warning)

    with st.spinner("지수와 금리 데이터를 불러오는 중입니다."):
        index_history, index_warning = load_index_history("6mo", APP_VERSION)
        treasury_history, treasury_warning = load_yfinance_history({"미국 10년물 금리": TREASURY_TICKER}, "6mo", APP_VERSION)
    if index_warning:
        st.warning(index_warning)
    if treasury_warning:
        st.warning(treasury_warning)

    market_metric_rows = {}
    if not index_history.empty:
        index_latest = latest_change_table(index_history)
        market_metric_rows.update({str(row["구분"]): row for row in index_latest.to_dict("records")})
    if not treasury_history.empty:
        latest = latest_change_table(treasury_history).iloc[0].to_dict()
        market_metric_rows["미국 국채 10년 금리"] = latest

    metric_cols = st.columns(4)
    for col, label in zip(metric_cols, ["KOSPI", "KOSDAQ", "NASDAQ", "미국 국채 10년 금리"], strict=False):
        row = market_metric_rows.get(label)
        with col:
            if row:
                value = f"{float(row['종가']):.2f}%" if label == "미국 국채 10년 금리" else f"{float(row['종가']):,.2f}"
                render_market_metric_card(label, value, row.get("전일대비"), row.get("기준일"))
            else:
                render_market_metric_card(label, "-", None, None)

    tabs = st.tabs(["종목 종가", "포트폴리오", "시장 지표", "코멘트"])

    with tabs[0]:
        if cap_date:
            st.caption(f"시가총액 기준일: {cap_date}")
        if stock_history.empty:
            st.error("종목 종가 데이터가 없습니다.")
        else:
            summary = stock_summary_table(stock_history, top_meta, selected_symbols)
            chart_options = summary[["종목코드", "종목명"]].copy()
            chart_options["label"] = chart_options["종목코드"] + " | " + chart_options["종목명"]
            option_labels = chart_options["label"].tolist()
            label_to_symbol = dict(zip(chart_options["label"], chart_options["종목코드"], strict=False))
            symbol_to_label = dict(zip(chart_options["종목코드"], chart_options["label"], strict=False))
            default_labels = [symbol_to_label[symbol] for symbol in portfolio_symbols if symbol in symbol_to_label]
            if not default_labels:
                default_labels = option_labels[: min(5, len(option_labels))]
            selected_chart_labels = st.multiselect(
                "그래프에 표시할 종목",
                options=option_labels,
                default=default_labels,
                key=f"chart_symbols_{'-'.join(portfolio_symbols)}",
                placeholder="종목을 선택하세요",
            )
            chart_symbols = {label_to_symbol[label] for label in selected_chart_labels}
            chart_history = stock_history[stock_history["symbol"].isin(chart_symbols)].copy()
            if chart_history.empty:
                st.info("그래프에 표시할 종목을 선택하세요.")
            else:
                st.plotly_chart(
                    line_chart(chart_history, "date", "close", "name", "선택 종목 일별 종가", normalize=normalize),
                    width="stretch",
                )
            table = summary.copy()
            for col in ["종가"]:
                table[col] = table[col].map(format_krw)
            for col in ["1일", "5일", "20일"]:
                table[col] = table[col].map(format_pct)
            table["시가총액"] = table["시가총액"].map(format_market_cap)
            table = table.sort_values(["순위", "종목코드"], na_position="last").reset_index(drop=True)
            st.caption("표 안에서 정렬하고 가로/세로로 스크롤할 수 있습니다.")
            st.dataframe(table, width="stretch", hide_index=True, height=520)

    with tabs[1]:
        render_portfolio_panel(portfolio_path, stock_history, symbol_names)

    with tabs[2]:
        cols = st.columns([1, 1])
        with cols[0]:
            if index_history.empty:
                st.warning("지수 데이터가 없습니다.")
            else:
                st.plotly_chart(
                    line_chart(index_history, "date", "close", "label", "코스피/코스닥/나스닥", normalize=True),
                    width="stretch",
                )
                index_table = latest_change_table(index_history)
                index_table["종가"] = index_table["종가"].map(lambda value: f"{value:,.2f}")
                index_table["전일대비"] = index_table["전일대비"].map(format_pct)
                st.caption("표 안에서 정렬할 수 있습니다.")
                st.dataframe(index_table, width="stretch", hide_index=True)
        with cols[1]:
            if treasury_history.empty:
                st.warning("미국 10년물 금리 데이터가 없습니다.")
            else:
                st.plotly_chart(
                    line_chart(treasury_history, "date", "close", "label", "미국 국채 10년 금리", normalize=False),
                    width="stretch",
                )
                rate_table = latest_change_table(treasury_history)
                rate_table["종가"] = rate_table["종가"].map(lambda value: f"{value:.2f}%")
                rate_table["전일대비"] = rate_table["전일대비"].map(format_pct)
                st.caption("표 안에서 정렬할 수 있습니다.")
                st.dataframe(rate_table, width="stretch", hide_index=True)

    with tabs[3]:
        render_comment_panel(comment_path)


if __name__ == "__main__":
    main()
