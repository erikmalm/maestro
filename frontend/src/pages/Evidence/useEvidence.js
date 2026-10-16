import { useEffect, useRef, useState } from "react";
import * as api from "../../api";

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

export default function useEvidence() {
  const [inventory, setInventory] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState({
    source: null,
    loading: false,
    error: "",
  });
  const listRevision = useRef(0);
  const detailRevision = useRef(0);
  const listBusy = useRef(false);
  const sourceButton = useRef(null);

  function closeSource(restoreFocus = false) {
    detailRevision.current += 1;
    setSelected(null);
    setDetail({ source: null, loading: false, error: "" });
    if (restoreFocus && sourceButton.current?.isConnected)
      sourceButton.current.focus();
    sourceButton.current = null;
  }

  async function load(cursor) {
    const revision = ++listRevision.current;
    listBusy.current = true;
    setLoading(true);
    setError("");
    closeSource();
    if (!cursor) {
      setInventory(null);
    }
    try {
      const next = await api.loadContextSources(25, cursor);
      if (revision !== listRevision.current) return;
      setInventory((previous) =>
        cursor && previous?.state === "ready" && next.state === "ready"
          ? {
              ...next,
              sources: [
                ...new Map(
                  [...previous.sources, ...next.sources].map((source) => [
                    source.capture_id,
                    source,
                  ]),
                ).values(),
              ],
              skipped_count: previous.skipped_count + next.skipped_count,
            }
          : next,
      );
    } catch (reason) {
      if (revision !== listRevision.current) return;
      closeSource();
      setInventory(null);
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load saved sources.",
      );
    } finally {
      if (revision === listRevision.current) {
        listBusy.current = false;
        setLoading(false);
      }
    }
  }

  async function openSource(source, button) {
    if (listBusy.current) return;
    sourceButton.current = button;
    const revision = ++detailRevision.current;
    setSelected(source);
    setDetail({ source: null, loading: true, error: "" });
    try {
      const capture = await api.loadContextCapture(
        source.capture_id,
        source.content_hash,
        source.manifest_hash,
      );
      if (revision === detailRevision.current)
        setDetail({ source: capture, loading: false, error: "" });
    } catch (reason) {
      if (revision === detailRevision.current)
        setDetail({
          source: null,
          loading: false,
          error:
            reason instanceof Error
              ? reason.message
              : "Could not open this saved source.",
        });
    }
  }

  useEffect(() => {
    void load();
    return () => {
      listRevision.current += 1;
      detailRevision.current += 1;
    };
  }, []);

  return {
    inventory,
    loading,
    error,
    selected,
    detail,
    refresh: () => void load(),
    loadMore: () => {
      if (!listBusy.current && inventory?.next_cursor)
        void load(inventory.next_cursor);
    },
    openSource,
    closeSource: () => closeSource(true),
  };
}
