import { useEffect, useRef, useState } from "react";
import * as api from "../../api";

export const ARCHIVE_STATUS_SYNC_INTERVAL_MS = 15_000;

export default function useArchiveSettings(visible, editable) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const currentStatus = useRef(null);
  const active = useRef(false);
  const canEdit = useRef(false);
  const mounted = useRef(false);
  const revision = useRef(0);
  const operation = useRef(null);
  const pending = useRef(false);

  function publish(next) {
    currentStatus.current = next;
    setStatus(next);
  }

  async function load(enabled) {
    if (!active.current) return;
    if (operation.current) {
      if (enabled === undefined) pending.current = true;
      return;
    }
    const savingNow = enabled !== undefined;
    const job = {
      revision: revision.current,
      controller: new AbortController(),
      writing: savingNow,
    };
    operation.current = job;
    pending.current = false;
    setSaving(savingNow);
    setLoading(!currentStatus.current);
    setRefreshing(!savingNow && Boolean(currentStatus.current));
    setError("");
    if (savingNow) setNotice("");
    const current = () =>
      mounted.current && active.current && revision.current === job.revision;
    try {
      if (!savingNow) {
        const fresh = await api.loadContextArchive(job.controller.signal);
        if (!current()) return;
        publish(fresh);
      } else {
        // The server merges this single change with its current permissions.
        const saved = await api.saveContextArchive(enabled);
        if (!current()) return;
        publish(saved);
        if (saved.config && !saved.config_error)
          setNotice(
            saved.config.enabled
              ? "Source saving enabled."
              : "Source saving disabled.",
          );
      }
    } catch (reason) {
      if (!current()) return;
      publish(null);
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load the source archive.",
      );
    } finally {
      if (operation.current === job) operation.current = null;
      if (mounted.current) {
        setLoading(false);
        setRefreshing(false);
        setSaving(false);
        if (active.current && pending.current) void load();
      }
    }
  }

  useEffect(() => {
    mounted.current = true;
    canEdit.current = editable;
    const sync = () => {
      revision.current += 1;
      active.current = visible && document.visibilityState === "visible";
      // A write may have committed already. Fence its receipt, then reread.
      if (!operation.current?.writing) operation.current?.controller.abort();
      publish(null);
      setError("");
      setNotice("");
      setLoading(active.current);
      pending.current = active.current;
      if (active.current) void load();
    };
    const timer = setInterval(() => {
      if (active.current) void load();
    }, ARCHIVE_STATUS_SYNC_INTERVAL_MS);
    window.addEventListener("focus", sync);
    document.addEventListener("visibilitychange", sync);
    sync();
    return () => {
      mounted.current = false;
      active.current = false;
      revision.current += 1;
      pending.current = false;
      if (!operation.current?.writing) operation.current?.controller.abort();
      clearInterval(timer);
      window.removeEventListener("focus", sync);
      document.removeEventListener("visibilitychange", sync);
    };
  }, [visible, editable]);

  return {
    status,
    loading,
    refreshing,
    saving,
    error,
    notice,
    setEnabled: (enabled) => {
      if (canEdit.current) void load(enabled);
    },
  };
}
