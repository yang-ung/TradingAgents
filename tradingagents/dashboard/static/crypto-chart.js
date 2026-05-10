(function () {
  async function refreshChart() {
    const charts = Array.from(document.querySelectorAll('[data-chart-endpoint]'));
    for (const chart of charts) {
      const canvas = chart.querySelector('.crypto-chart-canvas') || document.querySelector('.crypto-chart-canvas');
      if (!canvas) continue;
      try {
        const response = await fetch(chart.dataset.chartEndpoint, { cache: 'no-store' });
        if (!response.ok) continue;
        const data = await response.json();
        const candles = Array.isArray(data.candles) ? data.candles.slice(-60) : [];
        if (!candles.length) { canvas.textContent = '차트 데이터 없음'; continue; }
        const lows = candles.map(c => Number(c.low || c.close)).filter(Number.isFinite);
        const highs = candles.map(c => Number(c.high || c.close)).filter(Number.isFinite);
        const min = Math.min(...lows);
        const max = Math.max(...highs);
        const span = Math.max(max - min, 1e-9);
        const width = 760;
        const priceHeight = 292;
        const volumeHeight = 58;
        const timeAxisHeight = 30;
        const padL = 54;
        const padR = 82;
        const padT = 14;
        const plotW = width - padL - padR;
        const y = (value) => padT + ((max - Number(value)) / span) * (priceHeight - 28);
        const x = (idx) => padL + (candles.length === 1 ? plotW / 2 : (idx / (candles.length - 1)) * plotW);
        const candleW = Math.max(3, Math.min(9, plotW / candles.length * 0.62));
        const fmt = (value) => Number(value).toLocaleString('ko-KR', { maximumFractionDigits: 0 });
        const fmtTime = (c) => {
          const raw = c.time || c.close_time || c.open_time || c.timestamp;
          let d = null;
          if (typeof raw === 'number' || /^\d+$/.test(String(raw || ''))) {
            const n = Number(raw);
            d = new Date(n > 1e12 ? n : n * 1000);
          } else if (raw) {
            d = new Date(raw);
          }
          if (d && !Number.isNaN(d.getTime())) return d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
          return String(raw || '').slice(11, 16) || '-';
        };
        const volumes = candles.map(c => Number(c.volume || 0));
        const maxVol = Math.max(...volumes, 1);
        const grid = [0, 0.25, 0.5, 0.75, 1].map(r => {
          const price = max - span * r;
          const gy = y(price);
          return `<line class="chart-grid" x1="${padL}" x2="${width - padR}" y1="${gy.toFixed(1)}" y2="${gy.toFixed(1)}"></line><text class="chart-price-axis chart-axis-label" x="${width - padR + 10}" y="${(gy + 4).toFixed(1)}">${fmt(price)}</text>`;
        }).join('');
        const timeIndexes = [0, 0.25, 0.5, 0.75, 1].map(r => Math.min(candles.length - 1, Math.max(0, Math.round((candles.length - 1) * r))));
        const uniqueTimeIndexes = [...new Set(timeIndexes)];
        const timeAxisY = priceHeight + volumeHeight + 18;
        const timeAxis = uniqueTimeIndexes.map(idx => {
          const tx = x(idx);
          return `<line class="chart-time-tick" x1="${tx.toFixed(1)}" x2="${tx.toFixed(1)}" y1="${priceHeight + volumeHeight + 2}" y2="${priceHeight + volumeHeight + 7}"></line><text class="chart-time-label" x="${tx.toFixed(1)}" y="${timeAxisY}">${fmtTime(candles[idx])}</text>`;
        }).join('') + `<text class="chart-axis-title chart-time-title" x="${padL}" y="${priceHeight + volumeHeight + 27}">시간축</text>`;
        const bodies = candles.map((c, idx) => {
          const open = Number(c.open ?? c.close);
          const close = Number(c.close ?? c.open);
          const high = Number(c.high ?? Math.max(open, close));
          const low = Number(c.low ?? Math.min(open, close));
          const cx = x(idx);
          const up = close >= open;
          const cls = up ? 'up' : 'down';
          const top = Math.min(y(open), y(close));
          const h = Math.max(Math.abs(y(open) - y(close)), 1.5);
          const volH = Math.max(1, (Number(c.volume || 0) / maxVol) * volumeHeight);
          return `<g class="chart-candle ${cls}"><line x1="${cx.toFixed(1)}" x2="${cx.toFixed(1)}" y1="${y(high).toFixed(1)}" y2="${y(low).toFixed(1)}"></line><rect x="${(cx - candleW / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${candleW.toFixed(1)}" height="${h.toFixed(1)}" rx="1"></rect><rect class="chart-volume" x="${(cx - candleW / 2).toFixed(1)}" y="${(priceHeight + volumeHeight - volH).toFixed(1)}" width="${candleW.toFixed(1)}" height="${volH.toFixed(1)}"></rect></g>`;
        }).join('');
        const levelLine = (value, cls, label) => {
          const n = Number(value);
          if (!Number.isFinite(n) || n < min || n > max) return '';
          const ly = y(n);
          return `<line class="chart-level ${cls}" x1="${padL}" x2="${width - padR}" y1="${ly.toFixed(1)}" y2="${ly.toFixed(1)}"></line><text class="chart-level-label ${cls}" x="${padL + 6}" y="${(ly - 4).toFixed(1)}">${label} ${fmt(n)}</text>`;
        };
        const levels = data.levels || {};
        const levelMarkup = [levelLine(levels.entry_low, 'entry', '진입'), levelLine(levels.take_profit_1, 'target', '목표'), levelLine(levels.stop_loss, 'stop', '손절')].join('');
        const last = candles[candles.length - 1];
        const ticker = data.ticker || '암호화폐';
        canvas.innerHTML = `<div class="crypto-candlestick-chart"><svg viewBox="0 0 ${width} ${priceHeight + volumeHeight + timeAxisHeight}" role="img" aria-label="${ticker} 캔들 가격축 시간축 차트">${grid}${bodies}${levelMarkup}${timeAxis}<text class="chart-axis-title" x="${width - padR + 10}" y="16">가격축</text></svg><div class="chart-caption"><strong>${ticker}</strong><span>현재 ${fmt(last.close)} · 고가 ${fmt(max)} · 저가 ${fmt(min)}</span></div></div>`;
      } catch (_) {}
    }
  }
  refreshChart();
  setInterval(refreshChart, 60000);
})();
