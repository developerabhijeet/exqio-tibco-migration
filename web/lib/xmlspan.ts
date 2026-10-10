// The line span of the XML element whose start tag ends on `sourceLine` (lxml reports the line
// where the start tag closes). Pretty-printed TIBCO sources put one tag per line start, so the
// element opens on the nearest line above that starts with `<name` and closes at the matching
// `</name>` (or at `/>` for an empty element).

export interface XmlSpan {
  start: number; // 1-based, inclusive
  end: number;
  anchor: number;
}

/** The element band plus the line to land on (see focusLine). */
export interface XmlHighlight {
  start: number;
  end: number;
  focus: number;
}

const OPEN = /^\s*<([A-Za-z_][\w:.-]*)/;

function stripQuoted(s: string): string {
  return s.replace(/"[^"]*"|'[^']*'/g, '""');
}

export function elementSpan(text: string, sourceLine: number, maxLines = 600): XmlSpan | null {
  const lines = text.split(/\r\n|\n|\r/);
  if (sourceLine < 1 || sourceLine > lines.length) return null;
  let start = sourceLine;
  while (start > 1 && !OPEN.test(lines[start - 1]) && sourceLine - start < 60) start--;
  const m = OPEN.exec(lines[start - 1]);
  if (!m) return { start: sourceLine, end: sourceLine, anchor: sourceLine };
  const name = m[1];
  const esc = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  // Find where the start tag closes and whether it is empty (`/>`).
  let i = start;
  let head = stripQuoted(lines[start - 1].slice(lines[start - 1].indexOf("<")));
  while (!head.includes(">") && i < lines.length) {
    head += " " + stripQuoted(lines[i]);
    i++;
  }
  const close = head.indexOf(">");
  if (close > 0 && head[close - 1] === "/") return { start, end: i, anchor: sourceLine };

  const openRe = new RegExp(`<${esc}(?=[\\s>/]|$)`, "g");
  const selfRe = new RegExp(`<${esc}(?=[\\s/])[^<>]*/>`, "g");
  const closeRe = new RegExp(`</${esc}\\s*>`, "g");
  let depth = 0;
  for (let n = start; n <= Math.min(lines.length, start + maxLines); n++) {
    const line = stripQuoted(lines[n - 1]);
    const opens = (line.match(openRe) ?? []).length - (line.match(selfRe) ?? []).length;
    depth += opens;
    depth -= (line.match(closeRe) ?? []).length;
    if (n >= i && depth <= 0) return { start, end: n, anchor: sourceLine };
  }
  return { start, end: sourceLine, anchor: sourceLine };
}

/** XML attribute escaping as TIBCO Studio writes it (newlines become `&#xa;`). */
function xmlEscaped(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
}

/**
 * The line inside the element where the reader should land: the line holding the step's SQL
 * (matched on its first line of text), else the first line with a select, insert, update or
 * delete keyword, else the element's first line.
 */
export function focusLine(text: string, span: XmlSpan, sql?: string | null): number {
  const lines = text.split(/\r\n|\n|\r/);
  const inSpan = (test: (l: string) => boolean): number | null => {
    for (let n = span.start; n <= span.end && n <= lines.length; n++) if (test(lines[n - 1])) return n;
    return null;
  };
  const head = (sql ?? "").split(/\r\n|\n|\r/).map((l) => l.trim()).find(Boolean);
  if (head) {
    const needles = [head, xmlEscaped(head)].map((x) => x.toLowerCase());
    const hit = inSpan((l) => needles.some((x) => l.toLowerCase().includes(x)));
    if (hit) return hit;
  }
  return inSpan((l) => /\b(select|insert|update|delete)\b/i.test(l)) ?? span.start;
}
