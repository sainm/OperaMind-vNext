# Silver Review: VisionDemo Expense / Employee Cross-Screen Flow

## Confirmed by source inspection

- The expense create screen collects employee, date, description and details, then calls `POST /expense/api/save`.
- The screen does not submit `expenseNo`, while both the entity and database require a non-null unique value.
- The employee list exposes an edit modal and saves the employee through `POST /employee/api/save`.
- The expense list renders `expense.employee.name`, so a later query should observe the current employee name.
- Both expense and employee APIs provide DELETE endpoints suitable for failure cleanup in an isolated test environment.

## Observed in isolated E2E on 2026-07-18

- The unmodified base failed at expense creation because `expenseNo` was null.
- After fixing number assignment, employee edit still failed because the detail API serialized a Hibernate proxy.
- After returning an edit-screen DTO, the default expense list still returned no rows because blank status was not normalized.
- With all three isolated fixes, the browser flow displayed `EXP-20260719-0C60E2A`, the updated employee name, `¥12,345`, and `申請中` in one row.
- Cleanup deleted the expense before the employee and restored the isolated database to four seeded expenses and zero OperaMind test employees.
- Evidence: `readiness/evidence/visiondemo-cross-screen-e2e-20260718.json`.

## Required human decisions

- [ ] Business owner confirms that an expense number must be assigned automatically.
- [ ] Business owner confirms that the expense list should show the latest employee master name.
- [ ] Developer confirms the number format and collision policy.
- [ ] Developer confirms the proposed modification boundary.
- [ ] QA confirms the four-step cross-screen flow and cleanup ordering.
- [ ] Repository owner confirms base revision `ad23d0a7a54ce196c0ea6c41445e5f5492ae1ea6`.

This case remains Silver. It must not be promoted to Golden until the above decisions are recorded.
