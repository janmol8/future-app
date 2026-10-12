'use client'

import React from 'react'
import { useExtendedHours } from '@/hooks/useExtendedHours'

interface HeaderProps {
  symbol: string
  price: number
  change: number
  changePercent: number
}

export const Header: React.FC<HeaderProps> = ({ symbol, price, change, changePercent }) => {
  const extHours = useExtendedHours(symbol)

  return (
    <div className="flex items-center justify-between px-6 py-4 bg-[#131722] border-b border-[#2a2e39]">
      <div className="flex items-center gap-4">
        <h1 className="text-xl font-bold text-white">{symbol}</h1>
        <div className="flex items-center gap-2">
          <span className="text-2xl font-mono font-bold text-white">
            ₹{price.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
          </span>
          <span className={`text-sm font-mono ${change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {change >= 0 ? '+' : ''}{change.toFixed(2)} ({changePercent.toFixed(2)}%)
          </span>
        </div>

        {extHours.session === 'PRE_MARKET' && (
          <div className="flex items-center gap-1.5 ml-3">
            <span className="text-[10px] px-2 py-0.5 rounded-full
              bg-amber-500/15 text-amber-400 border border-amber-500/25
              font-medium tracking-wide">
              PRE-MKT
            </span>
            {extHours.preMarketPrice && (
              <>
                <span className="font-mono text-sm text-white font-bold">
                  ₹{extHours.preMarketPrice.toLocaleString('en-IN', {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2
                  })}
                </span>
                <span className={`text-xs font-mono ${
                  (extHours.preMarketChange ?? 0) >= 0
                    ? 'text-green-400'
                    : 'text-red-400'
                }`}>
                  {(extHours.preMarketChange ?? 0) >= 0 ? '+' : ''}
                  {extHours.preMarketChange?.toFixed(2)}%
                </span>
              </>
            )}
          </div>
        )}

        {extHours.session === 'POST_MARKET' && extHours.postMarketPrice && (
          <div className="flex items-center gap-1.5 ml-3">
            <span className="text-[10px] px-2 py-0.5 rounded-full
              bg-violet-500/15 text-violet-400 border border-violet-500/25
              font-medium tracking-wide">
              POST-MKT
            </span>
            <span className="font-mono text-sm text-white font-bold">
              ₹{extHours.postMarketPrice.toLocaleString('en-IN', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
              })}
            </span>
            <span className={`text-xs font-mono ${
              (extHours.postMarketChange ?? 0) >= 0
                ? 'text-green-400'
                : 'text-red-400'
            }`}>
              {(extHours.postMarketChange ?? 0) >= 0 ? '+' : ''}
              {extHours.postMarketChange?.toFixed(2)}%
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
