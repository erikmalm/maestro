import type { Route } from "@playwright/test";
import type { ContextArchive } from "../src/api";

const unconfiguredArchive: ContextArchive = {
  configured: false,
  available: false,
  database_path: "C:\\Synthetic Maestro fixture\\workspace.sqlite3",
  index_path: "C:\\Synthetic Maestro fixture\\context\\index.sqlite3",
  path: null,
  path_error: null,
  config: {
    enabled: false,
    capture_policy: "approved_sources",
    public_sources: [],
    reuse_hours: 24,
    max_bytes: 10737418240,
    max_items: 200000,
  },
  config_error: null,
  indexed_count: 0,
  archive_bytes: 0,
  archive_items: 0,
  missing_count: 0,
  corrupt_count: 0,
  last_indexed_at: null,
  last_capture: null,
  last_error: null,
};

export async function fulfillUnconfiguredArchiveRead(route: Route) {
  if (
    route.request().method() !== "GET" ||
    new URL(route.request().url()).pathname !== "/api/context"
  )
    return false;
  await route.fulfill({ json: unconfiguredArchive });
  return true;
}
