"use client";

import { usePathname } from "next/navigation";
import { TopBar } from "./TopBar";
import { Footer } from "./Footer";

/**
 * Routes drawn on their own, without the application chrome. A sign-in screen under the project
 * selector and the persona menu would be contradictory: both of those are the application, and
 * this route is what comes before it.
 */
const BARE = ["/signin"];

/**
 * The application frame: the top bar, the scrolling content area and the footer, or nothing but the
 * page on a route that draws its own.
 */
export function Chrome({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  if (BARE.some((p) => path === p || path.startsWith(`${p}/`))) {
    return <main className="flex-1 overflow-y-auto">{children}</main>;
  }
  return (
    <>
      <TopBar />
      <main className="flex-1 overflow-y-auto print:overflow-visible">
        {/* One centred column up to 1600 px with the gutter tokens (16 / 24 / 40 px); the bar above
            spans the window. Sticky bars inside pull out by the same gutter (-mx-[var(--gutter)]). */}
        <div className="mx-auto w-full max-w-content px-[var(--gutter)] py-8">{children}</div>
      </main>
      <Footer />
    </>
  );
}
