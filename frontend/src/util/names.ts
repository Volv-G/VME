/**
 * Display helpers for path-segment names.
 *
 * Team / tournament / match folder names are sanitized on the backend
 * (`safe_segment` in `paths.py`): whitespace becomes `_` so the folders are
 * safe on Windows + Unix. The API returns the sanitized form, but humans
 * prefer to read it with spaces. URLs and API calls must keep the raw name.
 */
export function displayName(name: string): string {
  return name.replace(/_/g, " ");
}
