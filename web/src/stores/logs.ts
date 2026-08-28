/**
 * LOG-1/2/3/4/5/6/7/9 · the log editor's state.
 *
 * The two things here that are not obvious from the endpoints:
 *
 * **1 · Autosave writes a version, and that is what makes S-7 true.** The backend
 * mints a version on save; the editor's job is only to save *often enough* that a
 * lapsed edit lock never costs unsaved work. So the debounce is short (2s of quiet)
 * and `save()` is idempotent about identical content — the backend skips a version
 * when nothing changed, so a paused typist does not mint one a second.
 *
 * **2 · The edit lock is advisory and must look advisory.** It is a 5-minute TTL
 * with a heartbeat (LOG-4 / S-7). When somebody else holds it the UI shows who and
 * does not block typing, because the backend's takeover rule already guarantees
 * nothing is lost: the previous editor's work *is* version N by the time the lock
 * lapses. A modal that refused to let a second person type would be inventing a
 * stricter rule than the design's.
 */
import { defineStore } from "pinia";
import { ref } from "vue";

import { api, ProblemError } from "@/api/client";
import type { Attachment, DiffLine, EditLock, Log, LogVersion, ShareLevel } from "@/api/types";

/** Quiet time before an autosave. Short: the lock's TTL is five minutes. */
export const AUTOSAVE_DELAY_MS = 2000;
/** Well inside the 5-minute TTL, so one dropped request does not lose the lock. */
export const HEARTBEAT_MS = 60_000;

export const useLogStore = defineStore("logs", () => {
  const items = ref<Log[]>([]);
  const current = ref<Log | null>(null);
  const versions = ref<LogVersion[]>([]);
  const diff = ref<DiffLine[]>([]);
  const attachments = ref<Attachment[]>([]);
  const lock = ref<EditLock | null>(null);
  const loading = ref(false);
  const saving = ref(false);
  const savedAt = ref<Date | null>(null);
  const error = ref<string | null>(null);
  const conflict = ref<string | null>(null);

  let autosaveTimer: number | undefined;
  let heartbeatTimer: number | undefined;

  async function load(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      items.value = await api.get<Log[]>("/web/logs");
    } catch (caught) {
      error.value = message(caught);
    } finally {
      loading.value = false;
    }
  }

  async function open(id: string): Promise<void> {
    loading.value = true;
    error.value = null;
    conflict.value = null;
    try {
      current.value = await api.get<Log>(`/web/logs/${id}`);
      versions.value = await api.get<LogVersion[]>(`/web/logs/${id}/versions`);
      attachments.value = await api.get<Attachment[]>("/web/attachments", {
        owner_type: "log",
        owner_id: id,
      });
    } catch (caught) {
      error.value = message(caught);
      current.value = null;
    } finally {
      loading.value = false;
    }
  }

  async function create(payload: {
    title: string;
    body: string;
    format?: string;
  }): Promise<Log | null> {
    error.value = null;
    try {
      const created = await api.post<Log>("/web/logs", payload);
      current.value = created;
      return created;
    } catch (caught) {
      error.value = message(caught);
      return null;
    }
  }

  /**
   * Save now. Called by the debounce and by the explicit save button.
   *
   * **No `If-Match` here, and that is the backend's contract rather than an
   * omission.** Tickets carry `rev` because two people editing one field is a lost
   * update; a log is a document, and LOG-4 answers the same question differently —
   * every save mints a version, and the edit lock (5-minute TTL + heartbeat, S-7)
   * is what keeps two authors from surprising each other. So a second writer does
   * not overwrite the first: they add version N+1, and version N is still there.
   */
  async function save(changes: { title?: string; body?: string }): Promise<boolean> {
    const log = current.value;
    if (!log) return false;
    saving.value = true;
    conflict.value = null;
    try {
      current.value = await api.patch<Log>(`/web/logs/${log.id}`, changes);
      savedAt.value = new Date();
      return true;
    } catch (caught) {
      if (caught instanceof ProblemError && caught.isConflict) {
        // Reachable if the backend ever adds a concurrency check here; kept
        // because the recovery is the same and the wording is the important part:
        // the author's work is a version, not a loss.
        conflict.value =
          "这篇日志已被其他人保存过。已载入最新版本 —— 你刚才的编辑保存在版本历史里，没有丢。";
        await open(log.id);
        return false;
      }
      error.value = message(caught);
      return false;
    } finally {
      saving.value = false;
    }
  }

  /** Debounced autosave. Cancels the pending one, so typing never queues saves. */
  function scheduleSave(changes: { title?: string; body?: string }): void {
    if (autosaveTimer !== undefined) window.clearTimeout(autosaveTimer);
    autosaveTimer = window.setTimeout(() => void save(changes), AUTOSAVE_DELAY_MS);
  }

  function cancelScheduledSave(): void {
    if (autosaveTimer !== undefined) window.clearTimeout(autosaveTimer);
    autosaveTimer = undefined;
  }

  async function acquireLock(id: string): Promise<void> {
    try {
      lock.value = await api.post<EditLock>(`/web/logs/${id}/lock`);
    } catch (caught) {
      // Not fatal, and not shown as an error: the lock is advisory. Losing it
      // means somebody else is typing too, which the banner reports.
      lock.value = null;
      if (!(caught instanceof ProblemError)) throw caught;
    }
    if (heartbeatTimer !== undefined) window.clearInterval(heartbeatTimer);
    heartbeatTimer = window.setInterval(() => {
      void api
        .post<EditLock>(`/web/logs/${id}/lock/heartbeat`)
        .then((renewed) => {
          lock.value = renewed;
        })
        .catch(() => {
          // Somebody took it over. The banner will say so on the next read; the
          // editor keeps working, because the takeover rule already saved the
          // previous content as a version (S-7).
          lock.value = null;
        });
    }, HEARTBEAT_MS);
  }

  async function releaseLock(id: string): Promise<void> {
    if (heartbeatTimer !== undefined) window.clearInterval(heartbeatTimer);
    heartbeatTimer = undefined;
    lock.value = null;
    try {
      await api.delete(`/web/logs/${id}/lock`);
    } catch {
      // Leaving on a closed laptop is the common case; the TTL handles it.
    }
  }

  async function loadDiff(id: string, from: number, to: number): Promise<void> {
    try {
      diff.value = await api.get<DiffLine[]>(`/web/logs/${id}/diff`, {
        from_version: from,
        to_version: to,
      });
    } catch (caught) {
      error.value = message(caught);
    }
  }

  /**
   * Roll back to a version. **Appends** — history is never rewritten (§6.2).
   *
   * Worth saying in the UI as well as here: the button does not "undo", it writes
   * a new version whose content equals the old one, and `rolled_back_from`
   * records where it came from.
   */
  async function rollback(id: string, version: number): Promise<boolean> {
    try {
      current.value = await api.post<Log>(`/web/logs/${id}/rollback`, { version_no: version });
      versions.value = await api.get<LogVersion[]>(`/web/logs/${id}/versions`);
      return true;
    } catch (caught) {
      error.value = message(caught);
      return false;
    }
  }

  async function setShare(id: string, level: ShareLevel, spaceId?: string): Promise<boolean> {
    try {
      current.value = await api.put<Log>(`/web/logs/${id}/share`, {
        share_level: level,
        space_id: spaceId ?? null,
      });
      return true;
    } catch (caught) {
      error.value = message(caught);
      return false;
    }
  }

  /** LOG-9 · the knowledge marker. Counting is S-16's rule, server-side. */
  async function setKnowledge(id: string, marked: boolean): Promise<boolean> {
    try {
      current.value = await api.put<Log>(`/web/logs/${id}/knowledge`, { marked });
      return true;
    } catch (caught) {
      error.value = message(caught);
      return false;
    }
  }

  /**
   * Turn an uploaded Markdown or HTML file into a log.
   *
   * The server converts HTML to Markdown and marks the result as a knowledge
   * candidate, so 浏览 / 编辑 work on the same routes as a hand-written log.
   */
  async function importNote(file: File): Promise<Log> {
    const created = (await api.upload("/web/logs/import", file, {})) as Log;
    current.value = created;
    items.value = [created, ...items.value.filter((one) => one.id !== created.id)];
    return created;
  }

  async function attach(id: string, file: File): Promise<Attachment | null> {
    try {
      const added = (await api.upload("/web/attachments", file, {
        owner_type: "log",
        owner_id: id,
      })) as Attachment;
      attachments.value = [...attachments.value, added];
      return added;
    } catch (caught) {
      error.value = message(caught);
      return null;
    }
  }

  /**
   * A short-lived signed URL for an attachment (S-11).
   *
   * Fetched per use rather than cached: it is valid for five minutes by design,
   * and a cached one would start failing in a way that looks like a broken image.
   */
  async function linkFor(attachmentId: string): Promise<string | null> {
    try {
      const { url } = await api.get<{ url: string }>(`/web/attachments/${attachmentId}/link`);
      return url;
    } catch (caught) {
      error.value = message(caught);
      return null;
    }
  }

  function message(caught: unknown): string {
    if (caught instanceof ProblemError) {
      const fields = caught.errors.map((one) => `${one.field}: ${one.message}`);
      return fields.length ? `${caught.message}（${fields.join("；")}）` : caught.message;
    }
    return caught instanceof Error ? caught.message : String(caught);
  }

  return {
    items,
    current,
    versions,
    diff,
    attachments,
    lock,
    loading,
    saving,
    savedAt,
    error,
    conflict,
    load,
    open,
    create,
    save,
    scheduleSave,
    cancelScheduledSave,
    acquireLock,
    releaseLock,
    loadDiff,
    rollback,
    setShare,
    setKnowledge,
    importNote,
    attach,
    linkFor,
  };
});
