# Silver Review: VisionDemo Expense Status Filter

## Confirmed by inspection

- Only `02_画面設計書_経費精算申請一覧.xlsx` changed between the two 27-document directories.
- `画面項目一覧!G5` changed from `申請中` to `すべて`.
- `画面項目一覧!I5` added `差戻し` to the filter description.
- The program design says null or empty status must return all expenses.
- Base commit `ad23d0a...` already renders `すべて` and `差戻し` in the JSP.
- The backend query handles `NULL`, while the page sends an empty string for All.

## Required human decisions

- [ ] Business owner confirms that All is the initial and reset state.
- [ ] Business owner confirms that Returned must be selectable.
- [ ] Developer confirms the candidate code paths and acceptable normalization layer.
- [ ] QA confirms the three UI scenarios and test data requirement.
- [ ] Repository owner confirms `ad23d0a...` as the base revision for this case.
- [ ] Dataset owner replaces local document paths with immutable storage references or approved Git LFS content.

After all checks are complete, rename `.silver.json` files to `.json`, set `dataset_stage` to `golden`, record reviewers and freeze a dataset version.
