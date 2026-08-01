interface BarItem {
  label: string
  value: number
  color?: string
}

export function SimpleBars({
  title,
  items,
}: {
  title: string
  items: BarItem[]
}) {
  const max = Math.max(1, ...items.map((i) => i.value))
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <h4 className="mb-3 text-sm font-semibold text-slate-800">{title}</h4>
      <div className="space-y-2">
        {items.map((item) => (
          <div key={item.label}>
            <div className="mb-0.5 flex justify-between text-xs text-slate-500">
              <span>{item.label}</span>
              <span className="font-medium text-slate-700">{item.value}</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full ${item.color ?? 'bg-slate-700'}`}
                style={{ width: `${(item.value / max) * 100}%` }}
              />
            </div>
          </div>
        ))}
        {items.length === 0 && <p className="text-xs text-slate-400">No data</p>}
      </div>
    </div>
  )
}
