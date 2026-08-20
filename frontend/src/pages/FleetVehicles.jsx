import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'

const FILTERS = {
  total: {
    title: 'Total Vehicles',
    desc: 'Unique vehicles (one per IMEI) on the fleet roster.',
  },
  active: {
    title: 'Active Vehicles',
    desc: 'Online now — telemetry in the last ~30 minutes (excluding battery-disconnected).',
  },
  running: {
    title: 'Running Vehicles',
    desc: 'Active vehicles with ignition ON and moving (speed or movement) — FuelArk Running.',
  },
  inactive: {
    title: 'Inactive Vehicles',
    desc: 'Not active — offline now, or online with battery-disconnected soft alert. Total = Active + Inactive.',
  },
}

export default function FleetVehicles() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const filterRaw = (params.get('filter') || 'total').toLowerCase()
  const filter = FILTERS[filterRaw] ? filterRaw : 'total'
  const [devices, setDevices] = useState([])
  const [search, setSearch] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      try {
        const d = await api.fleetDevices()
        if (!cancelled) {
          setDevices(d)
          setError('')
        }
      } catch (e) {
        if (!cancelled) setError(e.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const list = useMemo(() => {
    let rows = devices
    if (filter === 'active') rows = devices.filter((d) => d.active)
    if (filter === 'running') rows = devices.filter((d) => d.running)
    if (filter === 'inactive') rows = devices.filter((d) => !d.active)
    const q = search.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(
      (d) =>
        d.deviceName.toLowerCase().includes(q) ||
        String(d.imei || '').toLowerCase().includes(q)
    )
  }, [devices, filter, search])

  const meta = FILTERS[filter]

  return (
    <>
      <header className="topbar">
        <div>
          <button type="button" className="btn btn-ghost" onClick={() => navigate('/')}>
            ← Back to Fleet
          </button>
          <h1 style={{ marginTop: 8 }}>{meta.title}</h1>
          <p className="topbar-subtitle">{meta.desc}</p>
        </div>
      </header>
      <div className="content">
        <div className="fleet-filter-tabs">
          {Object.entries(FILTERS).map(([key, m]) => (
            <button
              key={key}
              type="button"
              className={`fleet-filter-tab${filter === key ? ' is-active' : ''}`}
              onClick={() => setParams({ filter: key })}
            >
              {m.title}
            </button>
          ))}
        </div>

        <div className="panel" style={{ padding: 16 }}>
          <div className="fleet-vehicle-panel-head">
            <h3>
              {meta.title} · {list.length}
            </h3>
            <input
              className="fleet-vehicle-search"
              placeholder="Search name or IMEI…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          {error ? <p className="error">{error}</p> : null}
          {loading ? <p className="muted">Loading vehicles…</p> : null}

          <div className="fleet-vehicle-table-wrap fleet-vehicle-table-wrap-page">
            <table className="fleet-vehicle-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Vehicle Name</th>
                  <th>IMEI</th>
                  <th>Status</th>
                  <th>Last Seen</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.map((d, i) => (
                  <tr key={d.deviceName}>
                    <td>{i + 1}</td>
                    <td>{d.deviceName}</td>
                    <td className="mono">{d.imei || '—'}</td>
                    <td>
                      {d.running ? (
                        <span className="badge badge-run">Running</span>
                      ) : d.active ? (
                        <span className="badge badge-active">Active</span>
                      ) : (
                        <span className="badge badge-idle">Inactive</span>
                      )}
                    </td>
                    <td>{d.lastSeen || '—'}</td>
                    <td>
                      <Link
                        className="btn btn-live"
                        to={`/live?device=${encodeURIComponent(d.deviceName.trim())}`}
                      >
                        Go Live
                      </Link>
                    </td>
                  </tr>
                ))}
                {!loading && !list.length ? (
                  <tr>
                    <td colSpan={6} className="muted">
                      No vehicles in this group.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  )
}
