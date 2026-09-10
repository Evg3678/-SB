"""Shared local charts and self-contained HTML. No network clients."""
from datetime import datetime, timezone
from html import escape
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from plotly.offline import get_plotlyjs
from sb_data import METRICS, summarize, display_summary, details


def styled(fig, title):
    fig.update_layout(template="plotly_white", title=title,
                      font={"family": "Arial, sans-serif"}, height=420,
                      margin={"l": 30, "r": 20, "t": 65, "b": 50})
    return fig


def chart_data(frame):
    result = frame.copy()
    for col in result.select_dtypes(include=["object", "string"]).columns:
        result[col] = result[col].map(lambda v: escape(v) if isinstance(v, str) else v)
    return result


def heatmap(frame, label):
    col, method = METRICS[label]
    data = chart_data(frame)
    single = data["date"].nunique() == 1
    data["period"] = (data.local_time.dt.floor("15min").dt.strftime("%H:%M")
                      if single else data["date"].astype(str))
    grouped = data.groupby(["model", "period"])[col]
    values = (grouped.size() if method == "size" else
              grouped.mean() if method == "mean" else grouped.sum(min_count=1))
    pivot = values.unstack("period").sort_index(axis=1)
    if col in ("source_row", "total_tokens"):
        pivot = pivot.fillna(0)
    fig = go.Figure(go.Heatmap(z=pivot.to_numpy(dtype=float), x=list(pivot.columns),
                              y=list(pivot.index), colorscale="Viridis",
                              hoverongaps=False, colorbar={"title": label}))
    styled(fig, label)
    fig.update_layout(height=max(420, len(pivot) * 27 + 140))
    fig.update_xaxes(title="Время (15 минут)" if single else "Дата")
    return fig


def share(frame, label):
    col, method = METRICS[label]
    data = chart_data(frame)
    grouped = data.groupby("model")[col]
    values = grouped.size() if method == "size" else grouped.sum(min_count=1)
    values = values[values.gt(0)].sort_values(ascending=False)
    fig = go.Figure(go.Pie(labels=list(values.index), values=list(values.values), hole=.45))
    styled(fig, "Соотношение использования моделей: " + label)
    if values.empty:
        fig.add_annotation(text="Нет положительных значений", showarrow=False)
    return fig


def daily_figure(frame):
    daily = summarize(frame, ["date"])
    fig = px.line(daily, x="date", y="cost_rub", markers=True,
                  labels={"date": "Дата", "cost_rub": "Известная стоимость, ₽"})
    return styled(fig, "Динамика известных расходов")


def number(value, precision=2):
    return "не указано" if pd.isna(value) else f"{value:,.{precision}f}".replace(",", " ")


def build_html(frame, source_name, timezone_name, filters_text, issues=()):
    """Export all views from the same filtered frame, independent of UI selectors."""
    if frame.empty:
        raise ValueError("Нет данных для отчёта.")
    def table(data):
        return '<div class="scroll">' + data.to_html(
            index=False, escape=True, na_rep="не указано",
            float_format=lambda x: number(x, 6), border=0) + "</div>"

    def plot(fig):
        return pio.to_html(fig, full_html=False, include_plotlyjs=False,
                           include_mathjax=False,
                           config={"displaylogo": False, "responsive": True})

    known = frame.cost_rub.notna().sum()
    cards = [
        ("Запросы", str(len(frame))),
        ("Токены", number(frame.total_tokens.sum(), 0)),
        ("Известная стоимость, ₽", number(frame.cost_rub.sum(min_count=1), 6)),
        ("Стоимость указана", f"{known} / {len(frame)}"),
        ("Средняя задержка, с", number(frame.latency_sec.mean(), 3)),
        ("Ошибки", str(frame.status.eq("error").sum())),
        ("Отмены", str(frame.status.eq("cancelled").sum())),
        ("Сотрудники", str(frame.employee_id.nunique())),
    ]
    sections = [
        '<section id="overview"><h2>Обзор</h2><div class="cards">' +
        "".join(f'<div class="card"><small>{escape(k)}</small><strong>{escape(v)}</strong></div>'
                for k, v in cards) + "</div>" + plot(daily_figure(frame)) + "</section>",
        '<section id="heatmaps"><h2>Тепловые карты</h2>'
        '<p>Все модели выборки. Пустая ячейка означает отсутствие данных; '
        'сумма включает только известную стоимость.</p>' +
        "".join(plot(heatmap(frame, label)) for label in METRICS) + "</section>",
        '<section id="shares"><h2>Соотношение использования моделей</h2>' +
        "".join(plot(share(frame, label)) for label in list(METRICS)[:3]) + "</section>",
    ]
    for col, title in (("organization", "Организации"), ("service", "Сервисы"),
                       ("employee_id", "Сотрудники"), ("model", "Модели"),
                       ("provider", "Провайдеры"), ("status", "Статусы")):
        sections.append(f'<section><h2>{title}</h2>' +
                        table(display_summary(summarize(frame, [col]))) + "</section>")
    sections += [
        '<section id="load"><h2>Детальная нагрузка по моделям</h2>' +
        table(display_summary(summarize(frame, ["model", "date"]))) + "</section>",
        '<section id="requests"><h2>Все запросы выбранной выборки</h2>' +
        table(details(frame, timezone_name)) + "</section>",
    ]
    notes = "".join("<li>" + escape(str(x)) + "</li>" for x in issues)
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Дашборд SB — отчёт</title><style>'
            'body{font:15px Arial,sans-serif;background:#f3f6fa;color:#172c42;margin:0;padding:24px}'
            'main{max-width:1400px;margin:auto}section{background:white;border-radius:12px;padding:24px;margin:20px 0}'
            '.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}'
            '.card{background:#edf5f1;padding:18px;border-radius:8px}strong{display:block;font-size:23px;margin-top:8px}'
            '.scroll{overflow:auto;max-height:750px}table{border-collapse:collapse;width:100%;font-size:12px}'
            'td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left;white-space:nowrap}'
            'th{position:sticky;top:0;background:#edf5f1}nav a{margin-right:16px;color:#16744b}'
            '@media print{.scroll{max-height:none;overflow:visible}nav{display:none}}'
            '</style><script>' + get_plotlyjs() + '</script></head><body><main>'
            '<h1>Дашборд SB — внутренняя аналитика LLM</h1>'
            f'<p>Источник: {escape(source_name)}. Сформирован: {escape(created)}.</p>'
            f'<p>Часовой пояс: {escape(timezone_name)}. Фильтры: {escape(filters_text)}.</p>'
            '<p>Стоимость взята из логов, без внешних тарифов. Пустая стоимость не равна нулю. '
            'Все статусы включаются, если явно не отфильтрованы. Задержка = duration_ms / 1000. '
            'Даты фильтруются по started_at. Сводки сохраняют полную точность при расчёте.</p>'
            '<details><summary>Проверки исходного файла</summary><ul>' + notes + '</ul></details>'
            '<nav><a href="#overview">Обзор</a><a href="#heatmaps">Тепловые карты</a>'
            '<a href="#shares">Доли моделей</a><a href="#load">Нагрузка</a>'
            '<a href="#requests">Запросы</a></nav>' +
            "".join(sections) + "</main></body></html>")
