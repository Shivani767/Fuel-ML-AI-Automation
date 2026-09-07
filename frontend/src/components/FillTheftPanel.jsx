import { FILL_MIN_LITERS } from '../api/client'

function fmtTime(t) {
  return String(t || '').replace('T', ' ').slice(0, 19)
}

function decisionClass(decision) {
  const d = String(decision || '')
  if (d === 'AUTO_CONFIRMED' || d === 'VERIFIED') return 'pred-ok'
  if (d === 'MANUAL_REVIEW') return 'pred-warn'
  return 'pred-mute'
}

function DecisionCell({ e }) {
  const ml =
    e.mlLabel != null
      ? `${e.mlLabel}${e.mlConfidence != null ? ` ${(Number(e.mlConfidence) * 100).toFixed(0)}%` : ''}`
      : null
  return (
    <td>
      <span className={`pred-badge ${decisionClass(e.decision)}`}>{e.decision || '—'}</span>
      {ml ? (
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
          ML predict: {ml}
          {e.mlAction && e.mlAction !== 'none' ? ` · ${e.mlAction}` : ''}
        </div>
      ) : null}
    </td>
  )
}

function EventTable({ rows, kind, loading }) {
  const isFill = kind === 'fill'
  const sorted = [...rows].sort((a, b) => {
    const ca = Number(a.mlConfidence ?? a.confidence ?? 0)
    const cb = Number(b.mlConfidence ?? b.confidence ?? 0)
    if (cb !== ca) return cb - ca
    return String(b.eventTime || '').localeCompare(String(a.eventTime || ''))
  })
  return (
    <div className="table-wrap" style={{ maxHeight: 320 }}>
      <table className="live-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Volume (L)</th>
            <th>Predicted decision</th>
            <th>Rule conf</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((e, i) => (
            <tr key={`${kind}-${e.eventTime}-${i}`}>
              <td>{fmtTime(e.eventTime)}</td>
              <td style={{ color: isFill ? '#16a34a' : '#dc2626', fontWeight: 600 }}>
                {isFill ? '+' : '-'}
                {e.volume}
              </td>
              <DecisionCell e={e} />
              <td>{e.confidence != null ? Number(e.confidence).toFixed(2) : '—'}</td>
              <td style={{ fontSize: 12 }}>{e.source || '—'}</td>
            </tr>
          ))}
          {!sorted.length && !loading ? (
            <tr>
              <td colSpan={5} className="muted">
                No {isFill ? 'filling' : 'theft'} predictions in this range
              </td>
            </tr>
          ) : null}
          {loading && !sorted.length ? (
            <tr>
              <td colSpan={5} className="muted">
                Running session detector + ML…
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  )
}

export default function FillTheftPanel({ eventsPayload, loading }) {
  const fills = eventsPayload?.fills || []
  const thefts = eventsPayload?.thefts || []
  const events = eventsPayload?.events || []
  const daily = eventsPayload?.dailyAnomalies || []
  const mlOn = eventsPayload?.mlEnabled

  const auto = events.filter((e) => e.decision === 'AUTO_CONFIRMED' || e.decision === 'VERIFIED')
  const review = events.filter((e) => e.decision === 'MANUAL_REVIEW')
  const withMl = events.filter((e) => e.mlLabel)

  return (
    <div className="panel pred-panel" style={{ marginTop: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Predicted fill &amp; theft</h2>
        <span className="muted" style={{ fontSize: 13 }}>
          {loading
            ? 'Detecting…'
            : `${eventsPayload?.fillCount ?? 0} fills · ${eventsPayload?.theftCount ?? 0} thefts${
                mlOn ? ' · RF model on' : ''
              }`}
        </span>
      </div>

      <div className="pred-summary">
        <div className="pred-stat">
          <span className="pred-stat-label">Auto confirmed</span>
          <strong>{loading ? '…' : auto.length}</strong>
        </div>
        <div className="pred-stat">
          <span className="pred-stat-label">Manual review</span>
          <strong>{loading ? '…' : review.length}</strong>
        </div>
        <div className="pred-stat">
          <span className="pred-stat-label">ML scored</span>
          <strong>{loading ? '…' : withMl.length}</strong>
        </div>
        <div className="pred-stat">
          <span className="pred-stat-label">Theft volume</span>
          <strong style={{ color: '#dc2626' }}>
            {loading ? '…' : `${eventsPayload?.totalTheftLiters ?? 0} L`}
          </strong>
        </div>
        <div className="pred-stat">
          <span className="pred-stat-label">Fill volume</span>
          <strong style={{ color: '#16a34a' }}>
            {loading ? '…' : `${eventsPayload?.totalFillLiters ?? 0} L`}
          </strong>
        </div>
      </div>

      <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>
        Graph loads first from local data; predictions run next (session detector + RandomForest).
        AUTO_CONFIRMED = high confidence; MANUAL_REVIEW needs a human check.
        Fuel fills of {FILL_MIN_LITERS} L or less are ignored.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <h3 style={{ color: '#16a34a', fontSize: 14, margin: '0 0 8px' }}>
            Fuel Filling ({fills.length}) — {eventsPayload?.totalFillLiters ?? 0} L
          </h3>
          <EventTable rows={fills} kind="fill" loading={loading} />
        </div>
        <div>
          <h3 style={{ color: '#dc2626', fontSize: 14, margin: '0 0 8px' }}>
            Fuel Theft ({thefts.length}) — {eventsPayload?.totalTheftLiters ?? 0} L
          </h3>
          <EventTable rows={thefts} kind="theft" loading={loading} />
        </div>
      </div>

      {daily.length > 0 ? (
        <details style={{ marginTop: 12 }} open>
          <summary className="muted" style={{ cursor: 'pointer' }}>
            Slow / persistent theft flags — IsolationForest ({daily.length} days)
          </summary>
          <div className="table-wrap" style={{ maxHeight: 160, marginTop: 8 }}>
            <table className="live-table">
              <thead>
                <tr>
                  <th>Day</th>
                  <th>Daily drop (L)</th>
                  <th>Residual</th>
                  <th>Score</th>
                </tr>
              </thead>
              <tbody>
                {daily.map((d) => (
                  <tr key={d.day}>
                    <td>{d.day}</td>
                    <td style={{ color: '#dc2626' }}>{d.dailyDrop}</td>
                    <td>{d.residual}</td>
                    <td>{d.anomalyScore}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}

      {events.length > 0 ? (
        <details style={{ marginTop: 12 }}>
          <summary className="muted" style={{ cursor: 'pointer' }}>
            All predicted events ({events.length})
          </summary>
          <div className="table-wrap" style={{ maxHeight: 220, marginTop: 8 }}>
            <table className="live-table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Time</th>
                  <th>Volume</th>
                  <th>Rule conf</th>
                  <th>ML</th>
                  <th>Decision</th>
                </tr>
              </thead>
              <tbody>
                {events.map((e, i) => (
                  <tr key={`a-${e.eventType}-${e.eventTime}-${i}`}>
                    <td style={{ color: e.eventType === 'Fuel Theft' ? '#dc2626' : '#16a34a' }}>
                      {e.eventType}
                    </td>
                    <td>{fmtTime(e.eventTime)}</td>
                    <td>{e.volume}</td>
                    <td>{e.confidence ?? '—'}</td>
                    <td>
                      {e.mlLabel
                        ? `${e.mlLabel}${e.mlConfidence != null ? ` ${(e.mlConfidence * 100).toFixed(0)}%` : ''}`
                        : '—'}
                    </td>
                    <td>
                      <span className={`pred-badge ${decisionClass(e.decision)}`}>
                        {e.decision || '—'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </div>
  )
}
