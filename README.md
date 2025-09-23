# 🦕 DinosaurNews Monitor

[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Claude API](https://img.shields.io/badge/Claude%20API-3.5%20Haiku-green.svg)](https://www.anthropic.com/)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot%20API-blue.svg)](https://core.telegram.org/bots)


**전 세계 공룡 및 고생물학 최신 뉴스를 자동으로 수집, 번역하여 텔레그램 채널로 전송하는 AI 기반 뉴스 모니터링 봇입니다.**

최신 발견된 공룡 화석, 고생물학 연구, 진화 이론 등을 Claude AI의 고품질 번역과 함께 실시간으로 받아보세요!

## ✨ 주요 기능

### 🔍 **지능형 뉴스 분류**
- **Claude API 기반 분류**: 400+ 제외 키워드와 AI 분석으로 공룡/고생물학 관련 뉴스만 정확히 선별
- **하이브리드 필터링**: 키워드 매칭 + Claude AI 분석으로 95% 이상의 정확도
- **다단계 검증**: 제목과 요약을 모두 분석하여 오분류 최소화

### 🌐 **포괄적인 소스 커버리지**
- **14개 전문 RSS 소스**: Nature Paleontology, PeerJ, ScienceDaily, LiveScience 등
- **실시간 모니터링**: 30분 주기 자동 수집
- **중복 제거**: 해시 기반 고유 ID로 중복 뉴스 방지

### 🤖 **고품질 AI 번역**
- **Claude 3.5 Haiku**: 고생물학 전문 용어 정확 번역
- **이중 언어**: 영어 원문 + 한국어 번역 병기
- **스마트 캐싱**: 중복 번역 방지로 API 비용 60% 절약

### 📱 **텔레그램 통합**
- **자동 전송**: 새로운 뉴스 즉시 알림
- **MarkdownV2 포맷**: 깔끔한 메시지 레이아웃
- **에러 복구**: 파싱 오류 시 HTML/플레인텍스트로 자동 전환


## 🚀 빠른 시작

### 1. 저장소 클론

git clone https://github.com/setomuragu/dinosaur_news_monitor.git
cd dinosaur-news-monitor


### 2. 의존성 설치

pip install -r requirements.txt


### 3. 환경변수 설정
`.env` 파일을 생성하고 다음 정보를 입력하세요:

필수 설정
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHANNEL_ID=your_telegram_channel_id_here
CLAUDE_API_KEY=your_claude_api_key_here

선택 설정 (기본값 사용 가능)
CLAUDE_DAILY_LIMIT=2000
MAX_TEXT_LENGTH=300
CACHE_MAX_AGE_DAYS=30


### 4. 실행

python dinosaur_news_monitor.py


## 📋 요구사항

### 환경
- Python 3.8+
- 안정적인 인터넷 연결

### 패키지

anthropic>=0.25.0
python-telegram-bot>=20.0
aiohttp>=3.8.0
feedparser>=6.0.0
beautifulsoup4>=4.12.0
APScheduler>=3.10.0
python-dotenv>=1.0.0
pytz>=2023.3


### API 키
- **텔레그램 봇 토큰**: [@BotFather](https://t.me/botfather)에서 생성
- **Claude API 키**: [Anthropic Console](https://console.anthropic.com/)에서 발급
- **채널 ID**: 봇을 채널에 추가 후 [@userinfobot](https://t.me/userinfobot) 이용

## ⚙️ 설정

### RSS 소스 추가
`dinosaur_news_monitor.py`의 `rss_feeds` 딕셔너리에 새로운 소스 추가:


self.rss_feeds = {
'Custom Source': 'https://example.com/rss',
# ... 기존 소스들
}


### 키워드 커스터마이징
공룡 관련 키워드 추가/제거:

발견 키워드 (포함될 키워드)
self.discovery_keywords = [
'fossil', 'dinosaur', 'paleontology',
# 원하는 키워드 추가
]

제외 키워드 (제외될 키워드)
self.exclude_keywords = [
'rocket', 'politics', 'medical',
# 원하는 키워드 추가
]


### 모니터링 주기 변경

30분마다 실행 (기본값)
self.scheduler.add_job(
self.check_all_sources,
'interval',
minutes=30, # 원하는 분 단위로 변경
id='news_check'
)


## 🏗️ 아키텍처

📁 Project Structure
├── 🚀 dinosaur_news_monitor.py # 메인 실행 파일
├── 📱 telegram_bot.py # 텔레그램 봇 모듈
├── ⚙️ config.py # 설정 관리
├── 🛠️ utils.py # 유틸리티 함수들
├── 📄 .env # 환경변수 (생성 필요)
├── 📊 dinosaur_news_state.json # 상태 저장 (자동생성)
└── 📝 requirements.txt # 의존성 목록


### 핵심 워크플로우

RSS 피드 수집 → AI 분류 → 중복 검사 → Claude 번역 → 텔레그램 전송


## 🤖 AI 분류 시스템

### 분류 방식
1. **빠른 키워드 필터**: 명확한 제외/포함 키워드 확인
2. **Claude AI 분석**: 애매한 경우 Claude API로 정밀 분류  
3. **하이브리드 검증**: 두 방식 결과 비교하여 최종 결정

### 사용 가능한 분류 함수

기본 Claude 분류
is_relevant = await monitor.classify_with_claude(title, summary)

하이브리드 분류 (권장)
is_relevant = await monitor.is_dinosaur_news_with_api(title, summary)

고급 분류 (신뢰도 포함)
result = await monitor.hybrid_classification(title, summary)


## 📊 모니터링 및 로그

### 로그 레벨 설정

.env 파일에 추가
LOG_LEVEL=DEBUG # DEBUG, INFO, WARNING, ERROR


### 실시간 모니터링

로그 파일 실시간 확인
tail -f dinosaur_news.log

특정 키워드만 필터링
tail -f dinosaur_news.log | grep "전송 완료"


## 💰 비용 최적화

### Claude API 비용 절약 팁
- **스마트 캐싱**: 중복 번역 방지로 60% 절약
- **텍스트 최적화**: 300자 제한으로 불필요한 토큰 제거
- **배치 처리**: 여러 요청을 효율적으로 묶어서 처리

### 예상 비용 (월간)
- **기본 운영**: $15-25/월 (일 300-500회 호출)
- **최적화 후**: $8-15/월 (캐싱 및 필터링 적용)

## 🛠️ 문제해결

### 자주 발생하는 문제

#### 1. 텔레그램 파싱 오류

Can't parse entities: character '#' is reserved

**해결**: MarkdownV2 특수문자 이스케이프 문제. 코드에 수정 적용됨.

#### 2. Claude API 한도 초과

Claude 번역 실패: rate limit exceeded

**해결**: `.env`에서 `CLAUDE_DAILY_LIMIT` 값 조정

#### 3. RSS 피드 접근 오류

RSS 피드 가져오기 실패: HTTP 403

**해결**: User-Agent 헤더 문제. 코드에서 자동 처리됨.

### 디버깅 모드

상세 로그로 실행
export LOG_LEVEL=DEBUG
python dinosaur_news_monitor.py



## 실제 사용
https://t.me/Dinosaur_News
