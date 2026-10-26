// What each memory node label and relationship type looks like on the canvas: an icon, a colour
// token (lib/theme.css) and a short label. Colours are CSS colours over the tokens, so they follow
// the theme without a re-render; no spaces, since React Flow builds marker ids from them.
import {
  AppWindow,
  Box,
  Brain,
  CircleDot,
  Database,
  FileCheck,
  FileCode2,
  FlaskConical,
  FolderGit2,
  KeyRound,
  Layers,
  ListOrdered,
  MessagesSquare,
  Package,
  PenLine,
  Plug,
  Radio,
  Share2,
  ShieldCheck,
  SquareFunction,
  TextQuote,
  TriangleAlert,
  Variable,
  Workflow,
  Activity,
  type LucideIcon,
} from "lucide-react";

export interface Kind {
  icon: LucideIcon;
  /** A theme colour token name (without the `--c-` prefix). */
  token: string;
  label: string;
}

const K = (icon: LucideIcon, token: string, label: string): Kind => ({ icon, token, label });

export const KINDS: Record<string, Kind> = {
  Domain: K(Layers, "heading", "Domain"),
  Application: K(AppWindow, "accent-ink", "Application"),
  Solution: K(Package, "accent-ink", "Solution"),
  CodeProject: K(FolderGit2, "accent-ink", "Project"),
  Process: K(Workflow, "accent", "Process"),
  Activity: K(Activity, "accent", "Activity"),
  Contract: K(FileCheck, "slate", "Contract"),
  SharedResource: K(Share2, "slate", "Shared resource"),
  SubstVar: K(Variable, "slate", "Variable"),
  Table: K(Database, "gate", "Table"),
  Queue: K(ListOrdered, "coral-dk", "Queue"),
  Topic: K(Radio, "coral-dk", "Topic"),
  MessagingEntity: K(MessagesSquare, "coral-dk", "Messaging"),
  Endpoint: K(Plug, "ai", "Endpoint"),
  ConfigKey: K(KeyRound, "slate", "Config key"),
  CodeFile: K(FileCode2, "ai", "Code file"),
  Class: K(Box, "ai", "Class"),
  Method: K(SquareFunction, "ai", "Method"),
  Test: K(FlaskConical, "gate", "Test"),
  Chunk: K(TextQuote, "decision", "Chunk"),
  Memory: K(Brain, "decision", "Memory"),
  Curation: K(PenLine, "decision", "Curation"),
  GateDecision: K(ShieldCheck, "coral-dk", "Gate decision"),
  Error: K(TriangleAlert, "error", "Error"),
};

const FALLBACK: Kind = K(CircleDot, "slate", "Node");

export function kindOf(label: string): Kind {
  return KINDS[label] ?? { ...FALLBACK, label };
}

/** A token as a CSS colour, with an optional alpha. */
export function tok(token: string, alpha?: number): string {
  return alpha === undefined ? `rgb(var(--c-${token}))` : `rgb(var(--c-${token})/${alpha})`;
}

/** Order of labels in the legend and within a layer (containers first, knowledge last). */
export const LABEL_ORDER = Object.keys(KINDS);

/** Relationship colours: structure quiet, data flow green and coral, calls the accent. */
const REL_TOKEN: Record<string, [string, number?]> = {
  CONTAINS: ["line-strong"],
  ABOUT: ["decision", 0.55],
  CALLS: ["accent"],
  READS: ["gate"],
  WRITES: ["coral-dk"],
  USES: ["slate", 0.8],
  EXPOSES: ["ai"],
  MIGRATED_TO: ["accent"],
  TESTS: ["gate"],
  FAILED_AT: ["error"],
};

export function relColor(type: string, status?: string | null): string {
  if (status === "rejected") return tok("error");
  if (status === "corrected") return tok("gate");
  const [t, a] = REL_TOKEN[type] ?? ["line-strong"];
  return tok(t, a);
}

/** Relationship types structural enough to go unlabelled until a node is picked or hovered. */
export const QUIET_RELS = new Set(["CONTAINS", "ABOUT", "filtered path"]);

/** Lineage relationship types (the selected node's chain). */
export const LINEAGE_TYPES = new Set(["MIGRATED_TO", "TESTS", "FAILED_AT"]);

export const relLabel = (type: string) => type.toLowerCase().replace(/_/g, " ");
