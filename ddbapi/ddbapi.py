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


def _validate_match(match: str) -> None:
    if match not in ("all", "any"):
        raise ValueError("match must be 'all' or 'any'")


def _query(kind: str, query: dict, *, limit=None, fields=None, match="all", count=False):
    if kind not in ("page", "issue"):
        raise ValueError("kind must be 'page' or 'issue'")
    _validate_match(match)
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("limit must be a non-negative integer or None")
    allowed = _FIELDS | ({"plainpagefulltext"} if kind == "page" else set())
    clauses = [f"type:{kind}"]
    for field, value in query.items():
        if field not in allowed:
            raise ValueError(f"{field} ist nicht erlaubt")
        if isinstance(value, list):
            if not value:
                raise ValueError(f"{field} requires a non-empty list")
            operator = " AND " if match == "all" else " OR "
            clauses.append("(" + operator.join(_term(field, item) for item in value) + ")")
        else:
            clauses.append(_term(field, value))

    params = {"rows": 1000, "sort": "id ASC", "q": " AND ".join(clauses),
              "cursorMark": "*", "wt": "json"}
    if fields is not None:
        if isinstance(fields, str):
            fields = fields.split(",")
        if not isinstance(fields, list) or not fields or any(
                not isinstance(field, str) or not field.strip() for field in fields):
            raise ValueError("fields must be a non-empty string or list of field names")
        params["fl"] = ",".join(dict.fromkeys(["id", *(field.strip() for field in fields)]))
    if count:
        params["rows"] = 0
        del params["cursorMark"]
    elif limit == 0:
        return pd.DataFrame()
    docs = []
    with requests.Session() as http:
        retry = Retry(total=3, status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=["GET"], backoff_factor=1)
        http.mount("https://", HTTPAdapter(max_retries=retry))
        while True:
            if not count and limit is not None:
                params["rows"] = min(1000, limit - len(docs))
            response = http.get(API_URL, params=params, timeout=60)
            response.raise_for_status()
            result = response.json()
            if "error" in result:
                raise ValueError(result["error"]["msg"])
            if count:
                return result["response"]["numFound"]
            batch = result["response"]["docs"]
            if limit is not None:
                batch = batch[:limit - len(docs)]
            docs.extend(batch)
            cursor = result["nextCursorMark"]
            if not batch or cursor == params["cursorMark"] or (limit is not None and len(docs) >= limit):
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


def zp_issues(*, limit=None, fields=None, match="all", **query) -> pd.DataFrame:
    """Fetch issues, combining filters with AND. Values may be strings or lists.

    Filters: language, place_of_distribution, publication_date, zdb_id,
    provider, paper_title. Publication dates accept Solr ranges [start TO end].
    """
    return _query("issue", query, limit=limit, fields=fields, match=match)


def zp_pages(*, limit=None, fields=None, match="all", **query) -> pd.DataFrame:
    """Fetch pages. Supports issue filters plus plainpagefulltext."""
    return _query("page", query, limit=limit, fields=fields, match=match)


def zp_count(*, kind="page", match="all", **query) -> int:
    """Count matching pages (default) or issues without fetching documents."""
    return _query(kind, query, match=match, count=True)


def list_column(series: pd.Series) -> pd.Series:
    """Flatten a column containing lists for ordinary pandas operations."""
    return pd.Series([x for values in series for x in values])


def filter(searchfor: typing.Union[str, list], searchin: str, inframe: pd.DataFrame, *, match="any") -> pd.DataFrame:
    """Filter list-containing columns; a search list matches any of its values."""
    values = searchfor if isinstance(searchfor, list) else [searchfor]
    _validate_match(match)
    if not values or any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("searchfor requires a non-empty string or list of strings")
    combine = all if match == "all" else any
    return inframe[inframe[searchin].apply(lambda row: combine(value in row for value in values))]
