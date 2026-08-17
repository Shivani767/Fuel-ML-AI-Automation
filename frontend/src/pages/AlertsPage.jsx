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

const fmtL = (v) => `${Number(v || 0).toFixed(1)} L`
const hhmm = (ts) => (ts ? String(ts).slice(11, 16) : '—')
const pretty = (r) => String(r || '').replace(/_/g, ' ')
const isTheft = (e) => /theft/i.test(e?.eventType || '')

function shiftHours(ts, h) {
  const d = new Date(String(ts).replace(' ', 'T'))
  if (Number.isNaN(d.getTime())) return null
  d.setHours(d.getHours() + h)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

function Caret({ open }) {
  return <span className="row-caret">{open ? '▾' : '▸'}</span>
}

function Dash() {
  return <span className="dash">—</span>
}

/** Load telemetry for a device/window once, when first shown. */
function useSeries(deviceName, from, to, enabled) {
  const [state, setState] = useState({ points: null, loading: false, error: '' })

  useEffect(() => {
    if (!enabled || !deviceName || state.points || state.loading) return
    let alive = true
    setState((s) => ({ ...s, loading: true }))
    api
      .deviceSeries(deviceName, from, to, { downsample: 1, limit: 5000 })
      .then((d) => alive && setState({ points: d.points || [], loading: false, error: '' }))
      .catch((e) => alive && setState({ points: [], loading: false, error: e.message }))
    return () => { alive = false }
  }, [enabled, deviceName, from, to, state.points, state.loading])

  return state
}

function Trace({ deviceName, from, to, emptyNote }) {
  const { points, loading, error } = useSeries(deviceName, from, to, true)
  if (loading) return <p className="alerts-hint">Loading trace…</p>
  if (error) return <p className="error">{error}</p>
  if (points && !points.length) return <p className="alerts-hint">{emptyNote}</p>
  if (!points) return null
  return (
    <>
      <p className="alerts-hint">
        {points.length} readings · {String(from).replace('T', ' ')} → {String(to).replace('T', ' ')}
      </p>
      <DeviceChart deviceName={deviceName} points={points} />
    </>
  )
}

/** A vehicle from the rollup: click to see its whole day and every event on it. */
function VehicleRow({ v, events, expanded, onToggle }) {
  const anchor = events[0]?.eventTime || new Date().toISOString()
  const from = shiftHours(anchor, -VEHICLE_CONTEXT_HOURS)
  const to = shiftHours(anchor, VEHICLE_CONTEXT_HOURS)

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
              deviceName={v.deviceName} from={from} to={to}
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
              deviceName={ev.deviceName} from={from} to={to}
              emptyNote="No telemetry in this window — the change was never observed."
            />
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
  const [profiles, setProfiles] = useState(null)
  const [history, setHistory] = useState(null)

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

  useEffect(() => { api.voltageProfiles().then(setProfiles).catch(() => {}) }, [])
  useEffect(() => { api.alertsDays().then(setHistory).catch(() => {}) }, [digest])

  const runNow = async () => {
    setRunning(true)
    try { setDigest(await api.alertsRun()) }
    catch (e) { setError(e.message) }
    finally { setRunning(false) }
  }

  const allEvents = useMemo(
    () => [...(digest?.thefts || []), ...(digest?.fills || [])],
    [digest]
  )
  const eventsFor = useCallback(
    (name) => allEvents.filter((e) => e.deviceName === name),
    [allEvents]
  )

  const rows = useMemo(() => {
    if (!digest) return []
    if (tab === 'theft') return digest.thefts || []
    if (tab === 'fill') return digest.fills || []
    return digest.suppressed || []
  }, [digest, tab])

  const needsCal = profiles?.needsRecalibration || []
  const scanned = digest ? (digest.devicesScanned || 0) - (digest.devicesSkipped || 0) : 0
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
        </div>
      </header>

      <div className="content">
        {error ? <p className="error">{error}</p> : null}
        {digest?.pending ? <div className="panel"><p>No alert cycle has completed yet.</p></div> : null}

        <div className="panel">
          <div className="alert-stats">
            <Stat variant="theft" value={digest?.theftCount ?? 0}
                  label={`Theft · ${fmtL(digest?.theftLiters)}`} />
            <Stat variant="fill" value={digest?.fillCount ?? 0}
                  label={`Fill · ${fmtL(digest?.fillLiters)}`} />
            <Stat variant="held" value={digest?.heldAwaitingSettle ?? 0}
                  label="Awaiting confirmation" />
            <Stat value={digest?.suppressedCount ?? 0} label="Filtered as artifacts" />
            <div className="alert-stat__scanned">{scanned} vehicles scanned</div>
          </div>
        </div>

        {digest?.byVehicle?.length ? (
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
                {digest.byVehicle.map((v) => (
                  <VehicleRow
                    key={v.deviceName}
                    v={v}
                    events={eventsFor(v.deviceName)}
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
              ['theft', `Thefts (${digest?.theftCount ?? 0})`],
              ['fill', `Fills (${digest?.fillCount ?? 0})`],
              ['suppressed', `Filtered (${digest?.suppressedCount ?? 0})`],
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
              happened rather than when the agent noticed it.
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
                  <tr key={d.day}>
                    <td className="col-time">{d.day}</td>
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
                      <a className="link-inline" href={api.alertsCsvUrl(d.day)}>alerts ↓</a>
                      {d.filteredCount ? (
                        <>
                          {' · '}
                          <a className="link-inline" href={api.alertsCsvUrl(d.day, 'filtered')}>
                            filtered ↓
                          </a>
                        </>
                      ) : null}
                    </td>
                  </tr>
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
