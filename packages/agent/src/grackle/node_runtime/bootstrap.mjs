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

try {
  // Import by file URL so absolute Windows paths (drive letters, backslashes)
  // and POSIX paths both resolve. A `.ts` target is type-stripped by Node.
  await import(pathToFileURL(target).href);
} catch (err) {
  const raw = err?.stack ? err.stack : String(err);
  // Flatten newlines (the agent reads one line per sentinel), strip any NUL
  // bytes (the sentinel prefix is NUL-delimited), and bound the length so a huge
  // stack cannot produce a pathologically long stderr line.
  const flat = raw.replace(/[\r\n]+/g, " ").replaceAll(NUL, " ");
  process.stderr.write(`${NUL}GRACKLE_ERROR ${flat.slice(0, 4000)}\n`);
} finally {
  process.stderr.write(`${NUL}GRACKLE_DONE\n`);
  // Keep the process alive briefly so the agent can stop the profiler and
  // detach cleanly. The agent normally terminates us well before this fires.
  setTimeout(() => process.exit(0), 30000);
}
