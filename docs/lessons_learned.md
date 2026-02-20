# Lessons Learned

Ongoing log of what worked and what didn't during development. Primarily intended as context for AI assistants to avoid repeating mistakes, but useful for anyone picking up the project.

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
