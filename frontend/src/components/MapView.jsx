import { useEffect, useMemo, useState } from 'react'
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

const icon = new L.Icon({
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
  iconSize: [25, 41],
  iconAnchor: [12, 41],
})

function validCoord(lat, lon) {
  const a = Number(lat)
  const b = Number(lon)
  return Number.isFinite(a) && Number.isFinite(b) && Math.abs(a) > 0.1 && Math.abs(b) > 0.1
}

function FitBounds({ markers }) {
  const map = useMap()
  useEffect(() => {
    if (!markers.length) return
    const bounds = L.latLngBounds(markers.map((d) => [Number(d.latitude), Number(d.longitude)]))
    map.fitBounds(bounds.pad(0.2))
  }, [map, markers])
  return null
}

export default function MapView({ devices }) {
  const [ready, setReady] = useState(false)
  useEffect(() => setReady(true), [])

  const markers = useMemo(
    () => (devices || []).filter((d) => validCoord(d.latitude, d.longitude)),
    [devices]
  )
  const center = markers.length
    ? [Number(markers[0].latitude), Number(markers[0].longitude)]
    : [22.9734, 78.6569]

  if (!ready) {
    return <div className="map-wrap muted" style={{ display: 'grid', placeItems: 'center' }}>Loading map…</div>
  }

  return (
    <div className="map-wrap">
      <MapContainer
        center={center}
        zoom={5}
        style={{ height: '100%', width: '100%' }}
        scrollWheelZoom
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <FitBounds markers={markers} />
        {markers.map((d) => (
          <Marker
            key={d.deviceName}
            position={[Number(d.latitude), Number(d.longitude)]}
            icon={icon}
          >
            <Popup>
              <strong>{d.deviceName}</strong>
              <br />
              Fuel: {d.fuel} L · {d.speed} km/h · Ignition {d.ignition}
            </Popup>
          </Marker>
        ))}
      </MapContainer>
    </div>
  )
}
