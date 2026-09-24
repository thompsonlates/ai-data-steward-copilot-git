DQ Intelligence Source-Aware REGEX Execution
============================================

Replace the matching project files with the supplied copies:

Backend
-------
quality_profiler_repository.py -> app/repositories/quality_profiler_repository.py
google_sheets_routes.py        -> app/api/routes/google_sheets_routes.py
onedrive_routes.py             -> app/api/routes/onedrive_routes.py
schemas.py                     -> app/api/schemas.py

Frontend
--------
DqIntelligence.tsx -> src/components/intelligence/DqIntelligence.tsx
types.ts            -> src/api/types.ts
client.ts           -> src/api/client.ts

Behavior
--------
- Persists the tenant/profile-bound spreadsheet execution target in
  DQ_PROFILE_SOURCE_CONTEXT.
- Returns source context with approved recommendations.
- Locks spreadsheet REGEX execution to the connection used for profiling.
- Routes Google Sheets REGEX to execute-google-sheets.
- Routes OneDrive REGEX to execute-onedrive.
- Keeps SQL on its existing validate-execution then execute workflow.
- Performs spreadsheet REGEX safety preflight inside the execution call before
  any write, matching the current backend contract.

Important test note
-------------------
Existing approved recommendations created before this change do not have a
DQ_PROFILE_SOURCE_CONTEXT row. Re-profile Google Sheets and OneDrive test data,
generate the fix, and approve it again before testing execution in DQ
Intelligence.

Recommended verification
------------------------
Backend:
  python -m compileall app

Frontend:
  npm run build

Test Google Sheets and OneDrive only in DEV/STG. Confirm the connection shown
in DQ Intelligence is the persisted profiling connection and cannot be changed.
