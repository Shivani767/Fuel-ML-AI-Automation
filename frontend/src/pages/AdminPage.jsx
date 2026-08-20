import { Link, useNavigate } from 'react-router-dom'
import ImeiRoster from '../components/ImeiRoster'

/**
 * Admin: manage lifetime IMEI / vehicle roster (add by IMEI).
 */
export default function AdminPage() {
  const navigate = useNavigate()

  return (
    <div className="admin-page">
      <header className="topbar topbar-center fleet-topbar">
        <div className="topbar-center-block">
          <h1>Vehicle Admin</h1>
          <div className="fleet-top-actions">
            <Link to="/" className="btn btn-sm btn-custom">
              Fleet
            </Link>
            <Link to="/alerts" className="btn btn-sm btn-scan">
              Fuel Alerts
            </Link>
            <Link to="/live" className="btn btn-sm btn-slab">
              Live Tracking
            </Link>
          </div>
        </div>
      </header>

      <div className="content">
        <p className="admin-lead">
          Existing IMEIs stay as stored. Sync from FuelArk or add a vehicle by IMEI — saved for lifetime.
        </p>

        <ImeiRoster
          onSelect={(row) => {
            if (row?.deviceName) {
              navigate(
                `/live?device=${encodeURIComponent(row.deviceName)}&stream=1`
              )
            }
          }}
          onAdded={(res) => {
            if (res?.deviceName) {
              navigate(
                `/live?device=${encodeURIComponent(res.deviceName)}&stream=1`
              )
            }
          }}
        />

        <div className="admin-howto panel">
          <h2>How it works</h2>
          <ul>
            <li>
              <strong>Sync from FuelArk</strong> pulls the vehicle list from your{' '}
              <a href="https://web1.fuelark.com/Home" target="_blank" rel="noreferrer">
                FuelArk Home
              </a>{' '}
              account and auto-adds new IMEIs (also runs every 30 min with the pipeline).
            </li>
            <li>Paste a 14–16 digit FuelArk IMEI and choose history days for a manual add.</li>
            <li>
              Telemetry is fetched, the device name is mapped, and the IMEI is written to{' '}
              <code>device_imei_map.json</code> permanently.
            </li>
            <li>
              Fleet Total / Active / Running / Inactive counts use unique IMEIs from this roster.
            </li>
            <li>
              Use <strong>Go Live</strong> to open the live graph for that vehicle.
            </li>
          </ul>
        </div>
      </div>
    </div>
  )
}
