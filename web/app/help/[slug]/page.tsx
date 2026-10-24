// One help topic. The guides are read from content/help/ (bundled by scripts/copy-guides.mjs) while
// the page is built, so nothing is fetched when it is viewed and nothing leaves the origin.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { notFound } from "next/navigation";
import { SecondaryScreen } from "@/components/shell/SecondaryScreen";
import { AboutInstall } from "@/components/help/AboutInstall";
import { GuideView } from "@/components/help/GuideView";
import { HelpNav, SupportFoot } from "@/components/help/HelpNav";
import { ABOUT, GUIDES } from "@/lib/help";

export const dynamicParams = false;

export function generateStaticParams() {
  return [...GUIDES, ABOUT].map(({ slug }) => ({ slug }));
}

export default async function Page({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const topic = [...GUIDES, ABOUT].find((t) => t.slug === slug);
  if (!topic) notFound();
  const markdown = slug === ABOUT.slug ? null : readFileSync(join(process.cwd(), "content", "help", `${slug}.md`), "utf8");
  return (
    <SecondaryScreen title="Help & Support" description="Install, configure and operate this install. Everything here is bundled with the app.">
      <div className="grid grid-cols-1 gap-8 md:grid-cols-[240px_minmax(0,1fr)]">
        <HelpNav current={slug} />
        <div className="min-w-0">
          {markdown === null ? <AboutInstall /> : <GuideView markdown={markdown} />}
          <SupportFoot />
        </div>
      </div>
    </SecondaryScreen>
  );
}
