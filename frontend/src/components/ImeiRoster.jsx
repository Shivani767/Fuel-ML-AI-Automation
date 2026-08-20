import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'

/**
 * Lifetime IMEI roster: show existing IMEIs as stored + add new (persisted forever).
 */
export default function ImeiRoster({ onSelect, onAdded, selectedImei }) {
  const [devices, setDevices] = useState([])
  const [loadingList, setLoadingList] = useState(true)
  const [q, setQ] = useState('')
  const [imei, setImei] = useState('')
  const [days, setDays] = useState(30)
  const [busy, setBusy] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [lastSync, setLastSync] = useState(null)

  const refresh = useCallback(async () => {
    setLoadingList(true)
    try {
      const res = await api.imeiList()
      setDevices(res.devices || [])
      if (res.lastFuelarkSync) setLastSync(res.lastFuelarkSync)
    } catch (e) {
      setErr(e.message)
    } finally {
      setLoadingList(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return devices
    return devices.filter(
      (d) =>
        String(d.deviceName || '')
          .toLowerCase()
          .includes(needle) ||
        String(d.imei || '')
          .toLowerCase()
          .includes(needle)
    )
  }, [devices, q])

  const stats = useMemo(() => {
    const total = devices.length
    const withData = devices.filter((d) => (d.points ?? 0) > 0).length
    const empty = total - withData
    const fuelark = lastSync?.fuelarkCount
    return { total, withData, empty, fuelark }
  }, [devices, lastSync])

  async function syncFromFuelark() {
    setSyncing(true)
    setErr('')
    setMsg('Syncing vehicle list from FuelArk…')
    try {
      const res = await api.syncFuelark({ fetchNew: true, days: 3 })
      setLastSync(res)
      if (!res.ok) {
        setErr(res.error || 'FuelArk sync failed')
        setMsg('')
        return
      }
      const n = res.addedCount || 0
      setMsg(
        n
          ? `FuelArk sync: ${res.fuelarkCount} on account · added ${n} new vehicle(s)`
          : `FuelArk sync: ${res.fuelarkCount} on account · already up to date`
      )
      await refresh()
    } catch (ex) {
      setErr(ex.message || 'Sync failed')
      setMsg('')
    } finally {
      setSyncing(false)
    }
  }

  async function submit(e) {
    e?.preventDefault?.()
    const cleaned = String(imei).replace(/\D/g, '')
    if (cleaned.length < 14 || cleaned.length > 16) {
      setErr('Enter a 14–16 digit IMEI')
      return
    }
    setBusy(true)
    setErr('')
    setMsg('Fetching from FuelArk (saved for lifetime)…')
    try {
      const res = await api.addImei(cleaned, days, true)
      if (!res.ok) {
        setErr(res.error || 'Failed to add IMEI')
        setMsg('')
        return
      }
      setMsg(
        `Saved forever: ${res.deviceName} · IMEI ${res.imei} · ${res.pointsFetched || 0} pts`
      )
      setImei('')
      await refresh()
      onAdded?.(res)
    } catch (ex) {
      setErr(ex.message || 'Request failed')
      setMsg('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="imei-roster">
      <div className="fleet-status-grid fleet-status-grid-4 admin-kpi-grid">
        <div className="fleet-stat fleet-stat-total">
          <div className="fleet-stat-value">{loadingList ? '—' : stats.total}</div>
          <div className="fleet-stat-label">Total IMEIs</div>
        </div>
        <div className="fleet-stat fleet-stat-running">
          <div className="fleet-stat-value">{loadingList ? '—' : stats.withData}</div>
          <div className="fleet-stat-label">With data</div>
        </div>
        <div className="fleet-stat fleet-stat-inactive">
          <div className="fleet-stat-value">{loadingList ? '—' : stats.empty}</div>
          <div className="fleet-stat-label">No points yet</div>
        </div>
        <div className="fleet-stat fleet-stat-active">
          <div className="fleet-stat-value">
            {stats.fuelark != null ? stats.fuelark : '—'}
          </div>
          <div className="fleet-stat-label">On FuelArk</div>
        </div>
      </div>

      <div className="alerts-actions alerts-actions--split">
        <div className="alerts-action-card admin-action--sync">
          <div className="alerts-action-card__head">
            <h3 className="alerts-action-card__title">Sync from FuelArk</h3>
            <button
              type="button"
              className="btn btn-sm btn-scan"
              disabled={syncing || busy}
              onClick={syncFromFuelark}
            >
              {syncing ? 'Syncing…' : 'Sync now'}
            </button>
          </div>
          <p className="alerts-action-card__meta">
            Pull the account vehicle list and auto-add any new IMEIs. Also runs every 30 min.
          </p>
          {lastSync?.syncedAt ? (
            <p className="admin-sync-meta">
              Last sync {String(lastSync.syncedAt).replace('T', ' ').slice(0, 19)}
              {lastSync.fuelarkCount != null ? ` · ${lastSync.fuelarkCount} on FuelArk` : ''}
              {lastSync.addedCount ? ` · +${lastSync.addedCount} new` : ''}
            </p>
          ) : (
            <p className="admin-sync-meta">No sync yet — press Sync now to pull from FuelArk.</p>
          )}
        </div>

        <form
          className="alerts-action-card alerts-action-card--custom"
          onSubmit={submit}
        >
          <div className="alerts-action-card__head">
            <h3 className="alerts-action-card__title">Add by IMEI</h3>
            <button
              type="submit"
              className="btn btn-sm btn-custom"
              disabled={busy || syncing}
            >
              {busy ? 'Adding…' : 'Add'}
            </button>
          </div>
          <p className="alerts-action-card__meta">
            Paste a 14–16 digit FuelArk IMEI. Saved permanently to the roster.
          </p>
          <div className="alerts-field-grid alerts-field-grid--2">
            <label className="alerts-field">
              <span>IMEI</span>
              <input
                type="text"
                inputMode="numeric"
                placeholder="e.g. 353691846962883"
                value={imei}
                onChange={(e) => setImei(e.target.value.trim())}
                disabled={busy || syncing}
                maxLength={16}
              />
            </label>
            <label className="alerts-field">
              <span>History days</span>
              <select
                value={days}
                onChange={(e) => setDays(Number(e.target.value))}
                disabled={busy || syncing}
              >
                <option value={7}>7</option>
                <option value={14}>14</option>
                <option value={30}>30 (lifetime pull)</option>
                <option value={60}>60</option>
              </select>
            </label>
          </div>
        </form>
      </div>

      {msg ? <p className="admin-toast admin-toast--ok">{msg}</p> : null}
      {err ? <p className="admin-toast admin-toast--err">{err}</p> : null}

      <div className="panel admin-roster-panel">
        <div className="admin-roster-head">
          <div>
            <h2>IMEI roster</h2>
            <p className="admin-roster-sub">Lifetime map — click Go Live to open tracking</p>
          </div>
          <div className="admin-roster-tools">
            <input
              type="search"
              placeholder="Search name or IMEI…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <span className="admin-roster-count">
              {loadingList ? 'Loading…' : `${filtered.length} / ${devices.length}`}
            </span>
          </div>
        </div>

        <div className="table-wrap imei-table-wrap">
          <table className="live-table admin-imei-table">
            <thead>
              <tr>
                <th>Device name</th>
                <th>IMEI</th>
                <th>Points</th>
                <th>Latest</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {filtered.map((d) => {
                const active = selectedImei && String(d.imei) === String(selectedImei)
                const hasPts = (d.points ?? 0) > 0
                return (
                  <tr
                    key={`${d.deviceName}-${d.imei}`}
                    className={active ? 'imei-row-active' : ''}
                  >
                    <td>
                      <span className="admin-device-name">{d.deviceName}</span>
                    </td>
                    <td className="mono">{d.imei || '—'}</td>
                    <td>
                      <span className={`admin-pts-chip${hasPts ? ' is-ok' : ''}`}>
                        {d.points ?? 0}
                      </span>
                    </td>
                    <td className="admin-latest">
                      {d.latestTime
                        ? String(d.latestTime).replace('T', ' ').slice(0, 19)
                        : '—'}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-sm btn-scan"
                        onClick={() => onSelect?.(d)}
                        disabled={!d.deviceName}
                      >
                        Go Live
                      </button>
                    </td>
                  </tr>
                )
              })}
              {!loadingList && !filtered.length ? (
                <tr>
                  <td colSpan={5} className="muted">
                    No IMEIs match — sync from FuelArk or add one above.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
