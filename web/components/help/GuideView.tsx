"use client";

// Renders one bundled guide. react-markdown builds React elements and never injects HTML (no
// rehype-raw), so the guides cannot smuggle markup in. Heading ids come from rehype-slug; a link
// icon on each heading copies the anchor. Links between the guides are rewritten to /help/<slug>;
// a link to any other file in the repository has nowhere to go inside the app and renders as code.
import { Children, isValidElement, useState, type ReactNode } from "react";
import Link from "next/link";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeSlug from "rehype-slug";
import remarkGfm from "remark-gfm";
import { Check, Copy, Link2 } from "lucide-react";
import { guideHref } from "@/lib/help";

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children);
  return Children.toArray(node).map(textOf).join("");
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    } catch {
      /* clipboard blocked (insecure origin or permissions): the text stays selectable */
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      aria-label="Copy"
      className="absolute right-2 top-2 flex items-center gap-1 rounded-btn bg-white/10 px-2 py-1 text-[12px] font-medium text-lilac opacity-80 transition hover:bg-white/20 hover:text-white hover:opacity-100 focus-visible:opacity-100"
    >
      {done ? <Check size={13} aria-hidden /> : <Copy size={13} aria-hidden />}
      {done ? "Copied" : "Copy"}
    </button>
  );
}

function heading(Tag: "h1" | "h2" | "h3" | "h4", cls: string) {
  const Heading = ({ id, children }: { id?: string; children?: ReactNode }) => (
    <Tag id={id} className={`group scroll-mt-6 ${cls}`}>
      {children}
      {id ? (
        <a href={`#${id}`} aria-label="Link to this section" className="ml-2 inline-block align-middle text-purple opacity-0 transition group-hover:opacity-100 focus-visible:opacity-100">
          <Link2 size={15} aria-hidden />
        </a>
      ) : null}
    </Tag>
  );
  return Heading;
}

const COMPONENTS: Components = {
  h1: heading("h1", "mb-4 font-display text-[28px] font-semibold leading-tight tracking-[-0.015em] text-plum"),
  h2: heading("h2", "mb-3 mt-10 border-t border-line pt-6 font-display text-[21px] font-semibold tracking-[-0.01em] text-plum"),
  h3: heading("h3", "mb-2 mt-7 text-[17px] font-semibold text-plum"),
  h4: heading("h4", "mb-2 mt-5 text-[15px] font-semibold text-plum"),
  p: ({ children }) => <p className="my-3 max-w-[72ch]">{children}</p>,
  ul: ({ children }) => <ul className="my-3 max-w-[72ch] list-disc space-y-1.5 pl-6">{children}</ul>,
  ol: ({ children }) => <ol className="my-3 max-w-[72ch] list-decimal space-y-1.5 pl-6">{children}</ol>,
  blockquote: ({ children }) => <blockquote className="my-4 max-w-[72ch] border-l-4 border-purple/40 bg-purple-lt/50 py-1 pl-4 pr-3 text-slate">{children}</blockquote>,
  hr: () => <hr className="my-8 border-line" />,
  a: ({ href = "", children }) => {
    const guide = guideHref(href);
    if (guide) return <Link href={guide} className="text-purple underline-offset-2 hover:underline">{children}</Link>;
    if (href.startsWith("#")) return <a href={href} className="text-purple underline-offset-2 hover:underline">{children}</a>;
    if (/^https?:\/\//i.test(href)) {
      return <a href={href} target="_blank" rel="noopener noreferrer" className="text-purple underline-offset-2 hover:underline">{children}</a>;
    }
    return <code className="rounded bg-warm px-1 py-0.5 font-mono text-[0.9em]">{children}</code>;
  },
  code: ({ className, children }) =>
    className ? (
      <code className={`${className} font-mono`}>{children}</code>
    ) : (
      <code className="rounded bg-warm px-1 py-0.5 font-mono text-[0.88em] text-plum">{children}</code>
    ),
  pre: ({ children }) => (
    <div className="relative my-4">
      <pre className="overflow-x-auto rounded-card bg-plum p-4 pr-20 text-[13px] leading-relaxed text-white/90">{children}</pre>
      <CopyButton text={textOf(children).replace(/\n$/, "")} />
    </div>
  ),
  table: ({ children }) => (
    <div className="my-5 overflow-x-auto rounded-card border border-line">
      <table className="w-full text-left text-[14px]">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="table-head">{children}</thead>,
  th: ({ children }) => <th className="px-3 py-2 align-bottom">{children}</th>,
  td: ({ children }) => <td className="border-t border-line px-3 py-2 align-top">{children}</td>,
};

export function GuideView({ markdown }: { markdown: string }) {
  return (
    <article data-testid="guide" className="text-[15px] leading-relaxed text-ink">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSlug]} components={COMPONENTS}>
        {markdown}
      </ReactMarkdown>
    </article>
  );
}
