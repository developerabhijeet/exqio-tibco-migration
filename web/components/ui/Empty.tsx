import { Inbox, type LucideIcon } from "lucide-react";

/**
 * An empty state: an icon, the title, one sentence and the next action, centred in its card.
 * `children` is the sentence (or any short body); `action` the button or link that moves on.
 */
export function Empty({
  title,
  children,
  icon: Icon = Inbox,
  action,
}: {
  title: string;
  children?: React.ReactNode;
  icon?: LucideIcon;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center rounded-card border border-dashed border-line-strong bg-sunken/50 px-6 py-10 text-center">
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-surface text-slate shadow-e1" aria-hidden>
        <Icon size={20} />
      </span>
      <p className="mt-3 text-base font-semibold text-heading">{title}</p>
      {children ? <div className="mt-1 max-w-md text-md text-slate">{children}</div> : null}
      {action ? <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div> : null}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  const text = error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="rounded-card border border-error/30 bg-error-bg px-4 py-3 text-md text-error">
      {text}
    </div>
  );
}
