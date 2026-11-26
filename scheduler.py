"""Run alongside Streamlit for scheduled, opt-in email delivery."""
import datetime as dt
import json
import os
import time
from pathlib import Path
from core import TOPICS, now, get_settings, set_setting, fetch_news, save_articles, list_articles, briefing, mail_body, send_mail
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'))
except ImportError: pass

def scheduled_check():
    config=get_settings()
    current=now()
    if config['enabled']!='true' or not config['recipient'] or current.strftime('%H:%M')!=config['hour'] or config['last_sent_date']==current.date().isoformat():
        return False
    topics=json.loads(config['topics']) or ['전체']
    added=0
    for topic in topics:
        if topic not in TOPICS:continue
        try: added+=save_articles(fetch_news(topic,15))
        except Exception as exc:print('뉴스 수집 실패:',exc,flush=True)
    records=list_articles(200)
    today=current.date()
    # Do not send outdated information as today's news if collection fails.
    current_articles=[a for a in records if not a['is_demo'] and a['collected'].startswith(today.isoformat())]
    if not current_articles:
        print('오늘 수집된 기사가 없어 발송하지 않습니다.',flush=True); return False
    response,used=briefing(current_articles)
    body=mail_body(response,used)
    send_mail(config['recipient'],f'[Morning Brief AI] {today} 모닝 브리핑',body)
    set_setting('last_sent_date',today.isoformat())
    print('메일 발송 완료:',current.isoformat(),flush=True)
    return True

if __name__=='__main__':
    print('예약 발송 시작. 앱 설정을 읽고 매 분 확인합니다. 종료: Ctrl+C',flush=True)
    while True:
        try:scheduled_check()
        except Exception as exc:print('발송 실패:',repr(exc),flush=True)
        time.sleep(30)
