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
          backgroundColor: fallback.map((s) => s.color),
          borderWidth: 2,
          borderColor: '#fff',
          hoverOffset: 6,
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
      labels: ['Total', 'Active', 'Running', 'Inactive'],
      datasets: [
        {
          label: 'Vehicles',
          data: [total, active, running, inactive],
          backgroundColor: ['#0f172a', '#2563eb', '#16a34a', '#94a3b8'],
          borderRadius: 6,
          maxBarThickness: 42,
        },
      ],
    }
  }, [summary])

  if (!summary) return null

  return (
    <div className="fleet-charts">
      <div className="fleet-chart-panel">
        <h3>Fleet mix now</h3>
        <p className="muted" style={{ marginTop: 0, fontSize: 12 }}>
          Running ⊂ Active · Inactive = offline · Total = Active + Inactive (+ battery)
        </p>
        <div className="fleet-doughnut-wrap">
          <Doughnut
            data={doughnut}
            options={{
              responsive: true,
              maintainAspectRatio: false,
              cutout: '62%',
              plugins: {
                legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
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
        <p className="muted" style={{ marginTop: 0, fontSize: 12 }}>
          {summary.definitions?.identityCheck ||
            `${summary.totalVehicle} total · ${summary.activeVehicle} active · ${summary.runningVehicle} running · ${summary.inactiveVehicle} inactive`}
        </p>
        <div style={{ height: 220 }}>
          <Bar
            data={bars}
            options={{
              responsive: true,
              maintainAspectRatio: false,
              plugins: { legend: { display: false } },
              scales: {
                y: {
                  beginAtZero: true,
                  ticks: { precision: 0 },
                  grid: { color: '#f1f5f9' },
                },
                x: { grid: { display: false } },
              },
            }}
          />
        </div>
      </div>
    </div>
  )
}
