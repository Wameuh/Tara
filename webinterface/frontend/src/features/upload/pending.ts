import type { InputKind } from "../../api/client";

export type PendingUploadState =
  | "queued"
  | "hashing"
  | "declaring"
  | "uploading"
  | "finalizing"
  | "done"
  | "failed";

export type PendingUpload = {
  key: string;
  file: File;
  inputKind: InputKind;
  person?: string;
  state: PendingUploadState;
  hashingLoaded: number;
  confirmedOffset: number;
  idempotencyKey: string;
  fileId?: string;
  fileRevision?: number;
  replacementFor?: string;
  error?: string;
};
const pending = new Map<string, PendingUpload[]>();
export function setPending(sessionId: string, files: PendingUpload[]): void {
  pending.set(sessionId, files);
}
export function getPending(sessionId: string): PendingUpload[] {
  return pending.get(sessionId) ?? [];
}
export function clearPending(sessionId: string): void { pending.delete(sessionId); }
