"use client";

// Queries for the Build, Verify and Deploy tabs and the secondary screens. Every read here is a
// GET that never writes; an empty or missing cache reads as null. The one mutation is the review
// verdict, which the next build acts on.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export const useBuilds = (processId: string | null) =>
  useQuery({ queryKey: ["builds", processId], queryFn: () => api.builds(processId!), enabled: !!processId });

/** The newest build of the process, or null. */
export function useLatestBuildId(processId: string | null): { buildId: string | null; loading: boolean } {
  const q = useBuilds(processId);
  return { buildId: q.data?.[0]?.id ?? null, loading: q.isLoading };
}

export const useBuild = (buildId: string | null) =>
  useQuery({ queryKey: ["build", buildId], queryFn: () => api.build(buildId!), enabled: !!buildId });

export const useBuildFiles = (buildId: string | null) =>
  useQuery({ queryKey: ["build-files", buildId], queryFn: () => api.buildFiles(buildId!), enabled: !!buildId });

export const useBuildFile = (buildId: string | null, path: string | null) =>
  useQuery({
    queryKey: ["build-file", buildId, path],
    queryFn: () => api.buildFile(buildId!, path!),
    enabled: !!buildId && !!path,
    staleTime: Infinity,
  });

export const useBuildTests = (buildId: string | null) =>
  useQuery({ queryKey: ["build-tests", buildId], queryFn: () => api.buildTests(buildId!), enabled: !!buildId });

export const useBuildReviews = (buildId: string | null) =>
  useQuery({ queryKey: ["build-reviews", buildId], queryFn: () => api.buildReviews(buildId!), enabled: !!buildId });

/** Keep or reject one review finding; the next build applies what was kept. */
export const useDecideReview = (buildId: string | null) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ index, decision, note }: { index: number; decision: "keep" | "reject" | null; note?: string }) =>
      api.decideReview(buildId!, index, { decision, note }),
    onSuccess: (rows) => qc.setQueryData(["build-reviews", buildId], rows),
  });
};

/** One verdict across the whole review panel; undecided ones only, unless told otherwise. */
export const useDecideAllReviews = (buildId: string | null) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { decision: "keep" | "reject" | null; only_undecided?: boolean }) =>
      api.decideAllReviews(buildId!, body),
    onSuccess: (rows) => qc.setQueryData(["build-reviews", buildId], rows),
  });
};

export const useInfra = (buildId: string | null) =>
  useQuery({ queryKey: ["infra", buildId], queryFn: () => api.infra(buildId!), enabled: !!buildId });

export const useRunState = (buildId: string | null) =>
  useQuery({
    queryKey: ["run", buildId],
    queryFn: () => api.runState(buildId!),
    enabled: !!buildId,
    refetchInterval: (q) => (q.state.data?.running ? 10_000 : false),
  });

export const useScenarioList = (processId: string | null) =>
  useQuery({
    queryKey: ["scenario-list", processId],
    queryFn: () => api.scenarioList(processId!),
    enabled: !!processId,
    retry: false,
  });

export const useParity = (buildId: string | null) =>
  useQuery({ queryKey: ["parity", buildId], queryFn: () => api.parity(buildId!), enabled: !!buildId, retry: false });

export const useTestData = (processId: string | null) =>
  useQuery({
    queryKey: ["test-data", processId],
    queryFn: () => api.testData(processId!),
    enabled: !!processId,
    retry: false,
  });

export const useAzure = (projectId: string | null) =>
  useQuery({ queryKey: ["azure", projectId], queryFn: () => api.azure(projectId!), enabled: !!projectId });

export const usePlan = (buildId: string | null) =>
  useQuery({ queryKey: ["plan", buildId], queryFn: () => api.plan(buildId!), enabled: !!buildId, retry: false });

export const useSettingsView = () => useQuery({ queryKey: ["settings-view"], queryFn: api.settingsView });

export const useHistory = (projectId: string | null) =>
  useQuery({ queryKey: ["history", projectId], queryFn: () => api.history(projectId!), enabled: !!projectId });

export const useResetTokenomics = (projectId: string | null) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.resetTokenomics(projectId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["history", projectId] }),
  });
};

export const useReadiness = (projectId: string | null) =>
  useQuery({ queryKey: ["readiness", projectId], queryFn: () => api.readiness(projectId!), enabled: !!projectId });
