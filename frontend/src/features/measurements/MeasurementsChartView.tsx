import { useId, useMemo, useState } from 'react'
import { EmptyState } from '../../components/EmptyState'
import { StateBanner } from '../../components/StateBanner'
import { StatusBadge } from '../../components/StatusBadge'
import { EChartsCanvas } from './EChartsCanvas'
import type { ChartOption } from './EChartsCanvas'
import {
  CHART_BATCH_MAX_PAGES,
  CHART_PAGE_SIZE_OPTIONS,
  DEFAULT_SELECTED_METRICS,
  METRIC_CHART_COLORS,
  buildMetricSeries,
  computeLoadedTimeSpan,
  formatChartAxisTimeVi,
  formatMetricTooltipHtml,
} from './chartData'
import type { ChartDatum, ChartPageSize, LoadedTimeSpan, MetricSeriesModel } from './chartData'
import { METRIC_META, formatUtcToVi } from './format'
import type { ChartSliceState } from './store'
import type { MetricCode } from './types'

type MeasurementsChartViewProps = {
  chart: ChartSliceState
  range: { from: string; to: string } | null
  onLoadMore: () => void
  onLoadBatch: () => void
  onCancelBatch: () => void
  onReloadFresh: () => void
  onChangePageSize: (pageSize: ChartPageSize) => void
  onSwitchUnknown: () => void
}

function buildPanelOption(
  series: MetricSeriesModel,
  loadedSpan: LoadedTimeSpan | null,
  showSliderZoom: boolean,
): ChartOption {
  const spanMs = loadedSpan?.spanMs ?? 0
  const singleTimestamp = loadedSpan !== null && loadedSpan.firstMs === loadedSpan.lastMs
  const xMin = loadedSpan
    ? singleTimestamp
      ? loadedSpan.firstMs - 60_000
      : loadedSpan.firstMs
    : undefined
  const xMax = loadedSpan
    ? singleTimestamp
      ? loadedSpan.lastMs + 60_000
      : loadedSpan.lastMs
    : undefined

  const denseSeries = series.data.length > 80

  return {
    animation: false,
    grid: {
      top: 24,
      right: 20,
      bottom: showSliderZoom ? 66 : 34,
      left: 64,
    },
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: '#ffffff',
      borderColor: '#dce6dc',
      borderWidth: 1,
      padding: [8, 10],
      textStyle: {
        color: '#18362b',
        fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
      },
      axisPointer: {
        type: 'line',
        lineStyle: {
          color: '#7e9587',
          width: 1,
          type: 'dashed',
        },
      },
      formatter(params: unknown) {
        const list = Array.isArray(params) ? params : [params]
        const matched: ChartDatum[] = []
        for (const entry of list) {
          if (entry && typeof entry === 'object' && 'data' in entry) {
            const rawData = (entry as { data?: { datum?: ChartDatum } }).data
            if (rawData?.datum) {
              matched.push(rawData.datum)
            }
          }
        }
        return formatMetricTooltipHtml(series, matched)
      },
    },
    xAxis: {
      type: 'time',
      min: xMin,
      max: xMax,
      axisLine: { lineStyle: { color: '#dce6dc' } },
      axisTick: { lineStyle: { color: '#7e9587' } },
      splitLine: { show: true, lineStyle: { color: '#eef3ee' } },
      axisLabel: {
        color: '#53665c',
        fontSize: 11,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
        hideOverlap: true,
        formatter(value: number) {
          return formatChartAxisTimeVi(value, spanMs)
        },
      },
    },
    yAxis: {
      type: 'value',
      scale: true,
      name: series.unit,
      nameTextStyle: {
        color: '#53665c',
        fontSize: 11,
        align: 'left',
        padding: [0, 0, 0, -24],
      },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: '#eef3ee' } },
      axisLabel: {
        color: '#53665c',
        fontSize: 11,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      },
    },
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: 0,
        filterMode: 'none',
      },
      ...(showSliderZoom
        ? [
            {
              type: 'slider' as const,
              xAxisIndex: 0,
              height: 22,
              bottom: 6,
              borderColor: '#dce6dc',
              fillerColor: 'rgba(40, 87, 62, 0.14)',
              handleStyle: {
                color: '#28573e',
                borderColor: '#28573e',
              },
              labelFormatter(value: number) {
                return formatChartAxisTimeVi(value, spanMs).replace('\n', ' ')
              },
              filterMode: 'none' as const,
            },
          ]
        : []),
    ],
    series: [
      {
        name: series.label,
        type: 'line',
        connectNulls: false,
        showSymbol: true,
        showAllSymbol: true,
        smooth: false,
        lineStyle: {
          color: series.color,
          width: 2,
        },
        itemStyle: {
          color: series.color,
        },
        emphasis: {
          focus: 'none',
          scale: true,
        },
        data: series.data.map(datum => ({
          value: datum.value,
          datum,
          symbolSize:
            datum.value[1] === null ? 0 : datum.isIsolated ? 7 : denseSeries ? 0 : 5,
        })),
      },
    ],
  }
}

export default function MeasurementsChartView({
  chart,
  range,
  onLoadMore,
  onLoadBatch,
  onCancelBatch,
  onReloadFresh,
  onChangePageSize,
  onSwitchUnknown,
}: MeasurementsChartViewProps) {
  const reactId = useId()
  const groupId = `meas-chart-${reactId}`
  const [selectedMetrics, setSelectedMetrics] = useState<MetricCode[]>(() => [
    ...DEFAULT_SELECTED_METRICS,
  ])
  const [resetZoomKey, setResetZoomKey] = useState(0)

  const loadedSpan = useMemo(() => computeLoadedTimeSpan(chart.points), [chart.points])

  const seriesByMetric = useMemo(() => {
    const map = new Map<MetricCode, MetricSeriesModel>()
    for (const meta of METRIC_META) {
      map.set(meta.code, buildMetricSeries(chart.points, meta.code))
    }
    return map
  }, [chart.points])

  const orderedSelected = useMemo(
    () => METRIC_META.filter(meta => selectedMetrics.includes(meta.code)).map(meta => meta.code),
    [selectedMetrics],
  )

  const allSelected = orderedSelected.length === METRIC_META.length

  function toggleMetric(code: MetricCode) {
    setSelectedMetrics(prev =>
      prev.includes(code) ? prev.filter(item => item !== code) : [...prev, code],
    )
  }

  return (
    <div className="stack">
      <div className="chart-toolbar" aria-label="Điều khiển biểu đồ chuỗi thời gian">
        <div className="chart-metric-chips" role="group" aria-label="Chọn đại lượng hiển thị">
          {METRIC_META.map(meta => {
            const checked = selectedMetrics.includes(meta.code)
            const unit = seriesByMetric.get(meta.code)?.unit ?? meta.unit
            return (
              <label
                key={meta.code}
                className={`chart-metric-chip${checked ? ' active' : ''}`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleMetric(meta.code)}
                />
                <span
                  className="chart-swatch"
                  style={{ backgroundColor: METRIC_CHART_COLORS[meta.code] }}
                  aria-hidden="true"
                />
                <span>{meta.label}</span>
                <small className="muted">({unit})</small>
              </label>
            )
          })}
        </div>

        <div className="chart-toolbar-actions">
          <div className="chart-pagesize-control">
            <label htmlFor="chart-page-size-select">Số điểm / lần tải</label>
            <select
              id="chart-page-size-select"
              value={String(chart.pageSize)}
              disabled={chart.loading || chart.loadingMore || chart.loadingBatch}
              onChange={event => {
                const parsed = Number(event.target.value) as ChartPageSize
                if (CHART_PAGE_SIZE_OPTIONS.includes(parsed)) {
                  onChangePageSize(parsed)
                }
              }}
            >
              {CHART_PAGE_SIZE_OPTIONS.map(size => (
                <option key={size} value={size}>
                  {size} điểm
                </option>
              ))}
            </select>
          </div>

          <button
            type="button"
            className="btn-secondary btn-sm"
            onClick={() =>
              setSelectedMetrics(
                allSelected ? [...DEFAULT_SELECTED_METRICS] : METRIC_META.map(m => m.code),
              )
            }
          >
            {allSelected ? 'Mặc định (2)' : 'Chọn tất cả (5)'}
          </button>

          <button
            type="button"
            className="btn-secondary btn-sm"
            disabled={chart.points.length === 0}
            onClick={() => setResetZoomKey(prev => prev + 1)}
          >
            Đặt lại zoom
          </button>
        </div>
      </div>

      {chart.error && (
        <StateBanner
          tone="err"
          message={
            chart.showingPrevious
              ? `Không tải được dữ liệu biểu đồ: ${chart.error} (đang giữ dữ liệu lần tải trước)`
              : `Không tải được dữ liệu biểu đồ: ${chart.error}`
          }
          action={
            chart.errorCode === 'CURSOR_INVALID' ? (
              <button type="button" className="btn-secondary btn-sm" onClick={onReloadFresh}>
                Tải snapshot mới
              </button>
            ) : undefined
          }
        />
      )}

      {chart.state === 'AVAILABLE' && chart.nextCursor !== null && (
        <StateBanner
          tone="warn"
          message={`Chưa tải hết dữ liệu trong khoảng lọc (đã tải ${chart.points.length} điểm, không downsample). Biểu đồ hiện mới hiển thị phần đầu chuỗi thời gian theo thứ tự tăng dần.`}
          action={
            <div className="row">
              {chart.loadingBatch ? (
                <button
                  type="button"
                  className="btn-secondary btn-sm"
                  onClick={onCancelBatch}
                >
                  Dừng tải
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    disabled={chart.loadingMore}
                    onClick={onLoadMore}
                  >
                    {chart.loadingMore ? 'Đang tải…' : `Tải thêm (+${chart.pageSize} điểm)`}
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    disabled={chart.loadingMore}
                    onClick={onLoadBatch}
                  >
                    Tải tiếp (+tối đa {chart.pageSize * CHART_BATCH_MAX_PAGES} điểm)
                  </button>
                </>
              )}
            </div>
          }
        />
      )}

      {chart.loading && chart.state === null ? (
        <p className="muted" role="status">
          Đang tải dữ liệu biểu đồ chuỗi thời gian…
        </p>
      ) : chart.state === 'NO_DATA' ? (
        <EmptyState
          title="Chưa có dữ liệu"
          description="Node chưa có bản ghi nào trong cơ sở dữ liệu."
        />
      ) : chart.state === 'NO_EVENT_TIME' ? (
        <EmptyState
          title="Chưa có dữ liệu xác định giờ đo"
          description="Tất cả bản ghi hiện có đều thiếu giờ đo (UNKNOWN) và không thể vẽ lên trục thời gian."
          action={
            <button type="button" className="btn-secondary btn-sm" onClick={onSwitchUnknown}>
              Mở nhóm Chưa xác định giờ đo (UNKNOWN)
            </button>
          }
        />
      ) : chart.state === 'EMPTY_RANGE' ? (
        <EmptyState
          title="Không có bản ghi trong khoảng lọc"
          description="Hãy mở rộng khoảng Từ/Đến (UTC+07:00) hoặc kiểm tra nhóm UNKNOWN."
        />
      ) : chart.state === 'AVAILABLE' ? (
        <>
          <div className="row-between">
            <small className="tabular muted">
              Đã tải <strong>{chart.points.length}</strong> điểm (không downsample · ngắt nét tại
              điểm lỗi · giữ nguyên giá trị 0)
              {loadedSpan
                ? ` · Dải điểm đã tải: ${formatUtcToVi(loadedSpan.firstIso)} → ${formatUtcToVi(loadedSpan.lastIso)}`
                : ''}
            </small>
            <div className="row">
              {chart.nextCursor === null ? (
                <StatusBadge tone="ok" label="Đã tải đủ khoảng lọc" />
              ) : (
                <StatusBadge
                  tone="warn"
                  label={chart.loadingBatch ? 'Đang tải tiếp…' : 'Còn dữ liệu chưa tải'}
                />
              )}
            </div>
          </div>

          {range && (
            <small className="muted">
              Mẹo: Cuộn chuột hoặc kéo thanh trượt bên dưới để phóng to / di chuyển theo thời gian
              (UTC+07:00); rê chuột vào biểu đồ để đối chiếu đồng bộ giữa các đại lượng và xem mã
              Boot / Seq.
            </small>
          )}

          {orderedSelected.length === 0 ? (
            <EmptyState
              title="Chưa chọn đại lượng nào"
              description="Hãy tích chọn ít nhất một đại lượng ở thanh công cụ phía trên để hiển thị biểu đồ."
            />
          ) : (
            <div className="chart-panel-list">
              {orderedSelected.map((code, index) => {
                const series = seriesByMetric.get(code)
                if (!series) return null
                const isLast = index === orderedSelected.length - 1
                const option = buildPanelOption(series, loadedSpan, isLast)
                return (
                  <article key={code} className="chart-panel" aria-label={`Biểu đồ ${series.label}`}>
                    <div className="chart-panel-head">
                      <div className="row">
                        <span
                          className="chart-swatch"
                          style={{ backgroundColor: series.color }}
                          aria-hidden="true"
                        />
                        <strong>{series.label}</strong>
                        <small className="muted">({series.unit})</small>
                      </div>
                      <div className="row">
                        <small className="tabular muted">
                          {series.validCount} hợp lệ
                          {series.errorCount > 0 ? ` · ${series.errorCount} lỗi (ngắt nét)` : ''}
                        </small>
                        {series.minValue !== null && series.maxValue !== null && (
                          <small className="mono tabular muted">
                            Min {series.minValue} · Max {series.maxValue} {series.unit}
                          </small>
                        )}
                      </div>
                    </div>

                    {series.validCount === 0 ? (
                      <div className="chart-panel-empty">
                        <EmptyState
                          title={`Không có điểm hợp lệ cho ${series.label}`}
                          description={`Toàn bộ ${series.errorCount} điểm đã tải của đại lượng này đều gặp lỗi cảm biến (READ_ERROR / INVALID_VALUE) — không quy đổi về 0.`}
                        />
                      </div>
                    ) : (
                      <EChartsCanvas
                        option={option}
                        groupId={groupId}
                        resetZoomKey={resetZoomKey}
                        height={isLast ? 256 : 216}
                        ariaLabel={`Biểu đồ chuỗi thời gian ${series.label} (${series.unit}), ${series.validCount} điểm hợp lệ`}
                      />
                    )}
                  </article>
                )
              })}
            </div>
          )}
        </>
      ) : null}
    </div>
  )
}
