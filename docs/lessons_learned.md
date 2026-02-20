# Lessons Learned

Ongoing log of what worked and what didn't during development. Primarily intended as context for AI assistants to avoid repeating mistakes, but useful for anyone picking up the project.

---

## Chromium/Brave MJPEG `<img>` behavior differs from Firefox

**Lesson:** MJPEG streams in `<img>` tags behave differently across browsers. Firefox fires `onload` per MJPEG frame. Chromium/Brave fires `onload` once on initial load, then never again. Chromium/Brave also fires `onerror` spuriously (e.g. when the stream is healthy). A browser-agnostic approach (server-side watchdog polling) is required for reliable disconnect/reconnect detection.

**Example (Sprint 2):** Initial MJPEG disconnect detection relied on `onerror` events, which worked in Firefox but produced false positives in Chromium/Brave. Replaced with a periodic server watchdog (8s interval) that checks server availability via `/api/settings`.

**Rule:** Don't rely on browser-specific MJPEG `<img>` events. Use a server health check for stream state detection.

---

## Foscam CGI `setVideoStreamParam` requires all parameters together

**Lesson:** The Foscam R2 CGI command `setVideoStreamParam` silently ignores requests that don't include both `streamType` AND all parameters together. Sending only the changed parameter does nothing — the camera simply doesn't apply it.

**Example (Sprint 2):** Attempts to change only the bitrate via `setVideoStreamParam&bitRate=4194304` were silently ignored. The fix reads all current values first (`getVideoStreamParam`), applies the override, then sends the complete parameter set.

**Rule:** Always read current camera values first, then send a complete parameter set for CGI commands that control multiple related settings.

---

## Foscam CGI bitRate is bits/second, ONVIF reports kbps

**Lesson:** The Foscam CGI API expects `bitRate` in **bits per second** (e.g. `4194304` for 4 Mbps). The ONVIF API reports the same value in **kilobits per second** (e.g. `4096`). These are different units and confusing them will produce wrong results.

**Rule:** When converting between CGI and ONVIF values, remember: CGI bps = ONVIF kbps × 1024.

---

## Server-side template rendering, not file generation

**Lesson:** Generating an HTML file on disk (with credentials baked in) is the wrong approach. It creates a file with sensitive data, requires cleanup, and complicates git-ignoring. The correct approach is server-side rendering: read the template, substitute values in memory, serve the result per request. No file on disk, no credentials in static files.

**Example (Sprint 2):** The original `generate_viewer()` function wrote `nerdcam.html` to disk with credentials embedded. Replaced with in-memory rendering in `server.py` — the template is read once per request, credentials are injected, and the result is served directly.

**Rule:** Never write credentials or sensitive config to generated files. Use server-side rendering with in-memory substitution.

---

## HEAD requests need explicit handling in http.server

**Lesson:** Python's `SimpleHTTPRequestHandler` only handles `HEAD` for static files it serves from disk. API routes handled in `do_GET` don't automatically get `HEAD` support. Browsers and monitoring tools send `HEAD` requests to check endpoint availability, and these will return 404 if not explicitly handled.

**Example (Sprint 2):** The `_checkServer` function in the web viewer sent HEAD requests to `/api/settings` to check server availability, but the server returned 404 because `do_HEAD` wasn't implemented for API routes. Fixed by adding a `do_HEAD` handler that dispatches to the same route logic.

**Rule:** When adding API endpoints to `http.server`, also handle `HEAD` requests if clients might probe for availability.

---

## Never set `img.src = ""` — use `removeAttribute("src")`

**Lesson:** Setting `img.src = ""` causes the browser to make a request to the current page URL (treating "" as a relative URL). In Firefox, this triggers an XML parsing error because it tries to parse the HTML page as XML. Use `img.removeAttribute("src")` instead to clear an image element without side effects.

**Example (Sprint 2):** Clearing the MJPEG `<img>` on server disconnect used `img.src = ""`, which caused `XML Parsing Error: not well-formed` in Firefox console. Switching to `removeAttribute("src")` eliminated that specific error, though a related Firefox XML error persists from another source (unresolved).

**Rule:** To clear an `<img>` element, use `removeAttribute("src")`, never `src = ""`.

---

---

## All written output must be in English — no exceptions

**Lesson:** The user communicates in Dutch, but every file written to disk must be in English. This includes plans, docs, summaries, comments, code, and commit messages. No Dutch documents, even if they are "user-friendly summaries."

**Example (Sprint 1):** A Dutch summary of the Sprint 1 plan (`samenvatting_sprint1_plan.md`) was created alongside the English plan. This violated the English-only rule and added a redundant document that duplicated information already in the English plan.

**Rule:** Never write Dutch to files. If the user asks for a summary or explanation, provide it in conversation (Dutch is fine there), but any file that gets created must be in English.

---

## Inventory the full project before acting

**Lesson:** At the start of a session, check what actually exists in the project — not just the files mentioned in instructions. Use `ls` on key directories (`claude_plans/`, `archive/`, `docs/`, root). Discovering files mid-session that should have been handled at the start wastes time and erodes trust.

**Example (Sprint 1 closure):** The Sprint 1 plan and todo were archived, but a Dutch summary file (`samenvatting_sprint1_plan.md`) was still sitting in `claude_plans/`. It was only discovered when the user pointed it out, because the session started without listing the directory contents.

**Rule:** At session start, after reading AI_INSTRUCTIONS.md and the task tracker, list the contents of `claude_plans/`, `docs/`, and `archive/` to know the full picture. Don't rely solely on what the docs say should be there.

---

## When archiving a tracker, create its replacement immediately

**Lesson:** The project should always have an active daily task tracker (`todo_YYYY-MM-DD.md`) in the root when there is work to do. Archiving the old one without creating a new one leaves a gap.

**Example (Sprint 1 closure):** The 2026-02-20 todo was archived as part of Sprint 1 closure, but no replacement was created for Sprint 2 work. The user had to point this out.

**Rule:** When archiving a todo file, immediately create the new one with the current/upcoming tasks. One action, not two separate steps.

---

## Don't put sprint-specific status in AI_INSTRUCTIONS.md

**Lesson:** AI_INSTRUCTIONS.md holds general rules and architectural context. Sprint status and current priorities belong in `roadmap.md`, current tasks in `todo_YYYY-MM-DD.md`. Don't duplicate this in AI_INSTRUCTIONS.md — it creates a second source of truth that goes stale.

**Example (Sprint 1 closure):** The "Current Priority" section and "Modularity" principle were updated with Sprint 2-specific references. The user corrected this — AI_INSTRUCTIONS.md should point to `roadmap.md` for current status, not contain it.

**Rule:** AI_INSTRUCTIONS.md says what is always true. Roadmap says what is current. Todo says what is today. One source of truth per concern.

---

## Keep archive simple — flat folder with date prefix

**Lesson:** The archive is just a folder with date-prefixed files. Don't create subfolders or invent organizational schemes that aren't in the instructions.

**Example (Sprint 1 closure):** An attempt was made to create an `archive/daily-schedules/` subfolder for todo files. The instructions say `archive/` with date prefix — nothing about subfolders.

**Rule:** Follow the archive rules literally: `archive/YYYY-MM-DD_filename.md`. No subfolders.

---

## Don't assume scope limits the user didn't state

**Lesson:** When the user asks for work to be done, plan for all of it in the current session unless they explicitly say to defer something.

**Example (Sprint 2 planning):** The initial todo split Sprint 2 work into "today" and "deferred to next session." The user asked why — they wanted everything planned for today.

**Rule:** Plan all work for the current session. If it turns out to be too much, the user will say so. Don't pre-decide what gets deferred.

---

## Keep docs up to date after every change

**Lesson:** After any structural or behavioral change, verify that all related docs still reflect reality. Don't wait for a big cleanup session — stale docs are worse than no docs.

**Example (Sprint 1 closure):** The `stream-debugger.md` agent still referenced the RTSP relay as a Sprint 2 item after it was moved to Sprint 3. The `concepts/concept.md` still said "no formal sprints" after the project had been using sprints for weeks. Both were caught by the doc-keeper agent, but should have been caught during the roadmap update.

**Rule:** When updating roadmap or plans, also check agent instructions and concept docs for stale references. Use the doc-keeper agent proactively.

---
