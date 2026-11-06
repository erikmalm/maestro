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
const activeStates = new Set(["queued", "running"]);
const terminalStates = new Set([
  "draft",
  "partial",
  "clarification",
  "cancelled",
  "failed",
  "interrupted",
]);

export default function useResearchRuns(chatId) {
  const [request, setRequest] = useState("");
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
  const [working, setWorking] = useState(false);
  const [historyReady, setHistoryReady] = useState(false);
  const mounted = useRef(false);
  const draft = useRef("");
  // Keep draft ownership in memory; only the opaque pending ID is stored.
  const submission = useRef(null);
  const activeRuns = useRef(new Set());
  const completedRuns = useRef(new Set());
  const historyRead = useRef(false);
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
    observe(run);
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
  function finishSubmission() {
    if (!submission.current) return;
    if (draft.current.trim() === submission.current.request) {
      draft.current = "";
      setRequest("");
    }
    submission.current = null;
  }
  function observe(run) {
    const owned = submission.current?.id === run.submission_id;
    if (owned) submission.current.runId = run.id;
    if (terminalStates.has(run.state)) {
      completedRuns.current.add(run.id);
      activeRuns.current.delete(run.id);
      if (owned) finishSubmission();
    } else if (
      activeStates.has(run.state) &&
      // A stale list/detail snapshot cannot revive a completed run's lock.
      !completedRuns.current.has(run.id)
    ) {
      activeRuns.current.add(run.id);
    }
    setWorking(activeRuns.current.size > 0);
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
      historyRead.current = true;
      setHistoryReady(true);
      const visible = list.value.runs.filter(
        (run) => !removed.current.has(run.id),
      );
      setRuns(visible);
      setSkipped(list.value.skipped_count);
      visible.forEach(observe);
      if (list.value.skipped_count === 0) {
        // Admission permits only one active run, so it must appear in the
        // newest history. A clean absence can reconcile deletion in another
        // tab; filtered or failed reads cannot establish that absence.
        const present = new Set(visible.map((run) => run.id));
        for (const id of activeRuns.current) {
          if (!present.has(id)) {
            activeRuns.current.delete(id);
            completedRuns.current.add(id);
            // Absence unlocks the form but is not accepted completion.
            if (submission.current?.runId === id) submission.current = null;
          }
        }
        setWorking(activeRuns.current.size > 0);
      }
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
      // Keep known active runs locked: a failed read is not completion.
      setRuns([]);
      errors.push(message(list.reason, "Could not load research runs."));
    }
    if (
      id &&
      detail.status === "fulfilled" &&
      detail.value &&
      !removed.current.has(id)
    ) {
      observe(detail.value);
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
    if (writing.current || activeRuns.current.size || !historyRead.current)
      return;
    if (pending.current && !pendingBody.current) return;
    const body = pendingBody.current || {
      submission_id: crypto.randomUUID().replaceAll("-", ""),
      request,
      query: "",
      workflow: "research_draft",
      chat_id: chatId || null,
    };
    pendingBody.current = body;
    submission.current ||= {
      id: body.submission_id,
      request: body.request,
      runId: null,
    };
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
        submission.current = null;
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
        activeRuns.current.delete(id);
        completedRuns.current.add(id);
        setWorking(activeRuns.current.size > 0);
        if (submission.current?.runId === id) finishSubmission();
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
    request,
    updateRequest: (value) => {
      if (
        writing.current ||
        pending.current ||
        activeRuns.current.size ||
        !historyRead.current
      )
        return;
      draft.current = value;
      setRequest(value);
    },
    runs,
    skipped,
    selectedId,
    selected,
    loading,
    detailLoading,
    busy,
    working,
    formLocked: Boolean(busy || pendingId || working || !historyReady),
    readError,
    actionError,
    notice,
    pendingId,
    canRetry: Boolean(pendingBody.current) && !working,
    submit,
    retry: () => submit(),
    newSubmission: () => {
      clearPending();
      submission.current = null;
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
