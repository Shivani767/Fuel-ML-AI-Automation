import { useMemo } from 'react'
import { Doughnut, Bar } from 'react-chartjs-2'
import {
  Chart as ChartJS,
  ArcElement,
  BarElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
} from 'chart.js'

ChartJS.register(ArcElement, BarElement, CategoryScale, LinearScale, Tooltip, Legend)

// Colorful Fleet mix / Counts palette.
const BAR_COLORS = ['#7c3aed', '#0ea5e9', '#22c55e', '#f97316']

const PIE_COLORS = {
  running: '#22c55e',
  idle: '#0ea5e9',
  inactive: '#f97316',
  battery: '#7c3aed',
}

const PIE_FALLBACK = ['#22c55e', '#0ea5e9', '#f97316', '#7c3aed', '#eab308', '#ec4899']

export default function FleetStatusChart({ summary }) {
  const segments = summary?.chart?.segments || []

  const doughnut = useMemo(() => {
    const rows = segments.filter((s) => Number(s.value) > 0)
    const fallback = rows.length
      ? rows
      : [{ label: 'No data', value: 1, color: '#e2e8f0' }]
    return {
      labels: fallback.map((s) => s.label),
      datasets: [
        {
          data: fallback.map((s) => s.value),
          backgroundColor: fallback.map((s, i) =>
            PIE_COLORS[s.key] || s.color || PIE_FALLBACK[i % PIE_FALLBACK.length]
          ),
          borderWidth: 3,
          borderColor: '#fff',
          hoverOffset: 10,
        },
      ],
    }
  }, [segments])

  const bars = useMemo(() => {
    const total = summary?.totalVehicle ?? 0
    const active = summary?.activeVehicle ?? 0
    const running = summary?.runningVehicle ?? 0
    const inactive = summary?.inactiveVehicle ?? 0
    return {
      labels: ['Total Vehicles', 'Active Vehicles', 'Running Vehicles', 'Inactive Vehicles'],
      datasets: [
        {
          label: 'Vehicles',
          data: [total, active, running, inactive],
          backgroundColor: BAR_COLORS,
          borderRadius: 8,
          maxBarThickness: 44,
        },
      ],
    }
  }, [summary])

  if (!summary) return null

  return (
    <div className="fleet-charts">
      <div className="fleet-chart-panel">
        <h3>Fleet mix</h3>
        <div className="fleet-doughnut-wrap">
          <Doughnut
            data={doughnut}
            options={{
              responsive: true,
              maintainAspectRatio: false,
              cutout: '64%',
              plugins: {
                legend: {
                  position: 'bottom',
                  labels: { boxWidth: 12, font: { size: 11 }, padding: 14 },
                },
                tooltip: {
                  callbacks: {
                    label: (ctx) => {
                      const v = ctx.raw || 0
                      const sum = ctx.dataset.data.reduce((a, b) => a + b, 0) || 1
                      return ` ${ctx.label}: ${v} (${Math.round((v / sum) * 100)}%)`
                    },
                  },
                },
              },
            }}
          />
          <div className="fleet-doughnut-center">
            <strong>{summary.totalVehicle ?? 0}</strong>
            <span>Total</span>
          </div>
        </div>
      </div>
      <div className="fleet-chart-panel">
        <h3>Counts</h3>
        <div className="fleet-bar-wrap">
          <Bar
            data={bars}
            options={{
              responsive: true,
              maintainAspectRatio: false,
              plugins: { legend: { display: false } },
              scales: {
                y: {
                  beginAtZero: true,
                  ticks: { precision: 0, color: '#64748b' },
                  grid: { color: '#e2e8f0' },
                  border: { display: false },
                },
                x: {
                  ticks: { color: '#475569', font: { weight: '600' } },
                  grid: { display: false },
                  border: { display: false },
                },
              },
            }}
          />
        </div>
      </div>
    </div>
  )
}
