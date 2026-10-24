// Job progress over SSE (GET /jobs/{id}/events). The stream ends with a `done` or `error` event;
// a resumed stream for a finished job answers 204, which closes the EventSource, so the job row
// is read to report the outcome. The browser reconnects on its own (resuming with Last-Event-ID);
// after `maxRetries` failed attempts in a row the stream gives up and reports `onLost`.
import { api, requireApiUrl } from "./api";
import type { JobEvent, JobEventKind } from "./types";

export interface SubscribeOptions {
  onLost?: () => void;
  maxRetries?: number;
}

export function subscribeJob(
  jobId: string,
  onEvent: (e: JobEvent) => void,
  { onLost, maxRetries = 5 }: SubscribeOptions = {},
): () => void {
  const source = new EventSource(`${requireApiUrl()}/jobs/${jobId}/events`);
  let finished = false;
  let failures = 0;
  source.addEventListener("open", () => {
    failures = 0;
  });

  const finish = () => {
    finished = true;
    source.close();
  };

  const handle = (kind: JobEventKind) => (raw: Event) => {
    const msg = raw as MessageEvent<string>;
    if (typeof msg.data !== "string") return; // a connection error, not the job's `error` event
    let body: Omit<JobEvent, "event">;
    try {
      body = JSON.parse(msg.data);
    } catch {
      return;
    }
    onEvent({ event: kind, ...body });
    if (kind === "done" || kind === "error") finish();
  };

  (["log", "stage", "done"] as const).forEach((k) => source.addEventListener(k, handle(k)));
  source.addEventListener("error", (raw) => {
    if (typeof (raw as MessageEvent).data === "string") {
      handle("error")(raw);
      return;
    }
    if (finished) return;
    if (source.readyState !== EventSource.CLOSED) {
      // The browser is reconnecting; give up after a few failures in a row.
      failures += 1;
      if (failures >= maxRetries) {
        finish();
        onLost?.();
      }
      return;
    }
    // Closed by the server (204) or refused: the job row says how it ended.
    finish();
    api
      .job(jobId)
      .then((job) => {
        const ts = job.finished_at ?? new Date().toISOString();
        if (job.status === "done") onEvent({ event: "done", seq: -1, tag: null, text: "done", data: job.result, ts });
        else if (job.status === "failed")
          onEvent({ event: "error", seq: -1, tag: null, text: job.error ?? "job failed", data: null, ts });
      })
      .catch((e: Error) =>
        onEvent({ event: "error", seq: -1, tag: null, text: e.message, data: null, ts: new Date().toISOString() }),
      );
  });

  return finish;
}
