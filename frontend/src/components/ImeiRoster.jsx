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
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [open, setOpen] = useState(true)

  const refresh = useCallback(async () => {
    setLoadingList(true)
    try {
      const res = await api.imeiList()
      setDevices(res.devices || [])
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
    <div className="imei-roster panel">
      <div className="imei-roster-head">
        <div>
          <h2 style={{ margin: 0, fontSize: 16 }}>IMEI roster (lifetime)</h2>
          <p className="muted" style={{ margin: '4px 0 0', fontSize: 12 }}>
            Existing IMEIs stay as stored. New ones are saved permanently to the map.
          </p>
        </div>
        <button type="button" className="btn btn-ghost" onClick={() => setOpen((v) => !v)}>
          {open ? 'Hide' : 'Show'}
        </button>
      </div>

      {open ? (
        <>
          <form className="add-imei" style={{ marginBottom: 10, border: 'none', padding: 0 }} onSubmit={submit}>
            <div className="add-imei-row">
              <label className="add-imei-field">
                Add new IMEI
                <input
                  type="text"
                  inputMode="numeric"
                  placeholder="e.g. 353691846962883"
                  value={imei}
                  onChange={(e) => setImei(e.target.value.trim())}
                  disabled={busy}
                  maxLength={16}
                />
              </label>
              <label className="add-imei-field add-imei-days">
                History days
                <select
                  value={days}
                  onChange={(e) => setDays(Number(e.target.value))}
                  disabled={busy}
                >
                  <option value={7}>7</option>
                  <option value={14}>14</option>
                  <option value={30}>30 (lifetime pull)</option>
                  <option value={60}>60</option>
                </select>
              </label>
              <button type="submit" className="btn btn-primary" disabled={busy}>
                {busy ? 'Adding…' : 'Add for lifetime'}
              </button>
            </div>
            {msg ? <p className="add-imei-msg ok">{msg}</p> : null}
            {err ? <p className="add-imei-msg error">{err}</p> : null}
          </form>

          <div className="imei-roster-tools">
            <input
              type="search"
              placeholder="Search existing name or IMEI…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <span className="muted" style={{ fontSize: 12 }}>
              {loadingList ? 'Loading…' : `${filtered.length} / ${devices.length} IMEIs`}
            </span>
          </div>

          <div className="table-wrap imei-table-wrap">
            <table className="live-table">
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
                  return (
                    <tr key={`${d.deviceName}-${d.imei}`} className={active ? 'imei-row-active' : ''}>
                      <td>{d.deviceName}</td>
                      <td className="mono">{d.imei || '—'}</td>
                      <td>{d.points ?? 0}</td>
                      <td style={{ fontSize: 12 }}>
                        {d.latestTime
                          ? String(d.latestTime).replace('T', ' ').slice(0, 19)
                          : '—'}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-ghost"
                          style={{ padding: '4px 10px', fontSize: 12 }}
                          onClick={() => onSelect?.(d)}
                          disabled={!d.deviceName}
                        >
                          Open lifetime
                        </button>
                      </td>
                    </tr>
                  )
                })}
                {!loadingList && !filtered.length ? (
                  <tr>
                    <td colSpan={5} className="muted">
                      No IMEIs match
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </div>
  )
}
