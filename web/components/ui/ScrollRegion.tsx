import type { ReactNode } from "react";

/** A horizontally scrollable table wrapper that keyboard users can focus and scroll. The label must
 * be distinct per table on a page, since screen readers list regions by name. */
export function ScrollRegion({
  label,
  className = "",
  as: Tag = "div",
  children,
}: {
  label: string;
  className?: string;
  as?: "div" | "section";
  children: ReactNode;
}) {
  return (
    <Tag tabIndex={0} role="region" aria-label={label} className={`table-scroll ${className}`.trim()}>
      {children}
    </Tag>
  );
}
