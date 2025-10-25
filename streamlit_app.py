# streamlit_app.py
import json
import re
from urllib.parse import urljoin, urlparse
from datetime import datetime

import streamlit as st
from bs4 import BeautifulSoup
import trafilatura
import requests
from openai import OpenAI

st.set_page_config(page_title="BX All-in-One Toolkit", page_icon="🧰", layout="wide")

# =========================
# 헬퍼: 크롤링/텍스트 추출
# =========================
def fetch_html(url, timeout=12):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (BrandToolkit/1.0)"}
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200 and "text/html" in resp.headers.get("Content-Type", ""):
            return resp.text
    except Exception:
        return None
    return None

def extract_text(html, base_url=None):
    # 1) trafilatura 시도 (가장 품질 좋음)
    txt = trafilatura.extract(html, include_comments=False, include_formatting=False, url=base_url)
    if txt and len(txt.split()) > 60:
        return txt.strip()
    # 2) fallback: BeautifulSoup로 가볍게 본문만 추출
    try:
        soup = BeautifulSoup(html, "lxml")
        # script/style 제거
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        return text
    except Exception:
        return ""

def discover_links(base_url, html, max_links=8, same_host=True):
    """홈에서 1뎁스 하이퍼링크를 뽑아 주요 페이지 몇 개만 탐색"""
    try:
        soup = BeautifulSoup(html, "lxml")
        anchors = soup.find_all("a", href=True)
        links = []
        host = urlparse(base_url).netloc
        for a in anchors:
            href = a["href"].strip()
            if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
                continue
            full = urljoin(base_url, href)
            if same_host and urlparse(full).netloc != host:
                continue
            if full not in links:
                links.append(full)
            if len(links) >= max_links:
                break
        return links
    except Exception:
        return []

def safe_get(url):
    html = fetch_html(url)
    if not html:
        return {"url": url, "title": url, "text": ""}
    # 제목 추출
    try:
        title = BeautifulSoup(html, "lxml").title
        title = title.get_text().strip() if title else url
    except Exception:
        title = url
    text = extract_text(html, base_url=url) or ""
    return {"url": url, "title": title, "text": text}

def summarize_corpus(client, model, corpus_items, company, max_chars=7000):
    """
    수집 텍스트 일부만(길이 제한) 붙여 요약/핵심/이슈 정리.
    """
    # 길이 제한 안에서 샘플링
    joined = []
    total = 0
    for item in corpus_items:
        if not item["text"]:
            continue
        snippet = item["text"][:2000]  # 각 문서에서 일부만
        block = f"\n\n[Source: {item['title']}]({item['url']})\n{snippet}"
        if total + len(block) > max_chars:
            break
        joined.append(block)
        total += len(block)
    bundle = "".join(joined) if joined else "No content collected."

    sys = (
        "You are a brand strategist. Read the provided sources and produce a Korean summary "
        "with: 1) 회사/제품 요약 2) 핵심 강점·약점 3) 메시지/비주얼 관점의 기회 4) 리브랜딩 시 위험요인."
        "Return clean Markdown. Never invent sources."
    )
    user = f"""
회사명: {company}

아래는 공식 사이트/기사 등에서 추출한 일부 텍스트입니다.
이를 바탕으로 **요약/핵심 포인트/리브랜딩 시 고려사항**을 작성하세요.
가능하면 인용 표시는 [Source:제목](URL)처럼 텍스트 링크 형태로 남기세요.

자료:
{bundle}
"""
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
        temperature=0.4,
        max_tokens=900,
    )
    return resp.choices[0].message.content

# =========================
# OpenAI 클라이언트
# =========================
with st.sidebar:
    st.subheader("⚙️ 설정", divider="rainbow")
    default_key = st.secrets.get("OPENAI_API_KEY", "")
    openai_api_key = st.text_input("OpenAI API Key", type="password", value=default_key or "")
    model = st.selectbox("모델", ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"], index=0)
    temperature = st.slider("창의성(temperature)", 0.0, 2.0, 0.8, 0.1)
    max_tokens = st.slider("최대 토큰", 256, 4096, 1600, 64)

client = OpenAI(api_key=openai_api_key) if openai_api_key else None

# =========================
# 입력 폼
# =========================
st.title("🧰 BX All-in-One Toolkit")
st.caption("리브랜딩 시: 공식 홈페이지·기사 자료를 먼저 분석하고 BX를 생성합니다.")

st.markdown("### 🧾 기본 정보 입력")
with st.form("brief_form"):
    col1, col2 = st.columns([1,1])
    with col1:
        company = st.text_input("기업명*", placeholder="예: 어썸컴퍼니")
        industry = st.text_input("산업/카테고리", placeholder="예: 핀테크, 식음료, SaaS")
        region = st.text_input("시장/지역", placeholder="예: 한국, 북미, 글로벌")
        competitors = st.text_area("경쟁사/레퍼런스", placeholder="경쟁사 또는 벤치마크 브랜드 리스트")
    with col2:
        target = st.text_area("타깃/세그먼트", placeholder="1차·2차 타깃, 페르소나 특징")
        request = st.text_area("요청사항(브리프)*", placeholder="배경/이슈/목표/KPI 등")
        constraints = st.text_area("제약/가드레일", placeholder="법규, 예산/일정 제한, 금지 요소 등")

    col3, col4, col5 = st.columns([1,1,1])
    with col3:
        mode = st.selectbox("프로젝트 유형", ["신규 브랜딩", "리브랜딩", "서비스 확장/하위브랜드"], index=1)
    with col4:
        tone = st.selectbox("브랜드 톤&매너", ["따뜻/친근", "기술/전문", "대담/혁신", "미니멀/정제"])
    with col5:
        depth = st.selectbox("디테일 수준", ["요약형", "표준형", "상세형"], index=1)

    submitted = st.form_submit_button("🚀 BX 자료 생성")

# =========================
# 리브랜딩 모드: 자료 수집 섹션
# =========================
corpus = []
corpus_summ = ""

if mode == "리브랜딩":
    st.markdown("### 🌐 리브랜딩: 공식 사이트/기사 분석")
    c1, c2 = st.columns([1,1])
    with c1:
        official = st.text_input("공식 홈페이지 URL", placeholder="https://example.com")
        discover_toggle = st.toggle("홈에서 주요 내부 링크 자동 수집(최대 8개)", value=True)
    with c2:
        article_urls = st.text_area("관련 기사·보도자료 URL들(줄바꿈으로 여러 개)", placeholder="https://news1...\nhttps://press2...")

    crawl = st.button("🕷 자료 수집 및 요약")
    if crawl:
        if not official and not article_urls.strip():
            st.warning("공식 사이트 또는 기사 URL 중 최소 1개를 입력해주세요.")
        else:
            with st.spinner("수집 중..."):
                # 1) 공식 사이트
                if official:
                    html = fetch_html(official)
                    if html:
                        corpus.append(safe_get(official))
                        if discover_toggle:
                            # 내부 링크 몇 개 더 가져와서 본문 추출
                            for link in discover_links(official, html, max_links=6, same_host=True):
                                corpus.append(safe_get(link))
                # 2) 기사들
                for raw in article_urls.splitlines():
                    url = raw.strip()
                    if not url:
                        continue
                    corpus.append(safe_get(url))

            # 비어있는 텍스트 제거 & 중복 제거
            clean = []
            seen = set()
            for item in corpus:
                key = item["url"]
                if key in seen:
                    continue
                seen.add(key)
                if item["text"] and len(item["text"].split()) > 40:
                    clean.append(item)
            corpus = clean

            st.success(f"수집 완료! 문서 {len(corpus)}개.")
            if not corpus:
                st.info("유의미한 본문을 찾지 못했습니다. URL을 교체하거나 내부 링크 자동 수집을 꺼보세요.")

            # 미리보기 & 요약
            if corpus and client:
                with st.spinner("요약 정리 중..."):
                    corpus_summ = summarize_corpus(client, model, corpus, company)
                st.subheader("📌 자료 요약(자동)")
                st.markdown(corpus_summ)

            # 소스 목록
            if corpus:
                st.subheader("🔗 수집 소스")
                for it in corpus:
                    st.markdown(f"- [{it['title']}]({it['url']})")

# =========================
# BX 시스템 프롬프트/유저 프롬프트
# =========================
def build_system_prompt():
    return (
        "You are a senior brand strategist and BX architect. "
        "Produce rigorous, production-ready brand strategy & experience documentation in Korean. "
        "Use clear sectioning, bullets, and tables where helpful. Always provide practical examples."
    )

def build_user_prompt(company, industry, region, competitors, target, mode, request, constraints, tone, depth, corpus_summ, sources):
    richness = {"요약형": "succinct with key bullets",
                "표준형": "balanced detail with examples",
                "상세형": "deep detail with frameworks, matrices and examples"}[depth]
    src_block = ""
    if corpus_summ:
        src_block = f"\n\n[리브랜딩 참고 자료 요약]\n{corpus_summ}\n\n"
        if sources:
            src_block += "[참고 소스 목록]\n" + "\n".join([f"- {s['title']} ({s['url']})" for s in sources[:12]]) + "\n\n"

    return f"""
다음 기업에 대해 BX 전 과정 문서를 만들어주세요. 결과는 **한국어**로, Markdown으로 구조화합니다.
특히 리브랜딩일 경우 제공된 자료 요약/소스에 기반해 작성하고, 링크를 새로 만들거나 추측하지 마세요.

[기업/시장 정보]
- 기업명: {company}
- 산업/카테고리: {industry or 'N/A'}
- 시장/지역: {region or 'N/A'}
- 경쟁/레퍼런스: {competitors or 'N/A'}
- 타깃/세그먼트: {target or 'N/A'}
- 프로젝트 유형: {mode}
- 요청사항/브리프: {request}
- 제약/가드레일: {constraints or 'N/A'}
- 톤&매너: {tone}
- 상세 수준: {depth} → {richness}

{src_block}
[필수 섹션]
1) 회사/시장/경쟁 분석
2) 브랜드 전략(미션/비전/가치·포지셔닝·가치제안)
3) 메시징 시스템(메시지 피라미드/슬로건/피치)
4) 아이덴티티 방향(무드/로고 컨셉/컬러·타이포/모션/접근성)
5) 어플리케이션 적용 계획(디지털/오프라인/커뮤니케이션; 우선순위/난이도/효과)
6) 브랜드 경험 여정 & 터치포인트 매트릭스
7) 론치/운영 플랜 & KPI(30/60/90)
8) 산출물 체크리스트
9) 참고 사례/가이드 제안(텍스트 링크 목록)

실행 가능하고 구체적으로 작성하고, 불확실한 가정은 명시하세요.
"""

# =========================
# BX 생성
# =========================
if submitted:
    if not company or not request:
        st.warning("기업명과 요청사항(브리프)은 필수입니다.")
    elif not client:
        st.warning("🔑 OpenAI API 키를 입력해주세요.")
    else:
        with st.spinner("BX 자료 생성 중…"):
            prompt = build_user_prompt(
                company, industry, region, competitors, target, mode, request,
                constraints, tone, depth,
                corpus_summ=corpus_summ,
                sources=corpus if corpus else []
            )
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": build_system_prompt()},
                          {"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        content = resp.choices[0].message.content
        st.success("완료! 아래 결과를 검토하세요.")
        st.markdown(content)

        # 다운로드
        cA, cB = st.columns(2)
        with cA:
            st.download_button(
                "💾 TXT로 저장",
                data=content,
                file_name=f"{company}_BX_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                mime="text/plain",
                use_container_width=True,
            )
        with cB:
            st.download_button(
                "💾 JSON로 저장",
                data=json.dumps({
                    "company": company, "industry": industry, "region": region,
                    "competitors": competitors, "target": target, "mode": mode,
                    "tone": tone, "depth": depth, "constraints": constraints,
                    "sources": [{"title": x.get("title"), "url": x.get("url")} for x in (corpus or [])],
                    "corpus_summary": corpus_summ,
                    "content": content
                }, ensure_ascii=False, indent=2),
                file_name=f"{company}_BX_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json",
                use_container_width=True,
            )

# =========================
# 고정 큐레이션(레퍼런스 사이트)
# =========================
st.markdown("### 🔗 참고할 만한 사례/가이드 (큐레이션)")
colr1, colr2, colr3 = st.columns(3)
with colr1:
    st.markdown("- **Behance | Branding Case Studies** – 다양한 브랜드 케이스")
    st.markdown("  - https://www.behance.net/search/projects/branding%20case%20study")
    st.markdown("- **Awwwards** – 트렌디한 웹/브랜딩 사이트")
    st.markdown("  - https://www.awwwards.com/websites/")
with colr2:
    st.markdown("- **Brand New (UnderConsideration)** – 리브랜딩 사례 리뷰")
    st.markdown("  - https://www.underconsideration.com/brandnew/archives/complete")
    st.markdown("- **BP&O** – 브랜딩/패키징 리뷰 & 인사이트")
    st.markdown("  - https://bpando.org/")
with colr3:
    st.markdown("- **Atlassian Design System** – 로고/토큰/콘텐츠 가이드")
    st.markdown("  - https://atlassian.design/")
    st.markdown("- **IBM Design Language**")
    st.markdown("  - https://www.ibm.com/design/language/")
    st.markdown("- **Material Design 3**")
    st.markdown("  - https://m3.material.io/")
    st.markdown("- **Apple HIG – Branding**")
    st.markdown("  - https://developer.apple.com/design/human-interface-guidelines/branding")
