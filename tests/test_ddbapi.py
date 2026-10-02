import unittest
from unittest.mock import MagicMock, patch

import pandas as pd
import requests
from ddbapi import filter, list_column, zp_count, zp_issues, zp_pages


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

    def test_count_uses_no_documents_or_cursor(self):
        with patch("ddbapi.ddbapi.requests.Session") as session:
            http = session.return_value.__enter__.return_value
            http.get.return_value.json.return_value = {"response": {"numFound": 1234}}
            self.assertEqual(zp_count(plainpagefulltext="Hochwasser"), 1234)
            params = http.get.call_args.kwargs["params"]
            self.assertEqual(params["rows"], 0)
            self.assertNotIn("cursorMark", params)
            self.assertIn("type:page", params["q"])

    def test_limit_fields_and_any(self):
        docs = [{"id": str(i)} for i in range(1000)]
        df, seen = self.run_query(zp_pages, [(docs, "a"), ([{"id": "last"}], "b")],
                                  limit=1001, fields=["paper_title"], match="any",
                                  place_of_distribution=["Münster", "Bielefeld"])
        self.assertEqual(len(df), 1001)
        self.assertEqual([p["rows"] for p in seen], [1000, 1])
        self.assertEqual(seen[0]["fl"], "id,paper_title")
        self.assertIn('place_of_distribution:"Münster" OR place_of_distribution:"Bielefeld"', seen[0]["q"])
        df, seen = self.run_query(zp_issues, [(docs[:2], "a")], limit=1, fields="id,paper_title")
        self.assertEqual(len(df), 1)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["fl"], "id,paper_title")

    def test_invalid_options_and_zero_limit(self):
        with patch("ddbapi.ddbapi.requests.Session") as session:
            self.assertTrue(zp_pages(limit=0).empty)
            session.assert_not_called()
            for options in ({"limit": -1}, {"limit": True}, {"limit": 1.5},
                            {"fields": []}, {"fields": [42]}, {"match": "other"}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    zp_pages(**options)
            with self.assertRaises(ValueError):
                zp_count(kind="other")
            session.assert_not_called()

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
        self.assertEqual(filter(["A", "B"], "places", df, match="all").index.tolist(), [0])
        self.assertEqual(filter(["A", "C"], "places", df, match="all").index.tolist(), [])
        with self.assertRaises(ValueError):
            filter("A", "places", df, match="other")


if __name__ == "__main__":
    unittest.main()
