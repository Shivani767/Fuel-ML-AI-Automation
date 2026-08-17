import { Link, useNavigate } from 'react-router-dom'
import ImeiRoster from '../components/ImeiRoster'

/**
 * Admin: manage lifetime IMEI / vehicle roster (add by IMEI).
 */
export default function AdminPage() {
  const navigate = useNavigate()

  return (
    <>
      <header className="topbar">
        <div>
          <div className="breadcrumb">Admin / Vehicles</div>
          <h1>Vehicle admin</h1>
          <p className="topbar-subtitle">
            Existing IMEIs stay as stored. Add a new vehicle by IMEI — saved for lifetime.
          </p>
        </div>
        <Link to="/" className="btn btn-ghost">
          ← Fleet
        </Link>
      </header>
      <div className="content">
        <ImeiRoster
          onSelect={(row) => {
            if (row?.deviceName) {
              navigate(`/live?device=${encodeURIComponent(row.deviceName)}`)
            }
          }}
          onAdded={(res) => {
            if (res?.deviceName) {
              navigate(`/live?device=${encodeURIComponent(res.deviceName)}`)
            }
          }}
        />
        <div className="panel" style={{ marginTop: 12 }}>
          <h2 style={{ marginTop: 0, fontSize: 16 }}>How it works</h2>
          <ul className="muted" style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.6 }}>
            <li>Paste a 14–16 digit FuelArk IMEI and choose history days.</li>
            <li>
              The system fetches telemetry, maps the device name, and writes it to{' '}
              <code>device_imei_map.json</code> permanently.
            </li>
            <li>
              Fleet Total / Active / Running / Inactive counts use unique IMEIs from this roster.
            </li>
            <li>
              Use <strong>Open lifetime</strong> to open the live graph for that vehicle.
            </li>
          </ul>
        </div>
      </div>
    </>
  )
}
