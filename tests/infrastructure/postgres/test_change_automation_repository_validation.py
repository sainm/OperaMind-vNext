from typing import Any, cast

import pytest

from operamind.infrastructure.postgres import ChangeAutomationRepository


@pytest.mark.parametrize("limit", [0, 501])
def test_coordinator_candidate_query_rejects_unbounded_limits(limit: int) -> None:
    repository = ChangeAutomationRepository(cast(Any, object()))

    with pytest.raises(ValueError, match="candidate limit"):
        repository.list_coordinator_candidates(limit=limit)
