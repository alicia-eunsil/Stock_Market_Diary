# Stock Market Dashboard

Streamlit 기반 주식시장 대시보드입니다.

## 주요 기능

- 한국 주식시장 시가총액 상위 종목 조회
- 사용자가 지정한 관심종목 일별 종가 그래프와 표
- 코스피, 코스닥, 나스닥 등락 확인
- 미국 국채 10년 금리 변화 확인
- 보유 종목의 매입가격, 보유수, 현재가, 수익률, -10% 가격 확인
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
- `paths.portfolio_file`: 보유 종목 저장 CSV 경로

## 데이터

- 국내 시가총액: 네이버 금융 시가총액 데이터를 사용합니다.
- 국내 종가: 네이버 금융 일봉 차트 데이터를 사용하고, 실패하면 기존 `data/raw/*.csv` 로컬 데이터를 fallback으로 사용합니다.
- 지수/금리: Yahoo Finance(`yfinance`)를 사용합니다.
- 보유 종목: Firebase Firestore에 저장합니다.
- 코멘트: Firebase Firestore에 저장합니다.

Streamlit Community Cloud에서는 로컬 CSV 저장이 영구 보존되지 않을 수 있습니다. 보유 종목과 코멘트를 유지하려면 Firebase Firestore 저장을 켭니다.

Streamlit secrets에 Firebase 서비스 계정 정보를 추가하면 보유 종목과 코멘트가 Firestore에 저장됩니다.

```toml
[firebase]
type = "service_account"
project_id = "YOUR_PROJECT_ID"
private_key_id = "YOUR_PRIVATE_KEY_ID"
private_key = "-----BEGIN PRIVATE KEY-----\nYOUR_PRIVATE_KEY\n-----END PRIVATE KEY-----\n"
client_email = "firebase-adminsdk-...@YOUR_PROJECT_ID.iam.gserviceaccount.com"
client_id = "YOUR_CLIENT_ID"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "YOUR_CLIENT_CERT_URL"
universe_domain = "googleapis.com"
```

선택적으로 `FIREBASE_COLLECTION_PREFIX = "stock_diary"`를 설정하면 Firestore 저장 위치 prefix를 바꿀 수 있습니다.
