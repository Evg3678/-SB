"""Synthetic-only regression checks for the redesigned Streamlit interface."""
from io import BytesIO
from unittest.mock import patch
import unittest
from streamlit.testing.v1 import AppTest
from test_sb import fixture


class UITests(unittest.TestCase):
    def test_empty(self):
        app = AppTest.from_file("app.py").run(timeout=30)
        self.assertFalse(app.exception)
        self.assertTrue(any(x.value == "Начните с выгрузки логов" for x in app.subheader))

    def test_tabs_filters_reports_reset(self):
        uploaded = BytesIO(fixture().to_csv(index=False, sep=";").encode("utf-8-sig"))
        uploaded.name = "synthetic.csv"
        with patch("streamlit.delta_generator.DeltaGenerator.file_uploader", return_value=uploaded):
            app = AppTest.from_file("app.py").run(timeout=30)
            self.assertFalse(app.exception)
            self.assertEqual([t.label for t in app.tabs],
                             ["Обзор", "Сотрудники", "Модели", "Детализация", "Отчёт"])
            self.assertEqual(app.metric[0].value, "3")
            self.assertIn("Уникальных чатов", app.tabs[1].dataframe[0].value)
            self.assertNotIn("ID чатов", app.tabs[1].dataframe[0].value)
            self.assertIn("ID чатов", app.tabs[1].dataframe[1].value)
            [b for b in app.button if b.label == "Сформировать HTML-отчёт"][0].click().run()
            self.assertIn("sb_report", app.session_state)
            [m for m in app.multiselect if m.label == "организация"][0].select("Org").run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[0].value, "2")
            self.assertNotIn("sb_report", app.session_state)
            employee = [m for m in app.multiselect if m.label == "ID сотрудника"][0]
            self.assertEqual(employee.options, ["e1"])
            [m for m in app.multiselect if m.label == "ID треда"][0].select("t2").run()
            self.assertEqual(app.metric[0].value, "1")
            [b for b in app.button if b.label == "Сбросить фильтры"][0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[0].value, "3")
            self.assertTrue(all(not m.value for m in app.multiselect))


if __name__ == "__main__":
    unittest.main()
