import { useEffect, useRef, useState } from "react";
import * as api from "../../api";

export const EVIDENCE_SYNC_INTERVAL_MS = 15_000;
const emptyDetail = () => ({ source: null, loading: false, error: "" });
const emptyFilters = () => ({
  query: "",
  domain: "",
  retrieved_from: "",
  retrieved_to: "",
});
const sameVersion = (left, right) =>
  left?.capture_id === right?.capture_id &&
  left?.content_hash === right?.content_hash &&
  left?.manifest_hash === right?.manifest_hash;

export function sourceDate(value) {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime()) ? date.toLocaleString() : null;
}

export function sourceURL(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}

export default function useEvidence(onRestoreFocus) {
  const [inventory, setInventory] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(emptyDetail);
  const [filters, setFilters] = useState(emptyFilters);
  const [appliedFilters, setAppliedFilters] = useState(null);
  const filtersRef = useRef(null);
  const inventoryRef = useRef(null);
  const selectedRef = useRef(null);
  const loadedPageLimit = useRef(1);
  const mounted = useRef(false);
  const listRevision = useRef(0);
  const detailRevision = useRef(0);
  const detailRequest = useRef(null);
  const selectedOffPage = useRef(false);
  const listBusy = useRef(false);
  const pendingRefresh = useRef(false);
  const listController = useRef(null);

  function cancelDetail() {
    detailRevision.current += 1;
    detailRequest.current?.controller.abort();
    detailRequest.current = null;
  }

  function closeSource(restoreFocus = false) {
    const source = selectedRef.current;
    cancelDetail();
    selectedOffPage.current = false;
    selectedRef.current = null;
    setSelected(null);
    setDetail(emptyDetail());
    if (restoreFocus) onRestoreFocus?.(source);
  }

  function withholdDetail() {
    // Snapshots started before a privacy boundary cannot publish afterward.
    listRevision.current += 1;
    cancelDetail();
    setDetail({
      source: null,
      loading:
        Boolean(selectedRef.current) && document.visibilityState === "visible",
      error: "",
    });
  }

  async function readSource(source, quiet = false) {
    if (sameVersion(detailRequest.current?.source, source)) return;
    cancelDetail();
    const request = {
      source,
      revision: detailRevision.current,
      controller: new AbortController(),
    };
    detailRequest.current = request;
    setDetail((previous) => ({
      source: quiet ? previous.source : null,
      loading: !quiet || !previous.source,
      error: "",
    }));
    try {
      const capture = await api.loadContextCapture(
        source.capture_id,
        source.content_hash,
        source.manifest_hash,
        request.controller.signal,
      );
      if (!sameVersion(capture, source))
        throw new Error(
          "This saved source version changed. Open it again from the list.",
        );
      if (mounted.current && request.revision === detailRevision.current)
        setDetail({ source: capture, loading: false, error: "" });
    } catch (reason) {
      if (mounted.current && request.revision === detailRevision.current)
        setDetail({
          source: null,
          loading: false,
          error:
            reason instanceof Error
              ? reason.message
              : "Could not open this saved source.",
        });
    } finally {
      if (detailRequest.current === request) detailRequest.current = null;
    }
  }

  async function load(mode = "refresh") {
    if (listBusy.current) {
      if (mode !== "append") pendingRefresh.current = true;
      return;
    }
    let cursor =
      mode === "append" ? inventoryRef.current?.next_cursor : undefined;
    if (mode === "append" && !cursor) return;
    const revision = ++listRevision.current;
    const search = filtersRef.current;
    const controller = new AbortController();
    listController.current = controller;
    listBusy.current = true;
    pendingRefresh.current = false;
    const foreground = mode !== "refresh" || !inventoryRef.current;
    setLoading(foreground);
    setRefreshing(!foreground);
    setError("");
    if (mode === "append") closeSource();
    try {
      let next = mode === "append" ? inventoryRef.current : null;
      const pages = !search && mode === "refresh" ? loadedPageLimit.current : 1;
      for (let page = 0; page < pages; page += 1) {
        let result;
        if (search) {
          const { candidates, ...summary } = await api.searchContextSources(
            search,
            controller.signal,
          );
          result = {
            ...summary,
            sources: candidates.map(({ content, ...source }) => ({
              ...source,
              content_bytes: new TextEncoder().encode(content).length,
            })),
            next_cursor: null,
          };
        } else {
          result = await api.loadContextSources(25, cursor, controller.signal);
        }
        if (
          !mounted.current ||
          revision !== listRevision.current ||
          document.visibilityState !== "visible"
        )
          return;
        next =
          next?.state === "ready" && result.state === "ready"
            ? {
                ...result,
                sources: [
                  ...new Map(
                    [...next.sources, ...result.sources].map((source) => [
                      source.capture_id,
                      source,
                    ]),
                  ).values(),
                ],
                skipped_count: next.skipped_count + result.skipped_count,
              }
            : result;
        cursor = result.next_cursor;
        if (result.state !== "ready" || !cursor) break;
      }
      if (mode === "append" && next.state === "ready")
        loadedPageLimit.current += 1;
      inventoryRef.current = next;
      setInventory(next);
      const current = selectedRef.current;
      if (current && !["ready", "limited"].includes(next.state)) {
        closeSource();
      } else if (current && document.visibilityState === "visible") {
        const listed = next.sources.some((source) =>
          sameVersion(source, current),
        );
        if (!listed && !selectedOffPage.current) {
          cancelDetail();
          setDetail({ source: null, loading: true, error: "" });
        }
        selectedOffPage.current = !listed;
        void readSource(current, true);
      }
    } catch (reason) {
      if (!mounted.current || revision !== listRevision.current) return;
      closeSource();
      inventoryRef.current = null;
      setInventory(null);
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load saved sources.",
      );
    } finally {
      if (listController.current === controller) listController.current = null;
      listBusy.current = false;
      if (mounted.current && revision === listRevision.current) {
        setLoading(false);
        setRefreshing(false);
      }
      const repeat = pendingRefresh.current;
      pendingRefresh.current = false;
      if (mounted.current && document.visibilityState === "visible" && repeat)
        void load();
    }
  }

  function applyFilters(next) {
    const search = Object.fromEntries(
      Object.entries(next)
        .map(([key, value]) => [key, value.trim()])
        .filter(([, value]) => value),
    );
    filtersRef.current = Object.keys(search).length
      ? { query: "", ...search }
      : null;
    setAppliedFilters(filtersRef.current);
    listRevision.current += 1;
    listController.current?.abort();
    closeSource();
    inventoryRef.current = null;
    setInventory(null);
    setLoading(true);
    void load();
  }

  function openSource(source) {
    if (document.visibilityState !== "visible") return;
    selectedOffPage.current = false;
    selectedRef.current = source;
    setSelected(source);
    void readSource(source);
  }

  useEffect(() => {
    mounted.current = true;
    let timer;
    const sync = (withhold = false) => {
      if (document.visibilityState !== "visible") return;
      if (withhold) withholdDetail();
      void load();
    };
    const focus = () => sync(true);
    const visibility = () => {
      clearInterval(timer);
      if (document.visibilityState === "visible") {
        timer = setInterval(() => sync(), EVIDENCE_SYNC_INTERVAL_MS);
        sync(true);
      } else {
        withholdDetail();
      }
    };
    window.addEventListener("focus", focus);
    document.addEventListener("visibilitychange", visibility);
    visibility();
    return () => {
      mounted.current = false;
      clearInterval(timer);
      window.removeEventListener("focus", focus);
      document.removeEventListener("visibilitychange", visibility);
      listRevision.current += 1;
      listController.current?.abort();
      cancelDetail();
      pendingRefresh.current = false;
    };
  }, []);

  return {
    inventory,
    loading,
    refreshing,
    error,
    selected,
    detail,
    filters,
    appliedFilters,
    updateFilter: (key, value) =>
      setFilters((previous) => ({ ...previous, [key]: value })),
    search: () => applyFilters(filters),
    clearSearch: () => {
      const next = emptyFilters();
      setFilters(next);
      applyFilters(next);
    },
    loadMore: () => {
      if (!listBusy.current && !filtersRef.current) void load("append");
    },
    openSource,
    closeSource: () => closeSource(true),
  };
}
