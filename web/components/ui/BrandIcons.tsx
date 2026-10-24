"use client";

import { useId } from "react";

/** Vendor marks drawn inline: lucide carries no brand logos and the app ships no icon fonts. */

/** The Microsoft four-square mark, in its brand colours. */
export function MicrosoftIcon({ size = 16, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 23 23" className={className} aria-hidden focusable="false">
      <rect x="1" y="1" width="10" height="10" fill="#F25022" />
      <rect x="12" y="1" width="10" height="10" fill="#7FBA00" />
      <rect x="1" y="12" width="10" height="10" fill="#00A4EF" />
      <rect x="12" y="12" width="10" height="10" fill="#FFB900" />
    </svg>
  );
}

/** The Azure "A" mark. The gradient id is per-instance, so several on a page do not collide. */
export function AzureIcon({ size = 16, className = "" }: { size?: number; className?: string }) {
  const id = useId();
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} aria-hidden focusable="false">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#114A8B" />
          <stop offset="45%" stopColor="#0078D4" />
          <stop offset="100%" stopColor="#50E6FF" />
        </linearGradient>
      </defs>
      <path
        fill={`url(#${id})`}
        d="M5.483 21.3H24L14.025 4.013l-3.038 8.347 5.836 6.938L5.483 21.3zM13.23 2.7L6.105 8.677 0 19.253h5.505L13.23 2.7z"
      />
    </svg>
  );
}
