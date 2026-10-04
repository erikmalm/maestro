import { useState } from "react";
import { List, Network, Pencil, Trash2 } from "lucide-react";
import MemoryMap from "./MemoryMap";
import * as api from "./api";
import type { Chat, Memory, MemoryCandidate } from "./api";

const kindLabels = {
  fact: "User fact",
  preference: "Preference",
  identity: "Working identity",
  lesson: "Lesson",
};

export default function MemorySetup({
  memories,
  candidates,
  automatic,
  chats,
  chatId,
  onChange,
  onAction,
}: {
  memories: Memory[];
  candidates: MemoryCandidate[];
  automatic: boolean;
  chats: Chat[];
  chatId: string | null;
  onChange: () => Promise<void>;
  onAction: (action: () => Promise<unknown>) => Promise<unknown>;
}) {
  const [content, setContent] = useState("");
  const [scope, setScope] = useState<Memory["scope"]>("workspace");
  const [editing, setEditing] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [view, setView] = useState<"map" | "list">("map");
  const [search, setSearch] = useState("");
  const [filterScope, setFilterScope] = useState("all");
  const [selectedId, setSelectedId] = useState("");
  const filtered = memories.filter(
    (memory) =>
      memory.content
        .toLocaleLowerCase()
        .includes(search.trim().toLocaleLowerCase()) &&
      (filterScope === "all" ||
        (memory.scope === filterScope &&
          (filterScope !== "conversation" || memory.chat_id === chatId))),
  );
  const selected =
    filtered.find((memory) => memory.id === selectedId) ?? filtered[0];

  async function save(
    action: () => Promise<unknown>,
    message: string,
    clearDraft = false,
  ) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await onAction(action);
      await onChange();
      if (clearDraft) {
        setContent("");
        setEditing("");
      }
      setNotice(message);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not save memory.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card reflection-box memory-setup">
      <div className="reflection-title">
        <div>
          <h2>Private memory</h2>
          <p>Private facts, preferences and notes on working style.</p>
        </div>
        <span className="badge">{memories.length} saved</span>
      </div>
      <p className="reflection-note">
        {automatic
          ? "Maestro maintains memory automatically. Edit to pin a correction, or forget any entry."
          : "You choose which memories are saved."}{" "}
        Relevant memories are used in local chats when hosted web search is off.
      </p>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <details
        className="reflection-cleanup"
        open={!automatic || !!editing || !!content}
      >
        <summary>{editing ? "Edit memory" : "Add a memory"}</summary>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!content.trim()) return;
            void save(
              () =>
                editing
                  ? api.correctMemory(editing, content)
                  : api.remember(content, scope, chatId),
              editing ? "Memory updated." : "Memory saved.",
              true,
            );
          }}
        >
          <label>
            {editing ? "Edit memory" : "What should Maestro remember?"}
            <textarea
              required
              maxLength={8000}
              rows={3}
              value={content}
              disabled={busy}
              onChange={(event) => setContent(event.target.value)}
            />
          </label>
          {!editing && (
            <label>
              Use this memory in
              <select
                value={scope}
                disabled={busy}
                onChange={(event) =>
                  setScope(event.target.value as Memory["scope"])
                }
              >
                <option value="workspace">All local chats</option>
                <option value="conversation" disabled={!chatId}>
                  This conversation
                </option>
              </select>
            </label>
          )}
          <div className="reflection-actions">
            <button
              className="button primary"
              disabled={busy || !content.trim()}
            >
              {editing ? "Update memory" : "Save memory"}
            </button>
            {editing && (
              <button
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={() => {
                  setEditing("");
                  setContent("");
                }}
              >
                Cancel edit
              </button>
            )}
          </div>
        </form>
      </details>
      {!!candidates.length && (
        <details className="reflection-cleanup" open={!automatic}>
          <summary>
            {automatic
              ? "Earlier suggestions awaiting review"
              : "Proposed memories"}
          </summary>
          <p className="reflection-note">
            These suggestions are not used until you accept them.
          </p>
          {candidates.map((candidate) => (
            <div className="memory-entry" key={candidate.id}>
              <div>
                <p>{candidate.content}</p>
                <small>
                  From{" "}
                  {chats.find((chat) => chat.id === candidate.chat_id)?.title ||
                    "Conversation"}
                </small>
                <blockquote className="reflection-note">
                  {candidate.evidence}
                </blockquote>
                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    const selected = new FormData(event.currentTarget).get(
                      "scope",
                    ) as Memory["scope"];
                    void save(
                      () => api.acceptMemoryCandidate(candidate.id, selected),
                      "Proposed memory saved.",
                    );
                  }}
                >
                  <label>
                    Use proposed memory in
                    <select
                      name="scope"
                      defaultValue="conversation"
                      disabled={busy}
                    >
                      <option value="conversation">This conversation</option>
                      <option value="workspace">All local chats</option>
                    </select>
                  </label>
                  <div className="reflection-actions">
                    <button className="button primary" disabled={busy}>
                      Accept memory
                    </button>
                    <button
                      type="button"
                      className="button secondary"
                      disabled={busy}
                      onClick={() =>
                        void save(
                          () => api.rejectMemoryCandidate(candidate.id),
                          "Proposed memory rejected.",
                        )
                      }
                    >
                      Reject
                    </button>
                  </div>
                </form>
              </div>
            </div>
          ))}
        </details>
      )}
      <div className="memory-view-controls">
        <div
          className="memory-view-toggle"
          role="group"
          aria-label="Memory view"
        >
          {(
            [
              ["map", "Map", Network],
              ["list", "List", List],
            ] as const
          ).map(([value, label, Icon]) => (
            <button
              key={value}
              type="button"
              className="button secondary"
              aria-pressed={view === value}
              onClick={() => setView(value)}
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
        </div>
        <label>
          Search memories
          <input
            type="search"
            placeholder="Find a fact, preference or note…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <label>
          Memory scope
          <select
            value={filterScope}
            onChange={(event) => setFilterScope(event.target.value)}
          >
            <option value="all">All memories</option>
            <option value="workspace">All local chats</option>
            <option value="conversation" disabled={!chatId}>
              This conversation
            </option>
          </select>
        </label>
      </div>
      {view === "map" && (
        <MemoryMap
          memories={filtered}
          chats={chats}
          selectedId={selected?.id}
          filterKey={`${search}:${filterScope}:${chatId}`}
          onSelect={setSelectedId}
        />
      )}
      {!filtered.length && (
        <p className="reflection-note">
          {memories.length
            ? "No memories match these filters."
            : "No saved memories yet. Add a memory or let Maestro learn from your conversations."}
        </p>
      )}
      {(view === "map" ? (selected ? [selected] : []) : filtered).map(
        (memory) => (
          <div className="memory-entry" key={memory.id}>
            <div>
              <p>{memory.content}</p>
              <small>
                {kindLabels[memory.kind ?? "fact"]}
                {" · "}
                {memory.pinned || memory.origin === "explicit"
                  ? "User-pinned"
                  : memory.origin === "reflective"
                    ? "AI reflection"
                    : "AI-curated"}
                {" · "}
                {memory.scope === "workspace"
                  ? "All local chats"
                  : chats.find((chat) => chat.id === memory.chat_id)?.title ||
                    "Conversation"}
              </small>
              {(memory.evidence ||
                memory.source_message_id ||
                !!memory.provenance?.length) && (
                <details open={view === "map"}>
                  <summary>Source</summary>
                  {memory.evidence && (
                    <blockquote className="reflection-note">
                      {memory.evidence}
                    </blockquote>
                  )}
                  {memory.provenance?.length
                    ? memory.provenance.map((source, index) => (
                        <p className="reflection-note" key={index}>
                          {"memory_id" in source ? (
                            <button
                              type="button"
                              className="memory-source-link"
                              onClick={() => {
                                setView("map");
                                setSearch("");
                                setFilterScope("all");
                                setSelectedId(source.memory_id);
                              }}
                            >
                              Source memory:{" "}
                              {memories.find(
                                (item) => item.id === source.memory_id,
                              )?.content || source.memory_id}
                            </button>
                          ) : (
                            `${chats.find((chat) => chat.id === source.chat_id)?.title || "Conversation"} · message ${source.message_id}`
                          )}
                        </p>
                      ))
                    : memory.source_message_id && (
                        <p className="reflection-note">
                          {chats.find((chat) => chat.id === memory.chat_id)
                            ?.title || "Conversation"}{" "}
                          · message {memory.source_message_id}
                        </p>
                      )}
                </details>
              )}
            </div>
            <button
              className="icon-button"
              aria-label="Edit saved memory"
              disabled={busy}
              onClick={() => {
                setEditing(memory.id);
                setContent(memory.content);
                setNotice("");
              }}
            >
              <Pencil size={16} />
            </button>
            <button
              className="icon-button"
              aria-label="Forget saved memory"
              disabled={busy}
              onClick={() =>
                void save(
                  () => api.forgetMemory(memory.id),
                  "Memory forgotten. Earlier chat messages and backups are retained.",
                )
              }
            >
              <Trash2 size={16} />
            </button>
          </div>
        ),
      )}
      <p className="reflection-note">
        Saved privately on this computer. Keep passwords and API keys out of
        memory. Forgetting removes future recall; it does not erase earlier
        messages or backups.
      </p>
    </section>
  );
}
