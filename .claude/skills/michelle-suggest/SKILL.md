---
name: michelle-suggest
description: Propose new Michelle test cases for a feature or area, without modifying any cases. Use when the user wants AI suggestions for what to test, before generating from a full PRD.
allowed-tools: [Bash, Read]
---

# /michelle-suggest — Propose test cases

Light-touch alternative to a full PRD upload. Useful for "what edge cases am I missing for the login flow?"

## Args

`$ARGUMENTS` — natural-language description of the feature/area, e.g.
`"loading multiple files into the storage console"`.

## Steps

1. POST to the suggest endpoint:
   ```
   curl -s -X POST http://localhost:8000/api/cases/suggest \
     -H 'Content-Type: application/json' \
     -d '{"description": "<args>", "max_cases": 8}'
   ```
2. The backend uses Claude (with project conventions in context) to produce a
   draft list. **Nothing is saved yet** — these are returned for review only.
3. Print as a numbered list, grouped into:
   - Happy path
   - Edge cases
   - Error / negative
   - Security-sensitive (if applicable)
4. Ask: "Which to keep? (e.g. 1,3,5 or 'all')" — then POST `/api/cases` to persist
   selected ones with `review_status=pending`.

## Why useful

- Faster than uploading a PRD chapter
- Returns *thinking partner* output, not authoritative cases — review-friendly
- Persisted cases enter the same Review workflow as PRD-generated ones
