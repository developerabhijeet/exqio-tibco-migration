"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { useProjects } from "./queries";
import { useAppState } from "./state";

const NEEDS_ROLE = "changing the target stack needs the SME, architect or admin role";
const NEEDS_ADMIN = "the default stack is set by an admin";

/** The stacks the API reports (GET /stacks) and what the acting user may do with them (the server's call). */
export function useStacks() {
  const { user } = useAppState();
  return useQuery({ queryKey: ["stacks", user], queryFn: api.stacks });
}

/** Whether the acting user may change a project's target stack, and the tooltip when they may not. */
export function useStackPermission(): { canChange: boolean; reason: string | undefined } {
  const stacks = useStacks();
  const canChange = stacks.data?.can_change_stack ?? false;
  return { canChange, reason: canChange ? undefined : NEEDS_ROLE };
}

/** Whether the acting user may set the install's default stack, and the tooltip when they may not. */
export function useDefaultStackPermission(): { canSet: boolean; reason: string | undefined } {
  const stacks = useStacks();
  const canSet = stacks.data?.can_set_default ?? false;
  return { canSet, reason: canSet ? undefined : NEEDS_ADMIN };
}

/** The open project's target stack key; undefined until the project list has arrived. */
export function useProjectStack(): string | undefined {
  const { projectId } = useAppState();
  const projects = useProjects();
  return projects.data?.find((p) => p.id === projectId)?.target_stack;
}

/** The short name of a stack label: its first segment ("Java 21 · Spring Boot 3.5 · Maven" is "Java 21"). */
export const shortLabel = (label: string) => label.split(" · ")[0];

/**
 * The brand subtitle: "TIBCO BusinessWorks to <stack>", naming the open project's target stack,
 * else the install's default; before the stacks are known (or signed out), no stack is named.
 */
export function useStackSubtitle(): string {
  const stacks = useStacks();
  const project = useProjectStack();
  const key = project ?? stacks.data?.default;
  const label = stacks.data?.stacks.find((s) => s.key === key)?.label;
  // Language and framework ("C# · .NET 10", "Java 21 · Spring Boot 3.5"), without the build tool.
  return label ? `TIBCO BusinessWorks to ${label.split(" · ").slice(0, 2).join(" · ")}` : "TIBCO BusinessWorks modernization";
}
