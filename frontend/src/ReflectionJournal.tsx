import type { Chat, WorkStatus } from "./api";

const changeLabels = { add: "Added", update: "Updated", remove: "Forgot" };

export default function ReflectionJournal({
  status,
  chats,
}: {
  status: WorkStatus;
  chats: Chat[];
}) {
  return (
    <section className="card reflection-box">
      <div className="reflection-title">
        <div>
          <h2>Private reflection journal</h2>
          <p>
            Brief outcomes, memory changes and sources, saved on this computer.
          </p>
        </div>
        <span className="badge neutral">
          {!status.worker_available
            ? "Not running"
            : status.waiting_for_ollama
              ? "Waiting for Ollama"
              : status.running
                ? "Reflecting"
                : status.config.enabled
                  ? "Enabled"
                  : "Paused"}
        </span>
      </div>
      {status.worker_available && (
        <p className="reflection-note">
          {status.queued} queued · {status.today_jobs} /{" "}
          {status.config.max_jobs_per_day} jobs today · {status.today_tokens} /{" "}
          {status.config.max_tokens_per_day} tokens today
          {status.last_stop_reason && <> · {status.last_stop_reason}</>}
          {status.next_reflection_at && (
            <>
              {" "}
              · Next: {new Date(status.next_reflection_at).toLocaleString()}.
            </>
          )}
        </p>
      )}
      {!status.journal?.length && (
        <p className="reflection-note">No reflections recorded yet.</p>
      )}
      {status.journal?.map((entry) => (
        <div className="memory-entry" key={entry.id}>
          <div>
            <p>{entry.summary}</p>
            <small>
              {entry.kind === "reflection"
                ? "Working-style reflection"
                : "Memory curation"}{" "}
              · {new Date(entry.created_at).toLocaleString()}
              {entry.outcome && <> · {entry.outcome}</>}
              {entry.models.length > 0 && <> · {entry.models.join(" → ")}</>}
            </small>
            <details>
              <summary>
                {entry.changes.length} memory changes · {entry.sources.length}{" "}
                source messages
              </summary>
              <ul>
                {entry.changes.map((change, index) => (
                  <li key={`${change.memory_id}:${index}`}>
                    {changeLabels[change.operation]} {change.kind}:{" "}
                    {change.content || `memory ${change.memory_id}`}
                  </li>
                ))}
              </ul>
              {entry.sources.map((source) => (
                <p
                  className="reflection-note"
                  key={`${source.chat_id}:${source.message_id}`}
                >
                  {chats.find((chat) => chat.id === source.chat_id)?.title ||
                    "Conversation"}{" "}
                  · message {source.message_id}
                </p>
              ))}
            </details>
            {!!entry.tasks_created?.length && (
              <details>
                <summary>{entry.tasks_created.length} tasks created</summary>
                <ul>
                  {entry.tasks_created.map((task) => (
                    <li key={task.id}>
                      {task.title} · Suggested for{" "}
                      {task.suggested_assignee === "maestro"
                        ? "Maestro"
                        : "You"}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        </div>
      ))}
    </section>
  );
}
