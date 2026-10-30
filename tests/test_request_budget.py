from unittest.mock import patch
import pytest
from src.request_budget import RequestBudget,CURRENT
from src import live_collector


def test_budget_exhaustion_stops_new_requests_without_network():
    budget=RequestBudget(seconds=40,max_requests=1)
    assert budget.begin()<=8
    with pytest.raises(TimeoutError):
        budget.begin()
    token=CURRENT.set(budget)
    try:
        with patch.object(live_collector.urllib.request,"urlopen") as network:
            with pytest.raises(TimeoutError):
                live_collector._get("/never")
            network.assert_not_called()
    finally:
        CURRENT.reset(token)
