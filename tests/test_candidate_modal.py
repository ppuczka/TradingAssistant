from datetime import UTC, datetime

import pytest
from textual.widgets import Button, DataTable, Input, Select, Static

from trading_assistant.application.dashboard import EmptyDashboardService
from trading_assistant.domain.candidates import CandidateBatch, StockCandidate
from trading_assistant.tui.candidate_modal import CandidateModal
from trading_assistant.tui.dashboard import DashboardApp


class FakeCandidates:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def fetch(self, markets, limit):
        self.calls.append((markets, limit))
        if self.fail:
            raise RuntimeError("private provider detail")
        now = datetime.now(UTC)
        return CandidateBatch(
            candidates=[
                StockCandidate(
                    symbol="TEST.WA",
                    name="Synthetic company",
                    market="PL",
                    exchange="WSE",
                    currency="PLN",
                    price="25.50",
                    market_cap="100000000",
                    average_volume="20000",
                    quote_as_of=now,
                    fetched_at=now,
                )
            ],
            excluded_owned=2,
            rejected=1,
            warnings=["Synthetic warning"],
            errors={"US": "Yahoo unavailable"},
        )


@pytest.mark.parametrize("size", [(80, 24), (140, 45)])
async def test_candidate_navigation_explicit_fetch_and_partial_results(size):
    service = FakeCandidates()
    factories = []

    def factory():
        factories.append(True)
        return service

    app = DashboardApp(EmptyDashboardService(), candidate_factory=factory)
    async with app.run_test(size=size) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("s")
        assert isinstance(app.screen, CandidateModal)
        assert service.calls == []
        screen = app.screen
        screen.query_one("#candidate-market", Select).value = "PL"
        screen.query_one("#candidate-limit", Input).value = "5"
        await pilot.pause()
        screen.query_one("#candidate-fetch", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert service.calls == [(["PL"], 5)]
        assert screen.query_one(DataTable).row_count == 1
        assert str(screen.query_one(DataTable).get_row_at(0)[2]) == "TEST.WA"
        assert "2 owned excluded" in str(screen.query_one("#candidate-status", Static).render())
        details = str(screen.query_one("#candidate-details", Static).render())
        assert "US: Yahoo unavailable" in details
        assert "Synthetic warning" in details
        await pilot.press("escape")
        assert app.screen is app.home_screen
        await pilot.press("s")
        assert len(factories) == 1
        assert len(service.calls) == 1


async def test_candidate_invalid_input_failure_retry_and_shortcut_isolation():
    service = FakeCandidates()
    app = DashboardApp(EmptyDashboardService(), candidate_factory=lambda: service)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("s")
        screen = app.screen
        limit = screen.query_one(Input)
        limit.value = "0"
        screen.query_one("#candidate-fetch", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert not service.calls
        assert "1 and 100" in str(screen.query_one("#candidate-status", Static).render())
        limit.focus()
        await pilot.press("a", "q", "p", "s")
        assert app.screen is screen
        assert app.is_running
        limit.value = "10"
        service.fail = True
        screen.query_one("#candidate-fetch", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        status = str(screen.query_one("#candidate-status", Static).render())
        assert "Retry" in status
        assert "private provider detail" not in status
        assert not screen.running
        service.fail = False
        screen.query_one("#candidate-fetch", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert screen.query_one(DataTable).row_count == 1


async def test_select_nonleading_rows_passes_only_selected_candidates_and_preserves_on_back():
    from trading_assistant.analysis_config import AnalysisSettings
    from trading_assistant.tui.opportunity_modal import OpportunityModal

    class Pool(FakeCandidates):
        async def fetch(self, markets, limit):
            batch = await super().fetch(markets, limit)
            template = batch.candidates[0]
            batch.candidates = [
                template.model_copy(update={"symbol": symbol})
                for symbol in ("FIRST.WA", "SECOND.WA", "THIRD.WA")
            ]
            return batch

    factories = []

    def analysis_factory():
        factories.append(True)
        return object(), AnalysisSettings(quick_model="quick", deep_model="deep")

    app = DashboardApp(
        EmptyDashboardService(), candidate_factory=Pool, analysis_factory=analysis_factory
    )
    async with app.run_test(size=(140, 55)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("s")
        screen = app.screen
        screen.query_one("#candidate-fetch", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        button = screen.query_one("#candidate-ai", Button)
        assert button.disabled
        screen.ai_review()
        assert not factories
        table = screen.query_one(DataTable)
        table.focus()
        await pilot.press("down", "space", "down", "enter")
        assert screen.selected_symbols == {"SECOND.WA", "THIRD.WA"}
        assert str(table.get_row_at(0)[0]) == "[ ]"
        assert str(table.get_row_at(1)[0]) == "[x]"
        assert "2 selected" in str(screen.query_one("#candidate-selection", Static).render())
        await pilot.press("space")
        assert screen.selected_symbols == {"SECOND.WA"}
        button.press()
        await pilot.pause()
        assert isinstance(app.screen, OpportunityModal)
        assert [c.symbol for c in app.screen.candidates] == ["SECOND.WA"]
        assert app.screen.query_one("#opportunity-limit", Input).value == "1"
        assert len(factories) == 1
        await pilot.press("escape")
        assert app.screen is screen
        assert screen.selected_symbols == {"SECOND.WA"}


async def test_selection_limit_and_refetch_clears_selection_even_on_failure():
    class Pool(FakeCandidates):
        async def fetch(self, markets, limit):
            batch = await super().fetch(markets, limit)
            template = batch.candidates[0]
            batch.candidates = [
                template.model_copy(update={"symbol": f"TEST{i}.WA"}) for i in range(11)
            ]
            return batch

    service = Pool()
    app = DashboardApp(EmptyDashboardService(), candidate_factory=lambda: service)
    async with app.run_test(size=(140, 55)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("s")
        screen = app.screen
        screen.query_one("#candidate-fetch", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        table = screen.query_one(DataTable)
        table.focus()
        for index in range(11):
            table.move_cursor(row=index)
            await pilot.press("space")
        assert len(screen.selected_symbols) == 10
        assert "TEST10.WA" not in screen.selected_symbols
        table.move_cursor(row=0)
        await pilot.press("space")
        table.move_cursor(row=10)
        await pilot.press("space")
        assert len(screen.selected_symbols) == 10
        assert "TEST10.WA" in screen.selected_symbols
        service.fail = True
        screen.query_one("#candidate-fetch", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert screen.selected_symbols == set()
        assert table.row_count == 0
        assert screen.query_one("#candidate-ai", Button).disabled
