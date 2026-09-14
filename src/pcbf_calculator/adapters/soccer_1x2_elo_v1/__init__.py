"""The first registered Soccer 1X2 forecasting adapter.

See ``adapter.py``'s own module docstring for the full evidence contract
and scope.
"""

from .adapter import ADAPTER_ID, SPORT_ID, SoccerOneXTwoEloV1Adapter, load_team_alias_book

__all__ = ["SoccerOneXTwoEloV1Adapter", "ADAPTER_ID", "SPORT_ID", "load_team_alias_book"]
