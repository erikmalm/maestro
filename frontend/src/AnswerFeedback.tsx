import { useEffect, useRef, useState } from "react";
import { MessageCircle, ThumbsDown, ThumbsUp, X } from "lucide-react";
import type { Message, MessageFeedback } from "./api";

export default function AnswerFeedback({
  feedback,
  disabled,
  onSave,
}: {
  feedback: Message["feedback"];
  disabled: boolean;
  onSave: (
    rating: MessageFeedback["rating"] | null,
    comment: string,
  ) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState(feedback?.comment ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);

  async function save(rating: MessageFeedback["rating"] | null, text = "") {
    setBusy(true);
    setError("");
    try {
      if ((await onSave(rating, text)) && active.current) {
        setOpen(false);
        setComment(text);
      }
    } catch (reason) {
      if (active.current)
        setError(
          reason instanceof Error ? reason.message : "Could not save feedback.",
        );
    } finally {
      if (active.current) setBusy(false);
    }
  }

  return (
    <div className="answer-feedback">
      <div className="feedback-actions">
        {(
          [
            ["positive", "Helpful", ThumbsUp],
            ["negative", "Needs improvement", ThumbsDown],
          ] as const
        ).map(([rating, label, Icon]) => (
          <button
            key={rating}
            type="button"
            className="icon-button"
            aria-label={label}
            title={label}
            aria-pressed={feedback?.rating === rating}
            disabled={disabled || busy || open}
            onClick={() => void save(rating, feedback?.comment ?? "")}
          >
            <Icon size={15} />
          </button>
        ))}
        {feedback && (
          <>
            <span className="feedback-saved">Saved</span>
            <button
              type="button"
              className="icon-button"
              aria-label={
                feedback.comment
                  ? "Edit feedback comment"
                  : "Add feedback comment"
              }
              title={
                feedback.comment
                  ? "Edit feedback comment"
                  : "Add feedback comment"
              }
              disabled={disabled || busy || open}
              onClick={() => {
                setComment(feedback.comment);
                setOpen(true);
              }}
            >
              <MessageCircle size={15} />
            </button>
            <button
              type="button"
              className="icon-button"
              aria-label="Clear feedback"
              title="Clear feedback"
              disabled={disabled || busy}
              onClick={() => void save(null)}
            >
              <X size={15} />
            </button>
          </>
        )}
      </div>
      {open && feedback && (
        <form
          className="feedback-form"
          onSubmit={(event) => {
            event.preventDefault();
            void save(feedback.rating, comment);
          }}
        >
          <label>
            Feedback comment (optional)
            <textarea
              rows={3}
              maxLength={4000}
              value={comment}
              disabled={disabled || busy}
              onChange={(event) => setComment(event.target.value)}
              placeholder="What worked, or what should Maestro improve?"
            />
          </label>
          <p className="form-note">Saved privately for periodic review.</p>
          <div className="reflection-actions">
            <button className="button secondary" disabled={disabled || busy}>
              Save feedback
            </button>
            <button
              type="button"
              className="button subtle"
              disabled={busy}
              onClick={() => setOpen(false)}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
      {error && (
        <p className="form-note" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
