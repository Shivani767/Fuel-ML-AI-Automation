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

export function LiveSummaryCards({ summary, eventStats, eventsLoading, loading = false }) {
  if (loading && (!summary || !summary.deviceName)) {
    return (
      <div className="kpi-grid live-kpi-grid">
        {[1, 2, 3].map((i) => (
          <div key={i} className="kpi-card live-kpi-skeleton">
            <div className="kpi-row" style={{ background: '#93c5fd' }}>
              <span>Loading metrics…</span>
              <span>—</span>
            </div>
          </div>
        ))}
      </div>
    )
  }

  if (!summary || !summary.deviceName) {
    return (
      <div className="live-empty-hint">
        Pick a vehicle and date range, then press <strong>Search</strong> to load fuel metrics.
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
    <div className="kpi-grid live-kpi-grid">
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

  const todayLabel =
    summary.asOfLabel ||
    summary.asOfDate ||
    new Date().toLocaleDateString(undefined, {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    })
  const timeLabel = summary.asOfTime || new Date().toLocaleTimeString()
  const batteryN = summary.batteryDisconnected || 0

  const stats = [
    {
      key: 'total',
      value: summary.totalVehicle ?? 0,
      label: 'Total Vehicles',
    },
    {
      key: 'active',
      value: summary.activeVehicle ?? 0,
      label: 'Active Vehicles',
    },
    {
      key: 'running',
      value: summary.runningVehicle ?? 0,
      label: 'Running Vehicles',
    },
    {
      key: 'inactive',
      value: summary.inactiveVehicle ?? 0,
      label: 'Inactive Vehicles',
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
          {timeLabel ? ` · ${timeLabel}` : ''}
        </span>
        {batteryN > 0 ? (
          <span className="fleet-battery-chip" title="Counted in Inactive">
            Battery · {batteryN}
          </span>
        ) : null}
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
          </button>
        ))}
      </div>
    </div>
  )
}
