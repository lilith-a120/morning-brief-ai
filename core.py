"""Morning Brief AI: RSS ingestion, retrieval, grounded answering, settings/email."""
from __future__ import annotations
import datetime as dt
import email.utils
import html
import json
import math
import os
import re
import smtplib
import sqlite3
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DB = Path(os.getenv('MORNING_DB', str(ROOT / 'morning.db')))
KST = ZoneInfo('Asia/Seoul')
TOPICS = {'전체': '', '국내': '대한민국 주요 뉴스', '국제': '세계 국제 뉴스', '경제': '한국 경제', 'AI·기술': '인공지능 기술', '금융시장': '주식 증시 환율'}
SAMPLE = [
    {'title':'[예시] AI 연구팀이 새로운 경량 모델을 공개했다', 'description':'연구팀은 작은 장치에서도 실행할 수 있는 경량 언어모델을 제안했다고 발표했다. 실제 검증되지 않은 연습용 데이터다.', 'category':'AI·기술'},
    {'title':'[예시] 국내 기업들이 반도체 연구 투자를 확대한다', 'description':'여러 기업이 반도체 분야 연구개발 투자를 늘리는 방안을 발표했다. 실제 뉴스가 아닌 테스트 문장이다.', 'category':'경제'},
    {'title':'[예시] 세계 각국이 환경 정책을 논의한다', 'description':'여러 국가 대표가 환경 협력과 관련된 회의를 진행했다. 실제 사건이 아닌 예시 데이터다.', 'category':'국제'},
]

def now(): return dt.datetime.now(KST)

def conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS articles (id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL, url TEXT NOT NULL UNIQUE, source TEXT, category TEXT, published TEXT, collected TEXT, is_demo INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)''')
        c.execute('INSERT OR IGNORE INTO settings VALUES (?, ?)', ('enabled','false'))
        c.execute('INSERT OR IGNORE INTO settings VALUES (?, ?)', ('hour','08:00'))
        c.execute('INSERT OR IGNORE INTO settings VALUES (?, ?)', ('topics','["전체"]'))
        c.execute('INSERT OR IGNORE INTO settings VALUES (?, ?)', ('recipient',''))
        c.execute('INSERT OR IGNORE INTO settings VALUES (?, ?)', ('last_sent_date',''))

def get_settings():
    init_db()
    with conn() as c:
        return {r['key']:r['value'] for r in c.execute('SELECT * FROM settings')}

def set_setting(key, value):
    if key not in {'enabled','hour','topics','recipient','last_sent_date'}: raise ValueError('unknown setting')
    init_db()
    with conn() as c: c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key,str(value)))

def strip_tags(s):
    s = re.sub(r'<[^>]+>', ' ', html.unescape(s or ''))
    return re.sub(r'\s+', ' ', s).strip()

def rss_url(topic):
    if not TOPICS.get(topic): return 'https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko'
    return 'https://news.google.com/rss/search?q=' + urllib.parse.quote(TOPICS[topic]) + '&hl=ko&gl=KR&ceid=KR:ko'

def fetch_news(topic='전체', limit=20):
    url = rss_url(topic)
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 MorningBriefAI/1.0'})
    with urllib.request.urlopen(req, timeout=12) as response:
        raw = response.read(2_500_000)
    root = ET.fromstring(raw)
    articles=[]
    for item in root.findall('.//channel/item')[:limit]:
        def val(tag): return (item.findtext(tag) or '').strip()
        title=strip_tags(val('title'))
        link=val('link')
        if not title or not link: continue
        source_node=item.find('source')
        source=(source_node.text or '').strip() if source_node is not None else 'Google News RSS'
        published=val('pubDate')
        try:
            parsed=email.utils.parsedate_to_datetime(published)
            published=parsed.astimezone(KST).isoformat()
        except Exception: published=''
        articles.append({'title':title, 'description':strip_tags(val('description')), 'url':link, 'source':source,'category':topic, 'published':published, 'is_demo':0})
    return articles

def save_articles(items):
    init_db()
    count=0
    with conn() as c:
        for a in items:
            result=c.execute('''INSERT OR IGNORE INTO articles (title,description,url,source,category,published,collected,is_demo) VALUES(?,?,?,?,?,?,?,?)''', (a['title'],a.get('description',''),a['url'],a.get('source',''),a.get('category','전체'),a.get('published',''),now().isoformat(),int(a.get('is_demo',0))))
            count+=result.rowcount
    return count

def load_demo():
    day=now().date().isoformat()
    return save_articles([{**a, 'source':'연습용 데이터', 'url':f'demo://{i}', 'published':day, 'is_demo':1} for i,a in enumerate(SAMPLE)])

def list_articles(limit=150, category=None):
    init_db()
    with conn() as c:
        if category and category!='전체':
            rows=c.execute('SELECT * FROM articles WHERE category=? ORDER BY collected DESC LIMIT ?', (category,limit)).fetchall()
        else: rows=c.execute('SELECT * FROM articles ORDER BY collected DESC LIMIT ?', (limit,)).fetchall()
    return [dict(r) for r in rows]

def ngrams(s):
    s=re.sub(r'\s+',' ',s.casefold()).strip()
    return Counter(s[i:i+n] for n in (2,3) for i in range(max(0,len(s)-n+1)) if s[i:i+n].strip())

def cosine(a,b):
    dot=sum(v*b.get(k,0) for k,v in a.items())
    return dot/(math.sqrt(sum(v*v for v in a.values()))*math.sqrt(sum(v*v for v in b.values()))) if a and b else 0

def retrieve(question, articles, k=5, semantic=False):
    if not articles: return []
    docs=[(a['title']+' '+a['description'])[:1500] for a in articles]
    scores=None
    if semantic:
        try:
            from sentence_transformers import SentenceTransformer
            model=SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
            vectors=model.encode([question]+docs, normalize_embeddings=True)
            scores=[float(vectors[0] @ v) for v in vectors[1:]]
        except ImportError: raise RuntimeError('의미 검색용 sentence-transformers가 설치되지 않았습니다. pip install sentence-transformers')
    else:
        q=ngrams(question)
        scores=[cosine(q,ngrams(d)) for d in docs]
    ranked=sorted(zip(scores,articles),key=lambda x:x[0],reverse=True)
    return [{**a,'score':round(score,3)} for score,a in ranked[:k] if score>0]

def evidence(articles):
    return '\n\n'.join(f"[{i}] 제목: {a['title']}\n설명: {a['description'][:550]}\n출처: {a['source']}\n발행: {a['published']}\nURL: {a['url']}" for i,a in enumerate(articles,1))

def ask_llm(prompt, articles):
    """Grounded RAG answers via Gemini. No API key => evidence-only preview."""
    if not articles:
        return '관련 자료를 찾지 못했습니다. 다른 키워드로 질문해 주세요.', False
    api_key = os.getenv('GEMINI_API_KEY', '').strip()
    if not api_key:
        return ('GEMINI_API_KEY가 없어 **검색 근거 미리보기 모드**입니다. (AI 생성 답변이 아닙니다.)\n\n'
                + '\n'.join(f"{i}. {a['title']} — {a['description'][:180]}" for i, a in enumerate(articles, 1))), False
    try:
        from google import genai
        from google.genai import types
        model_name = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
        client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60000))
        response = client.models.generate_content(
            model=model_name,
            contents=f'사용자 요청: {prompt}\n\n근거 기사:\n{evidence(articles)}',
            config=types.GenerateContentConfig(
                system_instruction=(
                    '당신은 한국어 뉴스 브리핑 AI다. 반드시 제공된 기사 목록만 근거로 답하라. '
                    '검색된 기사의 제목과 RSS 요약만 제공된 경우 전문을 읽은 것처럼 말하지 말라. '
                    '질문과 직접 관련 없는 기사는 무시하라. 근거가 부족하면 부족하다고 명시하라. '
                    '기사의 주장과 검증된 사실을 구별하라. 임의의 사실, 시세, 수치를 생성하지 말라. '
                    '각 주장 뒤에 관련 근거 번호 [1], [2] 등을 붙여라. '
                    '시연용 데이터는 실제 사건으로 표현하지 말라. 간결하고 읽기 쉬운 한국어로 답하라.'
                ),
                temperature=0.2,
                max_output_tokens=1200,
            ),
        )
        answer = (response.text or '').strip()
        if not answer:
            return 'Gemini가 빈 응답을 반환했습니다. 모델 및 무료 사용량을 확인해 주세요.', False
        return answer, True
    except ImportError:
        return 'Gemini 라이브러리가 설치되지 않았습니다. `python -m pip install google-genai`를 실행하세요.', False
    except Exception as exc:
        # Do not display API keys, other secrets in an exception message.
        message = str(exc).replace(api_key, '[API_KEY_REDACTED]')
        return f'Gemini API 요청 실패 ({type(exc).__name__}): {message}\n\n아래 검색 근거를 확인하세요.', False

def briefing(articles, semantic=False):
    if not articles: return '수집된 뉴스가 없습니다.',[]
    # Exclude demonstrative stories from live briefing, unless only demo is present.
    live=[a for a in articles if not a['is_demo']]
    selected=(live or articles)[:10]
    answer,generated=ask_llm('오늘 알아야 할 뉴스 5개를 중요도 순으로 요약하되, 실제로 제공된 기사 내용만 이용하고 출처 번호를 붙여줘.',selected)
    return answer,selected

def send_mail(recipient, subject, text):
    host=os.getenv('SMTP_HOST','').strip()
    user=os.getenv('SMTP_USER','').strip()
    password=os.getenv('SMTP_PASSWORD','').strip()
    port=int(os.getenv('SMTP_PORT','465'))
    if not all([host,user,password,recipient]): raise ValueError('SMTP_HOST / SMTP_USER / SMTP_PASSWORD / 수신 이메일을 설정해 주세요.')
    msg=EmailMessage()
    msg['From']=user
    msg['To']=recipient
    msg['Subject']=subject
    msg.set_content(text)
    if port==465:
        with smtplib.SMTP_SSL(host,port,context=ssl.create_default_context(),timeout=15) as smtp:
            smtp.login(user,password);smtp.send_message(msg)
    else:
        with smtplib.SMTP(host,port,timeout=15) as smtp:
            smtp.starttls(context=ssl.create_default_context());smtp.login(user,password);smtp.send_message(msg)

def mail_body(answer, articles):
    lines=[answer,'','출처 목록 (일부 RSS는 제목/요약만 제공):']
    for i,a in enumerate(articles,1):
        lines.append(f"{i}. {a['title']}\n{a['source']} | {a['published']}\n{a['url']}")
    return '\n\n'.join(lines)
