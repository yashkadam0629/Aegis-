import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, ScatterChart } from 'echarts/charts'
import { GridComponent, LegendComponent, MarkLineComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([BarChart, LineChart, ScatterChart, GridComponent, TooltipComponent, LegendComponent, TitleComponent, MarkLineComponent, CanvasRenderer])

export const PALETTE = ['#2457c5', '#c2570c', '#3f7d58', '#7a5aa6', '#a16207', '#0e7490', '#b42318', '#657089']
const AXIS = { axisLine: { lineStyle: { color: '#b8bfcc' } }, axisLabel: { color: '#4b556b', fontSize: 11 }, splitLine: { lineStyle: { color: '#eceef2' } }, nameTextStyle: { color: '#4b556b', fontSize: 11 } }

export function axis(extra: Record<string, unknown> = {}) {
  return { ...AXIS, ...extra }
}

export function EChart({ option, height = 260, ariaLabel }: { option: Record<string, unknown>; height?: number; ariaLabel: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)
  useEffect(() => {
    if (!ref.current) return
    let c: echarts.ECharts | null = null
    try {
      c = echarts.init(ref.current, undefined, { renderer: 'canvas' })
    } catch {
      return // canvas unavailable (e.g. test environment)
    }
    chart.current = c
    const ro = new ResizeObserver(() => c?.resize())
    ro.observe(ref.current)
    return () => {
      ro.disconnect()
      c?.dispose()
      chart.current = null
    }
  }, [])
  useEffect(() => {
    chart.current?.setOption({ color: PALETTE, animation: false, textStyle: { fontFamily: 'inherit' }, ...option }, true)
  }, [option])
  return <div ref={ref} style={{ height }} role="img" aria-label={ariaLabel} />
}
