"""Offline regression tests. Optional real fixture: SB_SAMPLE environment variable."""
import os
from pathlib import Path
from datetime import date
from html.parser import HTMLParser
import unittest
from unittest.mock import patch
import pandas as pd
from sb_data import SCHEMA, normalize, read_table, filter_data, linked_filters, summarize, csv_bytes, METRICS
from sb_report import build_html, heatmap, share


def fixture():
    return pd.DataFrame([
        ["u@example.test", "Org", "e1", "c1", "t1", "svc", "m1", "internal",
         "success", 10, 5, 15, 1.5, "2026-09-07T23:30:00Z", "2026-09-07T23:30:02Z", 2000],
        ["u@example.test", "Org", "e1", "c1", "t2", "svc", "m2", "internal",
         "error", 10, 0, 10, 0, "2026-09-08T00:30:00Z", "2026-09-08T00:30:04Z", 4000],
        ["v@example.test", "Org2", "e2", "c2", "t3", "svc2", "gigachat", "internal",
         "success", 10, 1, 11, None, "2026-09-08T01:30:00Z", "2026-09-08T01:30:01Z", 1000],
    ], columns=SCHEMA)


class ReportParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.external = []
        self.charts = 0
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "div" and "plotly-graph-div" in values.get("class", "").split():
            self.charts += 1
        if tag in ("script", "link", "iframe", "img"):
            for key in ("src", "href"):
                if values.get(key, "").startswith(("http", "//")):
                    self.external.append(values[key])


class SBTests(unittest.TestCase):
    def test_linked_filters(self):
        frame, _ = normalize(fixture())
        start, end = date(2026, 9, 7), date(2026, 9, 8)
        selected, options, removed = linked_filters(
            frame, start, end, "Europe/Moscow", {"organization": ["Org"], "model": ["m2"]})
        self.assertEqual(options["employee_id"], ["e1"])
        self.assertEqual(options["status"], ["error"])
        self.assertEqual(options["model"], ["m1", "m2"])
        self.assertFalse(removed)
        selected, options, removed = linked_filters(
            frame, start, end, "Europe/Moscow", {"status": ["success"]})
        self.assertEqual(options["model"], ["gigachat", "m1"])
        self.assertEqual(options["organization"], ["Org", "Org2"])
        selected, options, removed = linked_filters(
            frame, start, start, "Europe/Moscow", {"model": ["m2"]})
        self.assertEqual(selected["model"], [])
        self.assertEqual(removed["model"], ["m2"])
        self.assertTrue(all(not values for values in options.values()))

    def test_missing_is_not_zero(self):
        df, issues = normalize(fixture())
        self.assertEqual(len(df), 3)
        self.assertEqual(df.cost_rub.isna().sum(), 1)
        self.assertEqual(df.cost_rub.sum(min_count=1), 1.5)
        self.assertEqual(df.latency_sec.tolist(), [2, 4, 1])
        self.assertTrue(issues)
        group = summarize(df, ["model"]).set_index("model")
        self.assertTrue(pd.isna(group.loc["gigachat", "cost_rub"]))
        self.assertEqual(group.loc["m2", "cost_rub"], 0)

    def test_timezone_and_intersection(self):
        df, _ = normalize(fixture())
        self.assertEqual(len(filter_data(df, date(2026,9,7), date(2026,9,7))), 1)
        self.assertEqual(len(filter_data(df, date(2026,9,7), date(2026,9,7),
                                        "Europe/Moscow")), 0)
        self.assertEqual(len(filter_data(df, date(2026,9,8), date(2026,9,8),
                                        "Europe/Moscow")), 3)
        self.assertEqual(len(filter_data(df, date(2026,9,7), date(2026,9,8),
                                        selections={"model":["m2"], "status":["success"]})), 0)

    def test_malformed_rejected(self):
        for column, value in [("prompt_tokens","bad"), ("total_tokens",-1),
                              ("duration_ms",float("inf")), ("started_at","bad"),
                              ("стоимость, руб","unknown")]:
            frame = fixture()
            frame[column] = frame[column].astype(object)
            frame.loc[0, column] = value
            with self.assertRaises(ValueError):
                normalize(frame)
        with self.assertRaises(ValueError):
            normalize(fixture().drop(columns=["email"]))

    def test_duplicates_retained(self):
        frame = pd.concat([fixture(),fixture().iloc[:1]],ignore_index=True)
        df, issues = normalize(frame)
        self.assertEqual(len(df), 4)
        self.assertTrue(any("совпадающих" in s for s in issues))

    def test_csv(self):
        raw = fixture()
        content = raw.to_csv(index=False,sep=";").encode("utf-8-sig")
        df, _ = normalize(read_table(content, "logs.csv"))
        self.assertEqual(len(df), 3)
        safe = csv_bytes(pd.DataFrame({"email":["=1+2","@SUM(1)"]})).decode("utf-8-sig")
        self.assertIn("'=1+2",safe)

    def test_report_offline_and_escaped(self):
        raw = fixture()
        raw.loc[0,"модель"] = '<script>alert(1)</script>'
        frame, issues = normalize(raw)
        frame = filter_data(frame, date(2026,9,7),date(2026,9,8))
        with patch("socket.create_connection",side_effect=AssertionError("Network forbidden")):
            report = build_html(frame, "<img src=x>", "UTC", "<b>filter</b>", issues)
        parser = ReportParser()
        parser.feed(report)
        self.assertFalse(parser.external)
        self.assertNotIn("<script>alert(1)</script>",report)
        self.assertNotIn("<img src=x>",report)
        self.assertIn('id="requests"', report)
        self.assertIn("не указано", report)
        self.assertEqual(parser.charts, 8)
        for label in METRICS:
            self.assertIn(label,report)
            self.assertEqual(len(heatmap(frame,label).data),1)
        for label in list(METRICS)[:3]:
            self.assertEqual(len(share(frame,label).data),1)

    @unittest.skipUnless(os.environ.get("SB_SAMPLE"), "No external fixture supplied")
    def test_user_workbook(self):
        p = Path(os.environ["SB_SAMPLE"])
        frame, issues = normalize(read_table(p.read_bytes(),p.name,"Вызовы LLM"))
        self.assertEqual(len(frame), 4280)
        self.assertEqual(frame.total_tokens.sum(), 22741879)
        self.assertAlmostEqual(frame.cost_rub.sum(),1391.733353,places=6)
        self.assertEqual(frame.cost_rub.isna().sum(),80)
        self.assertEqual(frame.chat_id.nunique(),347)
        self.assertEqual(frame.thread_id.nunique(),554)
        self.assertEqual(frame.status.value_counts().to_dict(),
                         {"success":3693,"error":340,"cancelled":247})
        selected=filter_data(frame,date(2026,9,7),date(2026,9,13))
        self.assertEqual(len(selected),4280)
        self.assertEqual(summarize(selected,["model"]).requests.sum(),4280)
        report=build_html(selected,p.name,"UTC","Все логи",issues)
        parser=ReportParser(); parser.feed(report)
        self.assertFalse(parser.external)
        self.assertIn("4280",report)


if __name__ == "__main__":
    unittest.main()
