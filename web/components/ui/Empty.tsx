export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-card border border-dashed border-line bg-warm/60 px-6 py-8 text-center">
      <p className="font-medium text-plum">{title}</p>
      {children ? <div className="mt-1 text-[14px] text-slate">{children}</div> : null}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  const text = error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="rounded-card border border-error/30 bg-error-bg px-4 py-3 text-[14px] text-error">
      {text}
    </div>
  );
}
