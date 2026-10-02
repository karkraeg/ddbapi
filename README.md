# Wrapper for the DDB API

- Query the DDB API for Newspapers
- returns a Pandas Dataframe Object

## Installation and development

Requires Python 3.10 or newer and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run python -m unittest discover -s tests -v
```

Run scripts using `uv run python your_script.py`. Dependencies are pinned in
`uv.lock`; update them with `uv lock --upgrade` followed by `uv sync`.

Both query functions fetch all matching records using cursor pagination. Use
narrow filters for live queries: the index contains millions of records.
HTTP/network errors are raised; invalid filters raise `ValueError`.
Date ranges are sent as Solr ranges. Returned dates use `datetime64[s]`, including
dates before 1677. Page results contain both `page_id` and `ddb_item_id` when
`pagename` is available.

Usage:

```
from ddbapi import zp_issues, zp_pages, list_column, filter

df = zp_issues(publication_date='[1600-09-01T12:00:00Z TO 1699-12-31T12:00:00Z]')
print(df)
```

## `zp_issues`

- Returns a DataFrame containing Data on Newspaper-Issues.
- Use any combination of these keyword arguments:
  - `language`: Use ISO Codes, currently `ger`, `eng`, `fre`, `spa`, `ita`
  - `place_of_distribution`: Search inside "Verbreitungsort", use a list for multiple search-words
  - `publication_date`: Get newspapers by publication date. Use the following format: `1900-12-31T12:00:00Z` for a specific date, use square brackets and `TO` between two dates to get a daterange like so: `publication_date='[1935-09-01T12:00:00Z TO 1935-09-22T12:00:00Z]'` - time is always `12:00:00Z`.
  - `zdb_id`: Search by ZDB-ID
  - `provider`: Search by Data Provider
  - `paper_title`: Search inside the title of the Newspaper

## `zp_pages`

- Returns a DataFrame containing Data on Newspaper-Pages.
- Use any combination of these keyword arguments:
  - `plainpagefulltext`: Search inside the OCR Fulltext (Use a list for multiple search-words)
  - `language`: Use ISO Codes, currently `ger`, `eng`, `fre`, `spa`, `ita`
  - `place_of_distribution`: Search inside "Verbreitungsort", use a list for multiple search-words
  - `publication_date`: Get newspapers by publication date. Use the following format: `1900-12-31T12:00:00Z` for a specific date, use square brackets and `TO` between two dates to get a daterange like so: `publication_date='[1935-09-01T12:00:00Z TO 1935-09-22T12:00:00Z]'` - time is always `12:00:00Z`.
  - `zdb_id`: Search by ZDB-ID
  - `provider`: Search by Data Provider
  - `paper_title`: Search inside the title of the Newspaper

---

- Values of keyword arguments may contain lists to combine queries.
- Use `list_column` and `filter` to perform usual Pandas Operations on list-containing Columns (eg. `list_column(df['place_of_distribution']).value_counts()` or `filter('Altona', 'place_of_distribution', df)`)

## Example

See [this Notebook](https://deepnote.com/@karkraeg/Zeitungsportal-API-2SJN2o4mSzWm10DpUHsRUQ) for a usage example.
