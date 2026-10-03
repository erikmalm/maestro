import { useEffect, useId, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Minus,
  Plus,
  RotateCcw,
  Pin,
} from "lucide-react";
import type { Chat, Memory } from "./api";

export const memoryKinds = {
  fact: "Facts",
  preference: "Preferences",
  identity: "Working identity",
  lesson: "Lessons",
};
const kinds = Object.keys(memoryKinds) as (keyof typeof memoryKinds)[];
const pageSize = 5;
const conversationSources = (memory?: Memory) => [
  ...new Set([
    ...(memory?.provenance ?? []).flatMap((source) =>
      "chat_id" in source ? [source.chat_id] : [],
    ),
    ...(memory?.chat_id && memory.source_message_id ? [memory.chat_id] : []),
  ]),
];

export default function MemoryMap({
  memories,
  chats,
  selectedId,
  filterKey,
  onSelect,
}: {
  memories: Memory[];
  chats: Chat[];
  selectedId?: string;
  filterKey: string;
  onSelect: (id: string) => void;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(500);
  const [zoom, setZoom] = useState(1);
  const [selectedSource, setSelectedSource] = useState("");
  const [pages, setPages] = useState<
    Partial<Record<keyof typeof memoryKinds, number>>
  >({});
  const marker = useId().replaceAll(":", "");
  const groups = kinds.map((kind) => {
    const entries = memories
      .filter((memory) => (memory.kind ?? "fact") === kind)
      .sort(
        (a, b) =>
          a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id),
      );
    return {
      kind,
      entries,
      page: Math.min(
        pages[kind] ?? 0,
        Math.max(0, Math.ceil(entries.length / pageSize) - 1),
      ),
    };
  });
  const selectedGroup = groups.find(({ entries }) =>
    entries.some((memory) => memory.id === selectedId),
  );
  const selectedPage = selectedGroup
    ? Math.floor(
        selectedGroup.entries.findIndex((memory) => memory.id === selectedId) /
          pageSize,
      )
    : 0;
  useEffect(() => {
    const observer = new ResizeObserver(() =>
      setWidth(Math.max(360, viewport.current?.clientWidth ?? 500)),
    );
    if (viewport.current) observer.observe(viewport.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    setPages({});
    setSelectedSource("");
  }, [filterKey]);
  useEffect(() => {
    if (selectedGroup)
      setPages((current) => ({
        ...current,
        [selectedGroup.kind]: selectedPage,
      }));
  }, [selectedId, filterKey, selectedGroup?.kind, selectedPage]);
  useEffect(() => setSelectedSource(""), [selectedId]);

  const columnWidth = (width - 52) / 2;
  const rowHeights = [0, 2].map(
    (start) =>
      64 +
      Math.min(
        pageSize,
        Math.max(
          1,
          ...groups
            .slice(start, start + 2)
            .map(({ entries }) => entries.length),
        ),
      ) *
        70,
  );
  const pageEntries = groups.flatMap(({ entries, page }, groupIndex) =>
    entries
      .slice(page * pageSize, (page + 1) * pageSize)
      .map((memory, index) => ({
        memory,
        groupIndex,
        index,
      })),
  );
  const selectedSources = conversationSources(
    memories.find((memory) => memory.id === selectedId),
  );
  const sources = [
    ...new Set(
      pageEntries.flatMap(({ memory }) => conversationSources(memory)),
    ),
  ]
    .sort(
      (a, b) =>
        Number(selectedSources.includes(b)) -
          Number(selectedSources.includes(a)) || a.localeCompare(b),
    )
    .slice(0, 8)
    .map((id, index) => ({
      id,
      title: chats.find((chat) => chat.id === id)?.title || "Conversation",
      x: 16 + (index % 2) * (columnWidth + 20),
      y: 72 + Math.floor(index / 2) * 38,
    }));
  const groupTop = sources.length
    ? 92 + Math.ceil(sources.length / 2) * 38
    : 80;
  const rowTop = (groupIndex: number) =>
    groupTop + (groupIndex >= 2 ? rowHeights[0] + 20 : 0);
  const height = groupTop + rowHeights[0] + rowHeights[1] + 40;
  const visible = pageEntries.map(({ memory, groupIndex, index }) => ({
    memory,
    x: 16 + (groupIndex % 2) * (columnWidth + 20),
    y: rowTop(groupIndex) + 42 + index * 70,
  }));
  const positions = new Map(visible.map((entry) => [entry.memory.id, entry]));
  const links = visible
    .flatMap((target) =>
      (target.memory.provenance ?? []).flatMap((source) => {
        const from =
          "memory_id" in source ? positions.get(source.memory_id) : undefined;
        return from ? [{ from, to: target }] : [];
      }),
    )
    .sort(
      (a, b) =>
        Number(
          b.from.memory.id === selectedId || b.to.memory.id === selectedId,
        ) -
        Number(
          a.from.memory.id === selectedId || a.to.memory.id === selectedId,
        ),
    )
    .slice(0, 80);
  const related = new Set([
    ...links
      .filter(
        ({ from, to }) =>
          !selectedSource &&
          (from.memory.id === selectedId || to.memory.id === selectedId),
      )
      .flatMap(({ from, to }) => [from.memory.id, to.memory.id]),
    ...visible
      .filter(({ memory }) =>
        conversationSources(memory).includes(selectedSource),
      )
      .map(({ memory }) => memory.id),
  ]);
  const sourceLinks = sources.flatMap((source) =>
    visible
      .filter(({ memory }) => conversationSources(memory).includes(source.id))
      .map((to) => ({ source, to })),
  );

  return (
    <div className="memory-map">
      <div className="memory-map-toolbar">
        <span>Scroll to explore · Select a memory</span>
        <div className="memory-map-zoom">
          <button
            type="button"
            className="icon-button"
            aria-label="Zoom out memory map"
            disabled={zoom <= 0.6}
            onClick={() => setZoom((value) => Math.max(0.6, value - 0.2))}
          >
            <Minus size={15} />
          </button>
          <span>{Math.round(zoom * 100)}%</span>
          <button
            type="button"
            className="icon-button"
            aria-label="Zoom in memory map"
            disabled={zoom >= 1.6}
            onClick={() => setZoom((value) => Math.min(1.6, value + 0.2))}
          >
            <Plus size={15} />
          </button>
          <button
            type="button"
            className="icon-button"
            aria-label="Reset memory map view"
            onClick={() => {
              setZoom(1);
              viewport.current?.scrollTo(0, 0);
            }}
          >
            <RotateCcw size={15} />
          </button>
        </div>
      </div>
      <div
        className="memory-map-viewport"
        ref={viewport}
        tabIndex={0}
        role="region"
        aria-label="Memory map"
      >
        <div className="memory-map-surface" style={{ width, height, zoom }}>
          <div className="memory-map-hub">Private memory</div>
          <svg
            className="memory-map-links"
            width={width}
            height={height}
            aria-hidden="true"
          >
            <defs>
              <marker
                id={marker}
                markerWidth="7"
                markerHeight="7"
                refX="6"
                refY="3.5"
                orient="auto"
              >
                <path d="M0,0 L7,3.5 L0,7" fill="currentColor" />
              </marker>
            </defs>
            {sourceLinks.map(({ source, to }) => {
              const x1 = source.x + columnWidth / 2;
              const y1 = source.y + 30;
              const x2 = to.x + columnWidth / 2;
              return (
                <path
                  key={`${source.id}:${to.memory.id}`}
                  data-conversation-link={`${source.id}:${to.memory.id}`}
                  className={
                    (
                      selectedSource
                        ? source.id === selectedSource
                        : to.memory.id === selectedId
                    )
                      ? "active"
                      : ""
                  }
                  d={`M${x1},${y1} C${x1},${y1 + 30} ${x2},${to.y - 30} ${x2},${to.y}`}
                  markerEnd={`url(#${marker})`}
                />
              );
            })}
            {links.map(({ from, to }) => {
              const sameColumn = from.x === to.x;
              const forward = sameColumn ? from.y < to.y : from.x < to.x;
              const x1 =
                from.x +
                (sameColumn ? columnWidth / 2 : forward ? columnWidth : 0);
              const x2 =
                to.x +
                (sameColumn ? columnWidth / 2 : forward ? 0 : columnWidth);
              const y1 = from.y + (sameColumn ? (forward ? 58 : 0) : 29);
              const y2 = to.y + (sameColumn ? (forward ? 0 : 58) : 29);
              return (
                <path
                  key={`${from.memory.id}:${to.memory.id}`}
                  data-memory-link={`${from.memory.id}:${to.memory.id}`}
                  className={
                    !selectedSource &&
                    (from.memory.id === selectedId ||
                      to.memory.id === selectedId)
                      ? "active"
                      : ""
                  }
                  d={`M${x1},${y1} C${sameColumn ? x1 : (x1 + x2) / 2},${sameColumn ? (y1 + y2) / 2 : y1} ${sameColumn ? x2 : (x1 + x2) / 2},${sameColumn ? (y1 + y2) / 2 : y2} ${x2},${y2}`}
                  markerEnd={`url(#${marker})`}
                />
              );
            })}
          </svg>
          {sources.map((source) => (
            <button
              key={source.id}
              type="button"
              className={`memory-map-source ${selectedSources.includes(source.id) ? "related" : ""}`}
              style={{ left: source.x, top: source.y, width: columnWidth }}
              aria-label={`Highlight memories from ${source.title}`}
              aria-pressed={selectedSource === source.id}
              title={`Conversation source: ${source.title}`}
              onClick={() =>
                setSelectedSource(selectedSource === source.id ? "" : source.id)
              }
            >
              Conversation · {source.title}
            </button>
          ))}
          {groups.map(({ kind, entries, page }, index) => (
            <div
              className="memory-map-group"
              key={kind}
              style={{
                left: 16 + (index % 2) * (columnWidth + 20),
                top: rowTop(index),
                width: columnWidth,
                height: rowHeights[Math.floor(index / 2)],
              }}
            >
              <h3>
                {memoryKinds[kind]} <small>{entries.length}</small>
              </h3>
              {!entries.length && <p>No matching memories</p>}
              {entries.length > pageSize && (
                <div className="memory-map-pages">
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={`Previous ${memoryKinds[kind].toLowerCase()}`}
                    disabled={page === 0}
                    onClick={() => setPages({ ...pages, [kind]: page - 1 })}
                  >
                    <ChevronLeft size={15} />
                  </button>
                  <span>
                    {page * pageSize + 1}–
                    {Math.min(entries.length, (page + 1) * pageSize)} of{" "}
                    {entries.length}
                  </span>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={`Next ${memoryKinds[kind].toLowerCase()}`}
                    disabled={(page + 1) * pageSize >= entries.length}
                    onClick={() => setPages({ ...pages, [kind]: page + 1 })}
                  >
                    <ChevronRight size={15} />
                  </button>
                </div>
              )}
            </div>
          ))}
          {visible.map(({ memory, x, y }) => (
            <button
              type="button"
              key={memory.id}
              className={`memory-map-node ${related.has(memory.id) ? "related" : ""}`}
              style={{ left: x, top: y, width: columnWidth }}
              aria-label={`Select memory: ${memory.content}`}
              aria-pressed={memory.id === selectedId}
              title={memory.content}
              onClick={() => {
                setSelectedSource("");
                onSelect(memory.id);
              }}
            >
              <span>{memory.content}</span>
              {(memory.pinned || memory.origin === "explicit") && (
                <Pin size={12} aria-label="User-pinned" />
              )}
            </button>
          ))}
        </div>
      </div>
      <p className="reflection-note">
        Arrows show recorded sources. Select a conversation to highlight its
        memories; inspect full evidence below. Search or page through groups to
        explore more.
      </p>
    </div>
  );
}
