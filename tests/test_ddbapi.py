import unittest
from unittest.mock import MagicMock, patch

import pandas as pd
import requests
from ddbapi import filter, list_column, zp_issues, zp_pages


class QueryTests(unittest.TestCase):
    def run_query(self, function, batches, **query):
        responses = []
        for docs, cursor in batches:
            response = MagicMock()
            response.json.return_value = {"response": {"docs": docs}, "nextCursorMark": cursor}
            responses.append(response)
        with patch("ddbapi.ddbapi.requests.Session") as session:
            http = session.return_value.__enter__.return_value
            seen = []
            def get(url, *, params, timeout):
                seen.append(dict(params))
                return responses[len(seen) - 1]
            http.get.side_effect = get
            df = function(**query)
        return df, seen

    def test_pagination_historical_dates_and_range(self):
        doc = {"id": "A", "publication_date": "1650-01-01T12:00:00Z"}
        df, seen = self.run_query(zp_issues, [([doc], "a"), ([dict(doc, id="B")], "b"), ([], "b")],
                                  publication_date="[1600-01-01T12:00:00Z TO 1700-01-01T12:00:00Z]", language="ger")
        self.assertEqual(df.ddb_item_id.tolist(), ["A", "B"])
        self.assertEqual(df.publication_date.dt.year.tolist(), [1650, 1650])
        self.assertEqual([p["cursorMark"] for p in seen], ["*", "a", "b"])
        self.assertIn("publication_date:[1600", seen[0]["q"])

    def test_page_id_and_unchanged_cursor(self):
        df, seen = self.run_query(zp_pages, [([{"id": "A-page-1", "pagename": "page-1"}], "*")])
        self.assertEqual(df.page_id.tolist(), ["A-page-1"])
        self.assertEqual(df.ddb_item_id.tolist(), ["A"])
        self.assertEqual(len(seen), 1)

    def test_empty_and_escaped_filters(self):
        df, seen = self.run_query(zp_pages, [([], "*")], paper_title=['A "B"', "C D"], plainpagefulltext="test")
        self.assertTrue(df.empty)
        self.assertIn('(paper_title:"A \\"B\\"" AND paper_title:"C D")', seen[0]["q"])

    def test_invalid_filters(self):
        for query in ({"unknown": "x"}, {"language": []}, {"language": 42}, {"language": ""}, {"plainpagefulltext": "x"}):
            with self.subTest(query=query), self.assertRaises(ValueError):
                zp_issues(**query)

    def test_http_errors_propagate(self):
        with patch("ddbapi.ddbapi.requests.Session") as session:
            session.return_value.__enter__.return_value.get.return_value.raise_for_status.side_effect = requests.HTTPError("503")
            with self.assertRaises(requests.HTTPError):
                zp_issues()

    def test_solr_errors_propagate(self):
        with patch("ddbapi.ddbapi.requests.Session") as session:
            session.return_value.__enter__.return_value.get.return_value.json.return_value = {"error": {"msg": "bad query"}}
            with self.assertRaisesRegex(ValueError, "bad query"):
                zp_pages()

    def test_dataframe_helpers(self):
        df = pd.DataFrame({"places": [["A", "B"], ["C"]]})
        self.assertEqual(list_column(df.places).tolist(), ["A", "B", "C"])
        self.assertEqual(filter("A", "places", df).index.tolist(), [0])
        self.assertEqual(filter(["A", "C"], "places", df).index.tolist(), [0, 1])


if __name__ == "__main__":
    unittest.main()
