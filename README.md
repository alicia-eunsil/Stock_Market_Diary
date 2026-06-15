# Stock Market Dashboard

Streamlit 기반 주식시장 대시보드입니다.

## 주요 기능

- 한국 주식시장 시가총액 상위 종목 조회
- 사용자가 지정한 관심종목 일별 종가 그래프와 표
- 코스피, 코스닥, 나스닥 등락 확인
- 미국 국채 10년 금리 변화 확인
- 아침/저녁 주식변동 코멘트 저장

## 실행

```bash
python3 -m pip install -r requirements.txt
streamlit run app.py
```

접속코드를 쓰려면 실행 전에 `ACCESS_CODE`를 설정합니다.

```bash
export ACCESS_CODE='YOUR_CODE'
streamlit run app.py
```

## 설정

기본 관심종목과 조회 기간은 `config.json`에서 바꿉니다.

- `stock_dashboard.market_cap_limit`: 시가총액 상위 표시 개수
- `stock_dashboard.default_watchlist`: 기본 관심종목 코드 목록
- `stock_dashboard.history_days`: 종가 그래프 조회 기간
- `paths.comment_file`: 코멘트 저장 CSV 경로

## 데이터

- 국내 시가총액: 네이버 금융 시가총액 데이터를 사용합니다.
- 국내 종가: 네이버 금융 일봉 차트 데이터를 사용하고, 실패하면 기존 `data/raw/*.csv` 로컬 데이터를 fallback으로 사용합니다.
- 지수/금리: Yahoo Finance(`yfinance`)를 사용합니다.
- 코멘트: `data/comments/market_comments.csv`에 저장합니다.

Streamlit Community Cloud에서는 로컬 CSV 저장이 영구 보존되지 않을 수 있습니다. 장기 기록이 필요하면 Google Sheets, Supabase, Firebase 같은 외부 저장소로 옮기는 것이 좋습니다.
