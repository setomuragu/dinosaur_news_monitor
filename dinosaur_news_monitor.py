"""
공룡 뉴스 분류 추론 서비스 - 번역 및 텔레그램 전송 포함
"""

import asyncio
import json
import logging
import os
import re
import hashlib
from datetime import datetime
from typing import Dict, Optional

import anthropic
from transformers import AutoModelForSequenceClassification, AutoTokenizer
import torch
import feedparser
from telegram import Bot
from telegram.error import TelegramError

# .env 파일 로드 시도
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

def get_article_id(article: Dict) -> str:
    """기사의 고유 ID를 생성합니다. 링크를 우선 사용하고, 없으면 제목과 소스를 해시합니다."""
    title = article.get("title", "")
    # 링크가 없는 경우, 소스와 제목을 조합하여 고유 ID 생성
    return title.strip().lower()

def load_sent_cache(cache_file: str = "sent_articles.json") -> set:
    """전송 완료된 기사 캐시를 파일에서 불러옵니다."""
    if not os.path.exists(cache_file):
        return set()
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, IOError):
        logger.warning(f"{cache_file}을 읽는 데 실패했습니다. 새 캐시를 시작합니다.")
        return set()

def save_sent_cache(sent_set: set, cache_file: str = "sent_articles.json"):
    """전송 완료된 기사 캐시를 파일에 저장합니다."""
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(list(sent_set), f, ensure_ascii=False, indent=2)
    except IOError as e:
        logger.error(f"캐시 파일 저장에 실패했습니다: {e}")

class DinosaurClassifier:
    """공룡 뉴스 분류기 - 추론 전용"""

    def __init__(self, model_path: str = None):
        # Claude 클라이언트 초기화
        self.claude_client = None
        claude_api_key = os.getenv('ANTHROPIC_API_KEY') or os.getenv('CLAUDE_API_KEY')
        
        if claude_api_key:
            try:
                self.claude_client = anthropic.Anthropic(api_key=claude_api_key)
                logger.info("Claude API 클라이언트 초기화 완료")
            except Exception as e:
                logger.warning(f"Claude 초기화 실패: {e}")
        else:
            logger.warning("Claude API 키가 설정되지 않았습니다")

        # 훈련된 모델 로드 시도
        self.model = None
        self.tokenizer = None
        if model_path and os.path.exists(model_path):
            try:
                self.tokenizer = AutoTokenizer.from_pretrained(model_path)
                self.model = AutoModelForSequenceClassification.from_pretrained(model_path)
                self.model.eval()
                logger.info(f"훈련된 모델 로드 완료: {model_path}")
            except Exception as e:
                logger.warning(f"모델 로드 실패: {e}")

        # 키워드 리스트
        self.strong_include_keywords = [
            'dinosaur', 'dinosaurs', 'fossil', 'fossils', 'paleontology', 'paleontologist',
            'cretaceous', 'jurassic', 'triassic', 'mesozoic', 'extinct reptile', 'tyrannosaur',
            'sauropod', 'theropod', 'ceratopsian', 'hadrosau', 'Tyrannosaurus', 'Triceratops',
            'Stegosaurus', 'Velociraptor', 'pterosaur', 'archaeopteryx', 'prehistoric reptile',
            'paleocene', '공룡', '화석', '고생물학', '백악기', '쥐라기', '트라이아스기',
            'brachiosaurus', 'allosaurus', 'spinosaurus', 'iguanodon'
        ]

        self.strong_exclude_keywords = [
            # 의학 관련
            'cancer treatment', 'human disease', 'COVID', 'vaccine', 'clinical trial',
            'patient study', 'medical diagnosis', 'pharmaceutical', 'hospital', 'therapy',
            'human skull', 'human remains', 'modern medicine', 'brain cancer', 'drug',
            
            # 정치/경제
            'politics', 'stock market', 'economic', 'business', 'cryptocurrency', 'election', 'RFK',
            
            # 기술
            'smartphone', 'AI technology', 'nanotechnology', 'quantum computing',
            
            # 우주/천문학 (대폭 강화!!!)
            'rocket launch', 'space mission', 'satellite', 'mars rover', 'spacecraft',
            'black hole', 'neutron star', 'supernova', 'galaxy', 'galaxies',
            'exoplanet', 'planet', 'planetary', 'asteroid', 'comet', 'meteor',
            'solar system', 'jupiter', 'saturn', 'mars', 'venus', 'mercury', 'neptune', 'uranus',
            'moon landing', 'lunar', 'astronaut', 'space station', 'ISS',
            'telescope', 'hubble', 'james webb', 'JWST', 'observatory',
            'orbit', 'orbital', 'celestial', 'cosmic', 'cosmos',
            'star formation', 'stellar', 'interstellar', 'nebula',
            'dark matter', 'dark energy', 'universe expansion',
            'big bang', 'cosmology', 'astrophysics', 'astronomy',
            'lightyear', 'light-year', 'parsec', 'redshift',
            'gravitational wave', 'space exploration', 'NASA', 'ESA', 'SpaceX',
            'rocket engine', 'propulsion', 'launch pad', 'mission control',
            '우주', '행성', '위성', '로켓', '천문학', '블랙홀', '은하', '목성',
            
            # 인류학
            'homo sapiens', 'homo erectus', 'primates', 'Neanderthals', 'Denisovans',
            'human evolution', 'anthropology',
            
            # 기타
            'neutrino', 'particle physics', 'neandertal'
        ]

        self.medium_include_keywords = [
            'ancient', 'prehistoric', 'extinct', 'evolution', 'specimen', 'excavation',
            'discovery', 'bone', 'skeleton', 'species', 'vertebrate', 'reptile',
            'cretaceous period', 'jurassic period', 'triassic period',
            'sedimentary', 'geological', 'stratigraphy'
        ]

        self.medium_exclude_keywords = [
            'modern animal', 'human archaeology', 'medical research', 'technology',
            'engineering', 'plant biology', 'marine biology', 'cell biology',
            'molecular biology', 'genetics study', 'climate model', 'weather',
            'ocean current', 'volcano', 'earthquake', 'geology'
        ]

        # 번역 패턴 (백업용)
        self.translation_patterns = {
            r'\bTyrannosaurus\b': '티라노사우루스',
            r'\bTriceratops\b': '트리케라톱스',
            r'\bStegosaurus\b': '스테고사우루스',
            r'\bVelociraptor\b': '벨로키랍토르',
            r'\bBrachiosaurus\b': '브라키오사우루스',
            r'\bCretaceous\b': '백악기',
            r'\bJurassic\b': '쥐라기',
            r'\bTriassic\b': '트라이아스기',
            r'\bMesozoic\b': '중생대',
            r'\bfossil(?:s)?\b': '화석',
            r'\bdinosaur(?:s)?\b': '공룡',
            r'\bpaleontology\b': '고생물학',
            r'\bpaleontologist(?:s)?\b': '고생물학자',
            r'\bextinct(?:ion)?\b': '멸종',
            r'\bevolution(?:ary)?\b': '진화',
            r'\bspecies\b': '종',
            r'\bskeleton\b': '골격',
            r'\bbone(?:s)?\b': '뼈',
            r'\bdiscover(?:ed|y)?\b': '발견',
            r'\bfound\b': '발견된',
            r'\bannounce(?:d)?\b': '발표',
            r'\breveal(?:ed)?\b': '공개',
            r'\bstudy\b': '연구'
        }
    
    def title_prefilter_check(self, title: str) -> bool:
        """제목에서 명백히 관련 없는 기사를 사전에 걸러냄"""
        title_lower = title.lower()
    
        # 우주/천문학 관련 즉시 제외
        space_keywords = [
            'space', 'planet', 'star', 'galaxy', 'orbit', 'satellite', 
            'rocket', 'nasa', 'spacex', 'moon', 'mars', 'jupiter', 'saturn',
            'telescope', 'astronomy', 'cosmic', 'universe', 'solar system',
            'exoplanet', 'black hole', 'asteroid', 'comet', 'meteor'
        ]
        
        for keyword in space_keywords:
            if keyword in title_lower:
                # 공룡 키워드가 함께 있으면 통과
                dino_keywords = ['dinosaur', 'fossil', 'paleontology', '공룡', '화석']
                if not any(dk in title_lower for dk in dino_keywords):
                    logger.info(f"제목 사전필터 제외: {title} (우주 관련)")
                    return False
        
        return True

    
    def calculate_keyword_confidence(self, title: str, summary: str) -> Dict[str, float]:
        """키워드 기반 신뢰도 계산"""
        combined_text = (title + " " + summary).lower()
        scores = {
            'strong_include': 0,
            'strong_exclude': 0,
            'medium_include': 0,
            'medium_exclude': 0
        }

        for keyword in self.strong_include_keywords:
            if keyword.lower() in combined_text:
                scores['strong_include'] += 4

        for keyword in self.strong_exclude_keywords:
            if keyword.lower() in combined_text:
                scores['strong_exclude'] += 4

        for keyword in self.medium_include_keywords:
            if keyword.lower() in combined_text:
                scores['medium_include'] += 1

        for keyword in self.medium_exclude_keywords:
            if keyword.lower() in combined_text:
                scores['medium_exclude'] += 1

        include_score = scores['strong_include'] + (scores['medium_include'] * 0.5)
        exclude_score = (scores['strong_exclude'] + (scores['medium_exclude'] * 0.5)) * 1.5
        final_score = include_score - exclude_score

        if final_score >= 3:
            confidence = 0.95
        elif final_score >= 2:
            confidence = 0.85
        elif final_score >= 1:
            confidence = 0.75
        elif final_score <= -2:
            confidence = 0.95
        elif final_score <= -1:
            confidence = 0.85
        elif final_score <= 0:
            confidence = 0.75
        else:
            confidence = 0.3

        return {
            'score': final_score,
            'confidence': confidence,
            'include_score': include_score,
            'exclude_score': exclude_score,
            'decision': final_score > 0
        }

    async def classify_with_claude(self, title: str, summary: str) -> Optional[bool]:
        """Claude API로 분류"""
        if not self.claude_client:
            return None

        try:
            combined_text = f"{title}\n{summary}"

            classification_prompt = """You are a dinosaur and paleontology expert classifier.
Judge whether the following text is related to dinosaurs, fossils, and paleontology.

**RELEVANT (공룡/고생물학 관련):**
- Dinosaurs, prehistoric reptiles, pterosaurs
- Fossils from Mesozoic Era (Triassic, Jurassic, Cretaceous)
- Paleontology research and discoveries
- Ancient extinct reptiles and their evolution

**IRRELEVANT (관련 없음):**
- Space exploration, astronomy, planets, stars, galaxies
- Rockets, satellites, NASA missions
- Modern medicine, vaccines, clinical trials
- Human evolution, anthropology
- Technology, AI, engineering
- Politics, economics, business
- Modern animals and marine biology

Answer ONLY one word: RELEVANT or IRRELEVANT"""

            response = await asyncio.to_thread(
                self.claude_client.messages.create,
                model="claude-sonnet-4-20250514",
                max_tokens=10,
                temperature=0.1,
                system=classification_prompt,
                messages=[{"role": "user", "content": combined_text}]
            )

            classification = response.content[0].text.strip().upper()

            if "RELEVANT" in classification:
                return True
            elif "IRRELEVANT" in classification:
                return False
            else:
                logger.warning(f"Claude 분류 결과 애매함: {classification}")
                return None

        except Exception as e:
            logger.error(f"Claude 분류 오류: {e}")
            return None

    async def translate_to_korean(self, text: str) -> str:
        """Claude API로 한국어 번역"""
        if not self.claude_client or not text:
            return self.fallback_translate(text)

        try:
            text_to_translate = text[:200] if len(text) > 200 else text

            response = await asyncio.to_thread(
                self.claude_client.messages.create,
                model="claude-3-5-haiku-20241022",
                max_tokens=300,
                system="You are a dinosaur and paleontology expert translator. Translate English text into natural and accurate Korean. Translate academic terms precisely but make them easy to read. Provide only the translation.",
                messages=[{
                    "role": "user",
                    "content": f"Please translate the following English text into Korean:\n\n{text_to_translate}"
                }]
            )

            translated = response.content[0].text.strip()

            # 패턴 기반 보정
            for pattern, replacement in self.translation_patterns.items():
                translated = re.sub(pattern, replacement, translated, flags=re.IGNORECASE)

            if translated and len(translated) > 3:
                logger.info(f"Claude 번역 완료: {text[:30]}... → {translated[:30]}...")
                return translated
            else:
                logger.warning(f"Claude 번역 결과 이상: {translated}")
                return self.fallback_translate(text)

        except Exception as e:
            logger.warning(f"Claude 번역 실패: {e}")
            return self.fallback_translate(text)

    def fallback_translate(self, text: str) -> str:
        """백업 번역 (패턴 기반)"""
        if not text:
            return ""

        translated = text
        for pattern, replacement in self.translation_patterns.items():
            translated = re.sub(pattern, replacement, translated, flags=re.IGNORECASE)

        return translated

    async def classify(self, title: str, summary: str) -> Dict[str, any]:
        """통합 분류 함수"""
        # 0. 제목 사전 필터링 (NEW!)
        if not self.title_prefilter_check(title):
            return {
                'decision': False,
                'confidence': 0.99,
                'method': 'title_prefilter_reject',
                'keyword_score': -999
            }
        
        # 1. 자체 모델 분류 (최우선)
        if self.model and self.tokenizer:
            model_result = await self.classify_with_model(title, summary)
            if model_result:
                # 모델 신뢰도가 0.7 이상이면 즉시 채택
                if model_result['confidence'] >= 0.7:
                    logger.info(f"자체 모델 분류 성공: {model_result['confidence']:.2f}")
                    return model_result
                # 신뢰도가 낮으면 키워드로 보강
                else:
                    keyword_result = self.calculate_keyword_confidence(title, summary)
                    # 모델과 키워드가 일치하면 신뢰
                    if model_result['decision'] == keyword_result['decision']:
                        logger.info(f"모델+키워드 일치 분류: {model_result['confidence']:.2f}")
                        return {
                            'decision': model_result['decision'],
                            'confidence': min(0.85, model_result['confidence'] + 0.15),
                            'method': 'model_keyword_consensus',
                            'keyword_score': keyword_result['score']
                        }
                    # 불일치하면 Claude에게 최종 판단 위임
                    else:
                        logger.info("모델-키워드 불일치, Claude API 요청")
                        claude_result = await self.classify_with_claude(title, summary)
                        if claude_result is not None:
                            return {
                                'decision': claude_result,
                                'confidence': 0.75,
                                'method': 'model_keyword_conflict_claude',
                                'keyword_score': keyword_result['score']
                            }
                        # Claude도 실패하면 모델 결과 신뢰
                        return model_result
        
        # 2. 모델이 없는 경우 키워드 분류
        keyword_result = self.calculate_keyword_confidence(title, summary)
        if keyword_result['confidence'] >= 0.85:
            logger.info(f"키워드 분류 성공: {keyword_result['confidence']:.2f}")
            return {
                'decision': keyword_result['decision'],
                'confidence': keyword_result['confidence'],
                'method': 'keyword_only',
                'keyword_score': keyword_result['score']
            }
        
        # 3. Claude API 분류 (보조)
        claude_result = await self.classify_with_claude(title, summary)
        if claude_result is not None:
            logger.info("Claude API 분류 완료")
            return {
                'decision': claude_result,
                'confidence': 0.8,
                'method': 'claude_api',
                'keyword_score': keyword_result['score']
            }

        # 4. 보수적 키워드 판단
        conservative_decision = keyword_result['score'] >= 2
        return {
            'decision': conservative_decision,
            'confidence': 0.6,
            'method': 'conservative_keyword',
            'keyword_score': keyword_result['score']
        }

    async def classify_with_model(self, title: str, summary: str) -> Optional[Dict]:
        """훈련된 모델로 분류"""
        if not self.model or not self.tokenizer:
            logger.warning("자체 모델이 로드되지 않았습니다")
            return None

        try:
            text = f"{title} {summary}"
            inputs = self.tokenizer(text, truncation=True, padding=True, max_length=512, return_tensors="pt")

            with torch.no_grad():
                outputs = self.model(**inputs)
                probabilities = torch.nn.functional.softmax(outputs.logits, dim=-1)
                prediction = torch.argmax(probabilities, dim=-1).item()
                confidence = torch.max(probabilities).item()
                
            logger.info(f"자체 모델 추론 완료: prediction={prediction}, confidence={confidence:.3f}")

            return {
                'decision': bool(prediction),
                'confidence': float(confidence),
                'method': 'trained_model',
                'raw_logits': outputs.logits.tolist()[0]
            }
        except Exception as e:
            logger.error(f"모델 분류 오류: {e}")
            return None


# ========== 텔레그램 전송 함수 ==========

async def send_telegram_message(bot: Bot, channel_id: str, message: str) -> bool:
    """텔레그램 메시지 전송"""
    try:
        await bot.send_message(
            chat_id=channel_id,
            text=message,
            parse_mode='MarkdownV2',
            disable_web_page_preview=False
        )
        return True
    except TelegramError as e:
        logger.error(f"텔레그램 전송 실패: {e}")
        try:
            plain_message = re.sub(r'[_*\[\]()~`>#+=|{}.!-]', '', message)
            await bot.send_message(
                chat_id=channel_id,
                text=plain_message,
                disable_web_page_preview=False
            )
            logger.info("일반 텍스트로 전송 성공")
            return True
        except Exception as e2:
            logger.error(f"일반 텍스트 전송도 실패: {e2}")
            return False
    except Exception as e:
        logger.error(f"메시지 전송 오류: {e}")
        return False


def escape_markdownv2(text: str) -> str:
    """MarkdownV2용 특수문자 이스케이프"""
    if not text:
        return ""
    return re.sub(r'([_*\[\]()~`>#+=|{}.!-])', r'\\\1', text)


def format_bilingual_message(item: dict, classifier: DinosaurClassifier) -> str:
    """이중언어 메시지 포맷팅"""
    en_title = escape_markdownv2(item['title'])
    en_summary = ""
    if item.get('summary'):
        summary_text = item['summary']
        if len(summary_text) > 150:
            summary_text = summary_text[:150] + "..."
        en_summary = escape_markdownv2(summary_text)

    ko_title = escape_markdownv2(item.get('title_ko', item['title']))
    ko_summary = ""
    if item.get('summary_ko'):
        summary_ko_text = item['summary_ko']
        if len(summary_ko_text) > 150:
            summary_ko_text = summary_ko_text[:150] + "..."
        ko_summary = escape_markdownv2(summary_ko_text)

    source = item['source']
    link = item['link']

    source_info = {
        'Nature Paleontology': {'emoji': '🔬', 'koname': 'Nature 고생물학'},
        'PeerJ Paleontology': {'emoji': '📄', 'koname': 'PeerJ 고생물학'},
        'Live Science': {'emoji': '🌍', 'koname': 'Live Science'},
        'Science Daily Fossils': {'emoji': '🦴', 'koname': 'Science Daily 화석'},
        'Universe Today': {'emoji': '🌌', 'koname': 'Universe Today'}
    }

    emoji = source_info.get(source, {}).get('emoji', '📰')
    ko_source = source_info.get(source, {}).get('koname', source)
    separator = "━" * 30
    hashtag_source = source.replace(' ', '').replace('PeerJ', 'PeerJ')
    hash_symbol = "\\#"

    message = f"{emoji} *{escape_markdownv2(source)}*\n\n" \
              f"*{en_title}*\n\n" \
              f"{en_summary}\n\n" \
              f"[🔗 more]({link})\n\n" \
              f"{separator}\n\n" \
              f"{emoji} *{escape_markdownv2(ko_source)}*\n\n" \
              f"*{ko_title}*\n\n" \
              f"{ko_summary}\n\n" \
              f"{hash_symbol}{hashtag_source} {hash_symbol}공룡뉴스 {hash_symbol}DinosaurNews"

    return message


# ========== 메인 실행 함수 ==========

async def main():
    """RSS 피드에서 뉴스를 가져와 분류하고 텔레그램으로 전송"""

    # 환경 변수에서 설정 가져오기
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
    
    MODEL_PATH = os.getenv("MODEL_PATH", "./dinodistilbert")

    if not BOT_TOKEN or not CHANNEL_ID:
        print("오류: 텔레그램 설정이 필요합니다.")
        print("export TELEGRAM_BOT_TOKEN=your_bot_token")
        print("export TELEGRAM_CHANNEL_ID=your_channel_id")
        return

    # 봇과 분류기 초기화
    bot = Bot(token=BOT_TOKEN)
    
    if os.path.exists(MODEL_PATH):
        logger.info(f"'{MODEL_PATH}' 경로에서 훈련된 모델을 로드합니다.")
        classifier = DinosaurClassifier(model_path=MODEL_PATH)
        if classifier.model is None:
            logger.error("❌ 모델 로드 실패! 키워드와 Claude API로만 작동합니다.")
        else:
            logger.info("✅ 자체 모델이 메인 분류기로 설정되었습니다.")
    else:
        logger.warning(f"⚠️ '{MODEL_PATH}' 경로에 모델이 없습니다.")
        logger.warning("⚠️ 키워드와 Claude API로만 작동합니다.")
        classifier = DinosaurClassifier()
    
    # ★★★★★ 프로그램 시작 시 캐시를 딱 한 번만 불러옵니다. ★★★★★
    sent_cache = load_sent_cache()
    logger.info(f"프로그램 시작. 이전에 보낸 기사 {len(sent_cache)}개를 캐시에서 불러왔습니다.")

    # RSS 피드 목록
    rss_feeds = {
        'Nature Paleontology': 'https://www.nature.com/subjects/palaeontology.rss',
        'PeerJ Paleontology Highly rated': 'https://peerj.com/articles/index.atom?section=paleontology-evolutionary-science&rating=5',
        'Live Science': 'https://www.livescience.com/feeds/all',
        'Science Daily Fossils': 'https://www.sciencedaily.com/rss/fossils_ruins.xml',
        'Universe Today': 'https://www.universetoday.com/feed/',
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

    print("공룡 뉴스 모니터링 시작 (Ctrl+C로 종료)")

    
    while True:
        try:
            articles_to_check = []

            # RSS 피드에서 기사 수집
            for source_name, rss_url in rss_feeds.items():
                print(f"\n[{datetime.now()}] {source_name} RSS 피드 확인 중...")
                feed = feedparser.parse(rss_url)

                for entry in feed.entries[:10]:  # 최근 10개
                    articles_to_check.append({
                        'source': source_name,
                        'title': entry.get('title', ''),
                        'summary': entry.get('summary', entry.get('description', '')),
                        'link': entry.get('link', '')
                    })

                await asyncio.sleep(1)  # API 부하 방지

            # 분류 및 번역 후 텔레그램 전송
            print(f"\n총 {len(articles_to_check)}개 기사를 분류합니다...")
            sent_count_this_cycle = 0
            for article in articles_to_check:
                unique_id = get_article_id(article)
                if unique_id in sent_cache:
                    continue   # 이미 전송된 기사 skip
            
                result = await classifier.classify(article['title'], article['summary'])
                if result and result.get('decision'):
                    logger.info(f"공룡 뉴스 발견: '{article['title'][:30]}...' (방식: {result['method']})")

                    # 번역 수행
                    article['title_ko'] = await classifier.translate_to_korean(article['title'])
                    article['summary_ko'] = await classifier.translate_to_korean(article['summary'])

                    # 텔레그램 메시지 포맷팅 및 전송
                    message = format_bilingual_message(article, classifier)
                    
                    if await send_telegram_message(bot, CHANNEL_ID, message):
                        sent_cache.add(unique_id)
                        save_sent_cache(sent_cache)
                        sent_count_this_cycle += 1
                        logger.info(f"텔레그램 전송 완료: {article['title'][:50]}...")
                        await asyncio.sleep(5)  # 텔레그램 API 부하 방지

            print(f"\n이번 확인에서 {sent_count_this_cycle}개 뉴스를 전송했습니다.")

            # 10분 대기
            check_interval = 3600
            print(f"\n다음 확인까지 {check_interval // 60}분 대기합니다...")
            await asyncio.sleep(check_interval)

        except KeyboardInterrupt:
            print("\n프로그램을 종료합니다.")
            break
        except Exception as e:
            print(f"오류 발생: {e}")
            logger.error(f"오류 발생: {e}")
            await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
