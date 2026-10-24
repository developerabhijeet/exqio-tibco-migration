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
        <div className="w-full px-6 py-6">{children}</div>
      </main>
      <Footer />
    </>
  );
}
