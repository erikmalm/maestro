import { useEffect, useRef, useState } from "react";
import * as api from "../../api";

const PENDING_KEY = "maestro.research.pending";
const opaqueId = (value) => (/^[a-f0-9]{32}$/.test(value || "") ? value : null);
const initialSelection = () =>
  opaqueId(new URLSearchParams(window.location.search).get("research_run"));
function pendingIdentity(value) {
  try {
    if (value === undefined)
      return opaqueId(sessionStorage.getItem(PENDING_KEY));
    if (value) sessionStorage.setItem(PENDING_KEY, value);
    else sessionStorage.removeItem(PENDING_KEY);
  } catch {
    /* Storage may be disabled; no request text belongs here. */
  }
  return null;
}
export const researchPageRequested = () =>
  Boolean(initialSelection() || pendingIdentity());
const message = (reason, fallback) =>
  reason instanceof Error ? reason.message : fallback;

export default function useResearchRuns(chatId) {
  const [runs, setRuns] = useState([]);
  const [skipped, setSkipped] = useState(0);
  const [selectedId, setSelectedId] = useState(initialSelection);
  const [selected, setSelected] = useState(null);
  const [pendingId, setPendingId] = useState(pendingIdentity);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(Boolean(selectedId));
  const [readError, setReadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const mounted = useRef(false);
  const selection = useRef(selectedId);
  const pending = useRef(pendingId);
  const pendingBody = useRef(null);
  const writing = useRef(false);
  const revision = useRef(0);
  const reader = useRef(null);
  const refreshQueued = useRef(false);
  const removed = useRef(new Set());

  function fence() {
    revision.current += 1;
    reader.current?.abort();
    reader.current = null;
    refreshQueued.current = false;
  }
  function remember(run) {
    setRuns((previous) =>
      [run, ...previous.filter((item) => item.id !== run.id)]
        .filter((item) => !removed.current.has(item.id))
        .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
        .slice(0, 20),
    );
  }
  function clearPending() {
    pending.current = null;
    pendingBody.current = null;
    pendingIdentity(null);
    setPendingId(null);
  }
  function select(id, run = null) {
    fence();
    selection.current = id;
    setSelectedId(id);
    setSelected(run?.events ? run : null);
    setDetailLoading(Boolean(id));
    setReadError("");
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("research_run", id);
    else url.searchParams.delete("research_run");
    window.history.replaceState(null, "", url);
  }
  async function refresh() {
    if (
      !mounted.current ||
      document.visibilityState !== "visible" ||
      writing.current
    )
      return;
    if (reader.current) {
      // Slow reads must finish; ordinary polling/focus cannot restart them.
      refreshQueued.current = true;
      return;
    }
    fence();
    const current = revision.current;
    const id = selection.current;
    const controller = new AbortController();
    reader.current = controller;
    const [list, detail] = await Promise.allSettled([
      api.loadResearchRuns(controller.signal),
      id ? api.loadResearchRun(id, controller.signal) : Promise.resolve(null),
    ]);
    if (!mounted.current || revision.current !== current) return;
    reader.current = null;
    setLoading(false);
    const errors = [];
    if (list.status === "fulfilled") {
      const visible = list.value.runs.filter(
        (run) => !removed.current.has(run.id),
      );
      setRuns(visible);
      setSkipped(list.value.skipped_count);
      const restored =
        pending.current &&
        visible.find((run) => run.submission_id === pending.current);
      if (restored) {
        clearPending();
        if (restored.id !== id) {
          select(restored.id, restored);
          void refresh();
          return;
        }
      }
      if (!id && visible.length) {
        select(visible[0].id, visible[0]);
        void refresh();
        return;
      }
    } else {
      setRuns([]);
      errors.push(message(list.reason, "Could not load research runs."));
    }
    if (
      id &&
      detail.status === "fulfilled" &&
      detail.value &&
      !removed.current.has(id)
    ) {
      setSelected(detail.value);
      setDetailLoading(false);
    } else if (id && detail.status === "rejected") {
      setSelected(null);
      setDetailLoading(false);
      errors.push(message(detail.reason, "This research run is unavailable."));
      if (detail.reason?.status === 404) {
        setRuns((previous) => previous.filter((run) => run.id !== id));
        select(null);
      }
    }
    setReadError(errors.join(" "));
    if (refreshQueued.current) {
      refreshQueued.current = false;
      void refresh();
    }
  }

  useEffect(() => {
    mounted.current = true;
    const sync = () => {
      void refresh();
    };
    const timer = setInterval(sync, 2500);
    window.addEventListener("focus", sync);
    document.addEventListener("visibilitychange", sync);
    void refresh();
    return () => {
      mounted.current = false;
      fence();
      clearInterval(timer);
      window.removeEventListener("focus", sync);
      document.removeEventListener("visibilitychange", sync);
    };
  }, []);

  async function submit(request) {
    if (writing.current) return;
    if (pending.current && !pendingBody.current) return;
    const body = pendingBody.current || {
      submission_id: crypto.randomUUID().replaceAll("-", ""),
      request,
      query: "",
      workflow: "evidence_review",
      chat_id: chatId || null,
    };
    pendingBody.current = body;
    pending.current = body.submission_id;
    pendingIdentity(body.submission_id);
    setPendingId(body.submission_id);
    fence();
    writing.current = true;
    setBusy("submit");
    setActionError("");
    setNotice("");
    try {
      const response = await api.createResearchRun(body);
      if (!mounted.current) return;
      clearPending();
      remember(response.run);
      select(response.run.id, response.run);
      setNotice(
        response.created
          ? "Research request recorded."
          : "Existing submission restored.",
      );
    } catch (reason) {
      if (!mounted.current) return;
      if (reason?.status && reason.status < 500) {
        clearPending();
        setActionError(
          message(reason, "Could not record this research request."),
        );
      } else {
        setActionError(
          "The submission response was not confirmed. Check recent runs or retry this same submission.",
        );
      }
    } finally {
      writing.current = false;
      if (mounted.current) {
        setBusy("");
        void refresh();
      }
    }
  }
  async function change(kind) {
    const id = selection.current;
    if (!id || writing.current) return;
    fence();
    writing.current = true;
    setBusy(kind);
    setActionError("");
    setNotice("");
    try {
      if (kind === "delete") {
        await api.deleteResearchRun(id);
        if (!mounted.current) return;
        removed.current.add(id);
        setRuns((previous) => previous.filter((run) => run.id !== id));
        select(null);
        setNotice("Research run deleted.");
      } else {
        const run = await api.cancelResearchRun(id);
        if (!mounted.current) return;
        setSelected(run);
        remember(run);
        setNotice("Research run updated.");
      }
    } catch (reason) {
      if (mounted.current)
        setActionError(message(reason, "Could not update this research run."));
    } finally {
      writing.current = false;
      if (mounted.current) {
        setBusy("");
        void refresh();
      }
    }
  }
  return {
    runs,
    skipped,
    selectedId,
    selected,
    loading,
    detailLoading,
    busy,
    readError,
    actionError,
    notice,
    pendingId,
    canRetry: Boolean(pendingBody.current),
    submit,
    retry: () => submit(),
    newSubmission: () => {
      clearPending();
      setActionError("");
      setNotice("");
    },
    select: (run) => {
      if (!writing.current) {
        select(run.id, run);
        void refresh();
      }
    },
    cancel: () => change("cancel"),
    remove: () => change("delete"),
  };
}
