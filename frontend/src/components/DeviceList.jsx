import { useNavigate } from 'react-router-dom'

export default function DeviceList({ devices, search, onSearch }) {
  const navigate = useNavigate()

  return (
    <div className="device-list">
      <div className="device-list-header">
        <input
          placeholder="Search vehicle…"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
        />
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => navigate('/fleet/vehicles?filter=total')}
        >
          View all
        </button>
      </div>
      <div className="device-scroll">
        {(devices || []).map((d) => (
          <div className="device-card" key={d.deviceName}>
            <h3>{d.deviceName}</h3>
            <div className="device-meta">
              IMEI {d.imei || '—'} · {d.lastSeen || '—'} · {d.active ? 'Active' : 'Inactive'}
            </div>
            <div className="device-stats">
              <div>Fuel Level : {d.fuel} liter</div>
              <div>{d.speed} km/h</div>
              <div>
                Ignition {d.ignition}
                {d.running ? ' · Running' : ''}
              </div>
            </div>
            <button
              type="button"
              className="btn btn-live"
              onClick={() =>
                navigate(`/live?device=${encodeURIComponent(d.deviceName.trim())}`)
              }
            >
              Go Live
            </button>
            <div className="progress">
              <span style={{ width: `${Math.min(100, d.progress ?? 100)}%` }} />
            </div>
          </div>
        ))}
        {!devices?.length ? <p className="muted">No vehicles found</p> : null}
      </div>
    </div>
  )
}
