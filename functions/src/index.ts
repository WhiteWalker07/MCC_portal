/**
 * =============================================================================
 * Media Committee Portal — Cloud Functions (engine entry point)
 * =============================================================================
 * This file is the single export surface Firebase deploys. `./lib/setup` is
 * imported FIRST so the Admin SDK is initialized and global options are set
 * before any trigger registers.
 *
 * Build phase map:
 *   Phase 5  — onRequestCreated        ✅
 *   Phase 6  — onTaskCompleted         ✅
 *   Phase 7  — onRequestDecided        ✅ (approve/reject + real Resend email)
 *   Phase 8  — onTaskReassign + assignment callables  ✅
 *   Phase 9  — importTeamCsv callable  ✅ (secretary/admin team import)
 *   Phase 10 — onReadyToPost + markReadyToPost  ✅
 *   Phase 11 — scheduledDeadlineCheck  ✅ (hourly LATE + strikes)
 *   Phase 12 — Calendar wiring         (replace stub with real free/busy+invites)
 * =============================================================================
 */

import "./lib/setup"; // init Admin SDK + global options before anything registers
import { onRequest } from "firebase-functions/v2/https";

/**
 * healthCheck — smoke test. Hit it at:
 *   http://127.0.0.1:5001/<project-id>/asia-south1/healthCheck
 */
export const healthCheck = onRequest((_req, res) => {
  res.json({
    ok: true,
    service: "mcc-portal-functions",
    phase: 11,
    time: new Date().toISOString(),
  });
});

export { onRequestCreated } from "./triggers/onRequestCreated";
export { onTaskCompleted } from "./triggers/onTaskCompleted";
export { onRequestDecided } from "./triggers/onRequestDecided";
export { onTaskReassign } from "./triggers/onTaskReassign";
export { onReadyToPost } from "./triggers/onReadyToPost";
export { scheduledDeadlineCheck } from "./triggers/scheduledDeadlineCheck";
export {
  getEligibleMembers,
  listAssignableTasks,
  requestReassign,
  assignTask,
  markReadyToPost,
} from "./callable/assignments";
export { importTeamCsv, listTeamMembers, setDomainHead, setPointScheme } from "./callable/team";
export { getDashboardStats } from "./callable/dashboard";
