"use client";

// Read-only Monaco, loaded from this app's public/monaco (copied at install), never a CDN.
import { useEffect, useRef } from "react";
import Editor, { loader, type OnMount } from "@monaco-editor/react";
import type { XmlSpan } from "@/lib/xmlspan";

loader.config({ paths: { vs: "/monaco/vs" } });

type MonacoEditor = Parameters<OnMount>[0];
type Monaco = Parameters<OnMount>[1];

export default function CodeView({
  value,
  language,
  height,
  highlight,
  testId,
}: {
  value: string;
  language: string;
  height: number | string;
  highlight?: XmlSpan | null;
  testId?: string;
}) {
  const editorRef = useRef<MonacoEditor | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  const decorations = useRef<string[]>([]);

  const apply = () => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    if (!editor || !monaco) return;
    if (!highlight) {
      decorations.current = editor.deltaDecorations(decorations.current, []);
      return;
    }
    decorations.current = editor.deltaDecorations(decorations.current, [
      {
        range: new monaco.Range(highlight.start, 1, highlight.end, 1),
        options: { isWholeLine: true, className: "xml-hl-line", overviewRuler: { color: "#FFE27A", position: 4 } },
      },
      {
        range: new monaco.Range(highlight.anchor, 1, highlight.anchor, 1),
        options: { isWholeLine: true, className: "xml-hl-anchor" },
      },
    ]);
    editor.revealLineNearTop(highlight.start);
  };

  useEffect(apply, [highlight, value]);

  return (
    <div data-testid={testId} data-hl-start={highlight?.start} data-hl-end={highlight?.end} className="overflow-hidden rounded-btn border border-line">
      <Editor
        height={height}
        language={language}
        value={value}
        theme="vs"
        loading={<div className="p-4 text-[13px] text-slate">Loading editor</div>}
        options={{
          readOnly: true,
          domReadOnly: true,
          minimap: { enabled: false },
          fontSize: 12.5,
          lineHeight: 19,
          scrollBeyondLastLine: false,
          wordWrap: "off",
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
    </div>
  );
}
