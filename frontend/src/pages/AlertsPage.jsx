import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import DeviceChart from '../components/DeviceChart'

const POLL_MS = 15000 // pick up the 30-min agent soon after it finishes
const AGENT_INTERVAL_MS = 30 * 60 * 1000
const NEW_CASE_HIGHLIGHT_MS = 2 * 60 * 1000
// Window either side of an event: enough to show the level before the drop and
// whether it came back afterwards, which is the whole question.
const EVENT_CONTEXT_HOURS = 2
// Clicking a vehicle shows a wider view so all of its events sit in one picture.
const VEHICLE_CONTEXT_HOURS = 12
// Ad-hoc slab check: same length as the agent's cadence; any past day is allowed.
const SLAB_MINUTES = 30
const MAX_LIVE_POINTS = 2500

const fmtL = (v) => `${Number(v || 0).toFixed(1)} L`
const hhmm = (ts) => (ts ? String(ts).slice(11, 16) : '—')
const pretty = (r) => String(r || '').replace(/_/g, ' ')
const isTheft = (e) => /theft/i.test(e?.eventType || '')

function eventKey(e) {
  return `${e?.deviceName || ''}|${e?.eventTime || ''}|${e?.eventType || ''}|${e?.volume ?? ''}`
}

/** Short on-screen buzz when the 30-min agent finds new cases (Web Audio). */
function playAlertBuzz({ theft = 0, fill = 0 } = {}) {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext
    if (!Ctx) return
    const ctx = new Ctx()
    const beep = (freq, start, dur) => {
      const o = ctx.createOscillator()
      const g = ctx.createGain()
      o.type = 'square'
      o.frequency.value = freq
      g.gain.setValueAtTime(0.0001, start)
      g.gain.exponentialRampToValueAtTime(0.12, start + 0.02)
      g.gain.exponentialRampToValueAtTime(0.0001, start + dur)
      o.connect(g)
      g.connect(ctx.destination)
      o.start(start)
      o.stop(start + dur + 0.05)
    }
    const t0 = ctx.currentTime
    if (theft > 0) {
      beep(880, t0, 0.16)
      beep(660, t0 + 0.2, 0.16)
      beep(990, t0 + 0.4, 0.22)
    } else if (fill > 0) {
      beep(523, t0, 0.18)
      beep(659, t0 + 0.2, 0.22)
    }
    setTimeout(() => ctx.close?.(), 1200)
  } catch (_) {
    /* autoplay / unsupported — visual banner still shows */
  }
}

function fmtCountdown(ms) {
  if (ms == null || ms < 0) return '—'
  const s = Math.ceil(ms / 1000)
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${String(r).padStart(2, '0')}`
}

const p2 = (n) => String(n).padStart(2, '0')

/** Local wall-clock ISO — the backend compares against naive local timestamps. */
function toLocalIso(d) {
  return (
    `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}` +
    `T${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`
  )
}

function shiftHours(ts, h) {
  const d = new Date(String(ts).replace(' ', 'T'))
  if (Number.isNaN(d.getTime())) return null
  d.setHours(d.getHours() + h)
  return toLocalIso(d)
}

function isLiveWindow(toIso) {
  if (!toIso) return true
  const to = new Date(String(toIso).replace(' ', 'T'))
  if (Number.isNaN(to.getTime())) return true
  return Date.now() - to.getTime() < 3 * 3600 * 1000
}

/** For recent cases, stretch the right edge to "now" so SSE can keep appending. */
function liveTraceWindow(from, to) {
  const fromIso = from
  let toIso = to
  if (isLiveWindow(to)) {
    toIso = toLocalIso(new Date())
  }
  return { fromIso, toIso, live: isLiveWindow(toIso) }
}

const ymd = (d) => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`
const hhmmOf = (d) => `${p2(d.getHours())}:${p2(d.getMinutes())}`

/** Start of the half-hour slab in progress, as HH:MM. */
function currentSlabStart(nowMs) {
  const stepMs = SLAB_MINUTES * 60000
  return hhmmOf(new Date(Math.floor(nowMs / stepMs) * stepMs))
}

function Caret({ open }) {
  return <span className="row-caret">{open ? '▾' : '▸'}</span>
}

const asNum = (v) => (v === '' || v == null ? null : Number(v))
// CSV round-trips every field as text, so "False" arrives truthy.
const asBool = (v) => v === true || String(v).toLowerCase() === 'true'

/**
 * Rows read back from a day's CSV are all strings, and the volume column is
 * written as `volume_L` (see alerts.py). Bring them back to the shape the live
 * digest uses so the tables and the chart markers read one kind of event.
 */
function normalizeStoredEvent(r) {
  return {
    ...r,
    volume: asNum(r.volume ?? r.volume_L) ?? 0,
    confidence: asNum(r.confidence),
    durationMinutes: asNum(r.durationMinutes),
    mlConfidence: asNum(r.mlConfidence),
    tamper: asBool(r.tamper),
    trulyParked: asBool(r.trulyParked),
  }
}

/** "Today" / "Yesterday" / "Thu 20 Aug" for history day cards. */
function dayLabel(day) {
  const today = ymd(new Date())
  const yest = ymd(new Date(Date.now() - 86400000))
  if (day === today) return 'Today'
  if (day === yest) return 'Yesterday'
  const d = new Date(`${day}T12:00:00`)
  if (Number.isNaN(d.getTime())) return day
  return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
}

function emptyDaySummary(d) {
  return !(d.theftCount || d.fillCount || d.filteredCount || d.vehicles)
}

/**
 * Live / animated fuel graph for an alert case — same SSE path as Live Map.
 * Loads a seed series, then streams points so fills/thefts are not a static snapshot.
 */
function LiveTrace({ deviceName, from, to, emptyNote, events = [] }) {
  const [points, setPoints] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [streaming, setStreaming] = useState(true)
  const [streamStatus, setStreamStatus] = useState('')
  const esRef = useRef(null)
  const seenRef = useRef(new Set())
  const loadGen = useRef(0)
  const windowRef = useRef(liveTraceWindow(from, to))

  const stopStream = useCallback(() => {
    if (esRef.current) {
      esRef.current.close()
      esRef.current = null
    }
  }, [])

  const appendPoint = useCallback((point) => {
    const key = `${point.time}|${point.fuelLevel}|${point.powerEx}`
    if (seenRef.current.has(key)) return
    seenRef.current.add(key)
    setPoints((prev) => {
      const next = [...prev, point]
      return next.length > MAX_LIVE_POINTS
        ? next.slice(next.length - MAX_LIVE_POINTS)
        : next
    })
  }, [])

  const startStream = useCallback(
    (selected, fromIso, toIso, seedPoints, live) => {
      stopStream()
      if (!selected) return
      const hours = Math.max(
        2,
        Math.ceil(
          (new Date(String(toIso).replace(' ', 'T')) -
            new Date(String(fromIso).replace(' ', 'T'))) /
            3600000
        ) || 4
      )
      const since = seedPoints.length ? seedPoints[seedPoints.length - 1].time : ''
      const url =
        `/api/live/${encodeURIComponent(selected)}/sse` +
        `?from=${encodeURIComponent(fromIso)}` +
        `&to=${encodeURIComponent(toIso)}` +
        `&hours=${hours}&live=${live ? '1' : '0'}&slab=0&interval_ms=60&limit=2000` +
        (since ? `&since=${encodeURIComponent(since)}` : '')
      const es = new EventSource(url)
      esRef.current = es
      setStreamStatus(live ? 'LIVE streaming…' : 'Playing event window…')

      es.addEventListener('status', (ev) => {
        try {
          const data = JSON.parse(ev.data)
          if (data.status === 'streaming') {
            setStreamStatus(
              live
                ? `LIVE — ${data.queued || 0} points queued`
                : `Playing ${data.queued || 0} points`
            )
          } else if (data.status === 'complete') {
            setStreamStatus(live ? 'LIVE — waiting for next points…' : 'Playback complete')
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
        setStreamStatus((s) =>
          s.startsWith('LIVE') ? 'LIVE — waiting for next points…' : s
        )
      })

      es.onerror = () => {
        setStreamStatus('Stream reconnecting…')
      }
    },
    [appendPoint, stopStream]
  )

  useEffect(() => {
    if (!deviceName || !from || !to) return undefined
    const { fromIso, toIso, live } = liveTraceWindow(from, to)
    windowRef.current = { fromIso, toIso, live }
    const gen = ++loadGen.current
    let alive = true
    setLoading(true)
    setError('')
    setPoints([])
    seenRef.current = new Set()
    stopStream()

    ;(async () => {
      try {
        let series = await api.deviceSeries(deviceName, fromIso, toIso, {
          downsample: 1,
          limit: 5000,
          fetch: '0',
        })
        if (!alive || gen !== loadGen.current) return
        let pts = series.points || []
        if (!pts.length) {
          setStreamStatus('No local points — fetching FuelArk…')
          series = await api.deviceSeries(deviceName, fromIso, toIso, {
            downsample: 1,
            limit: 5000,
            fetch: 'auto',
          })
          if (!alive || gen !== loadGen.current) return
          pts = series.points || []
        }
        pts.forEach((p) => {
          seenRef.current.add(`${p.time}|${p.fuelLevel}|${p.powerEx}`)
        })
        setPoints(pts)
        setLoading(false)
        if (!pts.length) {
          setError('')
          setStreamStatus(emptyNote || 'No telemetry in this window')
          return
        }
        if (streaming) {
          startStream(deviceName, fromIso, toIso, pts, live)
        } else {
          setStreamStatus(`Loaded ${pts.length} points`)
        }
      } catch (e) {
        if (alive && gen === loadGen.current) {
          setLoading(false)
          setError(e.message)
        }
      }
    })()

    return () => {
      alive = false
      stopStream()
    }
    // streaming toggled separately below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceName, from, to])

  useEffect(() => {
    if (!streaming) {
      stopStream()
      setStreamStatus((s) => (s ? 'Streaming paused' : s))
      return undefined
    }
    if (!deviceName || !points.length || loading) return undefined
    const { fromIso, toIso, live } = windowRef.current
    startStream(deviceName, fromIso, toIso, points, live)
    return () => stopStream()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming])

  const pauseStreaming = useCallback(() => {
    if (!streaming) return
    setStreaming(false)
    stopStream()
    setStreamStatus('Streaming paused — click ● Live to resume')
  }, [streaming, stopStream])

  if (loading) return <p className="alerts-hint">Loading live graph…</p>
  if (error) return <p className="error">{error}</p>
  if (!points.length) {
    return <p className="alerts-hint">{streamStatus || emptyNote}</p>
  }

  return (
    <>
      <div className="detail-head" style={{ marginBottom: 8 }}>
        <span className={`chip ${streaming ? 'chip--good' : ''}`}>
          {streaming ? '● Live graph' : 'Paused'}
        </span>
        <span className="alerts-hint" style={{ margin: 0 }}>
          {points.length} readings
          {events.length
            ? ` · ${events.length} event${events.length === 1 ? '' : 's'} marked`
            : ''}
          {streamStatus ? ` · ${streamStatus}` : ''}
        </span>
        <button
          type="button"
          className={`btn-alert${streaming ? ' btn-alert--primary' : ''}`}
          onClick={() => setStreaming((v) => !v)}
        >
          {streaming ? '● Live ON' : 'Live OFF'}
        </button>
      </div>
      <DeviceChart
        deviceName={deviceName}
        points={points}
        events={events}
        streaming={streaming}
        onPauseStreaming={pauseStreaming}
      />
    </>
  )
}

function liveReportUrl(deviceName, eventTime, endTime, hours = VEHICLE_CONTEXT_HOURS) {
  const q = new URLSearchParams()
  q.set('device', String(deviceName || '').trim())
  const from = shiftHours(eventTime, -hours)
  const to = shiftHours(endTime || eventTime, hours)
  if (from) q.set('from', from)
  if (to) q.set('to', to)
  q.set('stream', '1')
  return `/live?${q.toString()}`
}

function AlertActions({ deviceName, eventTime, endTime, expanded, onGraph }) {
  const reportTo = liveReportUrl(deviceName, eventTime, endTime, EVENT_CONTEXT_HOURS)
  return (
    <div className="alert-actions" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        className={`btn-alert${expanded ? ' is-open' : ''}`}
        onClick={onGraph}
        title="Show live fuel graph around this case"
      >
        {expanded ? 'Hide graph' : 'View live graph'}
      </button>
      <Link
        className="btn-alert btn-alert--primary"
        to={reportTo}
        title="Open Live Map with streaming graph and fill/theft reports"
      >
        Full report →
      </Link>
    </div>
  )
}

/** A single event: buttons to view graph here or open full vehicle report. */
function EventRow({ ev, expanded, onToggle, showDate = false }) {
  const from = shiftHours(ev.eventTime, -EVENT_CONTEXT_HOURS)
  const to = shiftHours(ev.endTime || ev.eventTime, EVENT_CONTEXT_HOURS)
  const suppressed = Boolean(ev.suppressedReason) || ev.status === 'filtered'
  const theft = isTheft(ev)
  const timeLabel = showDate
    ? String(ev.eventTime || '').replace('T', ' ').slice(0, 16)
    : hhmm(ev.eventTime)

  return (
    <>
      <tr className={`row-clickable${expanded ? ' is-open' : ''}${suppressed ? ' row-suppressed' : ''}`}>
        <td className="col-time" onClick={onToggle}>
          <Caret open={expanded} />{timeLabel}
        </td>
        <td onClick={onToggle}>{ev.deviceName}</td>
        <td className={`num col-qty ${theft ? 'qty-theft' : 'qty-fill'}`} onClick={onToggle}>
          {fmtL(ev.volume)}
        </td>
        {suppressed
          ? <td className="col-reason" onClick={onToggle}>{pretty(ev.suppressedReason) || 'filtered'}</td>
          : <td className="num col-conf" onClick={onToggle}>{Number(ev.confidence || 0).toFixed(2)}</td>}
        <td className="col-actions">
          <AlertActions
            deviceName={ev.deviceName}
            eventTime={ev.eventTime}
            endTime={ev.endTime || ev.eventTime}
            expanded={expanded}
            onGraph={onToggle}
          />
        </td>
      </tr>
      {expanded ? (
        <tr>
          <td className="detail-cell" colSpan={5}>
            <div className="detail-head">
              <span className={`chip ${theft ? 'chip--critical' : 'chip--good'}`}>{ev.eventType}</span>
              <span className="detail-title">{ev.deviceName}</span>
              <Link
                className="btn-alert btn-alert--primary"
                to={liveReportUrl(ev.deviceName, ev.eventTime, ev.endTime || ev.eventTime)}
              >
                Open full vehicle report →
              </Link>
            </div>
            <div className="detail-meta" style={{ marginBottom: 10 }}>
              <span className="chip">{String(ev.eventTime).replace('T', ' ').slice(0, 19)}</span>
              {ev.endTime ? <span className="chip">ends {hhmm(ev.endTime)}</span> : null}
              {ev.durationMinutes != null
                ? <span className="chip">{Number(ev.durationMinutes).toFixed(1)} min</span> : null}
              {ev.ignition ? <span className="chip">ignition {ev.ignition}</span> : null}
              {ev.trulyParked ? <span className="chip">parked</span> : null}
              {ev.tamper ? <span className="chip chip--warning">tamper</span> : null}
              {ev.decision ? <span className="chip">{ev.decision}</span> : null}
              {ev.mlLabel
                ? <span className="chip">ML {ev.mlLabel} {Number(ev.mlConfidence || 0).toFixed(2)}</span>
                : null}
              {suppressed
                ? <span className="chip chip--warning">filtered: {pretty(ev.suppressedReason)}</span>
                : null}
            </div>
            <LiveTrace
              deviceName={ev.deviceName} from={from} to={to} events={[ev]}
              emptyNote="No telemetry in this window — the change was never observed."
            />
          </td>
        </tr>
      ) : null}
    </>
  )
}

/** A past day's stored alerts, fetched the first time the day is opened. */
function useDayAlerts(day, enabled) {
  const [state, setState] = useState({ rows: null, loading: false, error: '' })

  useEffect(() => {
    if (!enabled || !day) return undefined
    let alive = true
    setState({ rows: null, loading: true, error: '' })
    api
      .alertsHistory({ day, limit: 500, filtered: false })
      .then((d) => {
        if (!alive) return
        const rows = (d.alerts || []).map(normalizeStoredEvent)
        setState({ rows, loading: false, error: '' })
      })
      .catch((e) => alive && setState({ rows: [], loading: false, error: e.message }))
    return () => { alive = false }
  }, [day, enabled])

  return state
}

/** A day from the history: open it to reach every event it recorded, on the graph. */
function DayCard({ d, expanded, onToggle }) {
  const { rows, loading, error } = useDayAlerts(d.day, expanded)
  const [openEvent, setOpenEvent] = useState(null)
  const empty = emptyDaySummary(d)
  const hasAlerts = Boolean(d.theftCount || d.fillCount)

  return (
    <div className={`hist-day${expanded ? ' is-open' : ''}${empty ? ' is-empty' : ''}`}>
      <div className="hist-day__row">
        <button
          type="button"
          className="hist-day__main"
          onClick={onToggle}
          aria-expanded={expanded}
          title={empty ? 'No events this day' : 'Show this day’s events'}
        >
          <Caret open={expanded} />
          <div className="hist-day__date">
            <strong>{dayLabel(d.day)}</strong>
            <span className="hist-day__ymd">{d.day}</span>
          </div>
          {empty ? (
            <span className="hist-day__quiet">No events</span>
          ) : (
            <div className="hist-day__stats">
              {d.theftCount ? (
                <span className="hist-stat hist-stat--theft">
                  {d.theftCount} theft · {fmtL(d.theftLiters)}
                </span>
              ) : null}
              {d.fillCount ? (
                <span className="hist-stat hist-stat--fill">
                  {d.fillCount} fill · {fmtL(d.fillLiters)}
                </span>
              ) : null}
              {d.vehicles ? (
                <span className="hist-stat">{d.vehicles} vehicle{d.vehicles === 1 ? '' : 's'}</span>
              ) : null}
              {d.filteredCount ? (
                <span
                  className="hist-stat hist-stat--dropped"
                  title="Rejected by confirmation (noise / not a real fill or theft)"
                >
                  {d.filteredCount} dropped
                </span>
              ) : null}
            </div>
          )}
        </button>
        {!empty ? (
          <div className="hist-day__exports" onClick={(e) => e.stopPropagation()}>
            {hasAlerts ? (
              <a
                className="btn-alert"
                href={api.alertsCsvUrl(d.day)}
                download
                title="Confirmed thefts and fills for this day"
              >
                Alerts CSV
              </a>
            ) : null}
            {d.filteredCount ? (
              <a
                className="btn-alert"
                href={api.alertsCsvUrl(d.day, 'filtered')}
                download
                title="Events the system rejected as noise"
              >
                Dropped CSV
              </a>
            ) : null}
          </div>
        ) : null}
      </div>

      {expanded ? (
        <div className="hist-day__detail">
          {loading ? <p className="alerts-hint">Loading {d.day}…</p> : null}
          {error ? <p className="error">{error}</p> : null}
          {rows?.length ? (
            <>
              <p className="alerts-hint">
                {rows.length} recorded event{rows.length === 1 ? '' : 's'} · click one for its fuel trace
              </p>
              <div className="alerts-table-wrap">
                <table className="alerts-table">
                  <thead>
                    <tr>
                      <th className="col-time">Time</th>
                      <th>Vehicle</th>
                      <th className="col-qty num">Litres</th>
                      <th className="col-conf num">Confidence / reason</th>
                      <th className="col-actions">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((ev, i) => {
                      const key = `${d.day}-${i}`
                      return (
                        <EventRow
                          key={key}
                          ev={ev}
                          expanded={openEvent === key}
                          onToggle={() => setOpenEvent(openEvent === key ? null : key)}
                        />
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
          {rows && !rows.length && !loading ? (
            <p className="alerts-hint">No events stored for this day.</p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

/**
 * Fleet history: last 7 days day-wise on screen; older days via date picker.
 * Optional Scan day builds alerts CSV from stored May–July + live telemetry.
 */
function FleetHistoryBrowser({ days, onDaysChanged }) {
  const today = ymd(new Date())
  const weekAgo = ymd(new Date(Date.now() - 6 * 86400000))
  const [fromDay, setFromDay] = useState(weekAgo)
  const [toDay, setToDay] = useState(today)
  const [searching, setSearching] = useState(false)
  const [searchDays, setSearchDays] = useState(null)
  const [vehicleQ, setVehicleQ] = useState('')
  const [kind, setKind] = useState('all')
  const [rows, setRows] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [openEvent, setOpenEvent] = useState(null)
  const [backfillDay, setBackfillDay] = useState(today)
  const [backfilling, setBackfilling] = useState(false)
  const [backfillMsg, setBackfillMsg] = useState('')
  const [openDay, setOpenDay] = useState(null)

  const byDay = useMemo(() => {
    const map = new Map((days || []).map((d) => [d.day, d]))
    return map
  }, [days])

  /** Always show the last 7 calendar days (newest first), even if empty. */
  const last7 = useMemo(() => {
    const out = []
    for (let i = 0; i < 7; i += 1) {
      const d = new Date()
      d.setHours(12, 0, 0, 0)
      d.setDate(d.getDate() - i)
      const key = ymd(d)
      out.push(
        byDay.get(key) || {
          day: key,
          theftCount: 0,
          theftLiters: 0,
          fillCount: 0,
          fillLiters: 0,
          filteredCount: 0,
          vehicles: 0,
        }
      )
    }
    return out
  }, [byDay])

  const visibleDays = searching && searchDays ? searchDays : last7

  const runDaySearch = () => {
    if (!fromDay || !toDay) return
    const start = fromDay <= toDay ? fromDay : toDay
    const end = fromDay <= toDay ? toDay : fromDay
    const out = []
    const cur = new Date(`${start}T12:00:00`)
    const endD = new Date(`${end}T12:00:00`)
    while (cur <= endD) {
      const key = ymd(cur)
      out.push(
        byDay.get(key) || {
          day: key,
          theftCount: 0,
          theftLiters: 0,
          fillCount: 0,
          fillLiters: 0,
          filteredCount: 0,
          vehicles: 0,
        }
      )
      cur.setDate(cur.getDate() + 1)
    }
    out.reverse()
    setSearchDays(out)
    setSearching(true)
    setOpenDay(null)
    setRows(null)
  }

  const showLast7 = () => {
    setSearching(false)
    setSearchDays(null)
    setFromDay(weekAgo)
    setToDay(today)
    setRows(null)
    setOpenDay(null)
    setVehicleQ('')
  }

  const searchEvents = useCallback(async () => {
    const q = vehicleQ.trim()
    if (!q && !searching) {
      setRows(null)
      return
    }
    setLoading(true)
    setError('')
    setOpenEvent(null)
    try {
      const rangeFrom = searching && searchDays?.length
        ? searchDays[searchDays.length - 1].day
        : weekAgo
      const rangeTo = searching && searchDays?.length ? searchDays[0].day : today
      const data = await api.alertsHistory({
        from: rangeFrom,
        to: rangeTo,
        q,
        limit: 2000,
        filtered: kind === 'filtered' || kind === 'all',
      })
      let list = (data.alerts || []).map(normalizeStoredEvent)
      if (kind === 'theft') {
        list = list.filter((e) => e.status !== 'filtered' && isTheft(e))
      } else if (kind === 'fill') {
        list = list.filter((e) => e.status !== 'filtered' && !isTheft(e))
      } else if (kind === 'filtered') {
        list = list.filter((e) => e.status === 'filtered')
      } else {
        list = list.filter((e) => e.status !== 'filtered')
      }
      setRows(list)
    } catch (e) {
      setRows([])
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [vehicleQ, kind, searching, searchDays, weekAgo, today])

  const runBackfill = async () => {
    if (!backfillDay) return
    setBackfilling(true)
    setBackfillMsg('')
    setError('')
    try {
      const d = await api.alertsBackfill(backfillDay, true)
      setBackfillMsg(
        `${d.day}: ${d.theftCount} theft · ${d.fillCount} fill` +
          (d.suppressedCount ? ` · ${d.suppressedCount} dropped` : '') +
          ` (${d.tookSeconds}s)`
      )
      onDaysChanged?.()
    } catch (e) {
      setError(e.message || String(e))
    } finally {
      setBackfilling(false)
    }
  }

  return (
    <div className="panel alerts-section alerts-section--history">
      <div className="alerts-section__head">
        <h3>Fleet history</h3>
        <span className="chip chip--muted">Past &amp; present</span>
      </div>

      <div className="alerts-toolbar">
        <div className="alerts-toolbar__group">
          <span className="alerts-toolbar__label">Days</span>
          <button
            type="button"
            className={`btn btn-sm${!searching ? ' btn-primary' : ' btn-ghost'}`}
            onClick={showLast7}
          >
            Last 7 days
          </button>
          <label className="alerts-field">
            From
            <input type="date" value={fromDay} max={today}
                   onChange={(e) => setFromDay(e.target.value)} />
          </label>
          <label className="alerts-field">
            To
            <input type="date" value={toDay} max={today}
                   onChange={(e) => setToDay(e.target.value)} />
          </label>
          <button type="button" className="btn btn-sm btn-primary" onClick={runDaySearch}>
            Search
          </button>
        </div>

        <div className="alerts-toolbar__group">
          <span className="alerts-toolbar__label">Vehicle</span>
          <input
            type="search"
            className="alerts-input"
            placeholder="Name or IMEI…"
            value={vehicleQ}
            onChange={(e) => setVehicleQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && searchEvents()}
            aria-label="Filter by vehicle"
          />
          <select className="alerts-input" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="all">Fills + thefts</option>
            <option value="theft">Thefts only</option>
            <option value="fill">Fills only</option>
            <option value="filtered">Dropped only</option>
          </select>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={searchEvents}
            disabled={loading || !vehicleQ.trim()}
          >
            {loading ? '…' : 'Find'}
          </button>
        </div>

        <div className="alerts-toolbar__group alerts-toolbar__group--accent">
          <span className="alerts-toolbar__label">Build day</span>
          <input type="date" className="alerts-input" value={backfillDay} max={today}
                 onChange={(e) => setBackfillDay(e.target.value)} />
          <button
            type="button"
            className="btn btn-sm btn-custom"
            onClick={runBackfill}
            disabled={backfilling || !backfillDay}
          >
            {backfilling ? '…' : 'Scan day'}
          </button>
          {backfillMsg ? <span className="muted" style={{ fontSize: 12 }}>{backfillMsg}</span> : null}
        </div>
      </div>

      {error ? <p className="error">{error}</p> : null}

      {rows?.length ? (
        <div className="alerts-history-events">
          <div className="alerts-section__head">
            <h4>Matching events</h4>
            <button type="button" className="link-inline" onClick={() => setRows(null)}>
              Clear
            </button>
          </div>
          <div className="alerts-table-wrap">
            <table className="alerts-table">
              <thead>
                <tr>
                  <th className="col-time">Time</th>
                  <th>Vehicle</th>
                  <th className="col-qty num">Litres</th>
                  <th className="col-conf num">Confidence / reason</th>
                  <th className="col-actions">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((ev, i) => {
                  const key = `hist-${ev.deviceName}-${ev.eventTime}-${i}`
                  return (
                    <EventRow
                      key={key}
                      ev={ev}
                      showDate
                      expanded={openEvent === key}
                      onToggle={() => setOpenEvent(openEvent === key ? null : key)}
                    />
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      {rows && !rows.length && !loading ? (
        <p className="alerts-empty">No matching events.</p>
      ) : null}

      <div className="alerts-section__head alerts-history-days-head">
        <h4>{searching ? 'Days in range' : 'Last 7 days'}</h4>
        <p className="alerts-hint" style={{ margin: 0 }}>
          Expand a day for events. <strong>Alerts CSV</strong> = confirmed cases ·{' '}
          <strong>Dropped CSV</strong> = rejected noise (not real fills/thefts).
        </p>
      </div>
      <div className="hist-day-list">
        {visibleDays.map((d) => (
          <DayCard
            key={d.day}
            d={d}
            expanded={openDay === d.day}
            onToggle={() => setOpenDay(openDay === d.day ? null : d.day)}
          />
        ))}
      </div>
    </div>
  )
}

function Stat({ value, label, variant }) {
  return (
    <div className={`alert-stat${variant ? ` alert-stat--${variant}` : ''}`}>
      <div className="alert-stat__value">{value}</div>
      <div className="alert-stat__label">{label}</div>
    </div>
  )
}

export default function AlertsPage() {
  const [digest, setDigest] = useState(null)
  const [error, setError] = useState('')
  const [running, setRunning] = useState(false)
  const [openEvent, setOpenEvent] = useState(null)
  const [profiles, setProfiles] = useState(null)
  const [history, setHistory] = useState(null)
  const [slabResult, setSlabResult] = useState(null)
  const [slabRunning, setSlabRunning] = useState(false)
  const [slabClock, setSlabClock] = useState(() => Date.now())
  // Any calendar day + start time → 30-minute read-only slab.
  const [slabDay, setSlabDay] = useState(() => ymd(new Date()))
  const [slabStart, setSlabStart] = useState(() => currentSlabStart(Date.now()))
  const [customFromDay, setCustomFromDay] = useState(() => ymd(new Date()))
  const [customFromTime, setCustomFromTime] = useState(() => currentSlabStart(Date.now()))
  const [customToDay, setCustomToDay] = useState(() => ymd(new Date()))
  const [customToTime, setCustomToTime] = useState(() => {
    const d = new Date()
    d.setMinutes(d.getMinutes() + SLAB_MINUTES)
    return hhmmOf(d)
  })
  const [liveFlash, setLiveFlash] = useState(null)
  const [newKeys, setNewKeys] = useState(() => new Set())
  const [backendLoading, setBackendLoading] = useState(false)
  const prevDigestAt = useRef(null)
  const knownEventKeys = useRef(new Set())
  const highlightTimer = useRef(null)

  const load = useCallback(async () => {
    try {
      setDigest(await api.alertsLatest())
      setError('')
      setBackendLoading(false)
    } catch (e) {
      const msg = e.message || String(e)
      const loading = /loading|503|502|Bad Gateway/i.test(msg) || e.payload?.loading
      setBackendLoading(Boolean(loading))
      setError(
        loading
          ? (e.payload?.error
            || 'Backend is loading May–July data into memory. This can take several minutes — the page will retry automatically.')
          : msg
      )
    }
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, POLL_MS)
    return () => clearInterval(id)
  }, [load])

  // 1s tick for next-agent countdown; slab clock also refreshes.
  useEffect(() => {
    const id = setInterval(() => setSlabClock(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => { api.voltageProfiles().then(setProfiles).catch(() => {}) }, [])
  const refreshHistory = useCallback(() => {
    api.alertsDays().then(setHistory).catch(() => {})
  }, [])
  useEffect(() => { refreshHistory() }, [digest?.generatedAt, refreshHistory])

  // When the 30-min agent finishes a new cycle: buzz + show new cases on screen.
  useEffect(() => {
    if (!digest || digest.pending || !digest.generatedAt) return
    const at = String(digest.generatedAt)
    const events = [...(digest.thefts || []), ...(digest.fills || [])]
    const prev = prevDigestAt.current

    if (!prev) {
      // First paint — seed without buzzing.
      prevDigestAt.current = at
      events.forEach((e) => knownEventKeys.current.add(eventKey(e)))
      return
    }
    if (prev === at) return

    prevDigestAt.current = at
    const fresh = events.filter((e) => !knownEventKeys.current.has(eventKey(e)))
    events.forEach((e) => knownEventKeys.current.add(eventKey(e)))

    const theftN = fresh.filter(isTheft).length
    const fillN = fresh.filter((e) => !isTheft(e)).length
    if (!theftN && !fillN) return

    setSlabResult(null)
    setOpenEvent(null)
    setLiveFlash({
      theft: theftN,
      fill: fillN,
    })
    const keys = new Set(fresh.map(eventKey))
    setNewKeys(keys)
    playAlertBuzz({ theft: theftN, fill: fillN })

    if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
      try {
        new Notification('Fuel Alerts — new cases', {
          body: `${theftN} theft · ${fillN} fill · calibrated 4h window`,
          tag: 'fuel-alerts-cycle',
        })
      } catch (_) {}
    }

    if (highlightTimer.current) clearTimeout(highlightTimer.current)
    highlightTimer.current = setTimeout(() => {
      setNewKeys(new Set())
    }, NEW_CASE_HIGHLIGHT_MS)
  }, [digest])

  useEffect(() => () => {
    if (highlightTimer.current) clearTimeout(highlightTimer.current)
  }, [])

  const enableBrowserNotify = () => {
    if (typeof Notification === 'undefined') return
    Notification.requestPermission().catch(() => {})
  }

  const runNow = async () => {
    setRunning(true)
    try {
      const d = await api.alertsRun()
      setDigest(d)
      setSlabResult(null)
    } catch (e) { setError(e.message) }
    finally { setRunning(false) }
  }

  // Fixed 30-min slab window.
  const slab30 = useMemo(() => {
    if (!slabDay || !slabStart) return null
    const from = new Date(`${slabDay}T${slabStart}:00`)
    if (Number.isNaN(from.getTime())) return null
    const to = new Date(from.getTime() + SLAB_MINUTES * 60000)
    return {
      from: toLocalIso(from),
      to: toLocalIso(to),
      endLabel: hhmmOf(to),
      label: `${slabDay} ${slabStart}–${hhmmOf(to)}`,
      future: slabClock - from.getTime() < 0,
      minutes: SLAB_MINUTES,
      invalid: false,
      invalidReason: '',
    }
  }, [slabDay, slabStart, slabClock])

  // Custom from → to window (date + times, max 12h).
  const slabCustom = useMemo(() => {
    if (!customFromDay || !customFromTime || !customToDay || !customToTime) return null
    const from = new Date(`${customFromDay}T${customFromTime}:00`)
    const to = new Date(`${customToDay}T${customToTime}:00`)
    if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime())) return null
    const minutes = Math.round((to - from) / 60000)
    return {
      from: toLocalIso(from),
      to: toLocalIso(to),
      endLabel: `${customToDay} ${customToTime}`,
      label: `${customFromDay} ${customFromTime} → ${customToDay} ${customToTime}`,
      future: to.getTime() > slabClock,
      minutes,
      invalid: to <= from || minutes > 12 * 60,
      invalidReason:
        to <= from
          ? 'End must be after start'
          : minutes > 12 * 60
            ? 'Max window is 12 hours'
            : '',
    }
  }, [customFromDay, customFromTime, customToDay, customToTime, slabClock])

  const checkSlab = async (windowSpec) => {
    if (!windowSpec || windowSpec.future || windowSpec.invalid) return
    setSlabRunning(true)
    setError('')
    try {
      const d = await api.alertsSlab(windowSpec.from, windowSpec.to)
      setSlabResult({ ...d, slabLabel: windowSpec.label })
      setOpenEvent(null)
    } catch (e) { setError(e.message) }
    finally { setSlabRunning(false) }
  }

  // Everything below the header renders whichever result is on screen: the agent's
  // latest cycle, or the slab someone asked about.
  const view = slabResult || digest

  const allEvents = useMemo(
    () => [...(view?.thefts || []), ...(view?.fills || [])],
    [view]
  )

  const needsCal = profiles?.needsRecalibration || []
  const scanned = view ? (view.devicesScanned || 0) - (view.devicesSkipped || 0) : 0
  const days = history?.days || []

  const windowH = view?.windowHours ?? view?.slab?.lookbackHours ?? 4

  const nextAgentMs = useMemo(() => {
    if (!digest?.generatedAt) return null
    const last = new Date(String(digest.generatedAt).replace(' ', 'T')).getTime()
    if (Number.isNaN(last)) return null
    return last + AGENT_INTERVAL_MS - slabClock
  }, [digest?.generatedAt, slabClock])

  return (
    <div className="alerts-page">
      <header className="topbar topbar-center">
        <div className="topbar-center-block">
          <h1>Fuel Alerts</h1>
          <p className="topbar-subtitle">
            Auto every 30 min
            {digest?.generatedAt
              ? ` · last run ${String(digest.generatedAt).replace('T', ' ')}`
              : ''}
          </p>
          <div className="alerts-agent-status">
            <span className="chip chip--good">Auto ON</span>
            <span className="chip chip--blue">
              Next ~{fmtCountdown(nextAgentMs)}
            </span>
            {typeof Notification !== 'undefined' && Notification.permission === 'default' ? (
              <button type="button" className="btn btn-sm btn-ghost" onClick={enableBrowserNotify}>
                Desktop notify
              </button>
            ) : null}
          </div>
        </div>
      </header>

      <div className="content">
        {error ? <p className="error">{error}</p> : null}

        {liveFlash ? (
          <div
            className={`alerts-live-flash${liveFlash.theft ? ' alerts-live-flash--theft' : ' alerts-live-flash--fill'}`}
            role="status"
          >
            <div className="alerts-live-flash__body">
              <strong>New cases</strong>
              <span>
                {liveFlash.theft ? (
                  <span className="qty-theft">{liveFlash.theft} theft</span>
                ) : null}
                {liveFlash.theft && liveFlash.fill ? ' · ' : null}
                {liveFlash.fill ? (
                  <span className="qty-fill">{liveFlash.fill} fill</span>
                ) : null}
              </span>
            </div>
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => setLiveFlash(null)}
            >
              Dismiss
            </button>
          </div>
        ) : null}

        <div className="panel alerts-stats-panel">
          <div className="alert-stats">
            <Stat variant="theft" value={view?.theftCount ?? 0}
                  label={`Theft · ${fmtL(view?.theftLiters)}`} />
            <Stat variant="fill" value={view?.fillCount ?? 0}
                  label={`Fill · ${fmtL(view?.fillLiters)}`} />
            <Stat variant="held" value={view?.heldAwaitingSettle ?? 0}
                  label="Awaiting confirmation" />
            <div className="alert-stat__scanned">
              {scanned} vehicles · {windowH}h window
              {slabResult ? ' (slab)' : ' (scan)'}
            </div>
          </div>
        </div>

        {backendLoading ? (
          <div className="panel alerts-loading-banner">
            <strong>Backend loading telemetry…</strong>
            <p className="alerts-hint" style={{ marginBottom: 0 }}>
              May + June dumps + July daily are loading. Retrying every 15s.
            </p>
          </div>
        ) : null}

        {digest?.pending && !slabResult ? (
          <div className="panel"><p>No alert cycle yet — use Scan or wait for the auto agent.</p></div>
        ) : null}

        {slabResult ? (
          <div className="panel alerts-slab-banner">
            <div className="detail-head">
              <span className="chip">Slab prediction</span>
              <span className="detail-title">{slabResult.slabLabel}</span>
              <span className="muted" style={{ fontSize: 13 }}>
                {String(slabResult.slab?.from || '').replace('T', ' ').slice(0, 16)}
                {' → '}
                {String(slabResult.slab?.to || '').slice(11, 16)}
                {' · '}
                {slabResult.tookSeconds}s · not saved
              </span>
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                onClick={() => { setSlabResult(null); setOpenEvent(null) }}
              >
                Back to latest cycle
              </button>
            </div>
          </div>
        ) : null}

        <div className="alerts-scan-bar">
          <span className="alerts-scan-bar__title">Scan now</span>
          <span className="alerts-scan-bar__meta">Last 4 hours · all vehicles</span>
          <button
            type="button"
            className="btn btn-sm btn-scan"
            onClick={runNow}
            disabled={running || backendLoading}
          >
            {running ? '…' : 'Scan'}
          </button>
        </div>

        <div className="alerts-actions alerts-actions--split">
          <div className="alerts-action-card alerts-action-card--slab">
            <div className="alerts-action-card__head">
              <span className="alerts-action-card__title">30‑min slab</span>
              <button
                type="button"
                className="btn btn-sm btn-slab"
                onClick={() => checkSlab(slab30)}
                disabled={
                  !slab30 || slab30.future || slab30.invalid || slabRunning || backendLoading
                }
              >
                {slabRunning ? '…' : 'Predict'}
              </button>
            </div>
            <div className="alerts-field-grid alerts-field-grid--2">
              <label className="alerts-field">
                Day
                <input
                  type="date"
                  value={slabDay}
                  max={ymd(new Date(slabClock))}
                  onChange={(e) => setSlabDay(e.target.value)}
                  aria-label="Slab day"
                />
              </label>
              <label className="alerts-field">
                Start
                <input
                  type="time"
                  value={slabStart}
                  step={300}
                  onChange={(e) => setSlabStart(e.target.value)}
                  aria-label="Slab start time"
                />
              </label>
              <label className="alerts-field">
                End
                <input
                  type="time"
                  value={slab30?.endLabel || ''}
                  readOnly
                  aria-label="Slab end time"
                />
              </label>
              <label className="alerts-field">
                Length
                <input type="text" value={`${SLAB_MINUTES} min`} readOnly />
              </label>
            </div>
          </div>

          <div className="alerts-action-card alerts-action-card--custom">
            <div className="alerts-action-card__head">
              <span className="alerts-action-card__title">Custom from → to</span>
              <button
                type="button"
                className="btn btn-sm btn-custom"
                onClick={() => checkSlab(slabCustom)}
                disabled={
                  !slabCustom
                  || slabCustom.future
                  || slabCustom.invalid
                  || slabRunning
                  || backendLoading
                }
              >
                {slabRunning ? '…' : 'Predict'}
              </button>
            </div>
            <div className="alerts-field-grid alerts-field-grid--2">
              <label className="alerts-field">
                From date
                <input
                  type="date"
                  value={customFromDay}
                  max={ymd(new Date(slabClock))}
                  onChange={(e) => setCustomFromDay(e.target.value)}
                />
              </label>
              <label className="alerts-field">
                From time
                <input
                  type="time"
                  value={customFromTime}
                  step={60}
                  onChange={(e) => setCustomFromTime(e.target.value)}
                />
              </label>
              <label className="alerts-field">
                To date
                <input
                  type="date"
                  value={customToDay}
                  max={ymd(new Date(slabClock))}
                  onChange={(e) => setCustomToDay(e.target.value)}
                />
              </label>
              <label className="alerts-field">
                To time
                <input
                  type="time"
                  value={customToTime}
                  step={60}
                  onChange={(e) => setCustomToTime(e.target.value)}
                />
              </label>
            </div>
          </div>
        </div>

        {allEvents.length ? (
          <div className="panel alerts-section alerts-section--cases">
            <div className="alerts-section__head">
              <h3>Cases this cycle</h3>
              <span className="chip">{allEvents.length}</span>
            </div>
            <div className="alert-case-grid">
              {[...allEvents]
                .sort((a, b) => {
                  const an = newKeys.has(eventKey(a)) ? 0 : 1
                  const bn = newKeys.has(eventKey(b)) ? 0 : 1
                  return an - bn
                })
                .map((ev, i) => {
                const theft = isTheft(ev)
                const ek = eventKey(ev)
                const isNew = newKeys.has(ek)
                const key = `case-${ev.deviceName}-${ev.eventTime}-${i}`
                return (
                  <div
                    key={key}
                    className={`alert-case${theft ? ' alert-case--theft' : ' alert-case--fill'}${isNew ? ' alert-case--new' : ''}`}
                  >
                    <div className="alert-case__top">
                      <span className={`chip ${theft ? 'chip--critical' : 'chip--good'}`}>
                        {theft ? 'Theft' : 'Fill'}
                        {isNew ? ' · NEW' : ''}
                      </span>
                      <span className={`alert-case__liters ${theft ? 'qty-theft' : 'qty-fill'}`}>
                        {fmtL(ev.volume)}
                      </span>
                      <span className="alert-case__time">{hhmm(ev.eventTime)}</span>
                    </div>
                    <div className="alert-case__name" title={ev.deviceName}>{ev.deviceName}</div>
                    <AlertActions
                      deviceName={ev.deviceName}
                      eventTime={ev.eventTime}
                      endTime={ev.endTime || ev.eventTime}
                      expanded={openEvent === key}
                      onGraph={() => setOpenEvent(openEvent === key ? null : key)}
                    />
                    {openEvent === key ? (
                      <div className="alert-case__graph">
                        <LiveTrace
                          deviceName={ev.deviceName}
                          from={shiftHours(ev.eventTime, -EVENT_CONTEXT_HOURS)}
                          to={shiftHours(ev.endTime || ev.eventTime, EVENT_CONTEXT_HOURS)}
                          events={[ev]}
                          emptyNote="No telemetry in this window."
                        />
                      </div>
                    ) : null}
                  </div>
                )
              })}
            </div>
          </div>
        ) : null}

        <FleetHistoryBrowser days={days} onDaysChanged={refreshHistory} />

        {needsCal.length ? (
          <div className="panel alerts-section alerts-section--sensors">
            <div className="alerts-section__head">
              <h3>Sensors needing recalibration</h3>
              <span className="chip chip--warn">{needsCal.length}</span>
            </div>
            <div className="alerts-table-wrap">
              <table className="alerts-table">
                <thead>
                  <tr>
                    <th>Vehicle</th>
                    <th className="col-conf num">Correlation</th>
                    <th className="col-qty num">Phantom swing</th>
                    <th className="col-actions">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {needsCal.map((p) => (
                    <tr key={p.imei}>
                      <td>{p.vehicle || p.imei}</td>
                      <td className="num">{Number(p.corr).toFixed(2)}</td>
                      <td className="num qty-theft">{fmtL(p.swing_liters)}</td>
                      <td className="col-actions">
                        <Link className="btn-alert btn-alert--primary"
                              to={liveReportUrl(p.vehicle || '', new Date().toISOString(), new Date().toISOString(), 12)}>
                          Full report →
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}
