// Zips a chosen or dropped folder in the browser; the API takes a zip upload.
import { zipSync, type Zippable } from "fflate";

export async function zipFiles(entries: { path: string; file: File }[]): Promise<Blob> {
  const tree: Zippable = {};
  for (const { path, file } of entries) {
    tree[path.replace(/^\/+/, "")] = new Uint8Array(await file.arrayBuffer());
  }
  const bytes = zipSync(tree, { level: 1 });
  return new Blob([bytes as BlobPart], { type: "application/zip" });
}

type Entry = FileSystemEntry;

function readAll(dir: FileSystemDirectoryEntry): Promise<Entry[]> {
  const reader = dir.createReader();
  const out: Entry[] = [];
  return new Promise((resolve, reject) => {
    const next = () =>
      reader.readEntries((batch) => {
        if (batch.length === 0) resolve(out);
        else {
          out.push(...batch);
          next();
        }
      }, reject);
    next();
  });
}

/** Files under dropped directory entries, with paths relative to the drop. */
export async function walkEntries(entries: Entry[]): Promise<{ path: string; file: File }[]> {
  const out: { path: string; file: File }[] = [];
  const visit = async (e: Entry): Promise<void> => {
    if (e.isFile) {
      const file = await new Promise<File>((res, rej) => (e as FileSystemFileEntry).file(res, rej));
      out.push({ path: e.fullPath.replace(/^\/+/, ""), file });
    } else if (e.isDirectory) {
      for (const child of await readAll(e as FileSystemDirectoryEntry)) await visit(child);
    }
  };
  for (const e of entries) await visit(e);
  return out;
}
