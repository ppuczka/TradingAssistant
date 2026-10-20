"""Shared presentation of engine ratings and application recommendation actions."""

from textual.widgets import Collapsible, Static


class RatingLegend(Collapsible):
    DEFAULT_CSS = """
    RatingLegend { height: auto; margin-bottom: 1; }
    RatingLegend Static { height: auto; }
    """

    def __init__(self):
        super().__init__(
            Static(
                "Buy — Strong positive view; consider buying or adding.\n"
                "Overweight — Positive view; consider greater exposure.\n"
                "Hold — Neutral view; keep an owned position or watch an opportunity.\n"
                "Underweight — Cautious view; consider reducing exposure.\n"
                "Sell — Negative view; consider exiting or avoiding.\n\n"
                "Owned positions: Buy/Overweight → ADD; Hold → HOLD; "
                "Underweight → REDUCE; Sell → SELL.\n"
                "New opportunities: Buy/Overweight → BUY; Hold → WATCH; "
                "Underweight/Sell → IGNORE.\n"
                "Rating expresses recommendation strength, not confidence or a guarantee. "
                "Strategy and horizon still matter; the final decision is yours.",
                markup=False,
            ),
            title="Rating legend • meanings and actions",
            collapsed=True,
        )
