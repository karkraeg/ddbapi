import unittest
from unittest.mock import MagicMock, patch

import pandas as pd
import requests
from xml.etree import ElementTree as ET
from ddbapi import filter, list_column, zp_alto, zp_count, zp_issues, zp_mets, zp_pages


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


class XmlTests(unittest.TestCase):
    item_id = "NNTLPWFR4HBNAHVH3TC3KQH5S5Q7A7QN"
    mets = b'''<record xmlns="http://www.openarchives.org/OAI/2.0/"
        xmlns:mets="http://www.loc.gov/METS/" xmlns:xlink="http://www.w3.org/1999/xlink">
        <metadata><mets:mets><mets:fileSec><mets:fileGrp USE="DDB_FULLTEXT">
        <mets:file ID="other"><mets:FLocat xlink:href="https://example.org/wrong.xml"/></mets:file>
        <mets:file ID="page-1"><mets:FLocat xlink:href="https://example.org/alto.xml"/></mets:file>
        </mets:fileGrp></mets:fileSec></mets:mets></metadata></record>'''
    alto = b'<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#"/>'

    def test_mets_preserves_source_bytes(self):
        with patch("ddbapi.ddbapi.requests.Session") as session:
            http = session.return_value.__enter__.return_value
            http.get.return_value.content = self.mets
            self.assertEqual(zp_mets(self.item_id), self.mets)
            self.assertEqual(http.get.call_args.args[0],
                             f"https://api.deutsche-digitale-bibliothek.de/2/items/{self.item_id}/source/record")
            self.assertEqual(http.get.call_args.kwargs["headers"], {"Accept": "application/xml"})

    def test_alto_resolves_exact_page(self):
        with patch("ddbapi.ddbapi.requests.Session") as session:
            http = session.return_value.__enter__.return_value
            http.get.side_effect = [MagicMock(content=self.mets), MagicMock(content=self.alto)]
            self.assertEqual(zp_alto(self.item_id + "-page-1"), self.alto)
            self.assertEqual(http.get.call_args.args[0], "https://example.org/alto.xml")

    def test_missing_page_and_invalid_url(self):
        with patch("ddbapi.ddbapi.zp_mets", return_value=self.mets), patch("ddbapi.ddbapi.requests.Session") as session:
            with self.assertRaisesRegex(ValueError, "No DDB_FULLTEXT"):
                zp_alto(self.item_id + "-missing")
            session.assert_not_called()
        for href in (b"", b"file:///tmp/alto.xml"):
            mets = self.mets.replace(b"https://example.org/alto.xml", href)
            with patch("ddbapi.ddbapi.zp_mets", return_value=mets), self.assertRaises(ValueError):
                zp_alto(self.item_id + "-page-1")

    def test_invalid_ids_fail_before_network(self):
        with patch("ddbapi.ddbapi.requests.Session") as session:
            for item_id in (None, "", "../source", self.item_id.lower()):
                with self.subTest(item_id=item_id), self.assertRaises(ValueError):
                    zp_mets(item_id)
            for page_id in (None, "", "bad-page", self.item_id + "-"):
                with self.subTest(page_id=page_id), self.assertRaises(ValueError):
                    zp_alto(page_id)
            session.assert_not_called()

    def test_xml_and_http_errors(self):
        with patch("ddbapi.ddbapi.requests.Session") as session:
            http = session.return_value.__enter__.return_value
            http.get.return_value.content = b"<html/>"
            with self.assertRaisesRegex(ValueError, "no METS"):
                zp_mets(self.item_id)
            http.get.return_value.content = b"invalid XML"
            with self.assertRaises(ET.ParseError):
                zp_mets(self.item_id)
            http.get.return_value.raise_for_status.side_effect = requests.HTTPError("404")
            with self.assertRaises(requests.HTTPError):
                zp_mets(self.item_id)
        with patch("ddbapi.ddbapi.zp_mets", return_value=self.mets), patch("ddbapi.ddbapi.requests.Session") as session:
            http = session.return_value.__enter__.return_value
            http.get.return_value.content = b"<html/>"
            with self.assertRaisesRegex(ValueError, "no ALTO"):
                zp_alto(self.item_id + "-page-1")
            http.get.return_value.raise_for_status.side_effect = requests.HTTPError("404")
            with self.assertRaises(requests.HTTPError):
                zp_alto(self.item_id + "-page-1")


if __name__ == "__main__":
    unittest.main()
