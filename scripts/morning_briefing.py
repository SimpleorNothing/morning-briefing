#!/usr/bin/env python3
import os
import re
import json
import requests
import yfinance as yf
from datetime import datetime
import pytz


def get_market_data():
    tickers = {
        'S&P 500':     '^GSPC',
        '나스닥':       '^IXIC',
        '다우존스':     '^DJI',
        '코스피':       '^KS11',
        '코스닥':       '^KQ11',
        '미 국채 10년물': '^TNX',
    }
    results = {}
    for name, symbol in tickers.items():
        try:
            hist = yf.Ticker(symbol).history(period='5d')
            if len(hist) >= 2:
                prev = hist['Close'].iloc[-2]
                curr = hist['Close'].iloc[-1]
                chg  = curr - prev
                pct  = chg / prev * 100
                results[name] = {'value': float(curr), 'change': float(chg), 'pct': float(pct)}
            else:
                results[name] = None
        except Exception as e:
            print(f"[WARN] {name} fetch failed: {e}")
            results[name] = None
    return results


def get_fear_greed():
    try:
        url = 'https://production.dataviz.cnn.io/index/fearandgreed/graphdata'
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        fg = resp.json()['fear_and_greed']
        return round(fg['score']), fg['rating']
    except Exception as e:
        print(f"[WARN] Fear&Greed fetch failed: {e}")
        return None, None


def get_gas_price():
    api_key = os.environ.get('EIA_API_KEY', '')
    if not api_key:
        return None
    try:
        url = (
            'https://api.eia.gov/v2/petroleum/pri/gnd/data/'
            '?api_key=' + api_key +
            '&frequency=weekly'
            '&data[0]=value'
            '&facets[product][]=EMM_EPM0_PTE_NUS_DPG'
            '&sort[0][column]=period'
            '&sort[0][direction]=desc'
            '&length=1'
        )
        data = requests.get(url, timeout=10).json()
        return float(data['response']['data'][0]['value'])
    except Exception as e:
        print(f"[WARN] Gas price fetch failed: {e}")
        return None


def get_raw_headlines():
    news_api_key = os.environ.get('NEWS_API_KEY', '')
    if not news_api_key:
        return '', ''
    try:
        r1 = requests.get(
            'https://newsapi.org/v2/top-headlines'
            '?category=business&language=en&pageSize=15'
            f'&apiKey={news_api_key}',
            timeout=10
        )
        articles   = r1.json().get('articles', [])
        general_hl = '\n'.join(f"- {a['title']} [{a['source']['name']}]" for a in articles[:15])

        r2 = requests.get(
            'https://newsapi.org/v2/everything'
            '?q=US+Iran&sortBy=publishedAt&language=en&pageSize=5'
            f'&apiKey={news_api_key}',
            timeout=10
        )
        iran_articles = r2.json().get('articles', [])
        iran_hl = '\n'.join(f"- {a['title']} [{a['source']['name']}]" for a in iran_articles[:5])

        return general_hl, iran_hl
    except Exception as e:
        print(f"[WARN] NewsAPI fetch failed: {e}")
        return '', ''


def get_summary(general_hl, iran_hl):
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return '(ANTHROPIC_API_KEY 없음)', []

    import anthropic

    news_block = ''
    if general_hl:
        news_block = f'\n=== 주요 비즈니스 뉴스 헤드라인 ===\n{general_hl}'
    if iran_hl:
        news_block += f'\n\n=== 미국-이란 관련 최신 뉴스 ===\n{iran_hl}'

    prompt = f"""글로벌 금융·경제·지정학 모닝 브리핑을 위해 아래 헤드라인을 한국어로 요약해줘.
{news_block if news_block else '(뉴스 헤드라인 없음 — 최근 알려진 상황 기반으로 작성)'}

다음 두 항목을 JSON으로만 응답 (다른 텍스트 없이):
{{"iran": "미국-이란 동향 2~3문장", "news": ["1. ...", "2. ...", "3. ...", "4. ...", "5. ..."]}}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=1024,
            messages=[{'role': 'user', 'content': prompt}]
        )
        text = msg.content[0].text.strip()
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            result = json.loads(m.group())
            return result.get('iran', ''), result.get('news', [])
    except Exception as e:
        print(f"[WARN] Claude API failed: {e}")

    return '요약을 생성하지 못했습니다.', []


def build_slack_text(market_data, fg_score, fg_rating, gas_price, iran_summary, news_list):
    kst      = pytz.timezone('Asia/Seoul')
    date_str = datetime.now(kst).strftime('%Y년 %m월 %d일 (%a)')

    def line(name, d, is_yield=False):
        if not d:
            return f'• {name}: N/A'
        v, c, p = d['value'], d['change'], d['pct']
        emoji = '🟢' if c >= 0 else '🔴'
        sign  = '+' if c >= 0 else ''
        if is_yield:
            return f'• {name}: {v:.3f}% ({sign}{p:.2f}%)'
        return f'• {name}: {v:,.2f}  {emoji} {sign}{c:,.2f} ({sign}{p:.2f}%)'

    us_lines = '\n'.join([
        line('S&P 500',  market_data.get('S&P 500')),
        line('나스닥',   market_data.get('나스닥')),
        line('다우존스', market_data.get('다우존스')),
    ])
    kr_lines = '\n'.join([
        line('코스피', market_data.get('코스피')),
        line('코스닥', market_data.get('코스닥')),
    ])

    tnx = market_data.get('미 국채 10년물')
    tnx_str = (
        f"{tnx['value']:.3f}%  ({'+' if tnx['pct'] >= 0 else ''}{tnx['pct']:.2f}%)"
        if tnx else 'N/A'
    )

    if fg_score is None:
        fg_str, fg_emoji = 'N/A', '❓'
    elif fg_score < 25:
        fg_emoji = '😱'
        fg_str = f'{fg_score} — {fg_rating} {fg_emoji}'
    elif fg_score < 45:
        fg_emoji = '😰'
        fg_str = f'{fg_score} — {fg_rating} {fg_emoji}'
    elif fg_score < 55:
        fg_emoji = '😐'
        fg_str = f'{fg_score} — {fg_rating} {fg_emoji}'
    elif fg_score < 75:
        fg_emoji = '😊'
        fg_str = f'{fg_score} — {fg_rating} {fg_emoji}'
    else:
        fg_emoji = '🤑'
        fg_str = f'{fg_score} — {fg_rating} {fg_emoji}'

    gas_str  = f'${gas_price:.3f}/갤런' if gas_price else 'N/A (EIA_API_KEY 필요)'
    news_str = '\n'.join(news_list) if news_list else '뉴스를 가져오지 못했습니다.'

    return (
        f'📊 *{date_str} 모닝 브리핑*\n\n'
        f'🇺🇸 *미국 지수*\n{us_lines}\n\n'
        f'🇰🇷 *한국 지수*\n{kr_lines}\n\n'
        f'💵 *미 국채 10년물:* {tnx_str}\n'
        f'{fg_emoji} *Fear & Greed:* {fg_str}\n'
        f'⛽ *미국 휘발유 (전국 평균):* {gas_str}\n\n'
        f'🪖 *미국-이란 동향*\n{iran_summary}\n\n'
        f'📰 *주요 뉴스*\n{news_str}'
    )


def send_to_slack(webhook_url, text):
    payload = {'blocks': [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': text}}]}
    resp = requests.post(webhook_url, json=payload, timeout=10)
    resp.raise_for_status()
    print('✅ Slack 전송 완료')


def main():
    webhook_url = os.environ.get('SLACK_WEBHOOK_URL', '')
    if not webhook_url:
        raise SystemExit('ERROR: SLACK_WEBHOOK_URL 환경변수가 없습니다.')

    print('📈 시장 데이터 수집 중...')
    market_data = get_market_data()

    print('😨 Fear & Greed 수집 중...')
    fg_score, fg_rating = get_fear_greed()

    print('⛽ 휘발유 가격 수집 중...')
    gas_price = get_gas_price()

    print('📰 뉴스 헤드라인 수집 중...')
    general_hl, iran_hl = get_raw_headlines()

    print('🤖 Claude 요약 생성 중...')
    iran_summary, news_list = get_summary(general_hl, iran_hl)

    print('📨 Slack 전송 중...')
    text = build_slack_text(market_data, fg_score, fg_rating, gas_price, iran_summary, news_list)
    send_to_slack(webhook_url, text)


if __name__ == '__main__':
    main()
