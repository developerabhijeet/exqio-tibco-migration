// The line span of the XML element whose start tag ends on `sourceLine` (lxml reports the line
// where the start tag closes). Pretty-printed TIBCO sources put one tag per line start, so the
// element opens on the nearest line above that starts with `<name` and closes at the matching
// `</name>` (or at `/>` for an empty element).

export interface XmlSpan {
  start: number; // 1-based, inclusive
  end: number;
  anchor: number;
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
