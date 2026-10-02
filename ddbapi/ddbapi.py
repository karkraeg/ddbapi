"""Query the public DDB newspaper index."""

import typing
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_URL = "https://api.deutsche-digitale-bibliothek.de/search/index/newspaper-issues/select"
_FIELDS = {"language", "place_of_distribution", "publication_date", "zdb_id", "provider", "paper_title"}
_METS_NS = {"mets": "http://www.loc.gov/METS/"}


def _session() -> requests.Session:
    http = requests.Session()
    retry = Retry(total=3, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"], backoff_factor=1)
    http.mount("https://", HTTPAdapter(max_retries=retry))
    http.mount("http://", HTTPAdapter(max_retries=retry))
    return http


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
    with _session() as http:
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


def zp_mets(ddb_item_id: str) -> bytes:
    """Return the original METS source XML as bytes, possibly in an OAI wrapper."""
    if not isinstance(ddb_item_id, str) or not re.fullmatch(r"[A-Z2-7]{32}", ddb_item_id):
        raise ValueError("ddb_item_id must be a 32-character DDB item ID")
    url = f"https://api.deutsche-digitale-bibliothek.de/2/items/{ddb_item_id}/source/record"
    with _session() as http:
        response = http.get(url, headers={"Accept": "application/xml"}, timeout=60)
        response.raise_for_status()
    root = ET.fromstring(response.content)
    if root.tag != "{http://www.loc.gov/METS/}mets" and root.find(".//mets:mets", _METS_NS) is None:
        raise ValueError("Source record contains no METS document")
    return response.content


def zp_alto(page_id: str) -> bytes:
    """Resolve a page's DDB_FULLTEXT reference in METS and return ALTO XML bytes."""
    if not isinstance(page_id, str) or "-" not in page_id:
        raise ValueError("page_id must contain a DDB item ID and page name")
    item_id, pagename = page_id.split("-", 1)
    if not pagename:
        raise ValueError("page_id requires a non-empty page name")
    root = ET.fromstring(zp_mets(item_id))
    for file in root.findall(".//mets:fileGrp[@USE='DDB_FULLTEXT']/mets:file", _METS_NS):
        if file.get("ID") != pagename:
            continue
        location = file.find("mets:FLocat", _METS_NS)
        href = location.get("{http://www.w3.org/1999/xlink}href") if location is not None else None
        if not href:
            raise ValueError(f"ALTO reference for {page_id} has no URL")
        source_url = f"https://api.deutsche-digitale-bibliothek.de/2/items/{item_id}/source/record"
        url = urljoin(source_url, href)
        if urlsplit(url).scheme not in ("http", "https"):
            raise ValueError("ALTO URL must use HTTP or HTTPS")
        with _session() as http:
            response = http.get(url, headers={"Accept": "application/xml"}, timeout=60)
            response.raise_for_status()
        alto = ET.fromstring(response.content)
        if not alto.tag.startswith("{http://www.loc.gov/standards/alto/") or not alto.tag.endswith("}alto"):
            raise ValueError("Fulltext reference returned no ALTO document")
        return response.content
    raise ValueError(f"No DDB_FULLTEXT reference for {page_id}")


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
