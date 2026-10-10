// Job progress over SSE (GET /jobs/{id}/events). The stream ends with a `done` or `error` event;
// a resumed stream for a finished job answers 204, which closes the EventSource, so the job row
// is read to report the outcome.
import { API_URL, api } from "./api";
import type { JobEvent, JobEventKind } from "./types";

export function subscribeJob(jobId: string, onEvent: (e: JobEvent) => void): () => void {
  const source = new EventSource(`${API_URL}/jobs/${jobId}/events`);
  let finished = false;

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
    if (finished || source.readyState !== EventSource.CLOSED) return; // the browser reconnects
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
