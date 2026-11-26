/**
 * Emails the site wants sent. It has no mail of its own, so they wait
 * here until VME's next sync picks them up and sends them from Gmail
 * (backend/app/publish/notify.py), then acks them:
 *
 * - "request":  someone asked to join a team; goes to the admins.
 * - "approved": a request was approved; goes to the person who asked.
 *
 * A notice names a membership rather than carrying text, so what is
 * sent is current: a request already decided by the time VME sees it
 * is not announced, and an approval taken back is not sent.
 */
import type { Env } from "../shared/env";
import type { Membership, MembershipOut } from "../shared/api";
import { Names } from "./content";

export type NoticeKind = "request" | "approved";

export interface Notice {
  id: number;
  kind: NoticeKind;
  member: MembershipOut;
}

/** Old enough that nobody wants the email any more (VME was off). */
const MAX_AGE_MS = 14 * 24 * 3600 * 1000;

export async function queueNotice(env: Env, kind: NoticeKind, memberId: number): Promise<void> {
  await env.DB.prepare("INSERT INTO notices (kind, member_id, created_at) VALUES (?, ?, ?)")
    .bind(kind, memberId, Date.now())
    .run();
}

export async function pendingNotices(env: Env, names = new Names(env)): Promise<Notice[]> {
  await env.DB.prepare("DELETE FROM notices WHERE created_at < ?").bind(Date.now() - MAX_AGE_MS).run();
  const { results } = await env.DB.prepare(
    `SELECT n.id AS notice_id, n.kind AS notice_kind, m.*
       FROM notices n LEFT JOIN members m ON m.id = n.member_id
      ORDER BY n.id`
  ).all<Membership & { notice_id: number; notice_kind: NoticeKind }>();
  const out: Notice[] = [];
  const stale: number[] = [];
  for (const r of results) {
    const { notice_id, notice_kind, ...m } = r;
    const wanted = notice_kind === "request" ? "pending" : "approved";
    if (m.id == null || m.status !== wanted) {
      stale.push(notice_id);
      continue;
    }
    out.push({ id: notice_id, kind: notice_kind, member: await names.describe(m as Membership) });
  }
  await ackNotices(env, stale);
  return out;
}

export async function ackNotices(env: Env, ids: number[]): Promise<void> {
  const clean = ids.map(Number).filter(Number.isInteger);
  if (!clean.length) return;
  await env.DB.batch(clean.map((id) => env.DB.prepare("DELETE FROM notices WHERE id = ?").bind(id)));
}
