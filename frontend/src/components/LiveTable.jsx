export default function LiveTable({ points }) {
  const rows = (points || []).slice(-200).reverse()
  return (
    <div className="panel table-wrap">
      <table className="live-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Fuel Level</th>
            <th>Fuel Level EDC</th>
            <th>Ignition</th>
            <th>Movement</th>
            <th>Speed</th>
            <th>Power Ex</th>
            <th>Power In</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={`${p.time}-${p.fuelLevel}`}>
              <td>{p.time}</td>
              <td>{p.fuelLevel}</td>
              <td>{p.fuelLevelEDC}</td>
              <td>{p.ignition ? 'On' : 'Off'}</td>
              <td>{p.movement ? 'On' : 'Off'}</td>
              <td>{p.speed}</td>
              <td>{p.powerEx}</td>
              <td>{p.powerIn}</td>
            </tr>
          ))}
          {!rows.length ? (
            <tr>
              <td colSpan={8} className="muted">
                No points for selected range
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  )
}
