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
  { key: 'fuelLevel', name: 'Fuel Level', color: '#7dd3fc' },
  { key: 'fuelLevelEDC', name: 'Fuel Level EDC', color: '#6366f1' },
  { key: 'ignition', name: 'Ignition', color: '#16a34a' },
  { key: 'movement', name: 'Movement', color: '#f97316' },
  { key: 'speed', name: 'Speed', color: '#1d4ed8' },
  { key: 'powerEx', name: 'Power Ex', color: '#c026d3' },
  { key: 'powerIn', name: 'Power In', color: '#0d9488' },
]

const IGNITION_ON_FILL = 'rgba(34, 197, 94, 0.28)'
const IGNITION_OFF_FILL = '#ffffff'

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
    return {
      data: {
        labels,
        datasets: SERIES.map((s) => ({
          label: s.name,
          data: rows.map((p) => Number(p[s.key] ?? 0)),
          borderColor: s.color,
          backgroundColor: s.color,
          borderWidth: 1.5,
          pointRadius: 0,
          pointHoverRadius: 3,
          tension: 0.05,
        })),
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
            title: { display: true, text: 'Device Data' },
            beginAtZero: true,
          },
        },
      },
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceName, points, expanded, streaming, onPauseStreaming])

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
