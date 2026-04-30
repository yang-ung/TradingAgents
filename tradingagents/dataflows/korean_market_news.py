from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from tradingagents.ticker_utils import get_krx_company_info, normalize_ticker_symbol

_NAVER_FINANCE_BASE_URL = "https://finance.naver.com"
_DART_BASE_URL = "https://dart.fss.or.kr"
_DART_SEARCH_PAGE_URL = f"{_DART_BASE_URL}/html/search/SearchCompany_M2.html"
_DART_SEARCH_AJAX_URL = f"{_DART_BASE_URL}/dsab001/search.ax"
_DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _parse_date(date_text: str, *formats: str) -> datetime | None:
    value = " ".join(date_text.split())
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _in_range(value: datetime | None, start_date: str, end_date: str) -> bool:
    if value is None:
        return False
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    return start_dt.date() <= value.date() <= end_dt.date()


@lru_cache(maxsize=128)
def _get_dart_corp_code(search_term: str) -> str | None:
    response = requests.get(
        _DART_SEARCH_PAGE_URL,
        params={"textCrpNM": search_term},
        headers=_DEFAULT_HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    corp_code_input = soup.find("input", {"id": "textCrpCik"})
    if corp_code_input is None:
        return None
    return corp_code_input.get("value") or None


def _parse_naver_news_html(html: str, *, start_date: str, end_date: str, limit: int) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue

        title_link = tds[0].find("a", href=True)
        if title_link is None:
            continue

        title = " ".join(title_link.get_text(" ", strip=True).split())
        publisher = " ".join(tds[1].get_text(" ", strip=True).split())
        published_dt = _parse_date(tds[2].get_text(" ", strip=True), "%Y.%m.%d %H:%M", "%Y.%m.%d")
        if not title or published_dt is None or not _in_range(published_dt, start_date, end_date):
            continue

        published_at = published_dt.strftime("%Y-%m-%d %H:%M") if published_dt.hour or published_dt.minute else published_dt.strftime("%Y-%m-%d")
        dedupe_key = (title, published_at)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        rows.append(
            {
                "title": title,
                "publisher": publisher or "Unknown",
                "published_at": published_at,
                "link": urljoin(_NAVER_FINANCE_BASE_URL, title_link["href"]),
            }
        )
        if len(rows) >= limit:
            break

    return rows


def _parse_dart_disclosures_html(html: str, *, start_date: str, end_date: str, limit: int) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    tbody = soup.find("tbody", {"id": "tbody"})
    if tbody is None:
        return []

    rows: list[dict[str, str]] = []
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue

        title_link = tds[2].find("a", href=True)
        if title_link is None:
            continue

        filed_dt = _parse_date(tds[4].get_text(" ", strip=True), "%Y.%m.%d")
        if filed_dt is None or not _in_range(filed_dt, start_date, end_date):
            continue

        rows.append(
            {
                "title": " ".join(title_link.get_text(" ", strip=True).split()),
                "filer": " ".join(tds[3].get_text(" ", strip=True).split()),
                "filed_at": filed_dt.strftime("%Y-%m-%d"),
                "link": urljoin(_DART_BASE_URL, title_link["href"]),
            }
        )
        if len(rows) >= limit:
            break

    return rows


def get_korean_market_company_news(ticker: str, start_date: str, end_date: str, limit: int = 10) -> str:
    normalized = normalize_ticker_symbol(ticker)
    info = get_krx_company_info(normalized)
    if info is None:
        return ""

    articles: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for page in range(1, 3):
        response = requests.get(
            f"{_NAVER_FINANCE_BASE_URL}/item/news_news.naver",
            params={"code": info["code"], "page": page},
            headers={**_DEFAULT_HEADERS, "Referer": f"{_NAVER_FINANCE_BASE_URL}/item/news.naver?code={info['code']}"},
            timeout=20,
        )
        response.raise_for_status()
        for article in _parse_naver_news_html(response.text, start_date=start_date, end_date=end_date, limit=limit):
            dedupe_key = (article["title"], article["published_at"])
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            articles.append(article)
            if len(articles) >= limit:
                break
        if len(articles) >= limit:
            break

    if not articles:
        return ""

    body = "\n".join(
        f"### {article['title']} (source: {article['publisher']})\nPublished: {article['published_at']}\nLink: {article['link']}\n"
        for article in articles
    )
    return f"## Korean Local Market News\n\n{body}".strip()


def get_korean_market_disclosures(ticker: str, start_date: str, end_date: str, limit: int = 10) -> str:
    normalized = normalize_ticker_symbol(ticker)
    info = get_krx_company_info(normalized)
    if info is None:
        return ""

    corp_code = _get_dart_corp_code(info["code"])
    if not corp_code:
        corp_code = _get_dart_corp_code(info["name"])
    if not corp_code:
        return ""

    session = requests.Session()
    search_params = {"textCrpNM": info["code"]}
    search_page = session.get(_DART_SEARCH_PAGE_URL, params=search_params, headers=_DEFAULT_HEADERS, timeout=20)
    search_page.raise_for_status()

    payload = {
        "currentPage": "1",
        "maxResults": str(limit),
        "maxLinks": "10",
        "sort": "date",
        "series": "desc",
        "textCrpCik": corp_code,
        "pageGubun": "corp",
        "textCrpNm": info["name"],
        "finalReport": "recent",
        "startDate": start_date.replace("-", ""),
        "endDate": end_date.replace("-", ""),
    }
    response = session.post(
        _DART_SEARCH_AJAX_URL,
        data=payload,
        headers={
            **_DEFAULT_HEADERS,
            "Referer": f"{_DART_SEARCH_PAGE_URL}?textCrpNM={info['code']}",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=20,
    )
    response.raise_for_status()

    disclosures = _parse_dart_disclosures_html(response.text, start_date=start_date, end_date=end_date, limit=limit)
    if not disclosures:
        return ""

    body = "\n".join(
        f"### {item['title']}\nFiled: {item['filed_at']}\nFiler: {item['filer']}\nLink: {item['link']}\n"
        for item in disclosures
    )
    return f"## Korean Electronic Disclosures\n\n{body}".strip()
