"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, MessageSquare, Send, X } from "lucide-react";
import { api } from "@/lib/api";
import { useProcesses } from "@/lib/queries";
import { useAppState } from "@/lib/state";
import { Pill } from "@/components/ui/Pill";
import type { AnalysisView, AskAnswer, AskCitation } from "@/lib/types";

export const SUGGESTED = [
  "Which workflows read the funding table?",
  "Where is the late fee applied?",
  "Which workflows handle SSN?",
];

interface Turn {
  question: string;
  answer: AskAnswer | null;
  error: string | null;
}

/**
 * The step a knowledge item names: "Steps: LookupDealer." on rule chunks, else the first citation
 * of the rule or finding it names, when that process's analysis is loaded.
 */
function stepOf(c: AskCitation, analysis?: AnalysisView | null): string | null {
  const m = /Steps?:\s*([A-Za-z_][\w.-]*)/.exec(c.text);
  if (m) return m[1].replace(/\.+$/, "");
  const named = /^(Rule|Finding) (\S+?)(?: of| \(|\s)/.exec(c.text);
  if (!named || !analysis) return null;
  const items = named[1] === "Rule" ? analysis.rules : analysis.findings;
  const hit = items.find((x) => ("rule_key" in x ? x.rule_key : (x as { finding_key?: string }).finding_key) === named[2]);
  return hit?.citations?.[0]?.activity_id ?? null;
}

/** Side panel answering questions from the project's knowledge base, each answer with numbered citations. */
export function AskPanel() {
  const { projectId, jumpToStep, openProcess, setStep, askOpen: open, setAskOpen } = useAppState();
  const onClose = () => setAskOpen(false);
  const processes = useProcesses(projectId);
  const qc = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, staleTime: 60_000 });
  const provider = health.data?.provider;
  const analysisOf = (pid: string | null) => (pid ? qc.getQueryData<AnalysisView | null>(["analysis", pid]) : null);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);

  const ask = async (q: string) => {
    const text = q.trim();
    if (!text || !projectId || busy) return;
    setBusy(true);
    setQuestion("");
    setTurns((t) => [...t, { question: text, answer: null, error: null }]);
    let turn: Turn;
    try {
      turn = { question: text, answer: await api.ask(projectId, text), error: null };
    } catch (e) {
      turn = { question: text, answer: null, error: e instanceof Error ? e.message : String(e) };
    }
    setTurns((t) => [...t.slice(0, -1), turn]);
    setBusy(false);
  };

  const follow = (c: AskCitation) => {
    if (!c.process_id) return;
    const step = stepOf(c, analysisOf(c.process_id));
    if (step) jumpToStep(step, c.process_id);
    else {
      openProcess(c.process_id);
      setStep("discover");
    }
  };

  if (!open || !projectId) return null;
  const names = new Map((processes.data ?? []).map((p) => [p.id, p.name]));
  return (
    <aside
      aria-label="Ask AI"
      data-testid="ask-panel"
      className="fixed bottom-0 right-0 top-0 z-40 flex w-[440px] max-w-full flex-col border-l border-line bg-white shadow-2xl print:hidden"
    >
      <div className="flex items-start justify-between border-b border-line px-5 py-4">
        <div>
          <p className="eyebrow">Ask AI</p>
          <h2 className="mt-0.5 text-[18px] font-semibold text-plum">Questions about this estate</h2>
          <p className="mt-0.5 text-[12.5px] text-slate">Answered from the analyzed rules, findings and decisions, with citations.</p>
        </div>
        <button type="button" className="btn-ghost" onClick={onClose} aria-label="Close Ask AI">
          <X size={18} />
        </button>
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
        {turns.length === 0 ? (
          <div>
            <p className="label">Try</p>
            <ul className="mt-2 space-y-1.5">
              {SUGGESTED.map((s) => (
                <li key={s}>
                  <button
                    type="button"
                    onClick={() => ask(s)}
                    className="w-full rounded-btn border border-line bg-page px-3 py-2 text-left text-[14px] text-plum hover:border-purple hover:bg-purple-lt"
                  >
                    {s}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {turns.map((t, i) => (
          <div key={i} className="space-y-2">
            <p className="ml-8 rounded-card bg-purple-lt px-3 py-2 text-[14px] text-plum">{t.question}</p>
            {t.answer ? (
              <div data-testid="ask-answer" className="rounded-card border border-line px-3 py-2">
                <div className="mb-1 flex items-center gap-2">
                  <MessageSquare size={14} className="text-ai" aria-hidden />
                  <Pill
                    tone={t.answer.used_model ? "ai" : "det"}
                    testId="ask-answer-source"
                    title={
                      t.answer.used_model
                        ? "A model wrote this answer from the cited knowledge items"
                        : "No model answered: these are the closest items from the knowledge base"
                    }
                  >
                    {t.answer.used_model ? `AI answer${provider ? ` (${provider} provider)` : ""}` : "knowledge base search, no model"}
                  </Pill>
                </div>
                <p className="whitespace-pre-wrap text-[14px] leading-relaxed text-ink">{t.answer.text}</p>
                {t.answer.citations.length > 0 ? (
                  <ol className="mt-2 space-y-1.5 border-t border-line pt-2">
                    {t.answer.citations.map((c, n) => (
                      <li key={c.id}>
                        <button
                          type="button"
                          data-testid="ask-citation"
                          data-step={stepOf(c, analysisOf(c.process_id)) ?? ""}
                          disabled={!c.process_id}
                          onClick={() => follow(c)}
                          title={c.process_id ? "Open on the canvas" : "Not tied to a process"}
                          className="w-full rounded-btn px-2 py-1 text-left text-[13px] hover:bg-ai-bg disabled:hover:bg-transparent"
                        >
                          <span className="mr-1.5 font-mono font-semibold text-ai">[{n + 1}]</span>
                          <span className="font-medium text-plum">
                            {c.kind}
                            {c.process_id ? ` · ${names.get(c.process_id) ?? "process"}` : ""}
                            {stepOf(c, analysisOf(c.process_id)) ? ` · ${stepOf(c, analysisOf(c.process_id))}` : ""}
                          </span>
                          <span className="mt-0.5 line-clamp-2 text-slate">{c.text}</span>
                        </button>
                      </li>
                    ))}
                  </ol>
                ) : null}
              </div>
            ) : t.error ? (
              <p role="alert" className="rounded-card bg-error-bg px-3 py-2 text-[14px] text-error">
                {t.error}
              </p>
            ) : (
              <p className="flex items-center gap-2 text-[14px] text-slate">
                <Loader2 size={14} className="animate-spin" aria-hidden /> Searching the knowledge base
              </p>
            )}
          </div>
        ))}
      </div>
      <form
        className="flex gap-2 border-t border-line px-5 py-3"
        onSubmit={(e) => {
          e.preventDefault();
          ask(question);
        }}
      >
        <input
          className="input"
          aria-label="Question"
          placeholder="Ask about rules, tables, fields"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button type="submit" className="btn-primary px-3" disabled={busy || !question.trim()} aria-label="Ask">
          <Send size={16} aria-hidden />
        </button>
      </form>
    </aside>
  );
}
