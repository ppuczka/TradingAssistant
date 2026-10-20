"""Application-owned news metadata; article bodies are not stored."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, HttpUrl


class NewsArticle(BaseModel):
    title: str = Field(min_length=1)
    url: HttpUrl
    published_at: AwareDatetime
    publisher: str
    symbols: list[str] = Field(default_factory=list)
    timestamp_kind: Literal["published", "indexed"] = "published"
    discovery_source: str = "Yahoo"


class NewsUnavailableError(ValueError):
    pass
