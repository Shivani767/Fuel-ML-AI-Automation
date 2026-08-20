import { useMemo, useRef, useState } from 'react'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import zoomPlugin from 'chartjs-plugin-zoom'
import 'hammerjs'

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
  zoomPlugin
)

// FuelArk Device Data legend — same params & palette as FuelArk / legacy live graph.
const SERIES = [
  {
    key: 'fuelLevelEDC',
    name: 'Fuel Level EDC',
    color: '#10b981',
    fill: 'rgba(16, 185, 129, 0.18)',
    yAxisID: 'y',
    hidden: false,
    width: 2.5,
    showPoints: true,
    fillArea: true,
  },
  {
    key: 'movement',
    name: 'Movement',
    color: '#f59e0b',
    yAxisID: 'y1',
    hidden: true,
    width: 1.5,
  },
  {
    key: 'speed',
    name: 'Speed',
    color: '#3b82f6',
    yAxisID: 'y1',
    hidden: true,
    width: 1.5,
  },
  {
    key: 'powerEx',
    name: 'Power Ex',
    color: '#ef4444',
    yAxisID: 'y1',
    hidden: true,
    width: 1.25,
  },
  {
    key: 'powerIn',
    name: 'Power In',
    color: '#8b5cf6',
    yAxisID: 'y1',
    hidden: true,
    width: 1.25,
  },
  // Liters available via legend (hidden) — FuelArk primary view is EDC.
  {
    key: 'fuelLevel',
    name: 'Fuel Level (L)',
    color: '#2563eb',
    fill: 'rgba(37, 99, 235, 0.12)',
    yAxisID: 'y',
    hidden: true,
    width: 2,
    fillArea: true,
  },
]

const IGNITION_LEGEND = {
  name: 'Ignition',
  color: '#22c55e',
  fill: 'rgba(16, 185, 129, 0.32)',
}

const IGNITION_ON_FILL = 'rgba(16, 185, 129, 0.28)'
const IGNITION_OFF_FILL = '#ffffff'

// Same palette the alert tables use, so a red row and a red band read as one thing.
const THEFT_COLOR = '#ef4444'
const FILL_COLOR = '#10b981'
const SUPPRESSED_COLOR = '#94a3b8'
// An event whose timestamp falls this far outside the loaded window belongs to a
// different range and is not drawn, rather than being clamped onto the edge.
const EDGE_TOLERANCE_MS = 30 * 60 * 1000

const markerColor = (m) =>
  m.suppressed ? SUPPRESSED_COLOR : m.theft ? THEFT_COLOR : FILL_COLOR

function rgba(hex, alpha) {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

/** Event times arrive as "YYYY-MM-DD HH:MM:SS", series times as ISO with a T. */
function toMs(t) {
  if (!t) return NaN
  return new Date(String(t).replace(' ', 'T')).getTime()
}

/** Index of the reading closest in time to `ms`. Series is sorted ascending. */
function nearestIndex(times, ms) {
  let best = -1
  let bestGap = Infinity
  for (let i = 0; i < times.length; i += 1) {
    const gap = Math.abs(times[i] - ms)
    if (gap < bestGap) {
      bestGap = gap
      best = i
    } else if (times[i] > ms) {
      break // sorted: gaps only grow from here
    }
  }
  return best
}

/**
 * Place each detected fill/theft on the x axis of the loaded series.
 * A drain is a span, not an instant, so both ends are resolved and the band
 * between them is what gets shaded.
 */
function buildMarkers(rows, events) {
  if (!rows.length || !events?.length) return []
  const times = rows.map((p) => toMs(p.time))
  const first = times[0]
  const last = times[times.length - 1]

  const markers = []
  for (const e of events) {
    const startMs = toMs(e.eventTime)
    if (!Number.isFinite(startMs)) continue
    if (startMs < first - EDGE_TOLERANCE_MS || startMs > last + EDGE_TOLERANCE_MS) continue

    const startIdx = nearestIndex(times, startMs)
    if (startIdx < 0) continue
    const endMs = toMs(e.endTime)
    const endIdx = Number.isFinite(endMs) ? nearestIndex(times, endMs) : startIdx
    const row = rows[startIdx] || {}

    markers.push({
      startIdx,
      endIdx: Math.max(startIdx, endIdx),
      theft: /theft/i.test(e.eventType || ''),
      suppressed: Boolean(e.suppressedReason),
      volume: Number(e.volume || 0),
      confidence: e.confidence,
      reason: e.suppressedReason,
      decision: e.decision,
      eventTime: e.eventTime,
      // Pin markers to the visible FuelArk primary (EDC), fall back to liters.
      value: Number(
        row.fuelLevelEDC ?? row.fuelLevel ?? row.fuelInLiter ?? 0
      ),
    })
  }
  return markers.sort((a, b) => a.startIdx - b.startIdx)
}

/**
 * Draw the events over the trace: a shaded band for the span, a rule at the
 * moment it started, and the volume tagged along the top. Drawn after the
 * datasets so nothing hides behind the fuel line.
 */
const fuelEventMarkerPlugin = {
  id: 'fuelEventMarkers',
  afterDatasetsDraw(chart) {
    const { ctx, chartArea, scales } = chart
    const markers = chart.options.plugins?.fuelEventMarkers?.markers || []
    const xScale = scales.x
    if (!chartArea || !xScale || !markers.length) return

    ctx.save()
    ctx.font = '600 11px system-ui, -apple-system, sans-serif'
    ctx.textBaseline = 'middle'

    // Tags are stacked into the first row that is free at their x position.
    // A busy day puts a dozen events within an hour of each other, and a fixed
    // stagger just overprints them; markers arrive sorted so greedy packing holds.
    const placed = []
    const LABEL_ROWS = 6
    const ROW_HEIGHT = 17

    markers.forEach((m) => {
      const color = markerColor(m)
      const x0 = xScale.getPixelForValue(m.startIdx)
      const x1 = xScale.getPixelForValue(m.endIdx)

      // Span of the drain/fill itself.
      const left = Math.max(chartArea.left, Math.min(x0, x1))
      const right = Math.min(chartArea.right, Math.max(x0, x1))
      if (right - left > 1) {
        ctx.fillStyle = rgba(color, 0.16)
        ctx.fillRect(left, chartArea.top, right - left, chartArea.height)
      }

      // The moment it began.
      if (x0 >= chartArea.left && x0 <= chartArea.right) {
        ctx.strokeStyle = color
        ctx.lineWidth = m.suppressed ? 1 : 1.75
        ctx.setLineDash(m.suppressed ? [4, 3] : [])
        ctx.beginPath()
        ctx.moveTo(x0, chartArea.top)
        ctx.lineTo(x0, chartArea.bottom)
        ctx.stroke()
        ctx.setLineDash([])

        // Volume tag. When every row at this x is taken the label is dropped
        // rather than overprinted -- the rule and the tooltip still carry it.
        const label = `${m.theft ? '▼ −' : '▲ +'}${m.volume.toFixed(1)} L`
        const w = ctx.measureText(label).width + 10
        let tagX = x0 + 3
        if (tagX + w > chartArea.right) tagX = x0 - w - 3

        const row = (() => {
          for (let r = 0; r < LABEL_ROWS; r += 1) {
            const clash = placed.some(
              (p) => p.row === r && tagX < p.right + 2 && tagX + w > p.left - 2
            )
            if (!clash) return r
          }
          return -1
        })()

        if (row >= 0) {
          placed.push({ left: tagX, right: tagX + w, row })
          const y = chartArea.top + 9 + row * ROW_HEIGHT

          ctx.fillStyle = '#ffffff'
          ctx.strokeStyle = color
          ctx.lineWidth = 1
          ctx.beginPath()
          ctx.rect(tagX, y - 8, w, 16)
          ctx.fill()
          ctx.stroke()

          ctx.fillStyle = color
          ctx.fillText(label, tagX + 5, y)
        }
      }
    })
    ctx.restore()
  },
}

ChartJS.register(fuelEventMarkerPlugin)

/** Build [startIdx, endIdx] ranges where ignition is ON. */
function ignitionOnRanges(rows) {
  const ranges = []
  let start = null
  for (let i = 0; i < rows.length; i += 1) {
    const on = Number(rows[i]?.ignition ?? rows[i]?.iO_1 ?? 0) === 1
    if (on && start === null) start = i
    if (!on && start !== null) {
      ranges.push([start, i - 1])
      start = null
    }
  }
  if (start !== null) ranges.push([start, rows.length - 1])
  return ranges
}

/** Paint full chart height green while ignition is ON; white when OFF (FuelArk). */
const ignitionBackgroundPlugin = {
  id: 'ignitionBackground',
  beforeDraw(chart) {
    const { ctx, chartArea, scales } = chart
    if (!chartArea) return
    // Respect legend toggle on the Ignition entry (same as FuelArk).
    const ignIdx = chart.data.datasets.findIndex((d) => d._isIgnition)
    if (ignIdx >= 0) {
      const meta = chart.getDatasetMeta(ignIdx)
      if (meta?.hidden) return
    }
    const rows = chart.options.plugins?.ignitionBackground?.points || []
    const xScale = scales.x
    if (!xScale || !rows.length) return

    ctx.save()
    ctx.fillStyle = IGNITION_OFF_FILL
    ctx.fillRect(chartArea.left, chartArea.top, chartArea.width, chartArea.height)

    const ranges = ignitionOnRanges(rows)
    ctx.fillStyle = IGNITION_ON_FILL
    for (const [from, to] of ranges) {
      const x0 = xScale.getPixelForValue(from)
      const x1 = xScale.getPixelForValue(Math.min(to + 1, rows.length - 1))
      const left = Math.max(chartArea.left, Math.min(x0, x1))
      const right = Math.min(chartArea.right, Math.max(x0, x1))
      if (right > left) {
        ctx.fillRect(left, chartArea.top, right - left, chartArea.height)
      }
    }
    ctx.restore()
  },
}

ChartJS.register(ignitionBackgroundPlugin)

function getChartInstance(ref) {
  return ref?.current || null
}

function GraphNavControls({ chartRef, navMode, setNavMode, onInteract }) {
  const chart = () => getChartInstance(chartRef)

  return (
    <div className="chart-nav" onMouseDown={(e) => e.stopPropagation()}>
      <button
        type="button"
        className={`btn-alert${navMode === 'pan' ? ' is-open' : ''}`}
        title="Drag to move the graph"
        onClick={() => {
          onInteract?.()
          setNavMode('pan')
        }}
      >
        Move
      </button>
      <button
        type="button"
        className={`btn-alert${navMode === 'box' ? ' is-open' : ''}`}
        title="Drag a box to zoom into a region"
        onClick={() => {
          onInteract?.()
          setNavMode('box')
        }}
      >
        Box zoom
      </button>
      <button
        type="button"
        className="btn-alert"
        title="Zoom in"
        onClick={() => {
          onInteract?.()
          chart()?.zoom(1.2)
        }}
      >
        Zoom +
      </button>
      <button
        type="button"
        className="btn-alert"
        title="Zoom out"
        onClick={() => {
          onInteract?.()
          chart()?.zoom(0.8)
        }}
      >
        Zoom −
      </button>
      <button
        type="button"
        className="btn-alert btn-alert--primary"
        title="Reset zoom and pan"
        onClick={() => {
          onInteract?.()
          chart()?.resetZoom()
        }}
      >
        Reset
      </button>
    </div>
  )
}

export default function DeviceChart({
  deviceName,
  points,
  events = [],
  streaming = false,
  onPauseStreaming,
}) {
  const [expanded, setExpanded] = useState(false)
  const [navMode, setNavMode] = useState('pan') // pan = drag move · box = drag zoom
  const mainRef = useRef(null)
  const expandRef = useRef(null)

  function pauseForInspect() {
    if (streaming && onPauseStreaming) {
      onPauseStreaming()
    }
  }

  const chart = useMemo(() => {
    const rows = points || []
    const labels = rows.map((p) => p.time)
    const markers = buildMarkers(rows, events)

    const markerSet = (name, color, pick, rotation) => {
      const hits = markers.filter(pick)
      if (!hits.length) return null
      const byIndex = new Map(hits.map((m) => [m.startIdx, m.value]))
      return {
        label: name,
        data: rows.map((_, i) => (byIndex.has(i) ? byIndex.get(i) : null)),
        borderColor: color,
        backgroundColor: color,
        showLine: false,
        pointStyle: 'triangle',
        rotation,
        pointRadius: 7,
        pointHoverRadius: 10,
      }
    }

    const markerDatasets = [
      markerSet('Fuel Theft', THEFT_COLOR, (m) => m.theft && !m.suppressed, 180),
      markerSet('Fuel Filling', FILL_COLOR, (m) => !m.theft && !m.suppressed, 0),
      markerSet('Filtered as artifact', SUPPRESSED_COLOR, (m) => m.suppressed, 90),
    ].filter(Boolean)

    const pointRadius = rows.length <= 900 ? 2.5 : 0

    const seriesDatasets = SERIES.map((s) => ({
      label: s.name,
      data: rows.map((p) => Number(p[s.key] ?? 0)),
      borderColor: s.color,
      backgroundColor: s.fillArea ? (s.fill || rgba(s.color, 0.18)) : s.color,
      borderWidth: s.width ?? 1.5,
      pointRadius: s.showPoints ? pointRadius : 0,
      pointHoverRadius: 4,
      pointBackgroundColor: s.color,
      pointBorderColor: '#fff',
      pointBorderWidth: s.showPoints ? 1 : 0,
      pointStyle: s.showPoints ? 'circle' : 'circle',
      tension: 0.15,
      fill: Boolean(s.fillArea),
      yAxisID: s.yAxisID || 'y',
      hidden: Boolean(s.hidden),
    }))

    // FuelArk Ignition legend entry — toggles green ON bands (no line drawn).
    const ignitionDataset = {
      label: IGNITION_LEGEND.name,
      data: rows.map(() => null),
      borderColor: IGNITION_LEGEND.color,
      backgroundColor: IGNITION_LEGEND.fill,
      pointStyle: 'rect',
      showLine: false,
      pointRadius: 0,
      yAxisID: 'y',
      hidden: false,
      _isIgnition: true,
    }

    return {
      markers,
      data: {
        labels,
        datasets: [
          seriesDatasets[0], // Fuel Level EDC first
          ignitionDataset,
          ...seriesDatasets.slice(1),
          ...markerDatasets.map((ds) => ({ ...ds, yAxisID: 'y' })),
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: 'nearest', intersect: false, axis: 'x' },
        onClick() {
          pauseForInspect()
        },
        plugins: {
          title: {
            display: true,
            text: deviceName
              ? `Device Data · ${deviceName}`
              : 'Device Data',
            color: '#065f46',
            font: { size: 15, weight: '700' },
            padding: { bottom: 8 },
          },
          legend: {
            position: 'bottom',
            labels: {
              usePointStyle: true,
              boxWidth: 10,
              padding: 14,
              color: '#334155',
              font: { size: 11, weight: '600' },
            },
            onClick(e, legendItem, legend) {
              const chartInst = legend.chart
              const idx = legendItem.datasetIndex
              const meta = chartInst.getDatasetMeta(idx)
              meta.hidden =
                meta.hidden === null
                  ? !chartInst.data.datasets[idx].hidden
                  : !meta.hidden
              chartInst.update()
            },
          },
          ignitionBackground: { points: rows },
          fuelEventMarkers: { markers },
          tooltip: {
            backgroundColor: 'rgba(15, 23, 42, 0.92)',
            titleColor: '#fff',
            bodyColor: '#e2e8f0',
            borderColor: '#10b981',
            borderWidth: 1,
            padding: 10,
            filter(item) {
              return !item.dataset?._isIgnition
            },
            callbacks: {
              afterBody(items) {
                const idx = items?.[0]?.dataIndex
                return markers
                  .filter((m) => m.startIdx === idx)
                  .map((m) => {
                    const bits = [
                      `${m.theft ? 'Fuel Theft −' : 'Fuel Filling +'}${m.volume.toFixed(1)} L`,
                    ]
                    if (m.confidence != null) {
                      bits.push(`conf ${Number(m.confidence).toFixed(2)}`)
                    }
                    if (m.decision) bits.push(m.decision)
                    if (m.suppressed) {
                      bits.push(`filtered: ${String(m.reason).replace(/_/g, ' ')}`)
                    }
                    return bits.join(' · ')
                  })
              },
            },
          },
          zoom: {
            limits: {
              x: { min: 'original', max: 'original' },
              y: { min: 'original', max: 'original' },
              y1: { min: 'original', max: 'original' },
            },
            pan: {
              enabled: navMode === 'pan',
              mode: 'xy',
              threshold: 6,
              onPanStart: () => {
                pauseForInspect()
                return true
              },
            },
            zoom: {
              wheel: { enabled: true, speed: 0.12 },
              pinch: { enabled: true },
              drag:
                navMode === 'box'
                  ? {
                      enabled: true,
                      backgroundColor: 'rgba(16, 185, 129, 0.12)',
                      borderColor: '#10b981',
                      borderWidth: 1,
                    }
                  : { enabled: false },
              mode: 'xy',
              onZoomStart: () => {
                pauseForInspect()
                return true
              },
            },
          },
        },
        scales: {
          x: {
            grid: { color: '#f1f5f9' },
            ticks: {
              maxRotation: 45,
              autoSkip: true,
              maxTicksLimit: expanded ? 16 : 10,
              color: '#64748b',
              callback(value) {
                const label = labels[value]
                if (!label) return ''
                const d = new Date(label)
                if (Number.isNaN(d.getTime())) return String(label).slice(5, 16)
                return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
              },
            },
          },
          y: {
            type: 'linear',
            position: 'left',
            title: {
              display: true,
              text: 'Device Data (EDC)',
              color: '#065f46',
              font: { weight: '700', size: 12 },
            },
            beginAtZero: true,
            grace: '5%',
            grid: { color: '#e2e8f0' },
            ticks: { color: '#065f46' },
          },
          y1: {
            type: 'linear',
            position: 'right',
            title: {
              display: true,
              text: 'Speed / Power / Aux',
              color: '#64748b',
              font: { weight: '600', size: 11 },
            },
            beginAtZero: true,
            grace: '8%',
            grid: { drawOnChartArea: false },
            display: 'auto',
            ticks: { color: '#64748b' },
          },
        },
      },
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceName, points, events, expanded, streaming, onPauseStreaming, navMode])

  const markerCount = chart.markers.length

  return (
    <>
      <div
        className="panel chart-panel fuelark-chart"
        style={{ height: 500 }}
        onMouseDown={pauseForInspect}
        onTouchStart={pauseForInspect}
      >
        <div className="chart-toolbar fuelark-chart__toolbar">
          <div className="fuelark-chart__legend-hint">
            <span className="fuelark-chip fuelark-chip--edc">Fuel Level EDC</span>
            <span className="fuelark-chip fuelark-chip--ign">Ignition</span>
            <span className="fuelark-chip">Movement</span>
            <span className="fuelark-chip fuelark-chip--speed">Speed</span>
            <span className="fuelark-chip fuelark-chip--pex">Power Ex</span>
            <span className="fuelark-chip fuelark-chip--pin">Power In</span>
            {markerCount ? (
              <>
                <span className="fuelark-chip fuelark-chip--theft">▼ theft</span>
                <span className="fuelark-chip fuelark-chip--fill">▲ fill</span>
              </>
            ) : null}
            {streaming ? <span className="fuelark-chip fuelark-chip--live">LIVE</span> : null}
          </div>
          <div className="chart-toolbar-actions">
            <GraphNavControls
              chartRef={mainRef}
              navMode={navMode}
              setNavMode={setNavMode}
              onInteract={pauseForInspect}
            />
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={(e) => {
                e.stopPropagation()
                pauseForInspect()
                setExpanded(true)
              }}
            >
              Expand
            </button>
          </div>
        </div>
        <p className="fuelark-chart__hint">
          Same as FuelArk Device Data · click legend to show/hide · green bands = Ignition ON · scroll to zoom
        </p>
        <div style={{ height: 'calc(100% - 64px)' }}>
          <Line ref={mainRef} data={chart.data} options={chart.options} />
        </div>
      </div>

      {expanded ? (
        <div
          className="chart-expand-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Expanded live chart"
          onMouseDown={pauseForInspect}
        >
          <div className="chart-expand-modal" onMouseDown={(e) => e.stopPropagation()}>
            <div className="chart-expand-head">
              <div>
                <strong>{deviceName || 'Live Chart'}</strong>
                <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                  Scroll to zoom · Move / Box zoom · Reset
                  {markerCount
                    ? ` · ${markerCount} fill/theft event${markerCount === 1 ? '' : 's'} marked`
                    : ''}
                </div>
              </div>
              <div className="chart-toolbar-actions">
                <GraphNavControls
                  chartRef={expandRef}
                  navMode={navMode}
                  setNavMode={setNavMode}
                  onInteract={pauseForInspect}
                />
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => setExpanded(false)}
                >
                  Close
                </button>
              </div>
            </div>
            <div className="chart-expand-body">
              <Line ref={expandRef} data={chart.data} options={chart.options} />
            </div>
          </div>
        </div>
      ) : null}
    </>
  )
}
