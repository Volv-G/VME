/** Shapes the API returns besides the published documents (docs.ts). */

export type Role = "player" | "coach";
export type MemberStatus = "pending" | "approved" | "declined";

export interface Membership {
  id: number;
  email: string;
  team: string;
  role: Role;
  number: number | null;
  note: string;
  status: MemberStatus;
  requested_at: number;
  decided_at: number | null;
  decided_by: string | null;
}

/** A membership with the names a person reads. */
export interface MembershipOut extends Membership {
  team_name: string;
  player_name: string;
}

export interface Me {
  email: string;
  admin: boolean;
  /** In ADMIN_EMAILS: can't be removed as an admin on the site. */
  owner?: boolean;
  memberships: MembershipOut[];
  /** Admins only: requests waiting for a decision. */
  pending: number;
}

export interface JoinTeam {
  slug: string;
  name: string;
  color: string;
  logo: string | null;
  players: { number: number; name: string }[];
}

export interface Invite {
  team: string;
  team_name: string;
  code: string;
}

/** Someone made an admin on /admin (owners are in ADMIN_EMAILS instead). */
export interface AddedAdmin {
  email: string;
  added_at: number;
  added_by: string;
}

export interface AdminState {
  /** ADMIN_EMAILS: admins nobody can remove here. */
  owners: string[];
  /** Made admins on the site, from the people already approved. */
  admins: AddedAdmin[];
  invites: Invite[];
  requests: MembershipOut[];
  members: MembershipOut[];
  declined: MembershipOut[];
}

export interface PlayerInfo {
  team: string;
  number: number;
  name: string;
  short_name: string;
  photo: string | null;
  /** A change made here that VME has not picked up yet. */
  pending: { name: string | null; short_name: string | null; photo: boolean; submitted_at: number } | null;
}

export type Decision = "approve" | "decline" | "remove";
