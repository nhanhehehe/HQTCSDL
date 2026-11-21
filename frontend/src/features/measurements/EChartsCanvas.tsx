import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import type { LineSeriesOption } from 'echarts/charts'
import {
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  TooltipComponent,
} from 'echarts/components'
import type {
  AxisPointerComponentOption,
  DataZoomComponentOption,
  GridComponentOption,
  TooltipComponentOption,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  AxisPointerComponent,
  CanvasRenderer,
])

export type ChartOption = echarts.ComposeOption<
  | LineSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | DataZoomComponentOption
  | AxisPointerComponentOption
>

type EChartsCanvasProps = {
  option: ChartOption
  groupId: string
  resetZoomKey: number
  height: number
  ariaLabel: string
}

export function EChartsCanvas({
  option,
  groupId,
  resetZoomKey,
  height,
  ariaLabel,
}: EChartsCanvasProps) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    const el = hostRef.current
    if (!el) return
    const instance = echarts.init(el, undefined, { renderer: 'canvas' })
    instance.group = groupId
    echarts.connect(groupId)
    chartRef.current = instance

    const observer =
      typeof ResizeObserver !== 'undefined'
        ? new ResizeObserver(() => {
            instance.resize()
          })
        : null
    observer?.observe(el)

    return () => {
      observer?.disconnect()
      instance.dispose()
      chartRef.current = null
    }
  }, [groupId])

  useEffect(() => {
    const instance = chartRef.current
    if (!instance) return
    instance.setOption(option, { notMerge: true, lazyUpdate: false })
  }, [option])

  useEffect(() => {
    const instance = chartRef.current
    if (!instance || resetZoomKey === 0) return
    instance.dispatchAction({
      type: 'dataZoom',
      dataZoomIndex: 0,
      start: 0,
      end: 100,
    })
  }, [resetZoomKey])

  return (
    <div
      ref={hostRef}
      className="chart-canvas-host"
      style={{ height: `${height}px` }}
      role="img"
      aria-label={ariaLabel}
    />
  )
}
