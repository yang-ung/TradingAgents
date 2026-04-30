(() => {
  const payloadNode = document.getElementById('chart-payload');
  const container = document.getElementById('professional-price-chart');
  if (!payloadNode || !container) return;

  let payload;
  try {
    payload = JSON.parse(payloadNode.textContent || '{}');
  } catch (_error) {
    container.dataset.chartState = 'payload-error';
    return;
  }

  const library = window.LightweightCharts;
  const candles = Array.isArray(payload.candles) ? payload.candles : [];
  if (!library || !candles.length) {
    container.dataset.chartState = 'fallback';
    return;
  }

  const addSeries = (chart, legacyName, modernSeries, options) => {
    if (typeof chart[legacyName] === 'function') return chart[legacyName](options);
    if (modernSeries && typeof chart.addSeries === 'function') return chart.addSeries(modernSeries, options);
    return null;
  };

  try {
    const chart = library.createChart(container, {
      autoSize: true,
      height: 430,
      layout: {
        background: { type: 'solid', color: '#0b0d10' },
        textColor: '#aab2c0',
        fontFamily: 'Inter, system-ui, -apple-system, BlinkMacSystemFont, sans-serif',
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.045)' },
        horzLines: { color: 'rgba(255,255,255,0.045)' },
      },
      crosshair: {
        mode: library.CrosshairMode?.Normal ?? 0,
        vertLine: { color: 'rgba(139,140,255,0.45)', labelBackgroundColor: '#5e6ad2' },
        horzLine: { color: 'rgba(139,140,255,0.45)', labelBackgroundColor: '#5e6ad2' },
      },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.10)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.10)', timeVisible: true, secondsVisible: false },
    });

    const candleSeries = addSeries(chart, 'addCandlestickSeries', library.CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    });
    if (!candleSeries) throw new Error('candlestick_series_unavailable');
    candleSeries.setData(candles);

    const maConfig = [
      ['ma5', '#facc15', 'MA5'],
      ['ma20', '#60a5fa', 'MA20'],
      ['ma60', '#c084fc', 'MA60'],
    ];
    for (const [key, color, title] of maConfig) {
      const data = payload.moving_averages?.[key];
      if (!Array.isArray(data) || !data.length) continue;
      const series = addSeries(chart, 'addLineSeries', library.LineSeries, {
        color,
        lineWidth: key === 'ma5' ? 2 : 1,
        title,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      if (series) series.setData(data);
    }

    const volume = Array.isArray(payload.volume) ? payload.volume : [];
    if (volume.length) {
      const volumeSeries = addSeries(chart, 'addHistogramSeries', library.HistogramSeries, {
        priceFormat: { type: 'volume' },
        priceScaleId: '',
        scaleMargins: { top: 0.82, bottom: 0 },
        lastValueVisible: false,
        priceLineVisible: false,
      });
      if (volumeSeries) volumeSeries.setData(volume);
    }

    chart.timeScale().fitContent();
    container.dataset.chartState = 'rendered';
    document.querySelector('.chart-fallback')?.classList.add('is-hidden');
  } catch (error) {
    container.dataset.chartState = 'fallback';
    container.dataset.chartError = error?.message || 'render_failed';
  }
})();
