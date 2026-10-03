import { useState } from "react";
import { Pencil, Trash2 } from "lucide-react";
import * as api from "./api";
import type { Chat, Memory, MemoryCandidate } from "./api";

export default function MemorySetup({
  memories,
  candidates,
  chats,
  chatId,
  onChange,
}: {
  memories: Memory[];
  candidates: MemoryCandidate[];
  chats: Chat[];
  chatId: string | null;
  onChange: () => void;
}) {
  const [content, setContent] = useState("");
  const [scope, setScope] = useState<Memory["scope"]>("workspace");
  const [editing, setEditing] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

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
      await action();
      onChange();
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
          <p>Save preferences and facts for later local chats.</p>
        </div>
        <span className="badge">{memories.length} saved</span>
      </div>
      <p className="reflection-note">
        You control what is saved. Relevant memories are used in local chats
        when hosted web search is off.
      </p>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
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
            maxLength={1000}
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
          <button className="button primary" disabled={busy || !content.trim()}>
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
      {!!candidates.length && (
        <div>
          <h3>Proposed memories</h3>
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
        </div>
      )}
      {memories.map((memory) => (
        <div className="memory-entry" key={memory.id}>
          <div>
            <p>{memory.content}</p>
            <small>
              {memory.scope === "workspace"
                ? "All local chats"
                : chats.find((chat) => chat.id === memory.chat_id)?.title ||
                  "Conversation"}
            </small>
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
      ))}
      <p className="reflection-note">
        Saved privately on this computer. Keep passwords and API keys out of
        memory. Forgetting removes future recall; it does not erase earlier
        messages or backups.
      </p>
    </section>
  );
}
