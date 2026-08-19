import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { api } from '../api/client'
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

function defaultRange() {
  // Default 24h — faster charts than 48h
  const to = new Date()
  const from = new Date(to.getTime() - 24 * 3600 * 1000)
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

const MAX_CHART_POINTS = 2500

export default function LiveMap() {
  const [searchParams] = useSearchParams()
  const preset = searchParams.get('device') || ''
  const presetFrom = (searchParams.get('from') || '').replace(' ', 'T')
  const presetTo = (searchParams.get('to') || '').replace(' ', 'T')
  const defaults = useMemo(() => {
    if (presetFrom && presetTo) return { from: presetFrom, to: presetTo }
    return defaultRange()
  }, [presetFrom, presetTo])

  const [names, setNames] = useState([])
  const [device, setDevice] = useState(preset)
  const [from, setFrom] = useState(defaults.from)
  const [to, setTo] = useState(defaults.to)
  const [summary, setSummary] = useState(null)
  const [points, setPoints] = useState([])
  const [eventStats, setEventStats] = useState(null)
  const [eventsLoading, setEventsLoading] = useState(false)
  const [view, setView] = useState('chart')
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [streamStatus, setStreamStatus] = useState('')
  const [error, setError] = useState('')
  const esRef = useRef(null)
  const seenRef = useRef(new Set())
  const loadGen = useRef(0)

  const refreshNames = useCallback(async () => {
    const list = await api.deviceNames()
    const cleaned = list.map((n) => String(n))
    setNames(cleaned)
    return cleaned
  }, [])

  useEffect(() => {
    refreshNames()
      .then((cleaned) => {
        const want = (preset || '').trim()
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
  }, [preset, refreshNames])

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
              .deviceEvents(selected, fromIso, toIso, { theftMin: 5, fillMin: 5, fetch: '0' })
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
        fillMin: 5,
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
    const gen = ++loadGen.current
    setLoading(true)
    setError('')
    setEventStats(null)
    stopStream()
    seenRef.current = new Set()
    setPoints([])

    try {
      // Phase 1: local series + light summary (no FuelArk, no detector) → fast chart
      const [sum, series] = await Promise.all([
        api.deviceSummary(selected, fromIso, toIso, { fetch: '0', detect: '0' }),
        api.deviceSeries(selected, fromIso, toIso, {
          downsample: 25,
          limit: 2500,
          fetch: '0',
        }),
      ])
      if (gen !== loadGen.current) return

      let pts = series.points || []
      // Phase 1b: if empty locally, one FuelArk pull then redraw
      if (!pts.length) {
        setStreamStatus('No local points — fetching FuelArk…')
        const remote = await api.deviceSeries(selected, fromIso, toIso, {
          downsample: 25,
          limit: 2500,
          fetch: 'auto',
        })
        if (gen !== loadGen.current) return
        pts = remote.points || []
        if (remote.error) setStreamStatus(remote.error)
      }

      setSummary(sum && Object.keys(sum).length ? sum : null)
      pts.forEach((p) => {
        const key = `${p.time}|${p.fuelLevel}|${p.powerEx}`
        seenRef.current.add(key)
      })
      setPoints(pts)
      if (!pts.length) {
        setError(
          series.error ||
            'No points in this range. Try another day, or add/refresh the IMEI.'
        )
      }
      setLoading(false)

      // Phase 2: predictions in background (does not block the graph)
      loadPredictions(selected, fromIso, toIso, gen)

      if (streaming) {
        startStream(selected, fromIso, toIso, pts)
      } else {
        setStreamStatus(pts.length ? `Loaded ${pts.length} points` : '')
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

  return (
    <>
      <header className="topbar">
        <div>
          <div className="breadcrumb">Fuelark Device Data / Dashboard / Map</div>
          <h1>Live Map</h1>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="muted" style={{ fontSize: 13 }}>
            {streamStatus}
            {loading ? ' · Loading graph…' : ''}
            {eventsLoading ? ' · Predicting…' : ''}
          </span>
          <button
            type="button"
            className={`btn ${streaming ? 'btn-live' : 'btn-ghost'}`}
            onClick={() => {
              const next = !streaming
              setStreaming(next)
              if (next && device) startStream(device, from, to)
              else stopStream()
            }}
          >
            {streaming ? '● Streaming ON' : 'Streaming OFF'}
          </button>
        </div>
      </header>
      <div className="content">
        <div className="admin-banner">
          <span>
            {preset
              ? `Opened from Alerts for ${preset}${presetFrom ? ` · ${String(presetFrom).replace('T', ' ').slice(0, 16)} → ${String(presetTo).replace('T', ' ').slice(11, 16)}` : ''}. Chart + fill/theft reports load for this window.`
              : 'Add or manage vehicles by IMEI in Admin (saved for lifetime).'}
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            {preset ? (
              <Link to="/alerts" className="btn btn-ghost" style={{ padding: '6px 12px', fontSize: 13 }}>
                ← Back to Alerts
              </Link>
            ) : null}
            <Link to="/admin" className="btn btn-primary" style={{ padding: '6px 12px', fontSize: 13 }}>
              Open Admin
            </Link>
          </div>
        </div>

        <LiveSummaryCards
          summary={summary}
          eventStats={eventStats}
          eventsLoading={eventsLoading}
        />

        <div className="filters">
          <label>
            Select Device
            <select value={device} onChange={(e) => setDevice(e.target.value)}>
              {!device ? <option value="">Select…</option> : null}
              {names.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <label>
            From Date
            <input
              type="datetime-local"
              value={toInputValue(from)}
              onChange={(e) => setFrom(fromInputValue(e.target.value))}
            />
          </label>
          <label>
            To Date
            <input
              type="datetime-local"
              value={toInputValue(to)}
              onChange={(e) => setTo(fromInputValue(e.target.value))}
            />
          </label>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => runSearch()}
            disabled={loading || !device}
          >
            {loading ? 'Loading…' : 'Search'}
          </button>
        </div>

        <div className="view-toggle" style={{ marginTop: -4 }}>
          <button type="button" className="btn btn-ghost" onClick={() => applyPreset(rangeToday())}>
            Today
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => applyPreset(rangeLastDays(1))}>
            Last 24h
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => applyPreset(rangeLastDays(2))}>
            Last 48h
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => applyPreset(rangeLastDays(7))}>
            Last 7 days
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() =>
              applyPreset({
                from: '2026-06-01T00:00:00',
                to: '2026-06-30T23:59:59',
              })
            }
          >
            June 2026
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => {
              if (!device) return
              applyPreset(rangeLifetime())
            }}
          >
            Lifetime / All data
          </button>
        </div>

        {error ? <p className="error">{error}</p> : null}

        <div className="view-toggle">
          <button
            type="button"
            className={`btn btn-ghost ${view === 'chart' ? 'active' : ''}`}
            onClick={() => setView('chart')}
          >
            Live Chart
          </button>
          <button
            type="button"
            className={`btn btn-ghost ${view === 'table' ? 'active' : ''}`}
            onClick={() => setView('table')}
          >
            Live Table
          </button>
          <span className="muted" style={{ alignSelf: 'center', marginLeft: 8 }}>
            {points.length} points
            {eventsLoading ? ' · predictions loading…' : ''}
          </span>
        </div>

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
    </>
  )
}
