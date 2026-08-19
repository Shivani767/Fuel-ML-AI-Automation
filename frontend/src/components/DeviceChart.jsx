import { useMemo, useState } from 'react'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
} from 'chart.js'
import { Line } from 'react-chartjs-2'

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend
)

const SERIES = [
  // FuelArk-style: liters on the primary axis. Raw EDC/power drown that scale
  // (thousands / tens of thousands) so they sit on y1 and stay legend-hidden.
  { key: 'fuelLevel', name: 'Fuel Level (L)', color: '#4f46e5', yAxisID: 'y', hidden: false, width: 2.25 },
  { key: 'fuelLevelEDC', name: 'Fuel Level EDC', color: '#7dd3fc', yAxisID: 'y1', hidden: true, width: 1.25 },
  { key: 'speed', name: 'Speed', color: '#1d4ed8', yAxisID: 'y1', hidden: true, width: 1 },
  { key: 'movement', name: 'Movement', color: '#f97316', yAxisID: 'y1', hidden: true, width: 1 },
  { key: 'powerEx', name: 'Power Ex', color: '#c026d3', yAxisID: 'y1', hidden: true, width: 1 },
  { key: 'powerIn', name: 'Power In', color: '#0d9488', yAxisID: 'y1', hidden: true, width: 1 },
]

const IGNITION_ON_FILL = 'rgba(34, 197, 94, 0.28)'
const IGNITION_OFF_FILL = '#ffffff'

// Same palette the alert tables use, so a red row and a red band read as one thing.
const THEFT_COLOR = '#dc2626'
const FILL_COLOR = '#16a34a'
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
      value: Number(row.fuelLevel ?? row.fuelInLiter ?? 0),
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

/** Paint full chart height green while ignition is ON; white when OFF. */
const ignitionBackgroundPlugin = {
  id: 'ignitionBackground',
  beforeDraw(chart) {
    const { ctx, chartArea, scales } = chart
    if (!chartArea) return
    const rows = chart.options.plugins?.ignitionBackground?.points || []
    const xScale = scales.x
    if (!xScale || !rows.length) return

    ctx.save()
    // Base: ignition OFF = white
    ctx.fillStyle = IGNITION_OFF_FILL
    ctx.fillRect(chartArea.left, chartArea.top, chartArea.width, chartArea.height)

    const ranges = ignitionOnRanges(rows)
    ctx.fillStyle = IGNITION_ON_FILL
    for (const [from, to] of ranges) {
      const x0 = xScale.getPixelForValue(from)
      // extend to next index so the band covers that segment
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

export default function DeviceChart({
  deviceName,
  points,
  events = [],
  streaming = false,
  onPauseStreaming,
}) {
  const [expanded, setExpanded] = useState(false)

  function pauseForInspect() {
    if (streaming && onPauseStreaming) {
      onPauseStreaming()
    }
  }

  const chart = useMemo(() => {
    const rows = points || []
    const labels = rows.map((p) => p.time)
    const markers = buildMarkers(rows, events)

    // Marker points ride on the fuel-level trace, which is what puts each event
    // legibly in the legend and the hover tooltip as well as on the canvas.
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

    return {
      markers,
      data: {
        labels,
        datasets: [
          ...SERIES.map((s) => ({
            label: s.name,
            data: rows.map((p) => Number(p[s.key] ?? 0)),
            borderColor: s.color,
            backgroundColor: s.color,
            borderWidth: s.width ?? 1.5,
            pointRadius: 0,
            pointHoverRadius: 3,
            tension: 0.05,
            yAxisID: s.yAxisID || 'y',
            hidden: Boolean(s.hidden),
          })),
          ...markerDatasets.map((ds) => ({ ...ds, yAxisID: 'y' })),
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: 'index', intersect: false },
        onClick() {
          pauseForInspect()
        },
        plugins: {
          title: {
            display: true,
            text: deviceName
              ? `Device graph reports of ${deviceName}`
              : 'Device graph reports',
            font: { size: 16, weight: '600' },
          },
          legend: { position: 'bottom' },
          ignitionBackground: { points: rows },
          fuelEventMarkers: { markers },
          tooltip: {
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
        },
        scales: {
          x: {
            ticks: {
              maxRotation: 45,
              autoSkip: true,
              maxTicksLimit: expanded ? 16 : 10,
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
            title: { display: true, text: 'Fuel (liters)' },
            beginAtZero: false,
            grace: '8%',
          },
          y1: {
            type: 'linear',
            position: 'right',
            title: { display: true, text: 'EDC / power / speed' },
            beginAtZero: true,
            grid: { drawOnChartArea: false },
            display: 'auto',
          },
        },
      },
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceName, points, events, expanded, streaming, onPauseStreaming])

  const markerCount = chart.markers.length

  return (
    <>
      <div
        className="panel chart-panel"
        style={{ height: 480 }}
        onMouseDown={pauseForInspect}
        onTouchStart={pauseForInspect}
      >
        <div className="chart-toolbar">
          <span className="muted" style={{ fontSize: 12 }}>
            <span className="ign-legend-on" /> Ignition ON (green) ·{' '}
            <span className="ign-legend-off" /> Ignition OFF (white)
            {' · Fuel (L) primary — click legend for EDC/power'}
            {markerCount ? (
              <>
                {' · '}
                <span style={{ color: THEFT_COLOR, fontWeight: 600 }}>▼ theft</span>
                {' / '}
                <span style={{ color: FILL_COLOR, fontWeight: 600 }}>▲ fill</span>
                {' marked on the fuel trace'}
              </>
            ) : null}
            {streaming
              ? ' · Click graph to pause streaming'
              : ' · Streaming paused'}
          </span>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={(e) => {
              e.stopPropagation()
              pauseForInspect()
              setExpanded(true)
            }}
          >
            Expand graph
          </button>
        </div>
        <div style={{ height: 'calc(100% - 36px)' }}>
          <Line data={chart.data} options={chart.options} />
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
                  Green background = Ignition ON · White = Ignition OFF
                  {markerCount
                    ? ` · ${markerCount} fill/theft event${markerCount === 1 ? '' : 's'} marked`
                    : ''}
                </div>
              </div>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setExpanded(false)}
              >
                Close
              </button>
            </div>
            <div className="chart-expand-body">
              <Line data={chart.data} options={chart.options} />
            </div>
          </div>
        </div>
      ) : null}
    </>
  )
}
