import type { Metadata } from "next";
import localFont from "next/font/local";
import "@/lib/theme.css";
import { Providers } from "./providers";
import { TopBar } from "@/components/shell/TopBar";
import { SyntheticBanner } from "@/components/shell/SyntheticBanner";
import { Footer } from "@/components/shell/Footer";

// Poppins from the @fontsource package: the build machine cannot reach Google Fonts, and a
// local copy keeps the app self-contained.
const poppins = localFont({
  src: [
    { path: "../node_modules/@fontsource/poppins/files/poppins-latin-400-normal.woff2", weight: "400" },
    { path: "../node_modules/@fontsource/poppins/files/poppins-latin-500-normal.woff2", weight: "500" },
    { path: "../node_modules/@fontsource/poppins/files/poppins-latin-600-normal.woff2", weight: "600" },
    { path: "../node_modules/@fontsource/poppins/files/poppins-latin-700-normal.woff2", weight: "700" },
  ],
  variable: "--font-poppins",
  display: "swap",
});

export const metadata: Metadata = {
  title: "ExiQO Migrate",
  description: "TIBCO BusinessWorks to .NET migration",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={poppins.variable}>
      <body className="flex min-h-screen flex-col bg-page font-sans text-ink antialiased">
        <Providers>
          <TopBar />
          <SyntheticBanner />
          <main className="mx-auto w-full max-w-[1440px] flex-1 px-6 py-6">{children}</main>
          <Footer />
        </Providers>
      </body>
    </html>
  );
}
