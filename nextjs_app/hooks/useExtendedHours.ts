import { useState, useEffect, useRef, useCallback } from 'react'

export type SessionType = 
  'PRE_MARKET' | 'REGULAR' | 'POST_MARKET' | 'HOLIDAY' | 'CLOSED'

export interface ExtendedHoursData {
  session:          SessionType
  holidayName:      string | null
  preMarketPrice:   number | null
  preMarketChange:  number | null
  postMarketPrice:  number | null
  postMarketChange: number | null
  previousClose:    number | null
  openPrice:        number | null
  isLoading:        boolean
  error:            string | null
  lastFetched:      Date | null
}

export function useExtendedHours(symbol: string): ExtendedHoursData {
  const [data, setData] = useState<ExtendedHoursData>({
    session:          'CLOSED',
    holidayName:      null,
    preMarketPrice:   null,
    preMarketChange:  null,
    postMarketPrice:  null,
    postMarketChange: null,
    previousClose:    null,
    openPrice:        null,
    isLoading:        false,
    error:            null,
    lastFetched:      null,
  })

  const timerRef = useRef<NodeJS.Timeout | null>(null)

  const fetchData = useCallback(async () => {
    if (!symbol) return
    setData(prev => ({ ...prev, isLoading: true, error: null }))

    try {
      const res = await fetch(`/api/extended-hours/${symbol}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()

      setData({
        session:          json.session,
        holidayName:      json.holidayName ?? null,
        preMarketPrice:   json.preMarketPrice,
        preMarketChange:  json.preMarketChange,
        postMarketPrice:  json.postMarketPrice,
        postMarketChange: json.postMarketChange,
        previousClose:    json.previousClose,
        openPrice:        json.openPrice,
        isLoading:        false,
        error:            null,
        lastFetched:      new Date(),
      })

      // Schedule next poll based on session
      const interval = json.session === 'PRE_MARKET' ? 15000 : 30000
      timerRef.current = setTimeout(fetchData, interval)

    } catch (err: any) {
      setData(prev => ({
        ...prev,
        isLoading: false,
        error: err.message,
      }))
      // Retry after 30s on error
      timerRef.current = setTimeout(fetchData, 30000)
    }
  }, [symbol])

  useEffect(() => {
    fetchData()
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [fetchData])

  return data
}
