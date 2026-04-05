type Props = {
  title: string;
  value: string | number;
  detail?: string;
};

export function SummaryCard({ title, value, detail }: Props) {
  return (
    <div className="summary-card">
      <span className="summary-title">{title}</span>
      <strong className="summary-value">{value}</strong>
      {detail ? <span className="summary-detail">{detail}</span> : null}
    </div>
  );
}
