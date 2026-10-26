"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
import { codePointLength, type CiteRef, type CiteSpan } from "@/lib/citations";
import {
  askStream,
  INTRO,
  useConversation,
  useConversations,
  useCreateConversation,
  useDeleteConversation,
  useExiqoianProfile,
  type PersonaKey,
} from "@/lib/exiqoian";
import { useProjects } from "@/lib/queries";
import { useAppState } from "@/lib/state";
import { Composer } from "./Composer";
import { ConversationMenu } from "./ConversationMenu";
import { MessageView } from "./MessageView";

const STAGE_TEXT: Record<string, string> = {
  checking: "Checking your question…",
  searching: "Searching the project's memory…",
  writing: "Writing the answer…",
};

/** The question being answered: the sentences received so far, with their spans and refs. */
interface Pending {
  question: string;
  stage: string;
  text: string;
  spans: CiteSpan[];
  refs: CiteRef[];
  error: string | null;
}

/**
 * The ExiQoian slide-over: non-modal, mounted in the chrome so it stays open while the user moves
 * between steps and screens. Closed, it is inert (hidden, not focusable) but keeps its state.
 */
export function ExiQoianPanel() {
  const { projectId, chatOpen, setChatOpen } = useAppState();
  const qc = useQueryClient();
  const projects = useProjects();
  const profile = useExiqoianProfile(projectId);
  const conversations = useConversations(projectId);
  const create = useCreateConversation(projectId);
  const remove = useDeleteConversation(projectId);
  const [activeId, setActiveId] = useState<string | null>(null);
  const conversation = useConversation(projectId, activeId);
  const [persona, setPersona] = useState<PersonaKey | null>(null);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const abort = useRef<AbortController | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const log = useRef<HTMLDivElement>(null);

  const personas = profile.data?.personas ?? [];
  const current = persona ?? profile.data?.default_persona ?? null;
  const projectName = projects.data?.find((p) => p.id === projectId)?.name ?? "this project";

  useEffect(() => {
    if (!chatOpen) return;
    void profile.refetch(); // the starter questions follow the project (a scan or an analysis since last time)
    // The panel turns visible as it slides in; focus lands as soon as the browser allows it.
    let tries = 0;
    const t = window.setInterval(() => {
      input.current?.focus();
      if (document.activeElement === input.current || ++tries > 15) window.clearInterval(t);
    }, 20);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatOpen]);
  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight });
  }, [conversation.data, pending]);
  useEffect(() => () => abort.current?.abort(), []);

  const close = useCallback(() => {
    setChatOpen(false);
    document.querySelector<HTMLElement>('[data-testid="exiqoian-launcher"]')?.focus();
  }, [setChatOpen]);

  // Escape closes the panel from anywhere on the page while it is open, unless something on top of
  // it (a citation preview, a menu) took the key.
  useEffect(() => {
    if (!chatOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (document.querySelector('[data-testid="cite-preview"]')) return;
      e.preventDefault();
      close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [chatOpen, close]);

  const send = async (raw: string) => {
    const text = raw.trim();
    if (!text || !projectId || (pending && !pending.error)) return;
    setDraft("");
    setPending(null);
    const controller = new AbortController();
    abort.current = controller;
    setPending({ question: text, stage: "checking", text: "", spans: [], refs: [], error: null });
    const patch = (f: (p: Pending) => Pending) => setPending((p) => (p ? f(p) : p));
    let finished = false;
    let streamError: string | null = null;
    try {
      let id = activeId;
      if (!id) {
        id = (await create.mutateAsync(current)).id;
        setActiveId(id);
      }
      await askStream(
        projectId,
        id,
        text,
        current,
        {
          error: (d: { detail: string }) => {
            streamError = d.detail;
          },
          done: () => {
            finished = true;
          },
          status: (d: { stage: string }) => patch((p) => ({ ...p, stage: d.stage })),
          sources: (d: { refs: CiteRef[] }) => patch((p) => ({ ...p, refs: d.refs })),
          delta: (d: { text: string; spans: CiteSpan[] }) =>
            patch((p) => {
              const offset = codePointLength(p.text);
              const spans = d.spans.map((s) => ({ ...s, start: s.start + offset, end: s.end + offset }));
              return { ...p, text: p.text + d.text, spans: [...p.spans, ...spans] };
            }),
        },
        controller.signal,
      );
      // An error event with no done after it ends the stream: nothing was stored, so show it.
      if (!finished && streamError) patch((p) => ({ ...p, error: streamError }));
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        patch((p) => ({ ...p, error: e instanceof Error ? e.message : String(e) }));
      }
    }
    // The stored messages replace the draft (a stopped stream may have stored its reply too).
    await qc.invalidateQueries({ queryKey: ["exiqoian", projectId] });
    setPending((p) => (p?.error ? p : null));
    abort.current = null;
  };

  const stop = () => abort.current?.abort();
  const newChat = () => {
    abort.current?.abort();
    setActiveId(null);
    setPending(null);
    setDraft("");
    input.current?.focus();
  };
  const messages = conversation.data?.messages ?? [];
  const empty = messages.length === 0 && !pending;
  const suggestions = (current && profile.data?.suggestions[current]) || [];

  return (
    <aside
      role="complementary"
      aria-label="ExiQoian"
      data-testid="exiqoian-panel"
      data-open={chatOpen}
      inert={!chatOpen}
      className={`fixed inset-y-0 right-0 z-40 flex w-full flex-col md:top-16 border-l border-line bg-surface shadow-e3 transition-[transform,visibility] duration-200 motion-reduce:transition-none md:w-[480px] print:hidden ${
        chatOpen ? "visible translate-x-0" : "invisible translate-x-full"
      }`}
    >
      <div className="flex items-start justify-between gap-2 border-b border-line px-5 py-4">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold text-heading">ExiQoian</h2>
          <p className="mt-0.5 truncate text-sm text-slate">Answers from {projectName}&apos;s memory</p>
        </div>
        <button type="button" className="btn-ghost" onClick={close} aria-label="Close ExiQoian">
          <X size={18} aria-hidden />
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-5 py-2">
        {personas.length > 1 ? (
          <select
            aria-label="Answer as"
            className="input w-auto py-1"
            value={current ?? ""}
            onChange={(e) => setPersona(e.target.value as PersonaKey)}
          >
            {personas.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </select>
        ) : null}
        <button type="button" className="btn-ghost" onClick={newChat}>
          <Plus size={14} aria-hidden /> New chat
        </button>
        <div className="ml-auto">
          <ConversationMenu
            conversations={conversations.data ?? []}
            activeId={activeId}
            onSelect={(id) => {
              abort.current?.abort();
              setPending(null);
              setActiveId(id);
            }}
            onDelete={async (id) => {
              await remove.mutateAsync(id);
              if (id === activeId) setActiveId(null);
            }}
          />
        </div>
      </div>
      <div ref={log} role="log" aria-live="polite" aria-relevant="additions text" aria-label="Conversation" className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
        {empty ? (
          <div>
            <p className="text-md text-slate">{INTRO}</p>
            {suggestions.length > 0 ? (
              <ul className="mt-3 space-y-1.5">
                {suggestions.map((s) => (
                  <li key={s}>
                    <button
                      type="button"
                      onClick={() => send(s)}
                      className="w-full rounded-btn border border-line bg-page px-3 py-2 text-left text-md text-heading hover:border-accent hover:bg-accent-soft"
                    >
                      {s}
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
        {messages.map((m) => (
          <MessageView key={m.id} message={m} />
        ))}
        {pending ? (
          <>
            <div data-testid="exiqoian-message" data-role="user" data-verdict="" className="flex justify-end">
              <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-card bg-accent-soft px-3 py-2 text-md text-heading">
                {pending.question}
              </p>
            </div>
            {pending.text ? <MessageView draft={{ cited: { text: pending.text, spans: pending.spans, refs: pending.refs } }} /> : null}
            {pending.error ? (
              <p role="alert" className="rounded-card bg-error-bg px-3 py-2 text-md text-error">
                {pending.error}
              </p>
            ) : null}
          </>
        ) : null}
      </div>
      <p data-testid="exiqoian-status" role="status" aria-live="polite" className="min-h-5 px-5 pb-1 text-sm text-slate">
        {pending && !pending.error ? (STAGE_TEXT[pending.stage] ?? "") : ""}
      </p>
      <Composer ref={input} value={draft} onChange={setDraft} onSend={() => send(draft)} onStop={stop} busy={!!pending && !pending.error} />
    </aside>
  );
}
