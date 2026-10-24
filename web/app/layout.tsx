import type { Metadata } from "next";
import localFont from "next/font/local";
import "@/lib/theme.css";
import { Providers } from "./providers";
import { Chrome } from "@/components/shell/Chrome";

// Both faces come from @fontsource packages: the build machine cannot reach Google Fonts, and a
// local copy keeps the app self-contained.
//
// Two faces, because the screens ask two different things of the type. Poppins is a geometric
// display face: it carries the brand well in a screen title, and it is the wrong tool for a
// 13px table of file paths, hashes and test outcomes, where its wide round forms and single-storey
// "a" cost legibility exactly where this app is densest. Inter was drawn for that: it keeps its
// shape at small sizes and has real tabular figures, so columns of numbers and hashes line up.
// So Poppins stays on the titles and Inter takes everything else.
const poppins = localFont({
  src: [
    { path: "../node_modules/@fontsource/poppins/files/poppins-latin-500-normal.woff2", weight: "500" },
    { path: "../node_modules/@fontsource/poppins/files/poppins-latin-600-normal.woff2", weight: "600" },
    { path: "../node_modules/@fontsource/poppins/files/poppins-latin-700-normal.woff2", weight: "700" },
  ],
  variable: "--font-poppins",
  display: "swap",
});

const inter = localFont({
  src: [
    { path: "../node_modules/@fontsource/inter/files/inter-latin-400-normal.woff2", weight: "400" },
    { path: "../node_modules/@fontsource/inter/files/inter-latin-500-normal.woff2", weight: "500" },
    { path: "../node_modules/@fontsource/inter/files/inter-latin-600-normal.woff2", weight: "600" },
    { path: "../node_modules/@fontsource/inter/files/inter-latin-700-normal.woff2", weight: "700" },
  ],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "ExiQO Migrate",
  description: "TIBCO BusinessWorks to .NET migration",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${poppins.variable}`}>
      {/* The window itself never scrolls: the top bar and the footer are always on screen and the
          content area is the only scroller, so the stepper can pin itself to the top of it. Print
          undoes this, where a page is as tall as it needs to be. */}
      <body className="flex h-screen flex-col overflow-hidden bg-page font-sans text-ink antialiased print:h-auto print:overflow-visible">
        <Providers>
          <Chrome>{children}</Chrome>
        </Providers>
      </body>
    </html>
  );
}
