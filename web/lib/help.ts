// The Help & Support pages: which guides exist and how they are named in the left nav. The text
// itself is bundled at build time by scripts/copy-guides.mjs into content/help/<slug>.md.
export const GUIDES = [
  { slug: "install", label: "Install on GMF", title: "Install on GM Financial's AKS" },
  { slug: "deploy-step-secrets", label: "Configure Deploy-step secrets", title: "Configure Deploy-step secrets" },
  { slug: "runbook", label: "Runbook", title: "Runbook" },
  { slug: "troubleshooting", label: "Troubleshooting", title: "Troubleshooting" },
] as const;

export const ABOUT = { slug: "about", label: "About this install", title: "About this install" } as const;

/** The guide a relative link such as `RUNBOOK.md#scaling-workers` points at, as a /help path. */
const FILE_SLUGS: Record<string, string> = {
  "gmf-install.md": "install",
  "deploy-step-secrets.md": "deploy-step-secrets",
  "runbook.md": "runbook",
};

export function guideHref(href: string): string | null {
  const [file, hash] = href.split("#");
  const slug = FILE_SLUGS[file.replace(/^\.\//, "").toLowerCase()];
  return slug ? `/help/${slug}${hash ? `#${hash}` : ""}` : null;
}
