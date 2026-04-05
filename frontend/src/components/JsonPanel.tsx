type Props = {
  title: string;
  value: unknown;
};

export function JsonPanel({ title, value }: Props) {
  return (
    <section className="panel">
      <h3>{title}</h3>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </section>
  );
}
