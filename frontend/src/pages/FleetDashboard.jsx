import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { FleetKpiCards } from '../components/SummaryCards'
import FleetStatusChart from '../components/FleetStatusChart'
import DeviceList from '../components/DeviceList'
import MapView from '../components/MapView'

// A live fleet refresh polls ~104 IMEIs from FuelArk and takes ~107s. The old
// 30s interval therefore re-fired roughly 3x faster than a request could finish,
// so calls piled up behind the backend's fleet lock and never drained -- the
// dashboard sat on "Loading live fleet status…" indefinitely. Poll no faster than
// the documented >=5 min fleet policy.
const REFRESH_MS = 5 * 60 * 1000

export default function FleetDashboard() {
  const [summary, setSummary] = useState(null)
  const [devices, setDevices] = useState([])
  const [search, setSearch] = useState('')
  const [error, setError] = useState('')
  const [updating, setUpdating] = useState(false)

  // mode: '0' cache-only (instant), '1' live poll, 'force' bypass rate gate.
  // Only one of the two fleet endpoints should hit FuelArk — dual refresh=1
  // stacks behind the same lock and can leave the UI on zeros for minutes.
  const refresh = useCallback(async (mode = '0', silent = false) => {
    if (!silent) setUpdating(true)
    try {
      // Only summary hits FuelArk; devices read the cache that refresh just warmed.
      const s = await api.fleetSummary(mode)
      const d = await api.fleetDevices('', '0')
      setSummary(s)
      setDevices(d)
      setError('')
    } catch (e) {
      setError(e.message)
    } finally {
      setUpdating(false)
    }
  }, [])

  useEffect(() => {
    // Paint from cache immediately, then pull live in the background so the
    // first render never blocks on a fleet-wide fetch (~2 min after restart).
    refresh('0')
    const warm = setTimeout(() => refresh('1', true), 500)
    const id = setInterval(() => refresh('1', true), REFRESH_MS)
    return () => {
      clearTimeout(warm)
      clearInterval(id)
    }
  }, [refresh])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return devices
    return devices.filter(
      (d) =>
        d.deviceName.toLowerCase().includes(q) ||
        String(d.imei || '').toLowerCase().includes(q)
    )
  }, [devices, search])

  return (
    <>
      <header className="topbar topbar-center">
        <div className="topbar-center-block">
          <h1>Fleet Live Monitor</h1>
          <p className="topbar-subtitle">
            Total · Active (online now) · Running · Inactive
            {updating ? ' · refreshing…' : ''}
            {summary?.snapshot ? (
              <span
                style={{
                  color:
                    summary.snapshot.stale || summary.snapshot.ageMinutes == null
                      ? '#b91c1c'
                      : 'inherit',
                }}
              >
                {summary.snapshot.ageMinutes == null
                  ? ' · waiting for first FuelArk pull (~2 min) — Active/Running stay 0 until then'
                  : summary.snapshot.ageMinutes < 1
                    ? ' · data just now'
                    : ` · data ${summary.snapshot.ageMinutes}m old`}
                {summary.snapshot.stale && summary.snapshot.ageMinutes != null
                  ? ` — older than the ${summary.snapshot.onlineWindowMinutes}min online window, so Active/Running read 0`
                  : ''}
              </span>
            ) : null}
            {' · '}
            <button
              type="button"
              className="btn btn-ghost"
              style={{ padding: '2px 10px', fontSize: 13 }}
              onClick={() => refresh('force')}
              disabled={updating}
              title="Re-poll every vehicle from FuelArk (takes ~2 minutes)"
            >
              {updating ? 'Refreshing…' : 'Refresh now'}
            </button>
            {' · '}
            <Link to="/alerts">Fuel Alerts</Link>
            {' · '}
            <Link to="/admin">Admin: add vehicle by IMEI</Link>
          </p>
        </div>
      </header>
      <div className="content">
        <FleetKpiCards summary={summary} />
        <FleetStatusChart summary={summary} />
        {error ? <p className="error">{error}</p> : null}
        <div className="fleet-layout">
          <DeviceList devices={filtered} search={search} onSearch={setSearch} />
          <div className="panel" style={{ padding: 0 }}>
            <MapView devices={filtered} />
          </div>
        </div>
      </div>
    </>
  )
}
