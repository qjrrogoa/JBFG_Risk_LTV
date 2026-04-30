const DEFAULT_LOCAL_API_BASE_URL = "http://127.0.0.1:8000";

export function normalizeApiBaseUrl(rawValue) {
  const value = String(rawValue || "").trim().replace(/\/+$/, "");
  if (!value) return "";
  if (value.startsWith("http://") || value.startsWith("https://")) return value;
  return `https://${value}`;
}

export const API_BASE_URL = normalizeApiBaseUrl(
  import.meta.env.VITE_API_BASE_URL || (import.meta.env.DEV ? DEFAULT_LOCAL_API_BASE_URL : "")
);
