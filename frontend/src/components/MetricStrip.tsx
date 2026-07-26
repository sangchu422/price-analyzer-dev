export interface MetricStripItem {
  label: string;
  value: string;
  emphasis?: boolean;
}

export function MetricStrip({ items }: { items: MetricStripItem[] }) {
  return (
    <dl className="metric-strip">
      {items.map((item) => (
        <div className={item.emphasis ? "is-emphasis" : undefined} key={item.label}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
