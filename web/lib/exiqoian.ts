"use client";

// ExiQoian, the project assistant: types of its API and the hooks the panel uses.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { json } from "./api";
import type { CitedText } from "./citations-model";
import { useAppState } from "./state";
import { sseFetch, type SseHandlers } from "./sseFetch";

export type PersonaKey = "sme" | "architect" | "engineer" | "qa" | "ops" | "admin" | "viewer";
export type Verdict = "answer" | "intro" | "off_topic" | "injection" | "not_covered" | "unavailable";

export interface Profile {
  personas: { key: PersonaKey; label: string }[];
  default_persona: PersonaKey;
  suggestions: Record<string, string[]>;
  provider: string;
  memory: { configured: boolean; available: boolean };
}

export interface Conversation {
  id: string;
  title: string;
  persona: PersonaKey;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  cited: CitedText | null;
  verdict: Verdict | null;
  persona: PersonaKey | null;
  used_model: boolean;
  created_at: string;
}

export interface ConversationDetail {
  id: string;
  title: string;
  persona: PersonaKey;
  messages: ChatMessage[];
}

/** The fixed intro (engine/exiqoian/replies.py INTRO_REPLY), shown on an empty conversation. */
export const INTRO =
  "I'm ExiQoian, this project's assistant. I answer from the project's memory: the TIBCO workflows, what each phase found and decided, and the modernized application, with a citation on every answer. I can't take actions or answer questions outside this project.";

const base = (projectId: string) => `/exiqoian/${projectId}`;

// Every key carries the signed-in persona: profile, conversations and personas differ per user.
// `open`: the panel is open. Closed, it asks nothing: every project screen mounts it, and an API
// call it made there would count against (or, signed out, redirect) a page that never used it.
export const useExiqoianProfile = (projectId: string | null, open = true) => {
  const { user } = useAppState();
  return useQuery({
    queryKey: ["exiqoian", projectId, user, "profile"],
    queryFn: () => json<Profile>(`${base(projectId!)}/profile`),
    enabled: !!projectId && open,
    staleTime: 60_000,
  });
};

export const useConversations = (projectId: string | null, open = true) => {
  const { user } = useAppState();
  return useQuery({
    queryKey: ["exiqoian", projectId, user, "conversations"],
    queryFn: async () => (await json<{ conversations: Conversation[] }>(`${base(projectId!)}/conversations`)).conversations,
    enabled: !!projectId && open,
  });
};

export const useConversation = (projectId: string | null, id: string | null) => {
  const { user } = useAppState();
  return useQuery({
    queryKey: ["exiqoian", projectId, user, "conversation", id],
    queryFn: () => json<ConversationDetail>(`${base(projectId!)}/conversations/${id}`),
    enabled: !!projectId && !!id,
  });
};

export function useCreateConversation(projectId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (persona: PersonaKey | null) =>
      json<Conversation>(`${base(projectId!)}/conversations`, {
        method: "POST",
        body: JSON.stringify(persona ? { persona } : {}),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["exiqoian", projectId] }),
  });
}

export function useDeleteConversation(projectId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await json<unknown>(`${base(projectId!)}/conversations/${id}`, { method: "DELETE" }).catch((e: unknown) => {
        if (!(e instanceof SyntaxError)) throw e; // 204 has no body to parse
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["exiqoian", projectId] }),
  });
}

/** Ask a question; the answer streams as meta, status, error, sources, delta and done events. */
export const askStream = (
  projectId: string,
  conversationId: string,
  text: string,
  persona: PersonaKey | null,
  handlers: SseHandlers,
  signal?: AbortSignal,
) => sseFetch(`${base(projectId)}/conversations/${conversationId}/messages`, persona ? { text, persona } : { text }, handlers, signal);
