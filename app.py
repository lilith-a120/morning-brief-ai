import json
import os
from pathlib import Path
import streamlit as st
from core import TOPICS, now, init_db, get_settings, set_setting, fetch_news, save_articles, list_articles, load_demo, retrieve, ask_llm, briefing, mail_body, send_mail

st.set_page_config(page_title='Morning Brief AI',page_icon='☀️',layout='wide')
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'))
except ImportError: pass
init_db()
st.title('☀️ Morning Brief AI')
st.caption('RAG 기반 뉴스 검색 · 아침 브리핑 · 이메일 알림 | 과제용 MVP')
if not os.getenv('GEMINI_API_KEY'):
    st.info('GEMINI_API_KEY가 없어 검색 근거 미리보기 모드입니다. .env에 Gemini API 키를 설정한 후 앱을 재시작하면 실제 AI 답변이 활성화됩니다.')
settings=get_settings()
with st.sidebar:
    st.header('⚙️ 브리핑 설정')
    enabled=st.toggle('자동 이메일 브리핑',value=settings['enabled']=='true')
    hh=st.selectbox('발송 시각 (한국시간)',[f'{h:02d}:00' for h in range(24)],index=int(settings['hour'][:2]))
    recipient=st.text_input('수신 이메일',value=settings['recipient'],placeholder='you@example.com')
    chosen=st.multiselect('관심 분야',list(TOPICS),default=[t for t in json.loads(settings['topics']) if t in TOPICS] or ['전체'])
    if st.button('설정 저장',use_container_width=True):
        for key,value in [('enabled',str(enabled).lower()),('hour',hh),('recipient',recipient.strip()),('topics',json.dumps(chosen,ensure_ascii=False))]:set_setting(key,value)
        st.success('설정 저장! 자동 발송은 별도 scheduler.py 실행이 필요합니다.')
    st.divider()
    semantic=st.toggle('의미 기반 임베딩 검색 (선택)',value=False,help='sentence-transformers 설치 필요, 첫 실행 시 모델 다운로드')
    st.caption('기본 검색은 외부 모델이 없는 문자 n-gram 검색입니다. 의미 기반 검색은 추가 설치가 필요합니다.')

page1,page2,page3=st.tabs(['📰 오늘의 브리핑','🔎 뉴스에 질문하기','✉️ 알림 테스트'])
with page1:
    c1,c2=st.columns([2,1])
    topic=c1.selectbox('가져올 뉴스 분야',list(TOPICS))
    count=c2.slider('뉴스 수',5,40,15,5)
    x,y=st.columns(2)
    if x.button('🌐 실제 뉴스 RSS 수집',use_container_width=True):
        try:
            with st.spinner('RSS에서 뉴스를 가져오는 중...'):
                fetched=fetch_news(topic,count)
                saved=save_articles(fetched)
            st.success(f'{len(fetched)}건 조회, 새로운 기사 {saved}건 저장')
        except Exception as e: st.error(f'수집 실패: {e} (네트워크를 확인하거나 예시 데이터를 사용하세요.)')
    if y.button('🧪 연습용 기사 불러오기',use_container_width=True):
        load_demo();st.success('가상 예시 데이터를 저장했습니다. 실제 뉴스가 아닙니다.')
    articles=list_articles()
    live=[a for a in articles if not a['is_demo']]
    st.metric('저장된 실제 뉴스 수',len(live))
    st.caption(f'조회 기준 {now():%Y-%m-%d %H:%M} KST · 기사 발행일과 다를 수 있음')
    if st.button('✨ 저장된 뉴스로 브리핑 만들기',type='primary'):
        with st.spinner('브리핑 생성 중...'):
            response,used=briefing(articles)
        st.subheader('핵심 뉴스 브리핑')
        st.markdown(response)
        st.session_state['latest_brief']=(response,used)
    if 'latest_brief' in st.session_state:
        with st.expander('브리핑 근거 출처'):
            for i,a in enumerate(st.session_state['latest_brief'][1],1):
                st.write(f"[{i}] {a['title']} / {a['source']}")
                if not a['is_demo']: st.link_button('원문 확인',a['url'],key=f'bref{i}')
                else: st.caption('연습용 데이터')
    st.subheader('저장된 기사')
    if not articles:st.info('위 버튼으로 실제 뉴스를 수집하거나 예시 데이터를 불러오세요.')
    for a in articles[:30]:
        with st.expander(('🧪 ' if a['is_demo'] else '🗞️ ')+a['title']):
            st.write(a['description'] or '(RSS 요약 없음)')
            st.caption(f"{a['category']} · {a['source']} · {a['published']}")
            if not a['is_demo']:st.link_button('기사 열기',a['url'],key=f"article{a['id']}")
            else:st.warning('실제 발생한 사건이 아닌 시연용 기사입니다.')

with page2:
    st.subheader('저장된 기사를 근거로 질문하기')
    q=st.text_input('질문',placeholder='예: 인공지능과 관련된 중요한 소식은?')
    if st.button('검색하고 답변하기',type='primary') and q.strip():
        try:
            matched=retrieve(q,list_articles(),k=5,semantic=semantic)
            if not matched:st.warning('관련 기사를 찾지 못했어요. 먼저 뉴스 수집을 해보세요.')
            else:
                answer,is_llm=ask_llm(q,matched)
                st.markdown('#### '+('AI 답변' if is_llm else '검색 근거 / 상태'))
                st.markdown(answer)
                st.markdown('#### 검색된 기사 및 출처')
                for i,a in enumerate(matched,1):
                    st.write(f"[{i}] **{a['title']}** — {a['source']} (점수 {a['score']})")
                    if not a['is_demo']:st.link_button(f'기사 {i} 원문',a['url'],key=f'qa{i}')
                    else:st.caption('시연용 예시 기사')
        except Exception as e:st.error(f'검색 오류: {e}')

with page3:
    st.write('자동 이메일은 별도의 예약 프로그램을 켜두어야 작동합니다. 여기에서는 즉시 발송을 시험할 수 있습니다.')
    if st.button('📧 설정된 주소로 테스트 브리핑 발송'):
        try:
            conf=get_settings()
            if not conf['recipient']: raise ValueError('사이드바에서 수신 이메일을 저장하세요.')
            response,used=briefing(list_articles())
            if not used: raise ValueError('뉴스를 먼저 수집하세요.')
            send_mail(conf['recipient'],f'[Morning Brief AI] 테스트 브리핑 {now():%Y-%m-%d}',mail_body(response,used))
            st.success('이메일 발송 요청이 완료되었습니다. 수신함을 확인하세요.')
        except Exception as e:st.error(f'메일 발송 실패: {e}')
    st.code('python scheduler.py',language='bash')
    st.caption('브라우저를 닫더라도 scheduler.py 프로세스가 실행 중이면 예약 시각을 확인합니다. PC를 끄면 중단됩니다.')
