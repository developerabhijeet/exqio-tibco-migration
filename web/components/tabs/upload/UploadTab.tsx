"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Play } from "lucide-react";
import { api } from "@/lib/api";
import { useFiles, useProcesses, useReport } from "@/lib/queries";
import { useAppState } from "@/lib/state";
import type { ProcessSummary } from "@/lib/types";
import { ErrorNote } from "@/components/ui/Empty";
import { DropZone, sourceToUpload, type UploadSource } from "./DropZone";
import { FileTree } from "./FileTree";
import { ValidationPanel } from "./ValidationPanel";
import { ScanProgress } from "./ScanProgress";
import { ParseReportTable, ReportCounters, UnknownsPanel } from "./ParseReport";

export function UploadTab() {
  const { projectId, scan, watchScan, openProcess, setStep } = useAppState();
  const qc = useQueryClient();
  const files = useFiles(projectId);
  const report = useReport(projectId);
  const processes = useProcesses(projectId);
  const [lastUpload, setLastUpload] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: (s: UploadSource) => sourceToUpload(projectId!, s),
    onSuccess: async (res) => {
      // A files request started before the upload must not overwrite the new inventory.
      await qc.cancelQueries({ queryKey: ["files", projectId] });
      setLastUpload(res.upload_id);
      qc.setQueryData(["files", projectId], res.files);
    },
  });

  const startScan = useMutation({
    mutationFn: () => api.scan(projectId!, lastUpload ?? undefined),
    onSuccess: ({ job_id }) => watchScan(projectId!, job_id),
  });

  const myScan = scan && scan.projectId === projectId ? scan : null;
  const scanning = myScan?.status === "running";
  const fileList = files.data ?? [];

  const open = (p: ProcessSummary) => {
    openProcess(p.id, p.domain_id);
    setStep("discover");
  };

  return (
    <div className="space-y-5">
      <div>
        <p className="eyebrow">Step 1</p>
        <h2 className="panel-title">Upload the estate</h2>
      </div>
      <DropZone busy={upload.isPending || scanning} onSource={(s) => upload.mutate(s)} />
      {upload.isPending ? (
        <p className="flex items-center gap-2 text-[14px] text-slate">
          <Loader2 size={16} className="animate-spin" aria-hidden /> Uploading
        </p>
      ) : null}
      {upload.error ? <ErrorNote error={upload.error} /> : null}

      {fileList.length > 0 ? (
        <>
          <div className="grid gap-4 lg:grid-cols-[1fr_420px]">
            <FileTree files={fileList} />
            <div className="space-y-4">
              <ValidationPanel files={fileList} report={lastUpload && report.data?.upload_id !== lastUpload ? null : (report.data ?? null)} />
              <div className="card p-5">
                <p className="text-[14px] text-slate">
                  The scan parses every process, resource, schema and SQL file, builds the neutral model, groups
                  processes into domains and writes the parse report.
                </p>
                <button
                  type="button"
                  className="btn-primary mt-3 w-full"
                  disabled={scanning || startScan.isPending}
                  onClick={() => startScan.mutate()}
                >
                  {scanning ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Play size={16} aria-hidden />}
                  Scan
                </button>
                {startScan.error ? (
                  <div className="mt-2">
                    <ErrorNote error={startScan.error} />
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </>
      ) : null}

      {myScan ? <ScanProgress scan={myScan} /> : null}

      {report.data && processes.data && !scanning ? (
        <div className="space-y-4">
          <ReportCounters report={report.data} processes={processes.data} />
          <UnknownsPanel report={report.data} />
          <ParseReportTable report={report.data} processes={processes.data} onOpen={open} />
        </div>
      ) : null}
    </div>
  );
}
