/**
 * Calendar service — swappable behind an interface.
 *
 * Auto-selects at first use: if CALENDAR_SERVICE_ACCOUNT_JSON is set, a real
 * Google Calendar implementation is used (service account with domain-wide
 * delegation, impersonating each target member); otherwise a logging stub. So
 * the emulator and any unconfigured environment behave exactly as before.
 *
 * Setup (admin): see README "Google Calendar — domain-wide delegation". Grant the
 * service account the calendar + calendar.events scopes, then set
 * CALENDAR_SERVICE_ACCOUNT_JSON (path or inline JSON) in the functions env.
 */

import * as fs from "fs";
import { Timestamp } from "firebase-admin/firestore";
import { google, calendar_v3 } from "googleapis";

const TZ = "Asia/Kolkata";
const SCOPES = [
  "https://www.googleapis.com/auth/calendar",
  "https://www.googleapis.com/auth/calendar.events",
];

export interface CalendarService {
  isFree(email: string, start: Timestamp, end: Timestamp): Promise<boolean>;
  createHold(opts: {
    email: string;
    title: string;
    start: Timestamp;
    end: Timestamp;
    description?: string;
  }): Promise<void>;
  createReminder(opts: {
    email: string;
    title: string;
    due: Timestamp;
    description?: string;
  }): Promise<void>;
}

class StubCalendarService implements CalendarService {
  async isFree(): Promise<boolean> {
    return true;
  }
  async createHold(opts: { email: string; title: string }): Promise<void> {
    console.log(`[calendar:STUB] hold -> ${opts.email} | ${opts.title}`);
  }
  async createReminder(opts: { email: string; title: string }): Promise<void> {
    console.log(`[calendar:STUB] reminder -> ${opts.email} | ${opts.title}`);
  }
}

interface SaCreds {
  client_email: string;
  private_key: string;
}

function loadCreds(envVal: string): SaCreds {
  const trimmed = envVal.trim();
  const raw = trimmed.startsWith("{") ? trimmed : fs.readFileSync(trimmed, "utf8");
  const json = JSON.parse(raw);
  return { client_email: json.client_email, private_key: json.private_key };
}

class GoogleCalendarService implements CalendarService {
  constructor(private readonly creds: SaCreds) {}

  private clientFor(userEmail: string): calendar_v3.Calendar {
    const auth = new google.auth.JWT({
      email: this.creds.client_email,
      key: this.creds.private_key,
      scopes: SCOPES,
      subject: userEmail, // impersonate the member (domain-wide delegation)
    });
    return google.calendar({ version: "v3", auth });
  }

  async isFree(email: string, start: Timestamp, end: Timestamp): Promise<boolean> {
    try {
      const cal = this.clientFor(email);
      const res = await cal.freebusy.query({
        requestBody: {
          timeMin: start.toDate().toISOString(),
          timeMax: end.toDate().toISOString(),
          items: [{ id: "primary" }],
        },
      });
      const busy = res.data.calendars?.["primary"]?.busy || [];
      return busy.length === 0;
    } catch (err) {
      // Fail-open: don't block assignment because of a calendar error.
      console.error(`[calendar] isFree(${email}) failed:`, (err as Error).message);
      return true;
    }
  }

  async createHold(opts: {
    email: string;
    title: string;
    start: Timestamp;
    end: Timestamp;
    description?: string;
  }): Promise<void> {
    try {
      await this.clientFor(opts.email).events.insert({
        calendarId: "primary",
        requestBody: {
          summary: opts.title,
          description: opts.description || "",
          start: { dateTime: opts.start.toDate().toISOString(), timeZone: TZ },
          end: { dateTime: opts.end.toDate().toISOString(), timeZone: TZ },
        },
      });
    } catch (err) {
      console.error(`[calendar] createHold(${opts.email}) failed:`, (err as Error).message);
    }
  }

  async createReminder(opts: {
    email: string;
    title: string;
    due: Timestamp;
    description?: string;
  }): Promise<void> {
    try {
      const due = opts.due.toDate();
      const end = new Date(due.getTime() + 30 * 60000);
      await this.clientFor(opts.email).events.insert({
        calendarId: "primary",
        requestBody: {
          summary: opts.title,
          description: opts.description || "",
          start: { dateTime: due.toISOString(), timeZone: TZ },
          end: { dateTime: end.toISOString(), timeZone: TZ },
        },
      });
    } catch (err) {
      console.error(`[calendar] createReminder(${opts.email}) failed:`, (err as Error).message);
    }
  }
}

let cached: CalendarService | null = null;

function service(): CalendarService {
  if (cached) return cached;
  const credsEnv = process.env.CALENDAR_SERVICE_ACCOUNT_JSON;
  if (credsEnv) {
    try {
      cached = new GoogleCalendarService(loadCreds(credsEnv));
      console.info("[calendar] using Google Calendar");
    } catch (err) {
      console.error("[calendar] config error, falling back to stub:", (err as Error).message);
      cached = new StubCalendarService();
    }
  } else {
    cached = new StubCalendarService();
  }
  return cached;
}

export const calendarService: CalendarService = {
  isFree: (email, start, end) => service().isFree(email, start, end),
  createHold: (opts) => service().createHold(opts),
  createReminder: (opts) => service().createReminder(opts),
};
