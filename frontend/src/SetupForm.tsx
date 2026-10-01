import { useState } from "react";

export function useSetupAction<T>(
  update: (status: T) => void,
  failureMessage: string,
) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function act(
    name: string,
    action: () => Promise<T>,
    message: string,
    recover?: () => Promise<T>,
  ) {
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
          update(await recover());
        } catch {
          // Keep the original error if refreshing the local status also fails.
        }
      }
    } finally {
      setBusy("");
    }
  }

  return { busy, error, notice, setError, setNotice, act };
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
