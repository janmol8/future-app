'use client'

import React, { useEffect, useRef } from 'react'
import { createChart, ColorType, LineStyle, IChartApi, ISeriesApi } from 'lightweight-charts'
import { useExtendedHours } from '@/hooks/useExtendedHours'

interface ChartProps {
  symbol: string
  data: any[]
}

export const Chart: React.FC<ChartProps> = ({ symbol, data }) => {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const extLineRef = useRef<any>(null)
  
  const extHours = useExtendedHours(symbol)

  useEffect(() => {
    if (!chartContainerRef.current) return

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#131722' },
        textColor: '#d1d4dc',
      },
      width: chartContainerRef.current.clientWidth,
      height: 500,
      grid: {
        vertLines: { color: 'rgba(42, 46, 57, 0)' },
        horzLines: { color: 'rgba(42, 46, 57, 0.6)' },
      },
    })

    const series = chart.addCandlestickSeries({
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    })

    series.setData(data)
    
    chartRef.current = chart
    seriesRef.current = series

    const handleResize = () => {
      chart.applyOptions({ width: chartContainerRef.current?.clientWidth })
    }

    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
    }
  }, [data])

  // Extended Hours Line logic
  useEffect(() => {
    const series = seriesRef.current
    if (!series) return

    // Always remove previous extended hours line first
    if (extLineRef.current) {
      try {
        series.removePriceLine(extLineRef.current)
      } catch {}
      extLineRef.current = null
    }

    const { session, preMarketPrice, postMarketPrice,
            previousClose, openPrice } = extHours

    // PRE-MARKET: amber dashed line at IEX indicative price
    // Falls back to previousClose if IEP not yet available
    if (session === 'PRE_MARKET') {
      const price = preMarketPrice ?? previousClose
      if (price) {
        extLineRef.current = series.createPriceLine({
          price,
          color:            '#f59e0b',
          lineWidth:        1,
          lineStyle:        LineStyle.Dashed,
          axisLabelVisible: true,
          title:            preMarketPrice ? 'Pre-Mkt' : 'Prev Close',
        })
      }
    }

    // REGULAR SESSION: green dashed line at open price
    else if (session === 'REGULAR' && openPrice) {
      extLineRef.current = series.createPriceLine({
        price:            openPrice,
        color:            '#22c55e',
        lineWidth:        1,
        lineStyle:        LineStyle.Dashed,
        axisLabelVisible: true,
        title:            'Open',
      })
    }

    // POST-MARKET: violet dashed line at closing price
    else if (session === 'POST_MARKET' && postMarketPrice) {
      extLineRef.current = series.createPriceLine({
        price:            postMarketPrice,
        color:            '#a78bfa',
        lineWidth:        1,
        lineStyle:        LineStyle.Dashed,
        axisLabelVisible: true,
        title:            'Post-Mkt',
      })
    }

    return () => {
      if (extLineRef.current && series) {
        try { series.removePriceLine(extLineRef.current) } catch {}
      }
    }
  }, [
    extHours.session,
    extHours.preMarketPrice,
    extHours.postMarketPrice,
    extHours.openPrice,
  ])

  return <div ref={chartContainerRef} style={{ width: '100%', height: '500px' }} />
}
