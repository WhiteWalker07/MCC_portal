/**
 * Shared document shapes for the engine.
 *
 * Ported from functions/src/types.ts. The one systemic change vs. the Firebase
 * version: Firestore `Timestamp` is replaced by native `Date` everywhere. Over
 * the wire (JSON) dates serialize to ISO strings; the client parses them back.
 */

export interface TaskType {
  task: string;
  requiredSkill: string;
  points: number;
  slaHours: number;
  atEvent: boolean;
  /** Can a committee request this role on the New Request form? */
  requestable?: boolean;
  /** Can staff (admin / team) add this task to a request manually? (internal-only) */
  internalAssignable?: boolean;
  /** Vertical this task belongs to (for domain-head scope); "" = unscoped. */
  vertical?: string;
}

export interface Settings {
  slaHours: number;
  strikeLimit: number;
  campusStrict: boolean;
  requireApprovalAlways: boolean;
  strikeAssigneeToo: boolean;
  secretaryEmails: string[];
  adminEmails?: string[];
  headEmail?: string;
  committeeName?: string;
  allowedDomains?: string[];
  defaultAcronym?: string;
  generalSeq?: number;
}

/** Scoring scheme (config/points) — base points + completion-timing modifiers. */
export interface PointsConfig {
  coordinatorPoints: number;
  domainTaskPoints: number;
  vetterPoints: number;
  earlyWindowHours: number;
  earlyBonusPct: number;
  lateThresholdHours: number;
  latePenaltyPct: number;
  subsequentDelayHours: number;
  subsequentPenaltyPct: number;
}

export interface PlatformRow {
  platform: string;
  handlerEmail: string;
  points: number;
  active: boolean;
}

export interface Committee {
  email: string;
  name: string;
  type: string;
  campus: string;
  acronym: string;
  lastSeq: number;
  logo?: string;
}

export interface TeamMember {
  /** Mongo `_id` (the member's email). */
  id?: string;
  name: string;
  email: string;
  skills: string[];
  strikes: number;
  points: number;
  active: boolean;
  campus: string;
  phone?: string;
  /** One of the 4 verticals: Photography | Videography | Graphic Designs | Content Writing */
  vertical?: string;
  /** Academic year: 1 or 2 (2nd-years can manually assign tasks). */
  year?: number;
  /** Vertical this member is the domain head of; "" if not a head. */
  domainHeadOf?: string;
  /** Work availability: "available" (on work) or "out" (out of work / break). */
  availability?: "available" | "out";
  /** When the current availability state began (for day counting). */
  availabilityChangedAt?: Date;
  /** Cumulative days spent on work / out of work (banked completed segments). */
  onWorkDays?: number;
  outDays?: number;
}

export interface RosterEntry {
  role: string;
  name: string;
  email: string;
  phone: string;
}

export interface RequestDoc {
  /** Mongo `_id` as a hex string (set when read back for the client). */
  id?: string;
  refCode?: string;
  type: "Coverage" | "Post";
  eventName: string;
  eventStart?: Date | null;
  eventEnd?: Date | null;
  venue?: string;
  requester?: string;
  contactEmail: string;
  campus?: string;
  rolesNeeded?: string[];
  platforms?: string[];
  contentLinks?: string;
  notes?: string;
  status: string;
  createdAt?: Date;
  coordinatorEmail?: string;
  roster?: RosterEntry[];
  posts?: Array<{
    platform: string;
    handlerEmail: string;
    scheduledAt: Date | null;
    status: string;
  }>;
}

/** A task in the pipeline before it's written as a `tasks` doc. */
export interface PipelineTask {
  task: string;
  requiredSkill: string;
  points: number;
  slaHours: number;
  atEvent: boolean;
  vertical: string;
  deadline: Date | null;
}
