import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { api, FILL_MIN_LITERS } from '../api/client'
import { LiveSummaryCards } from '../components/SummaryCards'
import DeviceChart from '../components/DeviceChart'
import FillTheftPanel from '../components/FillTheftPanel'
import LiveTable from '../components/LiveTable'

function pad(n) {
  return String(n).padStart(2, '0')
}

function formatLocal(dt) {
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}:${pad(dt.getSeconds())}`
}

/** FuelArk TrackingById-style default: today 00:00 → now (live day). */
function defaultRange() {
  return rangeToday()
}

function rangeYesterday() {
  const now = new Date()
  const from = new Date(now)
  from.setDate(from.getDate() - 1)
  from.setHours(0, 0, 0, 0)
  const to = new Date(from)
  to.setHours(23, 59, 59, 0)
  return { from: formatLocal(from), to: formatLocal(to) }
}

function rangeLifetime() {
  return { from: '2000-01-01T00:00:00', to: formatLocal(new Date()) }
}

function rangeToday() {
  const now = new Date()
  const from = new Date(now)
  from.setHours(0, 0, 0, 0)
  return { from: formatLocal(from), to: formatLocal(now) }
}

function rangeLastDays(days) {
  const to = new Date()
  const from = new Date(to.getTime() - days * 24 * 3600 * 1000)
  return { from: formatLocal(from), to: formatLocal(to) }
}

function toInputValue(iso) {
  if (!iso) return ''
  return String(iso).replace(' ', 'T').slice(0, 16)
}

function fromInputValue(value) {
  if (!value) return ''
  return value.length === 16 ? `${value}:00` : value
}

function isLiveWindow(toIso) {
  if (!toIso) return true
  const to = new Date(toIso)
  if (Number.isNaN(to.getTime())) return true
  return Date.now() - to.getTime() < 3 * 3600 * 1000
}

function fuelarkTrackUrl(imei) {
  if (!imei) return ''
  return `https://web1.fuelark.com/Home/TrackingById/${encodeURIComponent(imei)}`
}

const MAX_CHART_POINTS = 2500

export default function LiveMap() {
  const [searchParams] = useSearchParams()
  const preset = searchParams.get('device') || ''
  const presetImei = (searchParams.get('imei') || '').replace(/\D/g, '')
  const presetFrom = (searchParams.get('from') || '').replace(' ', 'T')
  const presetTo = (searchParams.get('to') || '').replace(' ', 'T')
  // FuelArk Tracking streams live by default; only pause when stream=0.
  const autoStream = !['0', 'false', 'off'].includes(
    String(searchParams.get('stream') || '1').toLowerCase()
  )
  const defaults = useMemo(() => {
    if (presetFrom && presetTo) return { from: presetFrom, to: presetTo }
    return defaultRange()
  }, [presetFrom, presetTo])

  const [names, setNames] = useState([])
  const [imeiByName, setImeiByName] = useState(() => new Map())
  const [device, setDevice] = useState(preset)
  const [from, setFrom] = useState(defaults.from)
  const [to, setTo] = useState(defaults.to)
  const [summary, setSummary] = useState(null)
  const [points, setPoints] = useState([])
  const [eventStats, setEventStats] = useState(null)
  const [eventsLoading, setEventsLoading] = useState(false)
  const [view, setView] = useState('chart')
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState(autoStream)
  const [streamStatus, setStreamStatus] = useState('')
  const [error, setError] = useState('')
  const esRef = useRef(null)
  const seenRef = useRef(new Set())
  const loadGen = useRef(0)

  const currentImei = useMemo(() => {
    if (!device) return presetImei || ''
    return imeiByName.get(device) || presetImei || ''
  }, [device, imeiByName, presetImei])

  const refreshNames = useCallback(async () => {
    const [list, roster] = await Promise.all([
      api.deviceNames(),
      api.imeiList().catch(() => ({ devices: [] })),
    ])
    const cleaned = list.map((n) => String(n))
    const map = new Map()
    for (const d of roster.devices || []) {
      if (d.deviceName && d.imei) map.set(String(d.deviceName), String(d.imei))
    }
    setImeiByName(map)
    setNames(cleaned)
    return { cleaned, map }
  }, [])

  useEffect(() => {
    refreshNames()
      .then(({ cleaned, map }) => {
        let want = (preset || '').trim()
        if (!want && presetImei) {
          for (const [name, imei] of map.entries()) {
            if (String(imei) === presetImei) {
              want = name
              break
            }
          }
        }
        const match =
          cleaned.find((n) => n === want) ||
          cleaned.find((n) => n.trim() === want) ||
          cleaned[0] ||
          ''
        setDevice((prev) => {
          if (prev) {
            const p = prev.trim()
            return (
              cleaned.find((n) => n === prev) ||
              cleaned.find((n) => n.trim() === p) ||
              match
            )
          }
          return match
        })
      })
      .catch((e) => setError(e.message))
  }, [preset, presetImei, refreshNames])

  function stopStream() {
    if (esRef.current) {
      esRef.current.close()
      esRef.current = null
    }
  }

  const pauseStreamingForChart = useCallback(() => {
    if (!streaming) return
    setStreaming(false)
    stopStream()
    setStreamStatus('Streaming paused — inspect / expand the graph')
  }, [streaming])

  function appendPoint(point) {
    const key = `${point.time}|${point.fuelLevel}|${point.powerEx}`
    if (seenRef.current.has(key)) return
    seenRef.current.add(key)
    setPoints((prev) => {
      const next = [...prev, point]
      if (next.length > MAX_CHART_POINTS) {
        return next.slice(next.length - MAX_CHART_POINTS)
      }
      return next
    })
  }

  function startStream(selected, fromIso, toIso, existingPoints = []) {
    stopStream()
    if (!selected || !streaming) return
    const live = isLiveWindow(toIso) ? '1' : '0'
    const hours = Math.max(
      6,
      Math.ceil((new Date(toIso) - new Date(fromIso)) / 3600000) || 24
    )
    const seed = existingPoints.length ? existingPoints : points
    const since = seed.length > 0 ? seed[seed.length - 1].time : ''
    const url =
      `/api/live/${encodeURIComponent(selected)}/sse` +
      `?from=${encodeURIComponent(fromIso)}` +
      `&to=${encodeURIComponent(toIso)}` +
      `&hours=${hours}&live=${live}&slab=0&interval_ms=80&limit=2000` +
      (since ? `&since=${encodeURIComponent(since)}` : '')
    const es = new EventSource(url)
    esRef.current = es
    setStreamStatus(live === '1' ? 'LIVE streaming…' : 'Streaming selected day…')

    es.addEventListener('status', (ev) => {
      try {
        const data = JSON.parse(ev.data)
        if (data.status === 'streaming') {
          setStreamStatus(
            live === '1'
              ? `LIVE — ${data.queued || 0} points queued`
              : `Playing ${data.queued || 0} points`
          )
        } else if (data.status === 'complete') {
          setStreamStatus('Range complete')
          if (selected && fromIso && toIso) {
            api
              .deviceEvents(selected, fromIso, toIso, {
                theftMin: 5,
                fillMin: FILL_MIN_LITERS,
                fetch: '0',
              })
              .then((evPayload) => setEventStats(evPayload))
              .catch(() => {})
          }
        } else if (data.message) {
          setStreamStatus(data.message)
        }
      } catch (_) {}
    })

    es.addEventListener('point', (ev) => {
      try {
        appendPoint(JSON.parse(ev.data))
      } catch (_) {}
    })

    es.addEventListener('heartbeat', () => {
      setStreamStatus((s) => (s.startsWith('LIVE') ? 'LIVE — waiting for next points…' : s))
      if (live === '1') setTo(formatLocal(new Date()))
    })

    es.onerror = () => {
      setStreamStatus('Stream reconnecting…')
    }
  }

  async function loadPredictions(selected, fromIso, toIso, gen) {
    setEventsLoading(true)
    try {
      const ev = await api.deviceEvents(selected, fromIso, toIso, {
        theftMin: 5,
        fillMin: FILL_MIN_LITERS,
        includeStored: false,
        fetch: '0',
      })
      if (gen !== loadGen.current) return
      setEventStats(ev)
    } catch (e) {
      if (gen === loadGen.current) {
        setStreamStatus(`Predictions: ${e.message}`)
      }
    } finally {
      if (gen === loadGen.current) setEventsLoading(false)
    }
  }

  async function runSearch(selected = device, opts = {}) {
    if (!selected) return
    const fromIso = opts.from ?? from
    const toIso = opts.to ?? to
    const forceFetch = Boolean(opts.forceFetch)
    const gen = ++loadGen.current
    setLoading(true)
    setError('')
    setEventStats(null)
    stopStream()
    seenRef.current = new Set()
    setPoints([])

    // Shorter windows → denser FuelArk-like graph
    const hours =
      (new Date(toIso) - new Date(fromIso)) / 3600000 || 24
    const downsample = hours <= 36 ? 5 : hours <= 24 * 8 ? 12 : 25

    try {
      const [sum, series] = await Promise.all([
        api.deviceSummary(selected, fromIso, toIso, {
          fetch: forceFetch ? 'auto' : '0',
          detect: '0',
        }),
        api.deviceSeries(selected, fromIso, toIso, {
          downsample,
          limit: 2500,
          fetch: forceFetch ? 'auto' : '0',
        }),
      ])
      if (gen !== loadGen.current) return

      let pts = series.points || []
      if (!pts.length && !forceFetch) {
        setStreamStatus('No local points — fetching FuelArk…')
        const remote = await api.deviceSeries(selected, fromIso, toIso, {
          downsample,
          limit: 2500,
          fetch: 'auto',
        })
        if (gen !== loadGen.current) return
        pts = remote.points || []
        if (remote.error) setStreamStatus(remote.error)
      }

      setSummary(
        sum && Object.keys(sum).length
          ? { ...sum, deviceName: sum.deviceName || selected }
          : { deviceName: selected }
      )
      pts.forEach((p) => {
        const key = `${p.time}|${p.fuelLevel}|${p.powerEx}`
        seenRef.current.add(key)
      })
      setPoints(pts)
      if (!pts.length) {
        setError('')
        setStreamStatus('No telemetry yet — try Fetch live or another day')
      } else {
        setStreamStatus(`Loaded ${pts.length} points`)
      }
      setLoading(false)

      loadPredictions(selected, fromIso, toIso, gen)

      if (streaming) {
        startStream(selected, fromIso, toIso, pts)
      }
    } catch (e) {
      if (gen === loadGen.current) {
        setError(e.message)
        setLoading(false)
      }
    }
  }

  useEffect(() => {
    if (presetFrom) setFrom(presetFrom)
    if (presetTo) setTo(presetTo)
  }, [presetFrom, presetTo])

  useEffect(() => {
    if (!device) return
    runSearch(device, {
      from: presetFrom || undefined,
      to: presetTo || undefined,
    })
    return () => stopStream()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [device, presetFrom, presetTo])

  useEffect(() => {
    if (!streaming) stopStream()
  }, [streaming])

  function applyPreset(range) {
    setFrom(range.from)
    setTo(range.to)
    if (device) runSearch(device, range)
  }

  const lastPoint = points.length ? points[points.length - 1] : null

  return (
    <div className="live-page">
      <header className="topbar live-topbar">
        <div className="topbar-center-block" style={{ textAlign: 'left' }}>
          <h1>Live Tracking</h1>
          <p className="topbar-subtitle">
            FuelArk-style Device Data · live graph for every vehicle
          </p>
        </div>
        <div className="live-top-actions">
          <span className={`live-stream-chip${streaming ? ' is-on' : ''}`}>
            {streaming ? '● LIVE' : 'Paused'}
          </span>
          {(streamStatus || loading || eventsLoading) ? (
            <span className="live-status-chip">
              {loading ? 'Loading graph…' : null}
              {!loading && eventsLoading ? 'Predicting…' : null}
              {!loading && !eventsLoading && streamStatus ? streamStatus : null}
            </span>
          ) : null}
          <button
            type="button"
            className={`btn btn-sm ${streaming ? 'btn-live' : 'btn-ghost'}`}
            onClick={() => {
              const next = !streaming
              setStreaming(next)
              if (next && device) startStream(device, from, to)
              else stopStream()
            }}
          >
            {streaming ? 'Pause' : 'Go live'}
          </button>
        </div>
      </header>

      <div className="content">
        <div className="track-hero">
          <div className="track-hero__main">
            <div className="track-hero__title">{device || 'Select a vehicle'}</div>
            <div className="track-hero__meta">
              {currentImei ? <span>IMEI {currentImei}</span> : null}
              {lastPoint?.time ? (
                <span>Last point {String(lastPoint.time).replace('T', ' ').slice(0, 19)}</span>
              ) : null}
              {summary?.deviceStatus ? <span>{summary.deviceStatus}</span> : null}
            </div>
          </div>
          <div className="track-hero__stats">
            <div className="track-mini">
              <span>Fuel</span>
              <strong>
                {summary?.fuel ?? summary?.currentFuel ?? lastPoint?.fuelLevel ?? '—'}
                <small> L</small>
              </strong>
            </div>
            <div className="track-mini">
              <span>EDC</span>
              <strong>{lastPoint?.fuelLevelEDC ?? '—'}</strong>
            </div>
            <div className="track-mini">
              <span>Speed</span>
              <strong>
                {summary?.speed ?? lastPoint?.speed ?? 0}
                <small> km/h</small>
              </strong>
            </div>
            <div className="track-mini">
              <span>Ignition</span>
              <strong>
                {summary?.ignition
                  || (lastPoint?.ignition ? 'On' : lastPoint ? 'Off' : '—')}
              </strong>
            </div>
          </div>
          <div className="track-hero__actions">
            {currentImei ? (
              <a
                className="btn btn-sm btn-ghost"
                href={fuelarkTrackUrl(currentImei)}
                target="_blank"
                rel="noreferrer"
                title="Open same vehicle on FuelArk TrackingById"
              >
                Open FuelArk
              </a>
            ) : null}
            <Link to="/admin" className="btn btn-sm btn-custom">Admin</Link>
          </div>
        </div>

        <div className="live-filters">
          <label className="live-field">
            Vehicle
            <select value={device} onChange={(e) => setDevice(e.target.value)}>
              {!device ? <option value="">Select…</option> : null}
              {names.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
          <label className="live-field">
            From
            <input
              type="datetime-local"
              value={toInputValue(from)}
              onChange={(e) => setFrom(fromInputValue(e.target.value))}
            />
          </label>
          <label className="live-field">
            To
            <input
              type="datetime-local"
              value={toInputValue(to)}
              onChange={(e) => setTo(fromInputValue(e.target.value))}
            />
          </label>
          <button
            type="button"
            className="btn btn-sm btn-custom"
            onClick={() => runSearch()}
            disabled={loading || !device}
          >
            {loading ? '…' : 'Search'}
          </button>
          <button
            type="button"
            className="btn btn-sm btn-live"
            onClick={() => runSearch(device, { forceFetch: true })}
            disabled={loading || !device}
            title="Pull latest telemetry from FuelArk for this window"
          >
            Fetch live
          </button>
        </div>

        <div className="live-presets">
          <span className="live-presets__label">Quick range</span>
          {[
            ['Today', rangeToday],
            ['Yesterday', rangeYesterday],
            ['24h', () => rangeLastDays(1)],
            ['48h', () => rangeLastDays(2)],
            ['7 days', () => rangeLastDays(7)],
            ['May', () => ({ from: '2026-05-01T00:00:00', to: '2026-05-31T23:59:59' })],
            ['June', () => ({ from: '2026-06-01T00:00:00', to: '2026-06-30T23:59:59' })],
            ['July', () => ({ from: '2026-07-01T00:00:00', to: '2026-07-31T23:59:59' })],
            ['May–July', () => ({ from: '2026-05-01T00:00:00', to: '2026-07-31T23:59:59' })],
            ['All data', rangeLifetime],
          ].map(([label, fn]) => (
            <button
              key={label}
              type="button"
              className="live-preset-chip"
              onClick={() => applyPreset(fn())}
              disabled={!device && label === 'All data'}
            >
              {label}
            </button>
          ))}
        </div>

        {error ? <p className="error">{error}</p> : null}

        <LiveSummaryCards
          summary={summary}
          eventStats={eventStats}
          eventsLoading={eventsLoading}
          loading={loading}
        />

        <div className="live-view-bar">
          <div className="live-view-tabs">
            <button
              type="button"
              className={`live-view-tab${view === 'chart' ? ' is-active' : ''}`}
              onClick={() => setView('chart')}
            >
              Device Data
            </button>
            <button
              type="button"
              className={`live-view-tab${view === 'table' ? ' is-active' : ''}`}
              onClick={() => setView('table')}
            >
              Live Table
            </button>
          </div>
          <span className="live-points-chip">
            {points.length} point{points.length === 1 ? '' : 's'}
            {eventsLoading ? ' · predicting…' : ''}
          </span>
        </div>

        {!loading && !points.length && device ? (
          <div className="live-empty-panel">
            <strong>No points in this range yet</strong>
            <p>
              Like FuelArk Tracking, press <em>Fetch live</em> to pull this vehicle from FuelArk,
              or try <em>Today</em> / <em>Yesterday</em>.
              {currentImei ? (
                <>
                  {' '}Compare on{' '}
                  <a href={fuelarkTrackUrl(currentImei)} target="_blank" rel="noreferrer">
                    FuelArk TrackingById
                  </a>
                  .
                </>
              ) : null}
            </p>
            <button
              type="button"
              className="btn btn-sm btn-live"
              style={{ marginTop: 10 }}
              onClick={() => runSearch(device, { forceFetch: true })}
              disabled={loading}
            >
              Fetch live from FuelArk
            </button>
          </div>
        ) : null}

        {view === 'chart' ? (
          <DeviceChart
            deviceName={device}
            points={points}
            events={eventStats?.events || []}
            streaming={streaming}
            onPauseStreaming={pauseStreamingForChart}
          />
        ) : (
          <LiveTable points={points} />
        )}

        <FillTheftPanel eventsPayload={eventStats} loading={eventsLoading} />
      </div>
    </div>
  )
}
