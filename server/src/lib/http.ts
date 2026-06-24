/**
 * HTTP helpers — a typed error the central handler renders, and an async wrapper
 * so thrown errors in async route handlers reach the error middleware (Express 4
 * does not catch async rejections on its own).
 *
 * Maps the old Firebase HttpsError codes to status codes so ported callable logic
 * reads almost identically.
 */

import { Request, Response, NextFunction, RequestHandler } from "express";

export class HttpError extends Error {
  status: number;
  expose = true;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const CODE_STATUS: Record<string, number> = {
  unauthenticated: 401,
  "permission-denied": 403,
  "not-found": 404,
  "invalid-argument": 400,
  "failed-precondition": 400,
  "already-exists": 409,
};

/** Throw with a Firebase-style code, e.g. httpError("not-found", "..."). */
export function httpError(code: keyof typeof CODE_STATUS, message: string): HttpError {
  return new HttpError(CODE_STATUS[code] || 400, message);
}

export function asyncHandler(fn: (req: Request, res: Response, next: NextFunction) => Promise<unknown>): RequestHandler {
  return (req, res, next) => {
    fn(req, res, next).catch(next);
  };
}
