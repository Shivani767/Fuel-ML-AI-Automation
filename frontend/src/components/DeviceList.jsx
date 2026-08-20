import { useNavigate } from 'react-router-dom'

function statusBadge(d) {
  if (d.running) return { cls: 'badge badge-run', text: 'Running' }
  if (d.active) return { cls: 'badge badge-active', text: 'Active' }
  return { cls: 'badge badge-idle', text: 'Inactive' }
}

function fuelText(fuel) {
  if (fuel == null || fuel === '') return '—'
  const n = Number(fuel)
  if (Number.isNaN(n)) return String(fuel)
  return `${n} L`
}

export default function DeviceList({ devices, search, onSearch }) {
  const navigate = useNavigate()

  return (
    <div className="device-list">
      <div className="device-list-header">
        <input
          placeholder="Search vehicle…"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          aria-label="Search vehicle"
        />
        <button
          type="button"
          className="btn btn-sm btn-scan"
          onClick={() => navigate('/fleet/vehicles?filter=total')}
        >
          View all
        </button>
      </div>
      <div className="device-scroll">
        {(devices || []).map((d) => {
          const badge = statusBadge(d)
          const tone = d.running ? 'running' : d.active ? 'active' : 'inactive'
          return (
          <div className={`device-card device-card--${tone}`} key={d.deviceName}>
              <div className="device-card__top">
                <h3 title={d.deviceName}>{d.deviceName}</h3>
                <span className={badge.cls}>{badge.text}</span>
              </div>
              <div className="device-meta">
                {d.imei || '—'} ·{' '}
                {d.lastSeen ? String(d.lastSeen).replace('T', ' ').slice(0, 16) : '—'}
              </div>
              <div className="device-stats">
                <span className="device-chip device-chip--fuel">Fuel {fuelText(d.fuel)}</span>
                <span className="device-chip device-chip--speed">{d.speed ?? 0} km/h</span>
                <span className="device-chip device-chip--ign">Ign {d.ignition || 'Off'}</span>
              </div>
              <div className="device-card__actions">
                <button
                  type="button"
                  className="btn btn-live"
                  onClick={() =>
                    navigate(
                      `/live?device=${encodeURIComponent(d.deviceName.trim())}` +
                        (d.imei ? `&imei=${encodeURIComponent(d.imei)}` : '') +
                        `&stream=1`
                    )
                  }
                >
                  Go Live
                </button>
              </div>
            </div>
          )
        })}
        {!devices?.length ? <p className="muted">No vehicles found</p> : null}
      </div>
    </div>
  )
}
