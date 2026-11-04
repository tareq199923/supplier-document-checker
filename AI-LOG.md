# AI-LOG: how I used AI tools

I used an AI coding agent (Claude) for planning, tests and code, and I reviewed every step.
My rule: the agent makes a plan first, I approve it, tests come before code, and nobody edits a
test or `expected.json` to make something pass. This file lists mistakes the AI made and how I
caught them.

## Mistakes the AI made, and how they were caught

1. **"pytest must pass" with no tests.** An early plan said the tests must pass, but the tests
   folder was empty, so there was nothing to run. Caught by reading the plan against the folder.
2. **A live check before the code existed.** An early plan proposed a live extraction check before
   `extractor.py` existed. Caught in review; the order was fixed (tests, code, then live check).
3. **Expired approval tokens.** Tool approvals expired in the middle of a task and some writes had
   to be redone. Lesson: confirm each action promptly and check the file afterwards.
4. **A quoting error that looked like a code bug.** A one-line Python check failed because of
   PowerShell quotes, not because the code was wrong. Fixed by avoiding inner double quotes.
5. **The agent's tool shell was not PowerShell.** A command with `;` failed with "path not found"
   because the tool runs commands in `cmd.exe`. Fixed by using `cd /d ... && ...`. The test file
   itself was fine.
6. **Planning work that was already done, and nearly overwriting real tests.** The agent wrote a
   plan and new tests for Gate 2 (`validators.py`), but Gate 2 was already built, hardened and
   committed. Its staged write would have replaced my real `tests/test_validators.py` (29 KB) with
   a smaller one (19 KB). Caught before it was confirmed, because the agent checked `git status`,
   `git log` and the folder listing first. The approval had also expired, so nothing was lost.
   Lesson: read the current state of the repo before planning, and never trust a summary alone.
7. **A bug in the agent's own test helper.** A new test fixture tried to attach an attribute to a
   plain Python list, so 5 tests errored while 34 passed. Caught by running pytest straight away
   and fixed in the test helper only; no assertion was changed and the code was not touched.
8. **A memory claim checked against the file.** The agent said the Anthropic output cap was 2,000
   tokens. A code search found nothing, so it re-read `llm.py` before editing. The claim was right,
   and the cap was raised to 4,096 only after that check.

## Prompts that worked

(To be written by Tareq in his own words before submission: 3 to 4 prompts that gave good results.)
