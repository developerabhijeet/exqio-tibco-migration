"use client";

// Read-only Monaco, loaded from this app's public/monaco (copied at install), never a CDN.
import { useEffect, useRef, useState } from "react";
import Editor, { loader, type OnMount } from "@monaco-editor/react";
import { cssColor, isDarkNow, useThemed } from "@/lib/theme";
import type { XmlHighlight } from "@/lib/xmlspan";

// Monaco loads its language services into web workers it creates from a blob. A blob worker has no
// origin of its own, so a root-relative path like "/monaco/vs" cannot be resolved inside it — that
// is the console error "Failed to parse URL from /monaco/vs/language/json/jsonWorker.js". The loader
// and the worker bootstrap both get absolute URLs instead. Guarded because this module is also
// evaluated while the page is rendered on the server, where there is no window.
if (typeof window !== "undefined") {
  const base = `${window.location.origin}/monaco`;
  loader.config({ paths: { vs: `${base}/vs` } });
  const w = window as Window & { MonacoEnvironment?: { getWorkerUrl: () => string } };
  w.MonacoEnvironment = {
    getWorkerUrl: () =>
      URL.createObjectURL(
        new Blob(
          [
            `self.MonacoEnvironment = { baseUrl: ${JSON.stringify(`${base}/`)} };\n`,
            `importScripts(${JSON.stringify(`${base}/vs/base/worker/workerMain.js`)});\n`,
          ],
          { type: "text/javascript" },
        ),
      ),
  };
}

type MonacoEditor = Parameters<OnMount>[0];
type Monaco = Parameters<OnMount>[1];

export default function CodeView({
  value,
  language,
  height,
  highlight,
  testId,
  wrap = false,
}: {
  value: string;
  language: string;
  height: number | string;
  highlight?: XmlHighlight | null;
  testId?: string;
  /** Wrap long lines (the Build tab's TIBCO pane, where attribute values hold whole SQL statements). */
  wrap?: boolean;
}) {
  const editorRef = useRef<MonacoEditor | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  const decorations = useRef<string[]>([]);
  const [missing, setMissing] = useState(false);
  // Monaco's own theme follows the page; null until mounted so it never opens in the wrong one.
  const dark = useThemed(isDarkNow);

  // The editor files are copied into public/monaco by scripts/copy-monaco.mjs; say so if they are not there.
  useEffect(() => {
    let live = true;
    fetch("/monaco/vs/loader.js", { method: "HEAD" })
      .then((r) => live && !r.ok && setMissing(true))
      .catch(() => live && setMissing(true));
    return () => {
      live = false;
    };
  }, []);

  const apply = () => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    if (!editor || !monaco) return;
    if (!highlight) {
      decorations.current = editor.deltaDecorations(decorations.current, []);
      return;
    }
    // The band skips the focus line so the stronger focus color is not painted over.
    const band = [
      [highlight.start, highlight.focus - 1],
      [highlight.focus + 1, highlight.end],
    ].filter(([a, b]) => a <= b);
    decorations.current = editor.deltaDecorations(decorations.current, [
      ...band.map(([a, b]) => ({
        range: new monaco.Range(a, 1, b, 1),
        options: { isWholeLine: true, className: "xml-hl-line", overviewRuler: { color: cssColor("highlight-strong"), position: 4 } },
      })),
      {
        range: new monaco.Range(highlight.focus, 1, highlight.focus, 1),
        options: { isWholeLine: true, className: "xml-hl-focus" },
      },
    ]);
    editor.revealLineInCenter(highlight.focus);
  };

  useEffect(apply, [highlight, value, dark]);

  if (missing) {
    return (
      <div role="alert" className="rounded-btn border border-error/30 bg-error-bg p-4 text-md text-error">
        The code viewer files are missing from public/monaco. Run npm install (or npm run dev) in web/ to copy them.
      </div>
    );
  }

  return (
    <div
      data-testid={testId}
      data-hl-start={highlight?.start}
      data-hl-end={highlight?.end}
      data-hl-focus={highlight?.focus}
      data-hl-focus-text={highlight ? (value.split(/\r\n|\n|\r/)[highlight.focus - 1] ?? "").trim() : undefined}
      className="overflow-hidden rounded-btn border border-line"
    >
      {dark === null ? (
        <div className="p-4 text-sm text-slate" style={{ height }}>
          Loading editor
        </div>
      ) : (
        <Editor
          height={height}
          language={language}
          value={value}
          theme={dark ? "vs-dark" : "vs"}
          loading={<div className="p-4 text-sm text-slate">Loading editor</div>}
          options={{
            readOnly: true,
            domReadOnly: true,
            minimap: { enabled: false },
            fontSize: 12.5,
            lineHeight: 19,
            scrollBeyondLastLine: false,
            wordWrap: wrap ? "on" : "off",
            renderLineHighlight: "none",
            folding: true,
            stickyScroll: { enabled: false },
            automaticLayout: true,
          }}
          onMount={(editor, monaco) => {
            editorRef.current = editor;
            monacoRef.current = monaco;
            apply();
          }}
        />
      )}
    </div>
  );
}
