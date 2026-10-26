import Link from "next/link";
import { ChevronLeft } from "lucide-react";

/**
 * The header every screen opens with: an eyebrow (where the reader is), the title, one line of
 * description and the screen's actions, on one row that wraps under 768 px. `back` adds a
 * breadcrumb link above it (the secondary screens link back to the migration).
 */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  back,
  testId,
}: {
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  back?: { href: string; label: string };
  testId?: string;
}) {
  return (
    <header className="flex flex-col gap-3" data-testid={testId ?? "page-header"}>
      {back ? (
        <nav aria-label="Breadcrumb">
          <Link
            href={back.href}
            className="inline-flex items-center gap-1 rounded-btn text-sm font-medium text-slate transition-colors duration-fast hover:text-accent-ink"
          >
            <ChevronLeft size={16} aria-hidden />
            {back.label}
          </Link>
        </nav>
      ) : null}
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div className="min-w-0">
          {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
          <h1 className="screen-title mt-1 break-words">{title}</h1>
          {description ? <p className="mt-1.5 max-w-3xl text-base text-slate">{description}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap items-center gap-3">{actions}</div> : null}
      </div>
    </header>
  );
}
