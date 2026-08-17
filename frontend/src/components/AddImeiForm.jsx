import { useState } from 'react'
import { api } from '../api/client'

/**
 * Form to register a new FuelArk IMEI: fetch telemetry, map name, show live graph.
 */
export default function AddImeiForm({ onAdded }) {
  const [imei, setImei] = useState('')
  const [days, setDays] = useState(3)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  async function submit(e) {
    e?.preventDefault?.()
    const cleaned = String(imei).replace(/\D/g, '')
    if (cleaned.length < 14 || cleaned.length > 16) {
      setErr('Enter a 14–16 digit IMEI')
      return
    }
    setBusy(true)
    setErr('')
    setMsg('Fetching from FuelArk…')
    try {
      const res = await api.addImei(cleaned, days)
      if (!res.ok) {
        setErr(res.error || 'Failed to add IMEI')
        setMsg('')
        return
      }
      setMsg(
        `Added ${res.deviceName} · ${res.pointsFetched || 0} points` +
          (res.error ? ` (${res.error})` : '')
      )
      setImei('')
      onAdded?.(res)
    } catch (ex) {
      setErr(ex.message || 'Request failed')
      setMsg('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="add-imei" onSubmit={submit}>
      <div className="add-imei-row">
        <label className="add-imei-field">
          Add IMEI
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
          Days
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} disabled={busy}>
            <option value={1}>1</option>
            <option value={3}>3</option>
            <option value={7}>7</option>
          </select>
        </label>
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? 'Adding…' : 'Add & load'}
        </button>
      </div>
      {msg ? <p className="add-imei-msg ok">{msg}</p> : null}
      {err ? <p className="add-imei-msg error">{err}</p> : null}
    </form>
  )
}
