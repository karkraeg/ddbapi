"""Query the public DDB newspaper index."""

import typing
from datetime import datetime

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_URL = "https://api.deutsche-digitale-bibliothek.de/search/index/newspaper-issues/select"
_FIELDS = {"language", "place_of_distribution", "publication_date", "zdb_id", "provider", "paper_title"}


def _term(field: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} requires a non-empty string")
    if field == "publication_date" and value.startswith("[") and value.endswith("]"):
        return f"{field}:{value}"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{field}:"{escaped}"'


def _query(kind: str, query: dict) -> pd.DataFrame:
    allowed = _FIELDS | ({"plainpagefulltext"} if kind == "page" else set())
    clauses = [f"type:{kind}"]
    for field, value in query.items():
        if field not in allowed:
            raise ValueError(f"{field} ist nicht erlaubt")
        if isinstance(value, list):
            if not value:
                raise ValueError(f"{field} requires a non-empty list")
            clauses.append("(" + " AND ".join(_term(field, item) for item in value) + ")")
        else:
            clauses.append(_term(field, value))

    params = {"rows": 1000, "sort": "id ASC", "q": " AND ".join(clauses),
              "cursorMark": "*", "wt": "json"}
    docs = []
    with requests.Session() as http:
        retry = Retry(total=3, status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=["GET"], backoff_factor=1)
        http.mount("https://", HTTPAdapter(max_retries=retry))
        while True:
            response = http.get(API_URL, params=params, timeout=60)
            response.raise_for_status()
            result = response.json()
            if "error" in result:
                raise ValueError(result["error"]["msg"])
            batch = result["response"]["docs"]
            docs.extend(batch)
            cursor = result["nextCursorMark"]
            if not batch or cursor == params["cursorMark"]:
                break
            params["cursorMark"] = cursor

    df = pd.DataFrame(docs)
    if not df.empty:
        df.rename(columns={"id": "page_id" if kind == "page" else "ddb_item_id"}, inplace=True)
        if kind == "page" and "pagename" in df:
            df["ddb_item_id"] = [
                page_id.removesuffix("-" + pagename) if isinstance(pagename, str) else None
                for page_id, pagename in zip(df["page_id"], df["pagename"])
            ]
        if "publication_date" in df:
            # Seconds support historical dates outside pandas' nanosecond range.
            df["publication_date"] = pd.Series(
                [datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ") if pd.notna(value) else pd.NaT
                 for value in df["publication_date"]],
                index=df.index, dtype="datetime64[s]",
            )
    print(f"Got {len(df)} items.")
    return df


def zp_issues(**query) -> pd.DataFrame:
    """Fetch issues, combining filters with AND. Values may be strings or lists.

    Filters: language, place_of_distribution, publication_date, zdb_id,
    provider, paper_title. Publication dates accept Solr ranges [start TO end].
    """
    return _query("issue", query)


def zp_pages(**query) -> pd.DataFrame:
    """Fetch pages. Supports issue filters plus plainpagefulltext."""
    return _query("page", query)


def list_column(series: pd.Series) -> pd.Series:
    """Flatten a column containing lists for ordinary pandas operations."""
    return pd.Series([x for values in series for x in values])


def filter(searchfor: typing.Union[str, list], searchin: str, inframe: pd.DataFrame) -> pd.DataFrame:
    """Filter list-containing columns; a search list matches any of its values."""
    values = searchfor if isinstance(searchfor, list) else [searchfor]
    return inframe[inframe[searchin].apply(lambda row: any(value in row for value in values))]
