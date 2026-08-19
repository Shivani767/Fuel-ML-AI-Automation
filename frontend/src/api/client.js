const BASE = import.meta.env.VITE_API_BASE || ''

async function get(path) {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err = new Error(data.error || `${res.status} ${res.statusText}`)
    err.payload = data
    throw err
  }
  return data
}

function qs(params = {}) {
  const sp = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') sp.set(k, v)
  })
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export const api = {
  health: () => get('/api/health'),
  // Cache-first by default, matching the device endpoints below and the
  // documented policy. `refresh=1` re-polls all ~104 IMEIs from FuelArk and takes
  // ~107s; `refresh=0` serves the cached snapshot in ~5ms. Pass '1' or 'force'
  // only from an explicit user action.
  fleetSummary: (refresh = '0') => get(`/api/fleet/summary${qs({ refresh })}`),
  fleetDevices: (search = '', refresh = '0') =>
    get(`/api/fleet/devices${qs({ q: search, refresh })}`),
  deviceNames: () => get('/api/devices'),
  addImei: async (imei, days = 30, lifetime = true) => {
    const res = await fetch(`${BASE}/api/imei/add`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ imei, days, lifetime: lifetime ? 1 : 0 }),
    })
    const data = await res.json().catch(() => ({}))
    return { ok: Boolean(data.ok), ...data, httpStatus: res.status }
  },
  imeiList: () => get('/api/imei/list'),
  deviceSeries: (name, from, to, opts = {}) =>
    get(
      `/api/devices/${encodeURIComponent(name)}/series${qs({
        from,
        to,
        downsample: opts.downsample ?? 25,
        slab: opts.slab,
        limit: opts.limit ?? 2500,
        fetch: opts.fetch ?? '0',
      })}`
    ),
  deviceSummary: (name, from, to, opts = {}) =>
    get(
      `/api/devices/${encodeURIComponent(name)}/summary${qs({
        from,
        to,
        fetch: opts.fetch ?? '0',
        detect: opts.detect ?? '0',
      })}`
    ),
  deviceEvents: (name, from, to, opts = {}) =>
    get(
      `/api/devices/${encodeURIComponent(name)}/events${qs({
        from,
        to,
        fetch: opts.fetch ?? '0',
        theft_min: opts.theftMin ?? 5,
        fill_min: opts.fillMin ?? 5,
        include_stored: opts.includeStored ? '1' : '0',
      })}`
    ),
  mlStatus: () => get('/api/ml/status'),
  alertsLatest: () => get('/api/alerts/latest'),
  alertsRun: () => post('/api/alerts/run'),
  // Read-only scan of one explicit window ("what happened 12:00-12:30"). Writes
  // nothing, so unlike alertsRun it can be asked for the same slab repeatedly.
  alertsSlab: (from, to) => get(`/api/alerts/slab${qs({ from, to })}`),
  alertsHistory: (day = '', limit = 500, filtered = false) =>
    get(`/api/alerts${qs({ day, limit, filtered: filtered ? 1 : 0 })}`),
  alertsDays: () => get('/api/alerts/days'),
  alertsCsvUrl: (day, kind = 'alerts') =>
    `/api/alerts/download/${encodeURIComponent(day)}?kind=${kind}`,
  voltageProfiles: () => get('/api/alerts/voltage-profiles'),
}
