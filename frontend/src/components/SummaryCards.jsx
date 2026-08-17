import { useNavigate } from 'react-router-dom'

const PALETTES = {
  vehicle: ['#2563eb', '#38bdf8', '#0d9488'],
  battery: ['#dc2626', '#f97316'],
  speed: ['#ea580c', '#ca8a04'],
  summary: ['#7c3aed', '#38bdf8', '#f97316', '#0d9488'],
  reportA: ['#7c3aed', '#16a34a', '#dc2626', '#0f766e'],
}

export function MetricCard({ rows, palette = 'vehicle', footer = null }) {
  const colors = PALETTES[palette] || PALETTES.vehicle
  return (
    <div className="kpi-card">
      {rows.map((row, i) => (
        <div key={row.label} className="kpi-row" style={{ background: colors[i % colors.length] }}>
          <span>{row.label}</span>
          <span>{row.value}</span>
        </div>
      ))}
      {footer ? <div className="kpi-footer">{footer}</div> : null}
    </div>
  )
}

export function LiveSummaryCards({ summary, eventStats, eventsLoading }) {
  if (!summary || !summary.deviceName) {
    return (
      <div className="kpi-grid" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
        {[1, 2, 3].map((i) => (
          <div key={i} className="kpi-card">
            <div className="kpi-row" style={{ background: '#94a3b8' }}>
              <span>Loading…</span>
              <span>—</span>
            </div>
          </div>
        ))}
      </div>
    )
  }

  const fillLiters = eventStats ? eventStats.totalFillLiters ?? 0 : summary.totalFilling ?? 0
  const fillCount = eventStats ? eventStats.fillCount ?? 0 : summary.fillingCount ?? 0
  const theftLiters = eventStats ? eventStats.totalTheftLiters ?? 0 : summary.totalFuelTakenOut ?? 0
  const theftCount = eventStats ? eventStats.theftCount ?? 0 : summary.theftCount ?? 0
  const fillLabel = eventsLoading ? '…' : `${fillLiters} ltr (${fillCount})`
  const theftLabel = eventsLoading ? '…' : `${theftLiters} ltr (${theftCount})`

  return (
    <div className="kpi-grid" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
      <MetricCard
        palette="summary"
        rows={[
          { label: 'Fuel', value: `${summary.fuel ?? summary.currentFuel ?? 0} ltr` },
          { label: 'Ignition', value: summary.ignition ?? 'Off' },
          { label: 'Speed', value: `${summary.speed ?? 0} km/h` },
          { label: 'Device Status', value: summary.deviceStatus || '—' },
        ]}
      />
      <MetricCard
        palette="reportA"
        rows={[
          { label: 'Fuel Filling', value: fillLabel },
          { label: 'Fuel Theft', value: theftLabel },
          { label: 'External Voltage', value: `${summary.externalVoltage ?? 0} v` },
          {
            label: 'Running Time',
            value: summary.totalRunningTimeFormatted || '00D: 00H: 00M: 00S',
          },
        ]}
      />
      <MetricCard
        palette="speed"
        rows={[
          { label: 'Distance', value: `${summary.totalDistance ?? 0} km` },
          { label: 'Avg Consumption', value: summary.averageConsumption != null ? `${summary.averageConsumption} ltr/h` : 'N/A' },
          { label: 'Mileage', value: summary.mileage != null ? `${summary.mileage} km/ltr` : 'N/A' },
        ]}
      />
    </div>
  )
}

/** Fleet live status — today-based Total / Active / Running (opens list page) */
export function FleetKpiCards({ summary }) {
  const navigate = useNavigate()

  if (!summary) {
    return (
      <div className="fleet-status-card">
        <div className="fleet-status-loading">Loading live fleet status…</div>
      </div>
    )
  }

  const defs = summary.definitions || {}
  const todayLabel =
    summary.asOfLabel ||
    summary.asOfDate ||
    new Date().toLocaleDateString(undefined, {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    })
  const timeLabel = summary.asOfTime || new Date().toLocaleTimeString()

  const stats = [
    {
      key: 'total',
      value: summary.totalVehicle ?? 0,
      label: 'Total Vehicle',
      desc: defs.totalVehicle || 'Unique vehicles (one per IMEI).',
    },
    {
      key: 'active',
      value: summary.activeVehicle ?? 0,
      label: 'Active Vehicle',
      desc:
        defs.activeVehicle ||
        'Online now (telemetry in the last ~30 minutes), excluding battery-disconnected.',
    },
    {
      key: 'running',
      value: summary.runningVehicle ?? 0,
      label: 'Running Vehicle',
      desc:
        defs.runningVehicle ||
        'Active vehicles with ignition ON on the latest reading.',
    },
    {
      key: 'inactive',
      value: summary.inactiveVehicle ?? 0,
      label: 'Inactive Vehicle',
      desc:
        defs.inactiveVehicle ||
        'Not currently online (and not battery-disconnected).',
    },
  ]

  return (
    <div className="fleet-status-card">
      <div className="fleet-status-meta">
        <span className="live-pill">
          <span className="live-dot" />
          LIVE
        </span>
        <span className="fleet-date">
          Today · {todayLabel}
          {timeLabel ? ` · Updated ${timeLabel}` : ''}
        </span>
      </div>

      <div className="fleet-status-grid fleet-status-grid-4">
        {stats.map((s) => (
          <button
            key={s.key}
            type="button"
            className={`fleet-stat fleet-stat-btn fleet-stat-${s.key}`}
            onClick={() => navigate(`/fleet/vehicles?filter=${s.key}`)}
          >
            <div className="fleet-stat-value">{s.value}</div>
            <div className="fleet-stat-label">{s.label}</div>
            <p className="fleet-stat-desc">{s.desc}</p>
            <span className="fleet-stat-hint">Click to view →</span>
          </button>
        ))}
      </div>
      {(summary.batteryDisconnected || 0) > 0 ? (
        <p className="muted" style={{ textAlign: 'center', margin: '10px 0 0', fontSize: 12 }}>
          Battery disconnected soft-alert: {summary.batteryDisconnected} (excluded from Active /
          Inactive)
        </p>
      ) : null}
    </div>
  )
}
