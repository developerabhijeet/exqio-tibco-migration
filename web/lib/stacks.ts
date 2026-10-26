"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { useProjects } from "./queries";
import { useAppState } from "./state";
import { useActingPersona } from "./personas";

/** The stacks the API reports (GET /stacks): which are implemented and enabled is the server's call. */
export const useStacks = () => useQuery({ queryKey: ["stacks"], queryFn: api.stacks });

/** Roles that may change a project's target stack (the API's PATCH /projects/{id} rule). */
const STACK_ROLES = ["architect", "admin"];

/** Whether the acting user may change the target stack, and the tooltip when they may not. */
export function useStackPermission(): { canChange: boolean; reason: string | undefined } {
  const { auth, user } = useAppState();
  const persona = useActingPersona();
  const roles = auth.mode === "oidc" ? (auth.me?.roles ?? []) : [persona.roleKey, ...(user === "ops.sam" ? ["admin"] : [])];
  const canChange = roles.some((r) => STACK_ROLES.includes(r));
  return { canChange, reason: canChange ? undefined : "changing the target stack needs the architect or admin role" };
}

/** The open project's target stack key ("dotnet" until the project list arrives). */
export function useProjectStack(): string {
  const { projectId } = useAppState();
  const projects = useProjects();
  return projects.data?.find((p) => p.id === projectId)?.target_stack ?? "dotnet";
}

/** The short name of a stack label: its first segment ("Java 21 · Spring Boot 3.5 · Maven" is "Java 21"). */
export const shortLabel = (label: string) => label.split(" · ")[0];
