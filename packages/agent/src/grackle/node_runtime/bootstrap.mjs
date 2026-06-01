// grackle Node trace bootstrap (ADR-0022).
//
// Spawned as: node --inspect-brk=127.0.0.1:0 [--experimental-strip-types]
//                  bootstrap.mjs <abs target .ts/.js>
//
// `--inspect-brk` pauses Node before any user code runs, so the agent can
// attach over CDP and start the profiler before releasing execution. This shim
// then imports the user's script and signals completion so the agent can stop
// the profiler *before* the process exits.
//
// Sentinels go to stderr — the same channel the agent already reads for the
// "Debugger listening on ws://..." line. Each is one line prefixed with a NUL
// byte (String.fromCharCode(0)) so it cannot collide with ordinary output:
//   <NUL>GRACKLE_ERROR <message>   — the target threw (still followed by DONE)
//   <NUL>GRACKLE_DONE              — the target's top-level evaluation finished
//
// After DONE we hold the event loop open with a timer so the agent has a window
// to collect the profile and detach; the agent terminates this process once it
// has the data. The timer is a safety net if the agent never does.

import { pathToFileURL } from "node:url";

// NUL sentinel prefix, built at runtime so the source carries no literal NUL.
const NUL = String.fromCharCode(0);
const target = process.argv[2];

// Write a single-line GRACKLE_ERROR sentinel for `err`.
function reportError(err) {
  const raw = err?.stack ? err.stack : String(err);
  // Flatten newlines (the agent reads one line per sentinel), strip any NUL
  // bytes (the sentinel prefix is NUL-delimited), and bound the length so a huge
  // stack cannot produce a pathologically long stderr line.
  const flat = raw.replace(/[\r\n]+/g, " ").replaceAll(NUL, " ");
  process.stderr.write(`${NUL}GRACKLE_ERROR ${flat.slice(0, 4000)}\n`);
}

// Errors thrown AFTER the top-level `await import()` resolves — from a timer, a
// microtask, or an unhandled promise rejection — escape the try/catch below and
// would otherwise be silently dropped (DONE has already fired, so the agent sees
// a clean trace). Capture them here so they still surface as an exception event.
// Best-effort: the agent normally terminates the process shortly after DONE, so a
// late async error only reports if it fires before teardown. Installing these
// handlers also suppresses Node's default crash-on-uncaught, which is fine — the
// finally's safety-net timer still bounds the process.
process.on("uncaughtException", reportError);
process.on("unhandledRejection", reportError);

try {
  // Import by file URL so absolute Windows paths (drive letters, backslashes)
  // and POSIX paths both resolve. A `.ts` target is type-stripped by Node.
  await import(pathToFileURL(target).href);
} catch (err) {
  reportError(err);
} finally {
  process.stderr.write(`${NUL}GRACKLE_DONE\n`);
  // Keep the process alive briefly so the agent can stop the profiler and
  // detach cleanly. The agent normally terminates us well before this fires.
  setTimeout(() => process.exit(0), 30000);
}
