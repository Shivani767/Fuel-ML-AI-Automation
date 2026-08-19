import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import DeviceChart from '../components/DeviceChart'

const POLL_MS = 60000
// Window either side of an event: enough to show the level before the drop and
// whether it came back afterwards, which is the whole question.
const EVENT_CONTEXT_HOURS = 2
// Clicking a vehicle shows a wider view so all of its events sit in one picture.
const VEHICLE_CONTEXT_HOURS = 12
// Ad-hoc slab check: same length as the agent's cadence, over the last day.
const SLAB_MINUTES = 30
const SLAB_HOURS_BACK = 24
// Trace framing for a slab result — tight enough that 30 minutes fills the chart.
const SLAB_TRACE_CONTEXT_HOURS = 1.5
const SLAB_FIELD = {
  padding: '3px 8px',
  fontSize: 13,
  borderRadius: 6,
  border: '1px solid var(--border, #cbd5e1)',
}

const fmtL = (v) => `${Number(v || 0).toFixed(1)} L`
const hhmm = (ts) => (ts ? String(ts).slice(11, 16) : '—')
const pretty = (r) => String(r || '').replace(/_/g, ' ')
const isTheft = (e) => /theft/i.test(e?.eventType || '')

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

const ymd = (d) => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`
const hhmmOf = (d) => `${p2(d.getHours())}:${p2(d.getMinutes())}`

/** The two calendar days the last 24 hours can fall on. */
function dayOptions(nowMs) {
  const today = new Date(nowMs)
  const yesterday = new Date(nowMs - 86400000)
  const nice = (d) =>
    `${d.getDate()} ${d.toLocaleString(undefined, { month: 'short' })}`
  return [
    { value: ymd(today), label: `Today · ${nice(today)}` },
    { value: ymd(yesterday), label: `Yesterday · ${nice(yesterday)}` },
  ]
}

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

function Dash() {
  return <span className="dash">—</span>
}

/**
 * Load telemetry for a device/window once, when first shown.
 *
 * The dependency list is deliberately only the inputs. Including the hook's own
 * `loading`/`points` made setting `loading: true` re-run the effect, which fired
 * the previous run's cleanup and marked the in-flight request dead before it
 * resolved -- the trace then sat on "Loading trace…" for good.
 */
function useSeries(deviceName, from, to, enabled) {
  const [state, setState] = useState({ points: null, loading: false, error: '' })

  useEffect(() => {
    if (!enabled || !deviceName || !from || !to) return undefined
    let alive = true
    setState({ points: null, loading: true, error: '' })
    api
      .deviceSeries(deviceName, from, to, { downsample: 1, limit: 5000 })
      .then((d) => alive && setState({ points: d.points || [], loading: false, error: '' }))
      .catch((e) => alive && setState({ points: [], loading: false, error: e.message }))
    return () => { alive = false }
  }, [enabled, deviceName, from, to])

  return state
}

function Trace({ deviceName, from, to, emptyNote, events = [] }) {
  const { points, loading, error } = useSeries(deviceName, from, to, true)
  if (loading) return <p className="alerts-hint">Loading trace…</p>
  if (error) return <p className="error">{error}</p>
  if (points && !points.length) return <p className="alerts-hint">{emptyNote}</p>
  if (!points) return null
  return (
    <>
      <p className="alerts-hint">
        {points.length} readings · {String(from).replace('T', ' ')} → {String(to).replace('T', ' ')}
        {events.length
          ? ` · ${events.length} event${events.length === 1 ? '' : 's'} marked on the graph`
          : ''}
      </p>
      <DeviceChart deviceName={deviceName} points={points} events={events} />
    </>
  )
}

/** A vehicle from the rollup: click to see its whole day and every event on it. */
function VehicleRow({ v, events, expanded, onToggle, contextHours = VEHICLE_CONTEXT_HOURS }) {
  // Held steady across renders: the window feeds a fetch, and a `new Date()`
  // recomputed each render would re-request the series every time.
  const eventAnchor = events[0]?.eventTime
  const [fallbackAnchor] = useState(() => new Date().toISOString())
  const anchor = eventAnchor || fallbackAnchor
  const from = shiftHours(anchor, -contextHours)
  const to = shiftHours(anchor, contextHours)

  return (
    <>
      <tr
        className={`row-clickable${expanded ? ' is-open' : ''}`}
        onClick={onToggle}
        tabIndex={0}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onToggle())}
        title="Click to see this vehicle's fuel trace"
      >
        <td><Caret open={expanded} />{v.deviceName}</td>
        <td className="num">
          {v.theftCount ? <span className="qty-theft">{v.theftCount} · {fmtL(v.theftLiters)}</span> : <Dash />}
        </td>
        <td className="num">
          {v.fillCount ? <span className="qty-fill">{v.fillCount} · {fmtL(v.fillLiters)}</span> : <Dash />}
        </td>
      </tr>
      {expanded ? (
        <tr>
          <td className="detail-cell" colSpan={3}>
            <div className="detail-head">
              <span className="detail-title">{v.deviceName}</span>
              <span className="chip">
                {events.length} event{events.length === 1 ? '' : 's'} this cycle
              </span>
              <Link
                className="link-inline"
                to={`/live?device=${encodeURIComponent(v.deviceName)}`}
                onClick={(e) => e.stopPropagation()}
              >
                Open in Live Map →
              </Link>
            </div>

            {events.length ? (
              <table className="alerts-table" style={{ marginBottom: 12 }}>
                <thead>
                  <tr>
                    <th className="col-time">Time</th>
                    <th>Type</th>
                    <th className="col-qty num">Litres</th>
                    <th className="col-conf num">Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((e, i) => (
                    <tr key={i}>
                      <td className="col-time">{hhmm(e.eventTime)}</td>
                      <td>
                        <span className={`chip ${isTheft(e) ? 'chip--critical' : 'chip--good'}`}>
                          {e.eventType}
                        </span>
                      </td>
                      <td className={`num ${isTheft(e) ? 'qty-theft' : 'qty-fill'}`}>{fmtL(e.volume)}</td>
                      <td className="num">{Number(e.confidence || 0).toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}

            <Trace
              deviceName={v.deviceName} from={from} to={to} events={events}
              emptyNote="No telemetry in this window — the vehicle was not reporting."
            />
          </td>
        </tr>
      ) : null}
    </>
  )
}

/** A single event: click to see the trace tightly around it. */
function EventRow({ ev, expanded, onToggle }) {
  const from = shiftHours(ev.eventTime, -EVENT_CONTEXT_HOURS)
  const to = shiftHours(ev.endTime || ev.eventTime, EVENT_CONTEXT_HOURS)
  const suppressed = Boolean(ev.suppressedReason)
  const theft = isTheft(ev)

  return (
    <>
      <tr
        className={`row-clickable${expanded ? ' is-open' : ''}${suppressed ? ' row-suppressed' : ''}`}
        onClick={onToggle}
        tabIndex={0}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onToggle())}
        title="Click to see the fuel trace around this event"
      >
        <td className="col-time"><Caret open={expanded} />{hhmm(ev.eventTime)}</td>
        <td>{ev.deviceName}</td>
        <td className={`num col-qty ${theft ? 'qty-theft' : 'qty-fill'}`}>{fmtL(ev.volume)}</td>
        {suppressed
          ? <td className="col-reason">{pretty(ev.suppressedReason)}</td>
          : <td className="num col-conf">{Number(ev.confidence || 0).toFixed(2)}</td>}
      </tr>
      {expanded ? (
        <tr>
          <td className="detail-cell" colSpan={4}>
            <div className="detail-head">
              <span className={`chip ${theft ? 'chip--critical' : 'chip--good'}`}>{ev.eventType}</span>
              <span className="detail-title">{ev.deviceName}</span>
              <Link
                className="link-inline"
                to={`/live?device=${encodeURIComponent(ev.deviceName)}`}
                onClick={(e) => e.stopPropagation()}
              >
                Open in Live Map →
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
            <Trace
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
      .alertsHistory(day, 500, true)
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
function DayRow({ d, expanded, onToggle }) {
  const { rows, loading, error } = useDayAlerts(d.day, expanded)
  const [openEvent, setOpenEvent] = useState(null)

  return (
    <>
      <tr
        className={`row-clickable${expanded ? ' is-open' : ''}`}
        onClick={onToggle}
        tabIndex={0}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onToggle())}
        title="Click to see this day's events"
      >
        <td className="col-time"><Caret open={expanded} />{d.day}</td>
        <td className="num">
          {d.theftCount
            ? <span className="qty-theft">{d.theftCount} · {fmtL(d.theftLiters)}</span>
            : <Dash />}
        </td>
        <td className="num">
          {d.fillCount
            ? <span className="qty-fill">{d.fillCount} · {fmtL(d.fillLiters)}</span>
            : <Dash />}
        </td>
        <td className="num">{d.filteredCount || <Dash />}</td>
        <td className="num">{d.vehicles}</td>
        <td>
          <a className="link-inline" href={api.alertsCsvUrl(d.day)}
             onClick={(e) => e.stopPropagation()}>alerts ↓</a>
          {d.filteredCount ? (
            <>
              {' · '}
              <a className="link-inline" href={api.alertsCsvUrl(d.day, 'filtered')}
                 onClick={(e) => e.stopPropagation()}>filtered ↓</a>
            </>
          ) : null}
        </td>
      </tr>
      {expanded ? (
        <tr>
          <td className="detail-cell" colSpan={6}>
            {loading ? <p className="alerts-hint">Loading {d.day}…</p> : null}
            {error ? <p className="error">{error}</p> : null}
            {rows?.length ? (
              <>
                <p className="alerts-hint">
                  {rows.length} recorded event{rows.length === 1 ? '' : 's'} ·
                  {' '}click one for its trace with the event marked.
                </p>
                <table className="alerts-table">
                  <thead>
                    <tr>
                      <th className="col-time">Time</th>
                      <th>Vehicle</th>
                      <th className="col-qty num">Litres</th>
                      <th className="col-conf num">Confidence / reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((ev, i) => {
                      const key = `${d.day}-${i}`
                      return (
                        <EventRow key={key} ev={ev}
                                  expanded={openEvent === key}
                                  onToggle={() => setOpenEvent(openEvent === key ? null : key)} />
                      )
                    })}
                  </tbody>
                </table>
              </>
            ) : null}
            {rows && !rows.length && !loading
              ? <p className="alerts-hint">No events stored for this day.</p>
              : null}
          </td>
        </tr>
      ) : null}
    </>
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
  const [tab, setTab] = useState('theft')
  const [openEvent, setOpenEvent] = useState(null)
  const [openVehicle, setOpenVehicle] = useState(null)
  const [openDay, setOpenDay] = useState(null)
  const [profiles, setProfiles] = useState(null)
  const [history, setHistory] = useState(null)
  const [slabResult, setSlabResult] = useState(null)
  const [slabRunning, setSlabRunning] = useState(false)
  const [slabClock, setSlabClock] = useState(() => Date.now())
  // Only the start is chosen; the end follows it by SLAB_MINUTES.
  const [slabDay, setSlabDay] = useState(() => ymd(new Date()))
  const [slabStart, setSlabStart] = useState(() => currentSlabStart(Date.now()))

  const load = useCallback(async () => {
    try {
      setDigest(await api.alertsLatest())
      setError('')
    } catch (e) { setError(e.message) }
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, POLL_MS)
    return () => clearInterval(id)
  }, [load])

  useEffect(() => {
    const id = setInterval(() => setSlabClock(Date.now()), POLL_MS)
    return () => clearInterval(id)
  }, [])

  useEffect(() => { api.voltageProfiles().then(setProfiles).catch(() => {}) }, [])
  useEffect(() => { api.alertsDays().then(setHistory).catch(() => {}) }, [digest])

  const runNow = async () => {
    setRunning(true)
    try {
      setDigest(await api.alertsRun())
      setSlabResult(null) // a fresh cycle supersedes whatever slab was on screen
    } catch (e) { setError(e.message) }
    finally { setRunning(false) }
  }

  const slabDays = useMemo(() => dayOptions(slabClock), [slabClock])

  // One choice drives the window: the end is always SLAB_MINUTES after the start.
  const slab = useMemo(() => {
    if (!slabDay || !slabStart) return null
    const from = new Date(`${slabDay}T${slabStart}:00`)
    if (Number.isNaN(from.getTime())) return null
    const to = new Date(from.getTime() + SLAB_MINUTES * 60000)
    const ageMs = slabClock - from.getTime()
    return {
      from: toLocalIso(from),
      to: toLocalIso(to),
      endLabel: hhmmOf(to),
      label: `${slabStart}–${hhmmOf(to)}`,
      future: ageMs < 0,
      tooOld: ageMs > SLAB_HOURS_BACK * 3600000,
    }
  }, [slabDay, slabStart, slabClock])

  const slabReady = Boolean(slab && !slab.future && !slab.tooOld)

  const checkSlab = async () => {
    if (!slabReady) return
    setSlabRunning(true)
    setError('')
    try {
      const d = await api.alertsSlab(slab.from, slab.to)
      setSlabResult({ ...d, slabLabel: slab.label })
      setOpenEvent(null)
      setOpenVehicle(null)
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
  const eventsFor = useCallback(
    (name) => allEvents.filter((e) => e.deviceName === name),
    [allEvents]
  )

  const rows = useMemo(() => {
    if (!view) return []
    if (tab === 'theft') return view.thefts || []
    if (tab === 'fill') return view.fills || []
    return view.suppressed || []
  }, [view, tab])

  const needsCal = profiles?.needsRecalibration || []
  const scanned = view ? (view.devicesScanned || 0) - (view.devicesSkipped || 0) : 0
  const days = history?.days || []

  return (
    <div className="alerts-page">
      <header className="topbar topbar-center">
        <div className="topbar-center-block">
          <h1>Fuel Alerts</h1>
          <p className="topbar-subtitle">
            Agent scans every 30 min · reports an event only once its loss has held for{' '}
            {digest?.settleMinutes ?? 25} min
            {digest?.generatedAt ? ` · last run ${String(digest.generatedAt).replace('T', ' ')}` : ''}
            {' · '}
            <button type="button" className="btn btn-ghost"
                    style={{ padding: '2px 10px', fontSize: 13 }}
                    onClick={runNow} disabled={running}>
              {running ? 'Scanning…' : 'Scan now'}
            </button>
          </p>
          <p className="topbar-subtitle" style={{ marginTop: 6 }}>
            Or check a 30-minute slab — pick when it starts{' '}
            <select
              value={slabDay}
              onChange={(e) => setSlabDay(e.target.value)}
              style={SLAB_FIELD}
            >
              {slabDays.map((d) => (
                <option key={d.value} value={d.value}>{d.label}</option>
              ))}
            </select>{' '}
            <input
              type="time"
              value={slabStart}
              step={300}
              onChange={(e) => setSlabStart(e.target.value)}
              style={SLAB_FIELD}
              aria-label="Slab start time"
            />{' '}
            <span className="chip" title="Set automatically, 30 minutes after the start">
              → {slab?.endLabel || '—'} · {SLAB_MINUTES} min
            </span>{' '}
            <button type="button" className="btn btn-ghost"
                    style={{ padding: '2px 10px', fontSize: 13 }}
                    onClick={checkSlab} disabled={!slabReady || slabRunning}>
              {slabRunning ? 'Checking…' : 'Check slab'}
            </button>
            {slab?.future
              ? <span className="muted" style={{ fontSize: 12 }}> · that slab has not happened yet</span>
              : null}
            {slab?.tooOld
              ? <span className="muted" style={{ fontSize: 12 }}> · older than 24 hours</span>
              : null}
          </p>
        </div>
      </header>

      <div className="content">
        {error ? <p className="error">{error}</p> : null}
        {digest?.pending && !slabResult
          ? <div className="panel"><p>No alert cycle has completed yet.</p></div> : null}

        {slabResult ? (
          <div className="panel" style={{ borderLeft: '4px solid #2563eb' }}>
            <div className="detail-head">
              <span className="chip">Slab</span>
              <span className="detail-title">{slabResult.slabLabel}</span>
              <span className="muted" style={{ fontSize: 13 }}>
                {String(slabResult.slab?.from).replace('T', ' ')} →{' '}
                {String(slabResult.slab?.to).slice(11, 16)} · scanned in{' '}
                {slabResult.tookSeconds}s · nothing was written
              </span>
              <button type="button" className="btn btn-ghost"
                      style={{ padding: '2px 10px', fontSize: 13 }}
                      onClick={() => { setSlabResult(null); setOpenEvent(null); setOpenVehicle(null) }}>
                Back to latest cycle
              </button>
            </div>
          </div>
        ) : null}

        <div className="panel">
          <div className="alert-stats">
            <Stat variant="theft" value={view?.theftCount ?? 0}
                  label={`Theft · ${fmtL(view?.theftLiters)}`} />
            <Stat variant="fill" value={view?.fillCount ?? 0}
                  label={`Fill · ${fmtL(view?.fillLiters)}`} />
            <Stat variant="held" value={view?.heldAwaitingSettle ?? 0}
                  label="Awaiting confirmation" />
            <Stat value={view?.suppressedCount ?? 0} label="Filtered as artifacts" />
            <div className="alert-stat__scanned">{scanned} vehicles scanned</div>
          </div>
        </div>

        {view?.byVehicle?.length ? (
          <div className="panel">
            <h3>By vehicle</h3>
            <p className="alerts-hint">
              Click a vehicle to see its fuel trace with every event from this cycle.
            </p>
            <table className="alerts-table">
              <thead>
                <tr>
                  <th>Vehicle</th>
                  <th className="col-qty num">Theft</th>
                  <th className="col-qty num">Fill</th>
                </tr>
              </thead>
              <tbody>
                {view.byVehicle.map((v) => (
                  <VehicleRow
                    key={v.deviceName}
                    v={v}
                    events={eventsFor(v.deviceName)}
                    // A slab is 30 minutes; framing it in the 24h view used for a
                    // whole cycle squeezes every event against one edge.
                    contextHours={slabResult ? SLAB_TRACE_CONTEXT_HOURS : VEHICLE_CONTEXT_HOURS}
                    expanded={openVehicle === v.deviceName}
                    onToggle={() =>
                      setOpenVehicle(openVehicle === v.deviceName ? null : v.deviceName)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        <div className="panel">
          <div className="alert-tabs">
            {[
              ['theft', `Thefts (${view?.theftCount ?? 0})`],
              ['fill', `Fills (${view?.fillCount ?? 0})`],
              ['suppressed', `Filtered (${view?.suppressedCount ?? 0})`],
            ].map(([k, label]) => (
              <button key={k} type="button"
                      className={`alert-tab${tab === k ? ' is-active' : ''}`}
                      onClick={() => { setTab(k); setOpenEvent(null) }}>
                {label}
              </button>
            ))}
          </div>
          <p className="alerts-hint">
            Click any row for the trace ±{EVENT_CONTEXT_HOURS}h around that event.
          </p>
          {rows.length ? (
            <table className="alerts-table">
              <thead>
                <tr>
                  <th className="col-time">Time</th>
                  <th>Vehicle</th>
                  <th className="col-qty num">Litres</th>
                  {tab === 'suppressed'
                    ? <th className="col-reason">Filtered because</th>
                    : <th className="col-conf num">Confidence</th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((ev, i) => {
                  const key = `${tab}-${i}-${ev.deviceName}-${ev.eventTime}`
                  return (
                    <EventRow key={key} ev={ev}
                              expanded={openEvent === key}
                              onToggle={() => setOpenEvent(openEvent === key ? null : key)} />
                  )
                })}
              </tbody>
            </table>
          ) : <p className="alerts-hint">Nothing in this category for the last cycle.</p>}
        </div>

        {days.length ? (
          <div className="panel">
            <h3>Daily history</h3>
            <p className="alerts-hint">
              Every alert is written to its own day&rsquo;s CSV, filed by when the event
              happened rather than when the agent noticed it. Click a day to open its
              events and see any of them on the fuel trace.
            </p>
            <table className="alerts-table">
              <thead>
                <tr>
                  <th className="col-time">Day</th>
                  <th className="col-qty num">Theft</th>
                  <th className="col-qty num">Fill</th>
                  <th className="col-conf num">Filtered</th>
                  <th className="col-conf num">Vehicles</th>
                  <th>CSV</th>
                </tr>
              </thead>
              <tbody>
                {days.map((d) => (
                  <DayRow
                    key={d.day}
                    d={d}
                    expanded={openDay === d.day}
                    onToggle={() => setOpenDay(openDay === d.day ? null : d.day)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        {/* A large voltage swing is a hardware fault, not just a filter input: these
            sensors cannot resolve a realistic theft until they are recalibrated. */}
        {needsCal.length ? (
          <div className="panel">
            <h3>Sensors needing recalibration ({needsCal.length})</h3>
            <p className="alerts-hint">
              On these vehicles the reported fuel level tracks supply voltage, so any
              theft smaller than the swing below cannot be told apart from the sensor
              reacting to its own power. Alerts under that size are withheld.
            </p>
            <table className="alerts-table">
              <thead>
                <tr>
                  <th>Vehicle</th>
                  <th className="col-conf num">Correlation</th>
                  <th className="col-qty num">Phantom swing</th>
                  <th className="col-time" />
                </tr>
              </thead>
              <tbody>
                {needsCal.map((p) => (
                  <tr key={p.imei}>
                    <td>{p.vehicle || p.imei}</td>
                    <td className="num">{Number(p.corr).toFixed(2)}</td>
                    <td className="num">{fmtL(p.swing_liters)}</td>
                    <td>
                      <Link className="link-inline"
                            to={`/live?device=${encodeURIComponent(p.vehicle || '')}`}>
                        view →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </div>
  )
}
