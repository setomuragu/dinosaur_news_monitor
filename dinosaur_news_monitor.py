"""
공룡 뉴스 실시간 알림 서비스 v4.0
space_news_bot.py 구조 참고하여 완전 재작성

Claude API 번역 기능 포함:
- Claude API 우선 번역 + 키워드 폴백
- RSS 피드 수집 및 중복 방지  
- 1시간 주기 자동 모니터링
- 텔레그램 자동 전송
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

class DinosaurNewsMonitor:
    def __init__(self, bot_token: str, channel_id: str):
        self.bot = Bot(token=bot_token)
        self.channel_id = channel_id
        self.scheduler = AsyncIOScheduler(timezone=pytz.timezone('Asia/Seoul'))
        self.state_file = 'dinosaur_news_state.json'
        self.sent_items = self.load_state()
        self.claude_client = anthropic.Anthropic(api_key=os.getenv('CLAUDE_API_KEY'))
        self.session = None
        self.channel_id = self.validate_channel_id(channel_id)
        self.feed_check_delay = 3  # 피드 간 3초 지연
        
        async def __aenter__(self):
            """Context Manager 진입"""
            await self.init_session()
            return self
        
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            """Context Manager 종료 - 자동으로 세션 정리"""
            await self.close_session()
            if self.scheduler and self.scheduler.running:
                self.scheduler.shutdown()
        
        # 공룡/고생물학 발견 키워드
        self.discovery_keywords = [
            'fossil', 'dinosaur', 'paleontology', 'discovery', 'found',
            'extinct', 'prehistoric', 'ancient', 'evolution', 'species',
            'cretaceous', 'jurassic', 'triassic', 'mesozoic', 'skeleton',
            'discovered', 'found', 'unearthed', 'uncovered', 'revealed', 
            'excavated', 'identified', 'detected', 'located', 'recovered',
    
            # 연구 관련  
            'analyzed', 'studied', 'examined', 'investigated', 'described',
            'documented', 'confirmed', 'verified', 'established',
            
            # 새로운 발견
            'new', 'novel', 'first', 'earliest', 'oldest', 'largest', 
            'smallest', 'rare', 'unique', 'unprecedented',
            
            # 공룡 골격
            'skeleton', 'skull', 'bone', 'vertebra', 'rib', 'femur',
            'tibia', 'jaw', 'tooth', 'teeth', 'claw', 'spine',
            
            # 화석 종류
            'fossil', 'fossils', 'remains', 'specimen', 'trace fossil',
            'body fossil', 'coprolite', 'gastrolith',
            
            # 특수 화석[230][3]
            'footprint', 'trackway', 'footprints', 'tracks', 'trail',
            'egg', 'eggs', 'nest', 'eggshell', 'embryo',
            'skin impression', 'feather', 'soft tissue',
            
            # 기본 분류
            'dinosaur', 'dinosaurs', 'saurian', 'reptile',
            
            # 주요 분류군[229][3]
            'theropod', 'sauropod', 'ornithischian', 'saurischian',
            'ceratopsian', 'hadrosaur', 'stegosaur', 'ankylosaur',
            'ornithopod', 'dromaeosaurid', 'tyrannosaur',
            
            # 한국어 분류명
            '수각류', '조각류', '용각류', '각룡류', '하드로사우르스',
            
            'Tyrannosaurus', 'Triceratops', 'Stegosaurus', 'Brachiosaurus',
            'Velociraptor', 'Allosaurus', 'Diplodocus', 'Spinosaurus',
            'Archaeopteryx', 'Compsognathus', 'Iguanodon', 'Parasaurolophus',
            
            # 시대명
            'Mesozoic', 'Paleozoic', 'Cenozoic',
            'Triassic', 'Jurassic', 'Cretaceous',
            'Permian', 'Devonian', 'Carboniferous',
            
            # 한국어 시대명
            '중생대', '고생대', '신생대',
            '트라이아스기', '쥐라기', '백악기',
            '페름기', '데본기', '석탄기',
            
            # 세부 시기
            'Early Cretaceous', 'Late Cretaceous', 'Middle Jurassic',
            '전기백악기', '후기백악기', '중기쥐라기',
            
            # 신종 관련
            'new species', 'novel species', 'undescribed species',
            'first discovered', 'newly identified', 'previously unknown',
            
            # 분류학적
            'taxonomic', 'classification', 'phylogeny', 'evolutionary',
            'systematic', 'nomenclature', 'holotype', 'paratype',
            
            # 명명 관련
            'named after', 'honors', 'commemorates', 'dedicated to',
            'etymology', 'binomial', 'genus', 'species epithet'
        ]
        
        # 제외할 키워드  
        self.exclude_keywords = [
            'rocket', 'space', 'satellite', 'launch', 'funding', 
            'business', 'company', 'stock', 'investment',
            'clinical', 'medical', 'patient', 'treatment', 'therapy',
            'diagnosis', 'hospital', 'pharmaceutical', 'drug',
            'space', 'astronomy', 'satellite', 'nasa', 'mars rover',
            'planetary', 'cosmic', 'stellar', 'galaxy', 'universe', 'homo', 'archaeologists',
            'brain cells', 'engineer', 'engineers', 'battery', 'LSD', 'sperm', 'microplastics', 'plastics', 'birth control', 'plant waste', 'binge-watching', 'humanity', 'resilience', 'glycosphingolipids', 'glycosphingolipid', 'unhappiness', 'Marketability', 'daughter', 'daughters', 'obese teens', 'low-income', 'vendor', 'petawatt', 'game', 'WiFi', 'desserts', 'sugary drinks', 'human skull', 'BPA', 'medical condition', 'health risk', 'clinical study', 'health effects', 'symptoms', 'health complications',
            'patient', 'treatment', 'therapy', 'diagnosis', 'hospital', 'pharmaceutical', 'drug', 'clinical',
            '의학적 상태', '건강 위험', '임상 연구', '건강 영향', '증상', '건강 합병증', 'pregnant women', 'gestational diabetes', 'pregnancy complications', 'artificial sweeteners', 'diet beverages',
            'dose-response', 'pregnancy risk', 'beverages a week', 'higher risk', 'maternal health', 'pregnancy outcomes',
            '임신 여성', '임신성 당뇨', '임신 합병증', '인공 감미료', '다이어트 음료', '용량 반응', '임신 위험', 'diagnostic dilemma', 'autoimmune disease', 'behavioral changes', 'craving', 'taste of bleach', 'bleach craving',
            'hidden cause', 'blood test', 'diagnosed with', 'medical diagnosis', 'striking changes', 'craving taste',
            '진단 딜레마', '자동면역질환', '행동 변화', '표백제 맛', '숨겨진 원인', '혈액 검사', 'wailing infants', 'baby cry', 'infant crying', 'crying baby', 'woken by baby', 'newborn', 'infant distress', 
            'sleep disturbance', 'rapid emotional response', 'physically hotter', 'distressed baby', 'cry response',
            '영아 울음', '아기 울음소리', '수면 방해', '신생아', '감정 반응', '체온 상승', 'tomato', 'toBRFV', 'brown rugose fruit virus', 'tomato brown rugose fruit virus',
            '토마토', '토마토 바이러스', '갈색 주름 과일 바이러스', 'plant disease', 'virus', 'pepper', 'crop', 'yield loss', 'seed transmission',
            'disinfecting', 'sanitizing', 'greenhouse', 'farm', 'agriculture', 'plant pathology',
            'plant virus', 'tobamovirus', 'genetic resistance', 'resistant cultivar', 'genetic breeding',
            '식물병', '바이러스', '고추', '작물', '수확 손실', '씨앗 전파', '소독', '온실', '농장', '농업', '식물 병리학', 'temperature effect', 'viral infection', 'plant molecular genetics', 'disease prevention',
            'host-pathogen interaction', 'plant health', 'plant laboratory', 'plant molecular geneticist',
            'environmental cue', 'virus-resistant tomato', '온도 영향', '바이러스 감염', '식물 분자 유전학', '질병 예방',
            '숙주-병원체 상호작용', '식물 건강', '식물 연구소', '환경 요인', '바이러스 저항성 토마토', 'black hole', '블랙홀', 'triumphant', 'salmon', 'bleach', 'slavery', '노예제도', '항아리', 'neutrino laser', '파멸론', '파멸론자', 'COVID', '비만', 'optoexcitonic', '화석연료', '인류 두개골', '산불', 'ribosome', '현대화', 'modernize', '치료법', '로마인', 'neanderthals', 'mathematicians', 'mathematician', 'mathematical', '난류', '발암물질', 'calorie', 'nanostructure',
            
            # food_nutrition_keywords
            'school meals', 'food system transformation', 'diet change', 'brain cancer treatment',
            'economic growth', 'sustainable food', 'food security', 'nutrition standards',
            'menu planning', 'food education', 'school catering', 'institutional catering',
            'vegetarian meals', 'food waste', 'school canteen', 'food policy',
            
            # space_tech_keywords
            'extraterrestrial ice', 'lasers', 'drilling holes', 'icy bodies',
            'Mars biosignature', 'Perseverance rover', 'Jezero Crater',
            'astrobiologist', 'planetary exploration', 'space mission',
            
            # academic_publishing_keywords
            'publish or perish', 'scientific publishing', 'evolutionary pressures',
            'natural selection theory', 'Charles Darwin', 'academic pressure',
            'research publication', 'peer review', 'scientific journals',
            
            #parasites_disease_keywords
            'parasitic worms', 'moose brain', 'elk brain', 'disease spread',
            'diagnostic', 'pandemic protection', 'wildlife tracking',
            'infectious disease', 'pathogen', 'epidemic',
            
            # animal_behavior_keywords
            'mice ultrasound', 'mouse communication', 'ultrasound frequency',
            'animal behavior', 'wildlife communication', 'acoustic signals',
            'behavioral ecology', 'animal sounds',
            
            # politics_society_keywords
            'national identity', 'defense willingness', 'country defense',
            'political willingness', 'Ukraine invasion', 'military defense',
            'national security', 'geopolitics', 'warfare',
            
            # marine_ecology_keywords
            'kelp forests', 'beach ecosystems', 'seaweed', 'marine biology',
            'coastal ecology', 'ocean ecosystems', 'marine environment',
            'underwater ecosystems', 'sea life',
            
            # atomic_physics_keywords
            'atomic CT scan', 'gallium', 'fuel cell catalyst', 'hydrogen fuel',
            'catalyst durability', 'atomic structure', 'fuel cells',
            'hydrogen energy', 'clean mobility',
            
            # modern_medicine_keywords
            'T cells', 'cancer treatment', 'bulletproof cells', 'immunotherapy',
            'cell therapy', 'cancer research', 'medical breakthrough',
            'therapeutic cells', 'oncology',
            
            # 물리학/나노기술 관련
            'terahertz light', 'nanoscale', 'layered material', 'THz light',
            'nanotechnology', 'optical confinement', 'photonics',
            'electromagnetic waves', 'quantum entanglement',
            
            # 인류학/문화진화 관련 (공룡과 구분)
            'human evolution', 'culture genetics', 'social evolution',
            'cultural development', 'human behavior', 'anthropology',
            'human society', 'cultural anthropology',
            
            # 인간 미라/고고학 관련 (공룡 화석과 구분)
            'human mummies', 'smoke-dried', 'embalmed bodies', 'Egyptian mummies',
            '14,000 years ago', 'human remains', 'archaeological sites',
            'ancient humans', 'burial practices', 'human archaeology',
            
            # NIH/정책/규제 관련
            'NIH', 'fetal tissue research', 'research ban', 'Trump administration',
            'government policy', 'research ethics', 'funding policy',
            'regulatory changes', 'research restrictions'
    
        ]
        
        self.instant_approve_keywords = [
            'dinosaur', 'dinosaurs', 'fossil', 'fossils', 'paleontology', 'paleontologist',
            'tyrannosaurus', 'triceratops', 'stegosaurus', 'velociraptor', 'cretaceous',
            'jurassic', 'triassic', 'mesozoic', 'prehistoric reptile', 'ancient reptile',
            'fossil discovery', 'paleontological', 'dinosaur species', 'extinct reptile'
        ]
        
        self.instant_reject_keywords = [
            'covid', 'coronavirus', 'pandemic', 'vaccine', 'medical', 'clinical',
            'cancer', 'tumor', 'disease', 'treatment', 'therapy', 'hospital',
            'politics', 'election', 'government', 'policy', 'president', 'minister',
            'rocket', 'satellite', 'space mission', 'mars rover', 'nasa launch',
            'stock', 'investment', 'cryptocurrency', 'business', 'company',
            'smartphone', 'ai technology', 'artificial intelligence', 'machine learning', 'school meals', 'human mummies', 'T cells cancer', 'NIH research',
            'parasitic worms', 'national defense', 'fuel cell', 'm sorry', 'black hole', 'black-hole', 'neuro', 'vaccine', 'RFK'
        ]
        
        self.classification_prompt = """
        
            You are a paleontological text classifier.

                    === 분류 기준 ===

                    ✅ RELEVANT (관련 있음):
                    - Direct mention of dinosaurs, fossils, and paleontology
                    - Related to the Mesozoic Era (Triassic, Jurassic, Cretaceous periods)
                    - Ancient reptiles, pterosaurs, marine reptiles
                    - Dinosaur evolution, extinction events
                    - Paleontologist's excavation, research
                    - Dinosaur behavior, ecological reconstruction

                    ❌ IRRELEVANT (관련 없음):
                    - Modern animals (mammals, birds, fish)
                    - Human archaeology, history of civilization
                    - Modern medicine, technology, politics
                    - Space exploration, physics, chemistry
                    - Botany, agriculture, environment

                    === 응답 형식 ===
                    Output one of the following only:
                    - RELEVANT
                    - IRRELEVANT

                    === 판단 예시 ===
                    "New T-rex fossil found" → RELEVANT
                    "Ancient human burial" → IRRELEVANT  
                    "Mars rover discovers" → IRRELEVANT
                    "Cretaceous climate study" → RELEVANT
        """
        
        # 강력한 제외 키워드 (1개만 있어도 제외)
        self.strong_exclude_keywords = [
            'school meals', 'human mummies', 'T cells cancer', 'NIH research',
            'parasitic worms', 'national defense', 'fuel cell', 'm sorry', 'black hole', 'black-hole', 'neuro', 'COVID', 'vaccine', 'RFK'
        ]
        
        # Claude 클라이언트 초기화
        self.claude_client = None
        claude_api_key = os.getenv('ANTHROPIC_API_KEY') or os.getenv('CLAUDE_API_KEY')
        if claude_api_key:
            try:
                self.claude_client = anthropic.Anthropic(api_key=claude_api_key)
                logger.info("Claude API 클라이언트 초기화 완료")
            except Exception as e:
                logger.warning(f"Claude 초기화 실패: {e}")
        
        # RSS 피드 목록 (검증된 것들만)
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
            'Phys.org': 'https://phys.org/rss-feed/',
            
            # ✅ 새로 추가 (Tier 1)
            'Journal of Paleontology': 'https://www.cambridge.org/core/rss/product/id/A8663E6BE4FB448BB17B22761D7932B9',
            'Paleobiology': 'https://pubs.geoscienceworld.org/rss/site_65/LatestOpenIssueArticles_33.xml',  
            'Journal of Vertebrate Paleontology': 'https://www.tandfonline.com/feed/rss/ujvp20',
            'Palaeontology Wiley': 'https://onlinelibrary.wiley.com/feed/14754983/most-recent',
            'Cretaceous Research': 'https://rss.sciencedirect.com/publication/science/01956671',
            'Palaeogeography': 'https://rss.sciencedirect.com/publication/science/00310182',
            'Review of Palaeobotany': 'https://rss.sciencedirect.com/publication/science/00346667',
            'Alcheringa Journal': 'https://www.tandfonline.com/feed/rss/talc20',
            
            # ✅ 박물관 & 연구기관
            # 'Natural History Museum': 'https://www.nhm.ac.uk/discover/news.rss',
            'Smithsonian Paleontology': 'https://insider.si.edu/category/science-nature/paleontology/feed/',
            # 'Raymond M. Alf Museum': 'https://alfmuseum.org/feed/',
            # 'Denver Museum': 'https://www.dmns.org/feed/',
            'Royal Tyrrell Museum': 'https://tyrrellmuseum.com/feed/',
            
            # ✅ 대학 연구소
            'UC Berkeley Paleontology': 'https://ucmp.berkeley.edu/feed/',
            'Yale Peabody Museum': 'https://peabody.yale.edu/feed/',
            'Chicago Field Museum': 'https://www.fieldmuseum.org/about/news/feed',
            
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
    
    def validate_channel_id(self, channel_id: str) -> str:
        """채널 ID 형식 검증 및 수정"""
        if not channel_id:
            raise ValueError("TELEGRAM_CHANNEL_ID가 설정되지 않았습니다")
        
        # @username 형식인 경우 경고
        if channel_id.startswith('@'):
            logger.warning(f"채널명 형식 감지: {channel_id}")
            logger.warning("채널 ID 숫자 형식 사용을 권장합니다")
            return channel_id
        
        # 숫자 형식 검증
        try:
            channel_id_int = int(channel_id)
            
            # 채널은 반드시 음수여야 함
            if channel_id_int > 0:
                logger.error(f"❌ 잘못된 채널 ID: {channel_id} (양수)")
                logger.error("채널 ID는 음수(-100xxxxxxxxxx) 형태여야 합니다")
                raise ValueError("채널 ID는 음수여야 합니다")
            
            # 채널 ID는 보통 -100으로 시작
            if not channel_id.startswith('-100'):
                logger.warning(f"⚠️ 비표준 채널 ID 형식: {channel_id}")
                logger.warning("일반적으로 채널 ID는 -100으로 시작합니다")
            
            logger.info(f"✅ 채널 ID 검증 완료: {channel_id}")
            return channel_id
            
        except ValueError:
            logger.error(f"❌ 잘못된 채널 ID 형식: {channel_id}")
            raise ValueError("채널 ID는 숫자 또는 @username 형태여야 합니다")

    async def test_telegram_connection(self):
        """텔레그램 연결 테스트"""
        try:
            # 봇 정보 확인
            bot_info = await self.bot.get_me()
            logger.info(f"🤖 봇 연결 확인: @{bot_info.username}")
            
            # 채널 정보 확인 (권한 테스트)
            try:
                chat_info = await self.bot.get_chat(self.channel_id)
                logger.info(f"📢 채널 연결 확인: {chat_info.title} (ID: {chat_info.id})")
                
                # 테스트 메시지 전송
                test_message = "🦕 DinosaurNews 봇 연결 테스트"
                await self.bot.send_message(
                    chat_id=self.channel_id,
                    text=test_message
                )
                logger.info("✅ 테스트 메시지 전송 성공")
                return True
                
            except Exception as e:
                logger.error(f"❌ 채널 연결 실패: {e}")
                logger.error("채널 ID 확인 또는 봇 관리자 권한 설정이 필요합니다")
                return False
                
        except Exception as e:
            logger.error(f"❌ 봇 연결 실패: {e}")
            logger.error("TELEGRAM_BOT_TOKEN을 확인하세요")
            return False
    
    def prefilter_before_api(self, title: str, summary: str) -> Optional[bool]:
        """🔥 API 호출 전 사전 필터링 - 70% API 호출 절약"""
        combined_text = (title + ' ' + summary).lower()
        
        # 1단계: 강력한 공룡 키워드 확인 (즉시 승인)
        instant_approve_keywords = [
            'dinosaur', 'dinosaurs', 'fossil', 'fossils', 'paleontology', 'paleontologist',
            'tyrannosaurus', 'triceratops', 'stegosaurus', 'velociraptor', 'cretaceous',
            'jurassic', 'triassic', 'mesozoic', 'prehistoric reptile', 'ancient reptile',
            'fossil discovery', 'paleontological', 'dinosaur species', 'extinct reptile'
        ]
        
        for keyword in instant_approve_keywords:
            if keyword in combined_text:
                logger.debug(f"✅ 사전승인: '{keyword}' 키워드 발견")
                return True
        
        # 2단계: 강력한 제외 키워드 확인 (즉시 거부)
        instant_reject_keywords = [
            'covid', 'coronavirus', 'pandemic', 'vaccine', 'medical', 'clinical',
            'cancer', 'tumor', 'disease', 'treatment', 'therapy', 'hospital',
            'politics', 'election', 'government', 'policy', 'president', 'minister',
            'rocket', 'satellite', 'space mission', 'mars rover', 'nasa launch',
            'stock', 'investment', 'cryptocurrency', 'business', 'company',
            'smartphone', 'ai technology', 'artificial intelligence', 'machine learning',
            'school meals', 'food system', 'nutrition', 'diet change', 'brain cancer'
        ]
        
        for keyword in instant_reject_keywords:
            if keyword in combined_text:
                logger.debug(f"❌ 사전거부: '{keyword}' 키워드 발견")
                return False
        
        # 3단계: 애매한 경우 - API 분류 필요
        logger.debug(f"🤔 애매함: API 분류 필요 - {title[:30]}...")
        return None


    async def classify_with_prefilter(self, title: str, summary: str) -> bool:
        """🔥 사전 필터링 + Claude API 조합"""
        # 1차: 사전 필터링 (API 호출 없음)
        prefilter_result = self.prefilter_before_api(title, summary)
        
        if prefilter_result is not None:
            # 명확한 결과 - API 호출 없이 리턴
            return prefilter_result
        
        # 2차: 애매한 경우만 Claude API 호출
        logger.info(f"🤖 Claude API 분류 시작: {title[:30]}...")
        return await self.classify_with_claude(title, summary)

    async def fetch_rss_feed_optimized(self, feed_name: str, feed_url: str) -> List[Dict]:
        """🔥 사전 필터링을 적용한 최적화된 RSS 처리"""
        try:
            await self.init_session()
            async with self.session.get(feed_url) as response:
                if response.status == 200:
                    content = await response.text()
                    feed = feedparser.parse(content)
                    
                    new_entries = []
                    api_calls_saved = 0
                    api_calls_made = 0
                    
                    for entry in feed.entries[:10]:
                        try:
                            title = entry.title
                            link = entry.link
                            summary = entry.get('summary', entry.get('description', ''))
                            
                            # 발행 날짜 확인 (24시간 이내만)
                            pub_date = None
                            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                                pub_date = datetime(*entry.published_parsed[:6])
                                if datetime.now() - pub_date > timedelta(days=1):
                                    continue
                            
                            # 새로운 아이템인지 확인
                            if not self.is_new_item(title, link):
                                continue
                            
                            # 🔥 사전 필터링 적용
                            prefilter_result = self.prefilter_before_api(title, summary)
                            
                            if prefilter_result is True:
                                # 즉시 승인 - API 호출 없이 번역 진행
                                api_calls_saved += 1
                                is_relevant = True
                                continue
                            elif prefilter_result is False:
                                # 즉시 거부 - 건너뛰기
                                api_calls_saved += 1
                                
                            else:
                                # 애매한 경우만 API 호출
                                api_calls_made += 1
                                is_relevant = await self.classify_with_claude(title, summary)
                                if not is_relevant:
                                    continue
                            
                            # 관련 뉴스만 번역
                            if self.is_new_item(title, link):
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
                            
                            # API 호출 간 지연 (레이트 리밋 방지)
                            await asyncio.sleep(1)
                            
                        except Exception as e:
                            logger.error(f"항목 파싱 오류 ({feed_name}): {e}")
                            continue
                    
                    # 효율성 로그
                    total_checks = api_calls_saved + api_calls_made
                    if total_checks > 0:
                        savings_percent = (api_calls_saved / total_checks) * 100
                        logger.info(f"📊 {feed_name} 필터링 효율성: {api_calls_saved}개 사전처리, {api_calls_made}개 API 호출 ({savings_percent:.1f}% 절약)")
                    
                    return new_entries
                else:
                    logger.error(f"{feed_name} 피드 가져오기 실패: HTTP {response.status}")
        except Exception as e:
            logger.error(f"{feed_name} RSS 피드 처리 오류: {e}")
        
        return []

    async def check_all_sources_optimized(self):
        """🔥 사전 필터링을 적용한 전체 소스 확인"""
        logger.info("🔍 뉴스 소스 확인 시작 (사전 필터링 적용)...")
        all_items = []
        total_api_calls_saved = 0
        total_api_calls_made = 0
        
        # RSS 피드 확인
        for feed_name, feed_url in self.rss_feeds.items():
            try:
                items = await self.fetch_rss_feed_optimized(feed_name, feed_url)  # ✅ 최적화 버전
                all_items.extend(items)
                await asyncio.sleep(2)  # 피드 간 지연
            except Exception as e:
                logger.error(f"{feed_name} 피드 처리 오류: {e}")
        
        # 웹 스크래핑도 동일하게 적용 가능
        # esa_items = await self.scrape_esa_news_optimized()
        # jaxa_items = await self.scrape_jaxa_news_optimized()
        
        # 웹 스크래핑도 동일하게 적용
        try:
            esa_items = await self.scrape_esa_news()
            all_items.extend(esa_items)
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"ESA 스크래핑 오류: {e}")
        
        try:
            jaxa_items = await self.scrape_jaxa_news()  
            all_items.extend(jaxa_items)
        except Exception as e:
            logger.error(f"JAXA 스크래핑 오류: {e}")
        
        # 새 항목들을 텔레그램으로 전송
        sent_count = 0
        for item in all_items:
            message = self.format_bilingual_message(item)
            if await self.send_telegram_message(message):
                sent_count += 1
                logger.info(f"📤 전송 완료: {item['title'][:50]}... | 한글: {item.get('title_ko', 'N/A')[:30]}...")
            await asyncio.sleep(5)  # 텔레그램 제한 고려
        
        # 상태 저장
        self.save_state()
        logger.info(f"✅ 뉴스 확인 완료. {sent_count}개 새 항목 전송")
        
    async def classify_with_claude(self, title: str, summary: str) -> bool:
        """Claude API로 공룡/고생물학 관련성 분류"""
        if not self.claude_client:
            return False
            
        try:
            combined_text = f"제목: {title}\n내용: {summary}"
            
            response = await asyncio.to_thread(
                self.claude_client.messages.create,
                model="claude-3-5-haiku-20241022",
                max_tokens=10,
                temperature=0.1,  # 🔥 낮은 온도로 일관성 향상
                top_p=0.8,
                system=self.classification_prompt,
                messages=[
                    {"role": "user", "content": combined_text}
                ]
            )
            
            classification = response.content[0].text.strip().upper()
            
            if classification == "RELEVANT":
                logger.info(f"✅ Claude 분류: 관련 있음 - {title[:30]}...")
                return True
            elif classification == "IRRELEVANT":
                logger.info(f"❌ Claude 분류: 관련 없음 - {title[:30]}...")
                return False
            else:
                logger.warning(f"⚠️ Claude 분류 모호: {classification}")
                return False
                
        except Exception as e:
            logger.error(f"Claude 분류 실패: {e}")
            return False

    async def is_dinosaur_news_with_api(self, title: str, summary: str) -> bool:
        """API 기반 + 키워드 기반 하이브리드 분류"""
        # 1차: API 분류
        api_result = await self.classify_with_claude(title, summary)
        
        # 2차: 키워드 기반 검증 (기존 방식)
        keyword_result = self.is_dinosaur_news_traditional(title, summary)
        
        # 하이브리드 결정
        if api_result and keyword_result:
            return True  # 둘 다 관련 있음
        elif not api_result and not keyword_result:
            return False  # 둘 다 관련 없음
        else:
            # 의견이 다를 때는 API 우선
            logger.warning(f"API와 키워드 분류 불일치: API={api_result}, 키워드={keyword_result}")
            return api_result

    def is_dinosaur_news_traditional(self, title: str, summary: str) -> bool:
        """기존 키워드 기반 분류 (백업용)"""
        combined_text = (title + ' ' + summary).lower()
        
        # 기존 제외 키워드 확인
        for exclude_word in self.exclude_keywords:
            if exclude_word.lower() in combined_text:
                return False
        
        # 공룡 키워드 확인  
        for discovery_word in self.discovery_keywords:
            if discovery_word.lower() in combined_text:
                return True
                
        return False
    
    def is_dinosaur_news(self, title: str, summary: str) -> bool:
        """공룡/고생물학 뉴스인지 판단"""
        combined_text = (title + ' ' + summary).lower()
        
        # 제외 키워드가 있으면 거부
        for strong_exclude in self.strong_exclude_keywords:
            if strong_exclude.lower() in combined_text:
                logger.debug(f"강력한 제외 키워드: '{strong_exclude}'")
                return False
        
        # 일반 제외 키워드 점수 계산
        exclude_score = 0
        for exclude_word in self.exclude_keywords:
            if exclude_word.lower() in combined_text:
                exclude_score += 1
        
        # 제외 점수가 2개 이상이면 제외
        if exclude_score >= 2:
            return False
        
        # 공룡/고생물학 키워드 확인
        for discovery_word in self.discovery_keywords:
            if discovery_word.lower() in combined_text:
                return True
                
        return False
    
    async def init_session(self):
        """HTTP 세션 초기화"""
        if self.session is None:
            # 🔥 connector 설정 추가로 warning 방지
            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=10,
                ttl_dns_cache=300,
                use_dns_cache=True,
                keepalive_timeout=30,
                enable_cleanup_closed=True
            )
        
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={'User-Agent': 'DinosaurNewsBot/4.0'},
                connector=connector
            )
            logger.debug("✅ HTTP 세션 초기화 완료")
    
    async def close_session(self):
        """HTTP 세션 종료"""
        if self.session and not self.session.closed:
            logger.info("🔄 HTTP 세션 종료 중...")
            try:
                await self.session.close()
                # 🔥 커넥터가 완전히 정리될 때까지 대기
                await asyncio.sleep(0.1)
                logger.info("✅ HTTP 세션 정리 완료")
            except Exception as e:
                logger.error(f"세션 종료 중 오류: {e}")
            finally:
                self.session = None
    
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
        if not self.claude_client or not text:
            return ""
        
        try:
            # 텍스트 길이 제한 (비용 절약)
            text_to_translate = text[:200] if len(text) > 200 else text
            
            response = await asyncio.to_thread(
                self.claude_client.messages.create,
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
        if self.claude_client:
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
                            
                            # 공룡/고생물학 뉴스인지 확인
                            prefilter_result = self.prefilter_before_api(title, summary)
                            if prefilter_result is False:  # 즉시 거부 (API 호출 없음)
                                api_calls_saved += 1
                                continue
                            elif prefilter_result is True:  # 즉시 승인
                                api_calls_saved += 1
                                is_relevant = True
                            else:  # 애매한 경우만 Claude API 호출
                                api_calls_made += 1
                                is_relevant = await self.classify_with_claude(title, summary)
                                if not is_relevant:
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
        logger.info("공룡 뉴스 소스 확인 시작...")
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
        logger.info(f"공룡 뉴스 확인 완료. {sent_count}개 새 항목 전송")
    
    async def start_monitoring(self):
        """모니터링 시작 - 최적화 버전 사용"""
        logger.info("공룡 뉴스 모니터링 서비스 시작 (30분 주기, 효율적 Claude 사용)")
        
        # Claude 번역 테스트
        if self.claude_client:
            try:
                test_translation = await self.enhanced_translate("Scientists discover new dinosaur species")
                logger.info(f"Claude 번역 테스트: '{test_translation}'")
            except Exception as e:
                logger.warning(f"Claude 테스트 실패: {e}")
        
        # 🔥 최적화된 함수 사용
        self.scheduler.add_job(
            self.check_all_sources_optimized,  # ✅ 최적화 버전 사용
            'interval',
            minutes=30,
            id='news_check_optimized',
            max_instances=1
        )
        
        # 시작 시 한 번 실행 (최적화 버전)
        try:
            await self.check_all_sources_optimized()  # ✅ 최적화 버전 사용
        except Exception as e:
            logger.error(f"초기 뉴스 확인 실패: {e}")
        
        # 스케줄러 시작
        self.scheduler.start()
        
        try:
            logger.info("모니터링 루프 시작 (Ctrl+C로 종료)")
            while True:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("작업 취소됨")
            raise
        except Exception as e:
            logger.error(f"모니터링 루프 오류: {e}")
            raise

        
 
        except KeyboardInterrupt:
            logger.info("모니터링 중지...")
        except Exception as e:
            logger.error(f"모니터링 오류: {e}")
        finally:
            # 🔥 항상 정리 작업 수행
            logger.info("모니터링 종료 중...")
            if self.scheduler and self.scheduler.running:
                self.scheduler.shutdown()
            await self.close_session()
            logger.info("모니터링 완전 종료")

class OptimizedDinosaurClassifier:
    def __init__(self):
        self.claude_client = anthropic.Anthropic(api_key=os.getenv('CLAUDE_API_KEY'))
        
        # 🔥 분류 전용 고도화된 프롬프트
        self.classification_system_prompt = """
            You are a paleontological text classifier.

                    === 분류 기준 ===

                    ✅ RELEVANT (관련 있음):
                    - Direct mention of dinosaurs, fossils, and paleontology
                    - Related to the Mesozoic Era (Triassic, Jurassic, Cretaceous periods)
                    - Ancient reptiles, pterosaurs, marine reptiles
                    - Dinosaur evolution, extinction events
                    - Paleontologist's excavation, research
                    - Dinosaur behavior, ecological reconstruction

                    ❌ IRRELEVANT (관련 없음):
                    - Modern animals (mammals, birds, fish)
                    - Human archaeology, history of civilization
                    - Modern medicine, technology, politics
                    - Space exploration, physics, chemistry
                    - Botany, agriculture, environment

                    === 응답 형식 ===
                    Output one of the following only:
                    - RELEVANT
                    - IRRELEVANT

                    === 판단 예시 ===
                    "New T-rex fossil found" → RELEVANT
                    "Ancient human burial" → IRRELEVANT  
                    "Mars rover discovers" → IRRELEVANT
                    "Cretaceous climate study" → RELEVANT
                    """

    async def classify_text_advanced(self, title: str, summary: str) -> Dict[str, any]:
        """고도화된 Claude 분류"""
        try:
            combined_text = f"제목: {title}\n\n요약: {summary[:300]}"
            
            response = await asyncio.to_thread(
                self.claude_client.messages.create,
                model="claude-sonnet-4-20250514",  # 🔥 더 강력한 모델 사용
                max_tokens=10,
                temperature=0.1,  # 🔥 낮은 온도로 일관성 향상
                top_p=0.8,
                stop_sequences=["\n", ".", ","],
                system=self.classification_system_prompt,
                messages=[
                    {
                        "role": "user", 
                        "content": f"Classify the following text:\n\n{combined_text}"
                    }
                ]
            )
            

            
            classification = response.content[0].text.strip().upper()
            
            # 추가 검증 로직
            confidence = self._calculate_confidence(title, summary, classification)
            
            return {
                'decision': classification == "RELEVANT",
                'classification': classification,
                'confidence': confidence,
                'method': 'claude_advanced'
            }
            
        except Exception as e:
            logger.error(f"Claude 분류 실패: {e}")
            return {'decision': False, 'classification': 'ERROR', 'confidence': 0.0}

    def _calculate_confidence(self, title: str, summary: str, classification: str) -> float:
        """분류 신뢰도 계산"""
        combined = (title + " " + summary).lower()
        
        # 강력한 신호 키워드
        strong_positive = ['dinosaur', 'fossil', 'paleontology', 'cretaceous', 'jurassic', 'mesozoic']
        strong_negative = ['cancer', 'rocket', 'politics', 'covid', 'smartphone', 'AI technology']
        
        positive_count = sum(1 for kw in strong_positive if kw in combined)
        negative_count = sum(1 for kw in strong_negative if kw in combined)
        
        if classification == "RELEVANT":
            base_confidence = 0.7
            confidence = base_confidence + (positive_count * 0.1) - (negative_count * 0.2)
        else:
            base_confidence = 0.8
            confidence = base_confidence + (negative_count * 0.1) - (positive_count * 0.1)
        
        return max(0.0, min(1.0, confidence))

async def hybrid_classification(self, title: str, summary: str) -> Dict[str, any]:
    """Claude + 키워드 이중 검증"""
    
    # 1차: Claude 분류
    claude_result = await self.classify_text_advanced(title, summary)
    
    # 2차: 키워드 점수 계산
    keyword_score = self._calculate_keyword_score(title, summary)
    
    # 3차: 결정 로직
    if claude_result['confidence'] >= 0.8:
        # Claude 신뢰도 높음 → Claude 결과 사용
        final_decision = claude_result['decision']
        final_confidence = claude_result['confidence']
        method = "claude_high_confidence"
        
    elif abs(keyword_score) >= 5.0:
        # 키워드 점수 명확 → 키워드 결과 사용  
        final_decision = keyword_score > 0
        final_confidence = min(0.9, abs(keyword_score) / 10.0)
        method = "keyword_clear_signal"
        
    else:
        # 애매한 경우 → 보수적 접근 (거부)
        final_decision = False
        final_confidence = 0.3
        method = "conservative_reject"
    
    return {
        'decision': final_decision,
        'confidence': final_confidence,
        'method': method,
        'claude_result': claude_result,
        'keyword_score': keyword_score
    }

def _calculate_keyword_score(self, title: str, summary: str) -> float:
    """키워드 기반 점수 계산 (-10 ~ +10)"""
    combined = (title + " " + summary).lower()
    score = 0.0
    
    # 강력한 공룡 키워드 (+점수)
    strong_dino = {
        'dinosaur': 4.0, 'fossil': 3.0, 'paleontology': 4.0,
        'cretaceous': 3.0, 'jurassic': 3.0, 'triassic': 3.0,
        'mesozoic': 4.0, 'extinct reptile': 3.0
    }
    
    # 강력한 제외 키워드 (-점수)  
    strong_exclude = {
        'cancer': -5.0, 'rocket': -4.0, 'politics': -4.0,
        'smartphone': -3.0, 'covid': -4.0, 'AI technology': -3.0,
        'human burial': -4.0, 'modern medicine': -4.0
    }
    
    # 점수 계산
    for keyword, points in strong_dino.items():
        if keyword in combined:
            score += points
            
    for keyword, points in strong_exclude.items():
        if keyword in combined:
            score += points  # 음수 점수
    
    return score




class SmartTranslationCache:
    def __init__(self, cache_file='translation_cache.pkl', max_age_days=30):
        self.cache_file = cache_file
        self.max_age_days = max_age_days
        self.cache = self._load_cache()
        self.daily_api_calls = 0
        self.daily_limit = int(os.getenv('CLAUDE_DAILY_LIMIT', '2000'))
    
    def _load_cache(self) -> dict:
        """캐시 파일에서 번역 결과 로드"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'rb') as f:
                    cache_data = pickle.load(f)
                    # 오래된 캐시 정리
                    cutoff_date = datetime.now() - timedelta(days=self.max_age_days)
                    cleaned_cache = {
                        k: v for k, v in cache_data.items()
                        if v.get('timestamp', datetime.min) > cutoff_date
                    }
                    return cleaned_cache
        except Exception as e:
            logger.warning(f"캐시 로드 실패: {e}")
        return {}
    
    def _save_cache(self):
        """캐시를 파일에 저장"""
        try:
            with open(self.cache_file, 'wb') as f:
                pickle.dump(self.cache, f)
        except Exception as e:
            logger.error(f"캐시 저장 실패: {e}")
    
    def _get_cache_key(self, text: str, translation_type: str = 'translate') -> str:
        """텍스트와 유형별 캐시 키 생성"""
        combined = f"{translation_type}:{text[:200]}"  # 200자로 제한
        return hashlib.sha256(combined.encode()).hexdigest()
    
    async def get_or_translate(self, text: str, translate_func, translation_type: str = 'translate'):
        """캐시에서 확인 후 필요시에만 API 호출"""
        if not text or len(text.strip()) < 3:
            return ""
        
        # 텍스트 전처리 (공백 정리)
        cleaned_text = ' '.join(text.split())
        cache_key = self._get_cache_key(cleaned_text, translation_type)
        
        # 캐시 확인
        if cache_key in self.cache:
            cached_result = self.cache[cache_key]
            logger.debug(f"💾 캐시 히트: {text[:30]}...")
            return cached_result['result']
        
        # API 호출 제한 확인
        if self.daily_api_calls >= self.daily_limit:
            logger.warning(f"⚠️ 일일 API 한도 초과 ({self.daily_api_calls}/{self.daily_limit})")
            return self._fallback_translation(cleaned_text)
        
        # API 호출
        try:
            result = await translate_func(cleaned_text)
            if result:
                # 캐시에 저장
                self.cache[cache_key] = {
                    'result': result,
                    'timestamp': datetime.now(),
                    'original_length': len(cleaned_text)
                }
                self.daily_api_calls += 1
                logger.debug(f"🔄 API 호출: {text[:30]}... (호출수: {self.daily_api_calls})")
                
                # 주기적 캐시 저장
                if self.daily_api_calls % 10 == 0:
                    self._save_cache()
                
                return result
        except Exception as e:
            logger.error(f"번역 API 오류: {e}")
            
        return self._fallback_translation(cleaned_text)
    
    def _fallback_translation(self, text: str) -> str:
        """API 실패시 키워드 기반 번역"""
        # 기존 fallback_translate 로직 사용
        return text  # 단순화

class OptimizedDinosaurNewsMonitor(DinosaurNewsMonitor):
    def __init__(self, bot_token: str, channel_id: str):
        super().__init__(bot_token, channel_id)
        self.translation_cache = SmartTranslationCache()
        self.batch_size = int(os.getenv('TRANSLATION_BATCH_SIZE', '5'))
        
        # 번역 우선순위 설정
        self.translation_priority = {
            'title': 1,      # 제목 우선
            'summary': 2     # 요약 차순위
        }
    
    def _optimize_text_for_translation(self, text: str, max_length: int = 300) -> str:
        """번역할 텍스트 최적화"""
        if not text:
            return ""
        
        # HTML 태그 제거
        import re
        text = re.sub(r'<[^>]+>', '', text)
        
        # 과도한 공백 정리
        text = re.sub(r'\s+', ' ', text).strip()
        
        # 길이 제한 (문장 단위로 자르기)
        if len(text) > max_length:
            sentences = text.split('. ')
            truncated = ""
            for sentence in sentences:
                if len(truncated + sentence) <= max_length - 10:
                    truncated += sentence + ". "
                else:
                    break
            text = truncated.strip()
            if not text.endswith('.'):
                text += "..."
        
        return text
    
    async def _batch_translate(self, texts: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """여러 텍스트를 효율적으로 배치 번역"""
        results = []
        
        for batch_start in range(0, len(texts), self.batch_size):
            batch = texts[batch_start:batch_start + self.batch_size]
            batch_results = []
            
            # 배치 내 병렬 처리
            tasks = []
            for item in batch:
                if item['type'] == 'title':
                    task = self.translation_cache.get_or_translate(
                        item['text'], 
                        self._translate_title_optimized,
                        'title'
                    )
                else:  # summary
                    task = self.translation_cache.get_or_translate(
                        item['text'], 
                        self._translate_summary_optimized,
                        'summary'
                    )
                tasks.append(task)
            
            # 병렬 실행
            batch_translations = await asyncio.gather(*tasks, return_exceptions=True)
            
            for i, translation in enumerate(batch_translations):
                if isinstance(translation, Exception):
                    logger.error(f"배치 번역 오류: {translation}")
                    translation = batch[i]['text']  # 원본 사용
                
                batch_results.append({
                    'original': batch[i]['text'],
                    'translated': translation,
                    'type': batch[i]['type']
                })
            
            results.extend(batch_results)
            
            # 배치 간 지연 (API 제한 고려)
            await asyncio.sleep(0.5)
        
        return results
    
    async def _translate_title_optimized(self, text: str) -> str:
        """제목 전용 최적화 번역"""
        optimized_text = self._optimize_text_for_translation(text, 150)
        
        try:
            response = await asyncio.to_thread(
                self.claude_client.messages.create,
                model="claude-3-5-haiku-20241022",
                max_tokens=80,  # 제목용 짧은 토큰
                temperature=0.1,
                system="You are a dinosaur/paleontology expert translator. Translate the following titles into concise and accurate Korean. Output only the title.",
                messages=[{"role": "user", "content": f"제목 번역: {optimized_text}"}]
            )
            
            translated = response.content[0].text.strip()
            return translated if translated else optimized_text
            
        except Exception as e:
            logger.error(f"제목 번역 실패: {e}")
            return optimized_text
    
    async def _translate_summary_optimized(self, text: str) -> str:
        """요약 전용 최적화 번역"""
        optimized_text = self._optimize_text_for_translation(text, 300)
        
        try:
            response = await asyncio.to_thread(
                self.claude_client.messages.create,
                model="claude-3-5-haiku-20241022",
                max_tokens=150,  # 요약용 중간 토큰
                temperature=0.1,
                system="공룡/고생물학 요약을 자연스러운 한국어로 번역하세요. 핵심 내용을 간결하게 번역하세요.",
                messages=[{"role": "user", "content": f"요약 번역: {optimized_text}"}]
            )
            
            translated = response.content[0].text.strip()
            return translated if translated else optimized_text
            
        except Exception as e:
            logger.error(f"요약 번역 실패: {e}")
            return optimized_text


class APIUsageMonitor:
    def __init__(self):
        self.usage_file = 'api_usage.json'
        self.usage_data = self._load_usage_data()
    
    def _load_usage_data(self):
        try:
            if os.path.exists(self.usage_file):
                with open(self.usage_file, 'r') as f:
                    return json.load(f)
        except:
            pass
        return {'daily_calls': 0, 'date': datetime.now().date().isoformat(), 'monthly_cost': 0}
    
    def track_api_call(self, tokens_used: int, cost_per_token: float = 0.0008):
        """API 호출 추적"""
        today = datetime.now().date().isoformat()
        
        if self.usage_data['date'] != today:
            # 새로운 날
            self.usage_data = {'daily_calls': 0, 'date': today, 'monthly_cost': self.usage_data.get('monthly_cost', 0)}
        
        self.usage_data['daily_calls'] += 1
        self.usage_data['monthly_cost'] += tokens_used * cost_per_token
        
        self._save_usage_data()
        
        # 경고 발생
        if self.usage_data['daily_calls'] > 1500:
            logger.warning(f"⚠️ API 사용량 주의: {self.usage_data['daily_calls']}회")
        
        if self.usage_data['monthly_cost'] > 50:  # $50 초과시
            logger.warning(f"💰 월간 비용 주의: ${self.usage_data['monthly_cost']:.2f}")
    
    def _save_usage_data(self):
        with open(self.usage_file, 'w') as f:
            json.dump(self.usage_data, f)
    
    def get_daily_usage(self) -> Dict:
        return {
            'calls': self.usage_data['daily_calls'],
            'estimated_cost': self.usage_data.get('monthly_cost', 0)
        }


async def main():
    """메인 함수"""
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    channel_id = os.getenv('TELEGRAM_CHANNEL_ID')
    
    if not bot_token or not channel_id:
        print("환경 변수 설정이 필요합니다:")
        print("TELEGRAM_BOT_TOKEN=your_bot_token")
        print("TELEGRAM_CHANNEL_ID=your_channel_id") 
        print("CLAUDE_API_KEY=your_claude_api_key  # 선택사항")
        print("")
        print(".env 파일을 생성하거나 환경 변수로 설정하세요.")
        return
        
    
    try:
        monitor = DinosaurNewsMonitor(bot_token, channel_id)
        
        # 🔥 연결 테스트 먼저 수행
        logger.info("📞 텔레그램 연결 테스트 중...")
        connection_ok = await monitor.test_telegram_connection()
        
        if not connection_ok:
            logger.error("텔레그램 연결 실패. 환경변수와 봇 설정을 확인하세요")
            return

        await monitor.start_monitoring()
    except KeyboardInterrupt:
        logger.info("사용자 중단 요청")
    except Exception as e:
        logger.error(f"모니터링 오류: {e}")
    finally:
        # 🔥 항상 세션 정리 (가장 중요!)
        if monitor:
            logger.info("🧹 정리 작업 시작...")
            
            try:
                # 스케줄러 종료
                if hasattr(monitor, 'scheduler') and monitor.scheduler.running:
                    monitor.scheduler.shutdown(wait=False)
                    logger.info("✅ 스케줄러 종료 완료")
            except Exception as e:
                logger.error(f"스케줄러 종료 오류: {e}")
            
            try:
                # 세션 종료
                await monitor.close_session()
                logger.info("✅ HTTP 세션 종료 완료")
            except Exception as e:
                logger.error(f"세션 종료 오류: {e}")
                
        logger.info("🏁 프로그램 완전 종료")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n프로그램 종료")
