from unittest.mock import MagicMock, patch

import pytest

from tradingagents.dataflows.yfinance_news import get_news_yfinance
from tradingagents.dataflows.korean_market_news import (
    _parse_dart_disclosures_html,
    _parse_naver_news_html,
)


def test_parse_naver_news_html_filters_rows_and_builds_absolute_links():
    html = """
    <table>
      <tr><th>제목</th><th>정보제공</th><th>날짜</th></tr>
      <tr>
        <td><a href="/item/news_read.naver?article_id=0001&office_id=001&code=005930">삼성전자 실적 서프라이즈</a></td>
        <td>연합뉴스</td>
        <td>2026.04.30 13:47</td>
      </tr>
      <tr>
        <td><a href="/item/news_read.naver?article_id=0002&office_id=001&code=005930">너무 오래된 기사</a></td>
        <td>연합뉴스</td>
        <td>2026.04.10 09:00</td>
      </tr>
    </table>
    """

    rows = _parse_naver_news_html(html, start_date="2026-04-24", end_date="2026-04-30", limit=10)

    assert rows == [
        {
            "title": "삼성전자 실적 서프라이즈",
            "publisher": "연합뉴스",
            "published_at": "2026-04-30 13:47",
            "link": "https://finance.naver.com/item/news_read.naver?article_id=0001&office_id=001&code=005930",
        }
    ]


def test_parse_dart_disclosures_html_extracts_recent_filings():
    html = """
    <div class="tbListInner">
      <table class="tbList">
        <tbody id="tbody">
          <tr>
            <td>1</td>
            <td class="tL"><span class="innerWrap"><a>삼성전자</a></span></td>
            <td class="tL">
              <a href="/dsaf001/main.do?rcpNo=20260430800106" id="r_20260430800106">현금ㆍ현물배당결정</a>
            </td>
            <td class="tL ellipsis" title="삼성전자">삼성전자</td>
            <td>2026.04.30</td>
            <td><span>유</span></td>
          </tr>
          <tr>
            <td>2</td>
            <td class="tL"><span class="innerWrap"><a>삼성전자</a></span></td>
            <td class="tL">
              <a href="/dsaf001/main.do?rcpNo=20260401800106" id="r_20260401800106">오래된 공시</a>
            </td>
            <td class="tL ellipsis" title="삼성전자">삼성전자</td>
            <td>2026.04.01</td>
            <td><span>유</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    """

    rows = _parse_dart_disclosures_html(html, start_date="2026-04-24", end_date="2026-04-30", limit=10)

    assert rows == [
        {
            "title": "현금ㆍ현물배당결정",
            "filer": "삼성전자",
            "filed_at": "2026-04-30",
            "link": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260430800106",
        }
    ]


@patch("tradingagents.dataflows.yfinance_news.get_korean_market_disclosures", return_value="## Korean Electronic Disclosures\n- 현금ㆍ현물배당결정")
@patch("tradingagents.dataflows.yfinance_news.get_korean_market_company_news", return_value="## Korean Local Market News\n- 삼성전자 실적 서프라이즈")
@patch("tradingagents.dataflows.yfinance_news.yf_retry", return_value=[])
@patch("tradingagents.dataflows.yfinance_news.yf.Ticker")
def test_get_news_yfinance_appends_korean_sources_when_yahoo_is_empty(
    mock_ticker_cls,
    _mock_yf_retry,
    _mock_korean_news,
    _mock_disclosures,
):
    mock_ticker_cls.return_value = MagicMock()

    result = get_news_yfinance("005930.KS", "2026-04-24", "2026-04-30")

    assert "## 005930.KS News, from 2026-04-24 to 2026-04-30:" in result
    assert "## Korean Local Market News" in result
    assert "## Korean Electronic Disclosures" in result
    assert "No news found for 005930.KS" not in result
