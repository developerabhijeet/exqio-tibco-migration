"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export const useProjects = () => useQuery({ queryKey: ["projects"], queryFn: api.listProjects });

export const useReport = (projectId: string | null) =>
  useQuery({ queryKey: ["report", projectId], queryFn: () => api.report(projectId!), enabled: !!projectId });

export const useProcesses = (projectId: string | null) =>
  useQuery({ queryKey: ["processes", projectId], queryFn: () => api.processes(projectId!), enabled: !!projectId });

export const useFiles = (projectId: string | null) =>
  useQuery({ queryKey: ["files", projectId], queryFn: () => api.files(projectId!), enabled: !!projectId });

export const useDomains = (projectId: string | null) =>
  useQuery({ queryKey: ["domains", projectId], queryFn: () => api.domains(projectId!), enabled: !!projectId });

export const useGraph = (projectId: string | null) =>
  useQuery({ queryKey: ["graph", projectId], queryFn: () => api.graph(projectId!), enabled: !!projectId });

export const useGates = (processId: string | null) =>
  useQuery({ queryKey: ["gates", processId], queryFn: () => api.gates(processId!), enabled: !!processId });

export const useAnalysis = (processId: string | null) =>
  useQuery({
    queryKey: ["analysis", processId],
    queryFn: () => api.analysis(processId!),
    enabled: !!processId,
    retry: false,
  });

export const useCanvas = (processId: string | null) =>
  useQuery({ queryKey: ["canvas", processId], queryFn: () => api.canvas(processId!), enabled: !!processId });

export const useIOCard = (processId: string | null) =>
  useQuery({ queryKey: ["io-card", processId], queryFn: () => api.ioCard(processId!), enabled: !!processId });

export const useXml = (processId: string | null) =>
  useQuery({
    queryKey: ["xml", processId],
    queryFn: () => api.xml(processId!),
    enabled: !!processId,
    staleTime: Infinity,
  });

export const useScenarios = (processId: string | null) =>
  useQuery({
    queryKey: ["scenarios", processId],
    queryFn: () => api.scenarios(processId!),
    enabled: !!processId,
    retry: false,
  });

export const useAudit = (processId: string | null) =>
  useQuery({ queryKey: ["audit", processId], queryFn: () => api.audit(processId!), enabled: !!processId });

/** Answer or reject one open question on the design; the design hash does not move, so G3 holds. */
export const useAnswerDesignQuestion = (processId: string | null) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ index, decision, answer }: {
      index: number;
      decision: "answered" | "rejected" | null;
      answer?: string;
    }) => api.answerDesignQuestion(processId!, index, { decision, answer }),
    onSuccess: (view) => qc.setQueryData(["design", processId], view),
  });
};

export const useDesign = (processId: string | null) =>
  useQuery({
    queryKey: ["design", processId],
    queryFn: () => api.design(processId!),
    enabled: !!processId,
    retry: false,
  });
