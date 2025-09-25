"""
공룡 뉴스 실시간 알림 서비스 v5.0 - 키워드 우선 분류 개선

주요 개선사항:
- 키워드 우선 분류 -> 애매한 경우만 API 호출
- API 비용 절약 (예상 70-80% 절약)
- 3단계 신뢰도 기반 분류
- 실시간 절약 통계 
"""

import asyncio
import feedparser
import json
import hashlib
import pickle
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import aiohttp
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.error import TelegramError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz
import anthropic

# 환경 변수 로드
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("python-dotenv가 설치되지 않았습니다.")

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

class ImprovedDinosaurClassifier:
    """키워드 우선 + API 보완 분류기"""

    def __init__(self):
        # Claude 클라이언트 초기화
        self.claude_client = None
        claude_api_key = os.getenv('ANTHROPIC_API_KEY') or os.getenv('CLAUDE_API_KEY')
        if claude_api_key:
            try:
                self.claude_client = anthropic.Anthropic(api_key=claude_api_key)
                logger.info("Claude API 클라이언트 초기화 완료")
            except Exception as e:
                logger.warning(f"Claude 초기화 실패: {e}")

        # 확실한 포함 키워드 (높은 신뢰도)
        self.strong_include_keywords = [
            'dinosaur', 'dinosaurs', 'fossil', 'fossils', 'paleontology', 'paleontologist',
            'cretaceous', 'jurassic', 'triassic', 'mesozoic', 'extinct reptile',
            'tyrannosaur', 'sauropod', 'theropod', 'ceratopsian', 'hadrosau',
            'Tyrannosaurus', 'Triceratops', 'Stegosaurus', 'Velociraptor',
            'pterosaur', 'archaeopteryx', 'prehistoric reptile', 'paleocene',
            '공룡', '화석', '고생물학', '백악기', '쥐라기', '중생대',
            'brachiosaurus', 'allosaurus', 'spinosaurus', 'iguanodon', 'RFK'
        ]

        # 확실한 제외 키워드 (높은 신뢰도)  
        self.strong_exclude_keywords = [
            'cancer treatment', 'human disease', 'COVID', 'vaccine', 'politics',
            'rocket launch', 'space mission', 'satellite', 'mars rover',
            'smartphone', 'AI technology', 'clinical trial', 'patient study',
            'stock market', 'economic', 'business', 'cryptocurrency',
            'medical diagnosis', 'pharmaceutical', 'hospital', 'therapy',
            'human skull', 'human remains', 'modern medicine', 'brain cancer',
            'black hole', 'neutrino', 'quantum', 'nanotechnology'
        ]

        # 중간 강도 키워드들
        self.medium_include_keywords = [
            'ancient', 'prehistoric', 'extinct', 'evolution', 'specimen',
            'excavation', 'discovery', 'bone', 'skeleton', 'species',
            'vertebrate', 'reptile', 'cretaceous period', 'jurassic period'
        ]

        self.medium_exclude_keywords = [
            'modern animal', 'human archaeology', 'medical research', 
            'technology', 'engineering', 'astronomy', 'physics',
            'plant biology', 'marine biology', 'cell biology'
        ]

        # Claude 분류용 프롬프트
        self.classification_prompt = """
You are a dinosaur and paleontology expert classifier.
Judge whether the following text is related to dinosaurs, fossils, and paleontology:

- RELEVANT: "RELEVANT"
- IRRELEVANT: "IRRELEVANT"

Relevant: Dinosaurs, fossils, paleontology, Mesozoic Era, extinction, evolution, etc
Not Relevant: Modern medicine, space exploration, politics, agriculture, technology, etc

Just one word: RELEVANT or IRRELEVANT
"""

    def calculate_keyword_confidence(self, title: str, summary: str) -> Dict[str, float]:
        """키워드 기반 신뢰도 계산"""
        combined_text = (title + ' ' + summary).lower()

        scores = {
            'strong_include': 0,
            'strong_exclude': 0, 
            'medium_include': 0,
            'medium_exclude': 0
        }

        # 강력한 포함 키워드 체크
        for keyword in self.strong_include_keywords:
            if keyword.lower() in combined_text:
                scores['strong_include'] += 3

        # 강력한 제외 키워드 체크  
        for keyword in self.strong_exclude_keywords:
            if keyword.lower() in combined_text:
                scores['strong_exclude'] += 3

        # 중간 키워드들
        for keyword in self.medium_include_keywords:
            if keyword.lower() in combined_text:
                scores['medium_include'] += 1

        for keyword in self.medium_exclude_keywords:
            if keyword.lower() in combined_text:
                scores['medium_exclude'] += 1

        # 최종 점수 계산
        include_score = scores['strong_include'] + scores['medium_include'] * 0.5
        exclude_score = scores['strong_exclude'] + scores['medium_exclude'] * 0.5

        final_score = include_score - exclude_score

        # 신뢰도 계산 (0.0 ~ 1.0)
        if final_score >= 3:
            confidence = 0.95  # 매우 확실함
        elif final_score >= 2:
            confidence = 0.85  # 확실함  
        elif final_score >= 1:
            confidence = 0.75  # 어느정도 확실
        elif final_score <= -3:
            confidence = 0.95  # 매우 확실히 제외
        elif final_score <= -2:
            confidence = 0.85  # 확실히 제외
        elif final_score <= -1:
            confidence = 0.75  # 어느정도 확실히 제외
        else:
            confidence = 0.3   # 애매함 - API 필요

        return {
            'score': final_score,
            'confidence': confidence,
            'include_score': include_score,
            'exclude_score': exclude_score,
            'decision': final_score > 0
        }

    async def classify_with_keywords_first(self, title: str, summary: str) -> Dict[str, any]:
        """키워드 우선 + API 보완 분류"""

        # 1단계: 키워드 기반 분류
        keyword_result = self.calculate_keyword_confidence(title, summary)

        # 2단계: 신뢰도에 따른 결정
        if keyword_result['confidence'] >= 0.8:
            # 키워드만으로 확실한 분류 가능 - API 생략
            logger.info(f"🔍 키워드 분류 확정 (신뢰도: {keyword_result['confidence']:.2f})")
            return {
                'decision': keyword_result['decision'],
                'confidence': keyword_result['confidence'],
                'method': 'keyword_only',
                'api_used': False,
                'keyword_score': keyword_result['score']
            }

        elif keyword_result['confidence'] <= 0.4:
            # 애매한 경우 - API 호출
            logger.info(f"❓ 키워드 분류 애매함 (신뢰도: {keyword_result['confidence']:.2f}) - API 호출")

            api_result = await self.classify_with_claude(title, summary)

            # API와 키워드 결과 종합
            if api_result is not None:
                final_confidence = max(keyword_result['confidence'], 0.7)
                return {
                    'decision': api_result,
                    'confidence': final_confidence,
                    'method': 'hybrid_api_used',
                    'api_used': True,
                    'keyword_score': keyword_result['score'],
                    'api_decision': api_result
                }
            else:
                # API 실패시 키워드 결과 사용
                return {
                    'decision': keyword_result['decision'],
                    'confidence': keyword_result['confidence'],
                    'method': 'keyword_fallback',
                    'api_used': False,
                    'keyword_score': keyword_result['score']
                }
        else:
            # 중간 신뢰도 - 보수적 접근 (거부 우선)
            conservative_decision = keyword_result['score'] > 1  # 더 엄격한 기준
            return {
                'decision': conservative_decision,
                'confidence': 0.6,
                'method': 'conservative_keyword',
                'api_used': False,
                'keyword_score': keyword_result['score']
            }

    async def classify_with_claude(self, title: str, summary: str) -> Optional[bool]:
        """Claude API 분류"""
        if not self.claude_client:
            return None

        try:
            combined_text = f"제목: {title}\n내용: {summary}"
            response = await asyncio.to_thread(
                self.claude_client.messages.create,
                model="claude-sonnet-4-20250514",
                max_tokens=10,
                temperature=0.1,
                system=self.classification_prompt,
                messages=[{"role": "user", "content": combined_text}]
            )

            classification = response.content[0].text.strip().upper()
            if classification == "RELEVANT":
                return True
            elif classification == "IRRELEVANT":
                return False
            else:
                logger.warning(f"⚠️ Claude 분류 모호: {classification}")
                return None

        except Exception as e:
            logger.error(f"Claude 분류 실패: {e}")
            return None

class OptimizedDinosaurNewsMonitor:
    """키워드 우선 분류를 사용하는 최적화된 공룡 뉴스 모니터"""

    def __init__(self, bot_token: str, channel_id: str):
        self.bot = Bot(token=bot_token)
        self.channel_id = channel_id
        self.scheduler = AsyncIOScheduler(timezone=pytz.timezone('Asia/Seoul'))
        self.state_file = 'dinosaur_news_state.json'
        self.sent_items = self.load_state()

        # 개선된 분류기 초기화
        self.classifier = ImprovedDinosaurClassifier()

        # API 사용 통계
        self.api_stats = {
            'total_classifications': 0,
            'api_calls': 0,
            'keyword_only': 0,
            'cost_saved': 0
        }

        # RSS 피드 목록 
        self.rss_feeds = {
            'Nature Paleontology': 'https://www.nature.com/subjects/palaeontology.rss',
            'PeerJ Paleontology Highly rated': 'https://peerj.com/articles/index.atom?section=paleontology-evolutionary-science&rating=5',
            'Live Science': 'https://www.livescience.com/feeds/all',
            'Science Daily Fossils': 'https://www.sciencedaily.com/rss/fossils_ruins.xml',
            'Universe Today': 'https://www.universetoday.com/feed/',
            'Nature Palaeontology': 'https://www.nature.com/subjects/palaeontology.rss',
            'Papers in Palaeontology': 'https://onlinelibrary.wiley.com/feed/20562802/most-recent',
            'dinosaur paleontology': 'https://pubmed.ncbi.nlm.nih.gov/rss/search/12MSb85m6hH8GiP-RH4NXuZ7B20URJZ2xSdh0nILvbL9n-iQ64/?limit=15&utm_campaign=pubmed-2&fc=20250904041631',
            'fossil vertebrate paleontology': 'https://pubmed.ncbi.nlm.nih.gov/rss/search/1DeUIcPgNLDFQS_E3SIgJGohhLIM1emALmYrOGJPdeuo0Xv9VR/?limit=15&utm_campaign=pubmed-2&fc=20250904041812',
            'r/science': 'https://www.reddit.com/r/science/hot/.rss',
            'SciTechDaily': 'https://scitechdaily.com/feed/',
            'Nature News': 'https://www.nature.com/nature.rss',
            'Science Magazine': 'https://www.science.org/rss/news_current.xml',
            'New Scientist': 'https://www.newscientist.com/feed/home/',
            'Scientific American': 'http://rss.sciam.com/basic-science',
            'Phys.org': 'https://phys.org/rss-feed/'
        }

        # HTTP 세션
        self.session = None

        # 키워드 번역 사전 (백업용)
        self.translation_patterns = {
            # 공룡 이름
            r'\bTyrannosaurus\b': '티라노사우루스',
            r'\bTriceratops\b': '트리케라톱스',
            r'\bStegosaurus\b': '스테고사우루스',
            r'\bVelociraptor\b': '벨로키랍토르',
            r'\bBrachiosaurus\b': '브라키오사우루스',
            # 시대명
            r'\bCretaceous\b': '백악기',
            r'\bJurassic\b': '쥐라기',
            r'\bTriassic\b': '트라이아스기',
            r'\bMesozoic\b': '중생대',
            # 고생물학 용어
            r'\bfossil(?:s)?\b': '화석',
            r'\bdinosaur(?:s)?\b': '공룡',
            r'\bpaleontology\b': '고생물학',
            r'\bpaleontologist(?:s)?\b': '고생물학자',
            r'\bextinct(?:ion)?\b': '멸종',
            r'\bevolution(?:ary)?\b': '진화',
            r'\bspecies\b': '종',
            r'\bskeleton\b': '골격',
            r'\bbone(?:s)?\b': '뼈',
            # 동사
            r'\bdiscover(?:ed|y)?\b': '발견',
            r'\bfound\b': '발견된',
            r'\bannounce(?:d)?\b': '발표',
            r'\breveal(?:ed)?\b': '공개',
            r'\bstudy\b': '연구'
        }

    async def is_dinosaur_news_optimized(self, title: str, summary: str) -> bool:
        """최적화된 공룡 뉴스 분류"""

        result = await self.classifier.classify_with_keywords_first(title, summary)

        # 통계 업데이트
        self.api_stats['total_classifications'] += 1
        if result['api_used']:
            self.api_stats['api_calls'] += 1
        else:
            self.api_stats['keyword_only'] += 1
            self.api_stats['cost_saved'] += 0.001  # 예상 절약 비용

        # 로깅
        method_emoji = {
            'keyword_only': '⚡',
            'hybrid_api_used': '🔄', 
            'conservative_keyword': '🛡️',
            'keyword_fallback': '🔙'
        }

        emoji = method_emoji.get(result['method'], '❓')
        logger.info(f"{emoji} {result['method']}: {result['decision']} (신뢰도: {result['confidence']:.2f})")

        return result['decision']

    def print_api_savings_stats(self):
        """API 절약 통계 출력"""
        if self.api_stats['total_classifications'] > 0:
            keyword_ratio = (self.api_stats['keyword_only'] / self.api_stats['total_classifications']) * 100
            logger.info(f"💰 API 절약 통계:")
            logger.info(f"   전체 분류: {self.api_stats['total_classifications']}회")
            logger.info(f"   키워드만 사용: {self.api_stats['keyword_only']}회 ({keyword_ratio:.1f}%)")
            logger.info(f"   API 호출: {self.api_stats['api_calls']}회")
            logger.info(f"   예상 절약 비용: ${self.api_stats['cost_saved']:.3f}")

    async def init_session(self):
        """HTTP 세션 초기화"""
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={'User-Agent': 'DinosaurNewsBot/5.0'}
            )

    async def close_session(self):
        """HTTP 세션 종료"""
        if self.session:
            await self.session.close()

    def load_state(self) -> set:
        """이전에 전송된 뉴스 항목들을 로드"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return set(data.get('sent_items', []))
        except Exception as e:
            logger.error(f"상태 파일 로드 실패: {e}")
        return set()

    def save_state(self):
        """현재 상태를 파일에 저장"""
        try:
            state_data = {
                'sent_items': list(self.sent_items),
                'last_update': datetime.now().isoformat()
            }
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"상태 파일 저장 실패: {e}")

    def generate_item_id(self, title: str, link: str) -> str:
        """뉴스 항목의 고유 ID 생성"""
        return hashlib.md5(f"{title}{link}".encode('utf-8')).hexdigest()

    def is_new_item(self, title: str, link: str) -> bool:
        """새로운 뉴스 항목인지 확인"""
        item_id = self.generate_item_id(title, link)
        if item_id not in self.sent_items:
            self.sent_items.add(item_id)
            return True
        return False

    async def translate_with_claude(self, text: str) -> str:
        """Claude API를 사용한 번역"""
        if not self.classifier.claude_client or not text:
            return ""

        try:
            # 텍스트 길이 제한 (비용 절약)
            text_to_translate = text[:200] if len(text) > 200 else text

            response = await asyncio.to_thread(
                self.classifier.claude_client.messages.create,
                model="claude-3-5-haiku-20241022",
                max_tokens=300,
                system="You are a dinosaur and paleontology expert translator. Translate English text into natural and accurate Korean. Translate academic terms precisely but make them easy to read. Provide only the translation.",
                messages=[
                    {"role": "user", "content": f"Please translate the following English text into Korean.: {text_to_translate}"}
                ]
            )

            translated = response.content[0].text.strip()

            # 번역 결과 검증
            if translated and len(translated) > 3 and "번역할 수 없습니다" not in translated:
                logger.info(f"Claude 번역 성공: {text[:30]}... → {translated[:30]}...")
                return translated
            else:
                logger.warning(f"Claude 번역 결과 부적절: {translated}")
                return ""

        except Exception as e:
            logger.warning(f"Claude 번역 실패: {e}")
            return ""

    def fallback_translate(self, text: str) -> str:
        """키워드 기반 백업 번역"""
        if not text:
            return ""

        translated = text

        # 패턴별 번역 적용
        for pattern, replacement in self.translation_patterns.items():
            translated = re.sub(pattern, replacement, translated, flags=re.IGNORECASE)

        return translated

    async def enhanced_translate(self, text: str) -> str:
        """Claude API 우선 + 키워드 백업 번역"""
        if not text:
            return ""

        # 1. Claude API 번역 시도
        if self.classifier.claude_client:
            claude_result = await self.translate_with_claude(text)
            if claude_result:
                return claude_result

        # 2. Claude 실패 시 키워드 번역으로 백업
        logger.info("Claude 번역 실패, 키워드 번역 사용")
        return self.fallback_translate(text)

    async def fetch_rss_feed(self, feed_name: str, feed_url: str) -> List[Dict]:
        """RSS 피드를 가져와서 파싱"""
        try:
            await self.init_session()
            async with self.session.get(feed_url) as response:
                if response.status == 200:
                    content = await response.text()
                    feed = feedparser.parse(content)

                    if feed.bozo:
                        logger.warning(f"{feed_name} 피드 파싱 경고: {feed.bozo_exception}")

                    new_entries = []
                    for entry in feed.entries[:10]:
                        try:
                            title = entry.title
                            link = entry.link
                            summary = entry.get('summary', entry.get('description', ''))

                            # 발행 날짜 확인
                            pub_date = None
                            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                                pub_date = datetime(*entry.published_parsed[:6])

                            # 24시간 이내의 새 항목만
                            if pub_date and datetime.now() - pub_date > timedelta(days=1):
                                continue

                            # 🔥 개선된 분류 사용
                            if not await self.is_dinosaur_news_optimized(title, summary):
                                continue

                            if self.is_new_item(title, link):
                                # Claude 번역 수행
                                title_ko = await self.enhanced_translate(title)
                                summary_ko = await self.enhanced_translate(summary) if summary else ""

                                new_entries.append({
                                    'title': title,
                                    'title_ko': title_ko,
                                    'link': link,
                                    'summary': summary,
                                    'summary_ko': summary_ko,
                                    'published': pub_date,
                                    'source': feed_name
                                })

                                # 번역 지연 (API 제한 고려)
                                await asyncio.sleep(1)

                        except Exception as e:
                            logger.error(f"항목 파싱 오류 ({feed_name}): {e}")
                            continue

                    return new_entries
                else:
                    logger.error(f"{feed_name} 피드 가져오기 실패: HTTP {response.status}")

        except Exception as e:
            logger.error(f"{feed_name} RSS 피드 처리 오류: {e}")

        return []

    def escape_markdown_v2(self, text: str) -> str:
        """텔레그램 MarkdownV2용 특수문자 이스케이프"""
        if not text:
            return ""

        # 모든 특수문자를 한 번에 이스케이프
        return re.sub(r'([_*\[\]()~`>#+=|{}.!-])', r'\\\1', text)

    def format_bilingual_message(self, item: Dict) -> str:
        """영어/한국어 병기 텔레그램 메시지 포맷팅"""
        en_title = self.escape_markdown_v2(item['title'])
        en_summary = ""
        if item.get('summary'):
            summary_text = item['summary']
            if len(summary_text) > 150:
                summary_text = summary_text[:150] + "..."
            en_summary = "\n\n" + self.escape_markdown_v2(summary_text)

        ko_title = self.escape_markdown_v2(item.get('title_ko', item['title']))
        ko_summary = ""
        if item.get('summary_ko'):
            summary_ko_text = item['summary_ko']
            if len(summary_ko_text) > 150:
                summary_ko_text = summary_ko_text[:150] + "..."
            ko_summary = "\n\n" + self.escape_markdown_v2(summary_ko_text)

        source = item['source']
        link = item['link']

        source_info = {
            'Nature Paleontology': {'emoji': '🔬', 'ko_name': 'Nature 고생물학'},
            'PeerJ Paleontology': {'emoji': '📄', 'ko_name': 'PeerJ 고생물학'},
            'Live Science': {'emoji': '🧬', 'ko_name': 'Live Science'},
            'Science Daily Fossils': {'emoji': '🦴', 'ko_name': 'Science Daily 화석'},
            'Universe Today': {'emoji': '🌌', 'ko_name': 'Universe Today'}
        }

        emoji = source_info.get(source, {}).get('emoji', '🦕')
        ko_source = source_info.get(source, {}).get('ko_name', source)

        separator = "━━━━━━━━━━━━━━━━"
        hashtag_source = source.replace(' ', '').replace('PeerJ', 'PeerJ')
        hash_symbol = "\\#"  # 이스케이프 유지

        message = f"""{emoji} *{self.escape_markdown_v2(source)}*\n\n*{en_title}*{en_summary}\n\n[Read more]({link})\n\n{separator}\n\n{emoji} *{self.escape_markdown_v2(ko_source)}*\n\n*{ko_title}*{ko_summary}\n\n[자세히 보기]({link})\n\n{hash_symbol}{hashtag_source} {hash_symbol}공룡뉴스 {hash_symbol}DinosaurNews"""

        return message

    async def send_telegram_message(self, message: str) -> bool:
        """텔레그램 채널에 메시지 전송"""
        try:
            await self.bot.send_message(
                chat_id=self.channel_id,
                text=message,
                parse_mode='MarkdownV2',
                disable_web_page_preview=False
            )
            return True

        except TelegramError as e:
            logger.error(f"텔레그램 전송 오류: {e}")

            # MarkdownV2 파싱 오류 시 일반 텍스트로 재시도
            if "parse entities" in str(e).lower():
                try:
                    # 마크다운 제거한 플레인 텍스트 버전
                    plain_message = re.sub(r'[*_\\#\[\]]', '', message)
                    plain_message = plain_message.replace('━━━━━━━━━━━━━━━━', '----------------')

                    await self.bot.send_message(
                        chat_id=self.channel_id,
                        text=plain_message,
                        disable_web_page_preview=False
                    )
                    logger.info("마크다운 오류로 인해 플레인 텍스트로 전송됨")
                    return True

                except Exception as e2:
                    logger.error(f"플레인 텍스트 전송도 실패: {e2}")
                    return False

        except Exception as e:
            logger.error(f"메시지 전송 실패: {e}")
            return False

    async def check_all_sources(self):
        """모든 소스에서 새 뉴스 확인"""
        logger.info("🦕 공룡 뉴스 소스 확인 시작... (키워드 우선 분류)")
        all_items = []

        # RSS 피드 확인
        for feed_name, feed_url in self.rss_feeds.items():
            items = await self.fetch_rss_feed(feed_name, feed_url)
            all_items.extend(items)
            await asyncio.sleep(2)  # Claude API 호출 간격 고려

        # 새 항목들을 텔레그램으로 전송
        sent_count = 0
        for item in all_items:
            message = self.format_bilingual_message(item)
            if await self.send_telegram_message(message):
                sent_count += 1
                logger.info(f"전송 완료: {item['title'][:50]}... → {item.get('title_ko', 'N/A')[:30]}...")
                await asyncio.sleep(5)  # 전송 간격

        # 상태 저장
        self.save_state()

        # API 절약 통계 출력
        self.print_api_savings_stats()

        logger.info(f"공룡 뉴스 확인 완료. {sent_count}개 새 항목 전송")

    async def start_monitoring(self):
        """모니터링 시작"""
        logger.info("🦕 공룡 뉴스 모니터링 서비스 시작 (키워드 우선 분류, API 절약)")

        # Claude 번역 테스트
        if self.classifier.claude_client:
            test_translation = await self.enhanced_translate("Scientists discover new dinosaur species")
            logger.info(f"Claude 번역 테스트: 'Scientists discover new dinosaur species' → '{test_translation}'")
        else:
            logger.info("Claude API 미사용, 키워드 번역만 사용")

        # 스케줄러 설정 - 30분마다 실행
        self.scheduler.add_job(
            self.check_all_sources,
            'interval',
            minutes=30,
            id='news_check',
            max_instances=1
        )

        # 시작 시 한 번 실행
        await self.check_all_sources()

        # 스케줄러 시작
        self.scheduler.start()

        try:
            while True:
                await asyncio.sleep(60)
        except KeyboardInterrupt:
            logger.info("모니터링 중지...")
            self.scheduler.shutdown()
            await self.close_session()

async def main():
    """메인 함수"""
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    channel_id = os.getenv('TELEGRAM_CHANNEL_ID')

    if not bot_token or not channel_id:
        print("환경 변수 설정이 필요합니다:")
        print("TELEGRAM_BOT_TOKEN=your_bot_token")
        print("TELEGRAM_CHANNEL_ID=your_channel_id")
        print("CLAUDE_API_KEY=your_claude_api_key # 선택사항")
        print("")
        print(".env 파일을 생성하거나 환경 변수로 설정하세요.")
        return

    monitor = OptimizedDinosaurNewsMonitor(bot_token, channel_id)
    await monitor.start_monitoring()

if __name__ == "__main__":
    asyncio.run(main())
