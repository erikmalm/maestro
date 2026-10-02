import { useRef, useState } from "react";

function unsaved<T extends object>(draft: Partial<T>, saved: T): Partial<T> {
  const changes = { ...draft };
  for (const key of Object.keys(draft) as (keyof T)[]) {
    if (Object.is(draft[key], saved[key])) delete changes[key];
  }
  return changes;
}

export function useSetupForm<T extends { config: object }>(
  initialStatus: T,
  onChange: (status: T) => void,
  failureMessage: string,
) {
  const source = useRef(initialStatus);
  const [status, setStatus] = useState(initialStatus);
  const [draft, setDraft] = useState<Partial<T["config"]>>({});
  const [apiKey, setApiKey] = useState("");
  const [persist, setPersist] = useState(true);
  const submitted = useRef<T["config"] | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const config = { ...(status.config as T["config"]), ...draft };

  if (source.current !== initialStatus) {
    source.current = initialStatus;
    setStatus(initialStatus);
    if (!submitted.current) setDraft(unsaved(draft, initialStatus.config));
  }

  function setConfig(next: Partial<T["config"]>) {
    const pending = submitted.current !== null;
    setNotice("");
    setDraft((previous) => {
      const changes = { ...previous, ...unsaved(next, config) };
      return pending ? changes : unsaved(changes, status.config);
    });
  }

  function update(next: T, confirmed = true) {
    const saved = confirmed ? submitted.current : null;
    const latest = source.current === initialStatus ? next : source.current;
    if (latest === next) setStatus(next);
    setDraft((previous) => unsaved(previous, saved ?? latest.config));
    if (saved) submitted.current = next.config;
    onChange(next);
  }

  async function act(
    name: string,
    action: () => Promise<T>,
    message: string,
    recover?: () => Promise<T>,
  ) {
    submitted.current = name === "save" || name === "test" ? config : null;
    setApiKey("");
    setBusy(name);
    setError("");
    setNotice("");
    try {
      update(await action());
      setNotice(message);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : failureMessage);
      if (recover) {
        try {
          update(await recover(), false);
        } catch {
          // Keep the original error if refreshing the local status also fails.
        }
      }
    } finally {
      submitted.current = null;
      setBusy("");
    }
  }

  return {
    status,
    config,
    setStatus,
    setConfig,
    update,
    apiKey,
    setApiKey,
    persist,
    setPersist,
    dirty: Object.keys(draft).length > 0 || apiKey.length > 0,
    busy,
    error,
    notice,
    setError,
    setNotice,
    act,
  };
}

type NumericKey<T> = {
  [K in keyof T]: T[K] extends number ? K : never;
}[keyof T];

export function NumberFields<T extends object>({
  fields,
  values,
  onChange,
  step = 1,
}: {
  fields: readonly (readonly [NumericKey<T>, string, number, number])[];
  values: T;
  onChange: (key: NumericKey<T>, value: number) => void;
  step?: number | "any";
}) {
  return (
    <>
      {fields.map(([key, label, min, max]) => (
        <label key={String(key)}>
          {label}
          <input
            required
            type="number"
            min={min}
            max={max}
            step={step}
            value={Number(values[key])}
            onChange={(event) => onChange(key, Number(event.target.value))}
          />
        </label>
      ))}
    </>
  );
}
