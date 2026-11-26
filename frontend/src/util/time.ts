/** "18:30" -> "6:30 PM" (or however this machine writes times). */
export function formatTime(hhmm: string | null | undefined): string {
  if (!hhmm) return "";
  const [h, m] = hhmm.split(":").map(Number);
  const d = new Date(2000, 0, 1, h, m);
  return isNaN(d.getTime()) ? hhmm : d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
