import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import AppShell from './components/AppShell'
import FleetDashboard from './pages/FleetDashboard'
import FleetVehicles from './pages/FleetVehicles'
import LiveMap from './pages/LiveMap'
import AdminPage from './pages/AdminPage'
import AlertsPage from './pages/AlertsPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<FleetDashboard />} />
          <Route path="/fleet/vehicles" element={<FleetVehicles />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/live" element={<LiveMap />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
