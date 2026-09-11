"""Дашборд SB: аналитика синтетических логов LLM."""
import hashlib
import json
from datetime import datetime
from zipfile import BadZipFile
import streamlit as st
from sb_data import (CATEGORIES, LABELS, METRICS, SCHEMA, excel_sheets, read_table,
                     normalize, filter_data, linked_filters, summarize, display_summary, details, csv_bytes)
from sb_report import heatmap, share, daily_figure, number, build_html
from sb_ui import style, summary_table

st.set_page_config(page_title="Дашборд SB", page_icon="📊", layout="wide")
style()
st.html('<p class="sb-eyebrow">SB / LLM ANALYTICS</p>')
st.title("Использование ИИ")
st.caption("Расходы, активность и качество работы моделей — в одном месте.")
st.html('<div class="sb-test">Тестовый стенд · Только синтетические данные. '
        'Не загружайте реальные корпоративные логи и персональные данные.</div>')
st.sidebar.subheader("Дашборд SB")
st.sidebar.caption("Москва · UTC+3")


def reset_filters(keys, period_key, bounds):
    for key in keys.values():
        st.session_state[key] = []
    st.session_state.pop(period_key, None)


def render_dashboard(frame, source_name, source_id, issues):
    timezone = "Europe/Moscow"
    dates = frame.started_at.dt.tz_convert(timezone).dt.date
    bounds = (dates.min(), dates.max())
    period_key = "period_" + source_id[:12] + timezone
    keys = {col: "filter_" + source_id[:12] + col for col in CATEGORIES}
    st.sidebar.subheader("Фильтры")
    st.sidebar.button("Сбросить фильтры", on_click=reset_filters,
                      args=(keys, period_key, bounds), width="stretch")
    period = st.sidebar.date_input("Период", value=bounds, min_value=bounds[0],
                                   max_value=bounds[1], key=period_key)
    if not isinstance(period, (tuple, list)) or len(period) != 2:
        st.info("Выберите начало и конец периода.")
        return
    selections, options, removed = linked_filters(
        frame, period[0], period[1], timezone,
        {col: st.session_state.get(key, []) for col, key in keys.items()})
    for col, key in keys.items():
        if st.session_state.get(key) != selections[col]:
            st.session_state[key] = selections[col]
    if removed:
        st.sidebar.info("Сняты недоступные значения: " + ", ".join(LABELS[c] for c in removed))
    # Render all controls even when collapsed so linked selections retain state.
    for col in [c for c in CATEGORIES if c not in ("email", "chat_id", "thread_id")]:
        selections[col] = st.sidebar.multiselect(LABELS[col], options[col], key=keys[col],
            help="Учитывает период и остальные фильтры. Пустой выбор — все доступные значения.")
    with st.sidebar.expander("Дополнительные · email и ID"):
        for col in ("email", "chat_id", "thread_id"):
            selections[col] = st.multiselect(LABELS[col], options[col], key=keys[col],
                help="Учитывает период и остальные фильтры. Пустой выбор — все доступные значения.")
    selected = filter_data(frame, period[0], period[1], timezone, selections)
    context = {"period": [str(d) for d in period], "timezone": timezone,
               "selections": selections, "source": source_id}
    signature = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
    if st.session_state.get("report_signature") != signature:
        st.session_state.pop("sb_report", None)
    st.sidebar.caption(f"В выборке {len(selected):,} из {len(frame):,} вызовов.")
    st.caption(f"{period[0]:%d.%m.%Y} — {period[1]:%d.%m.%Y} · Москва · "
               f"Активных фильтров: {sum(bool(v) for v in selections.values())}")
    if selected.empty:
        st.info("По этим условиям вызовов нет. Измените период или сбросьте фильтры.")
        return
    known, total = int(selected.cost_rub.notna().sum()), len(selected)
    metrics = st.columns(4)
    metrics[0].metric("Вызовы LLM", f"{total:,}")
    metrics[1].metric("Токены", f"{int(selected.total_tokens.sum()):,}")
    metrics[2].metric("Известные расходы, ₽", number(selected.cost_rub.sum(min_count=1), 2),
        help="Сумма указанных стоимостей. Полная точность сохранена в таблицах и отчётах.")
    metrics[3].metric("Средняя задержка, с", number(selected.latency_sec.mean(), 3))
    st.caption(f"Стоимость указана в {known} из {total} вызовов ({known / total:.1%}). "
               "Отсутствующая стоимость не равна нулю. Все выбранные статусы учтены.")
    overview, people, models, detail, export = st.tabs(
        ["Обзор", "Сотрудники", "Модели", "Детализация", "Отчёт"])
    with overview:
        st.subheader("Динамика расходов")
        st.plotly_chart(daily_figure(selected), width="stretch")
        counts = st.columns(4)
        counts[0].metric("Сотрудники", selected.employee_id.nunique())
        counts[1].metric("Чаты / треды", f"{selected.chat_id.nunique()} / {selected.thread_id.nunique()}")
        counts[2].metric("Ошибки", f"{selected.status.eq('error').sum()} ({selected.status.eq('error').mean():.1%})")
        counts[3].metric("Отмены", f"{selected.status.eq('cancelled').sum()} ({selected.status.eq('cancelled').mean():.1%})")
        with st.expander("Тепловая карта", expanded=False):
            heat_label = st.radio("Показатель карты", list(METRICS), horizontal=True)
            st.caption("Все модели. Один день — интервалы 15 минут, иначе — дни. "
                       "Пустая ячейка суммы или задержки означает отсутствие данных.")
            st.plotly_chart(heatmap(selected, heat_label), width="stretch")
        with st.expander("Полнота и качество данных"):
            st.write(f"Стоимость указана в {known} из {total} вызовов. "
                     "Для некоторых моделей она не предоставляется.")
            for issue in issues:
                st.write(issue)
    with people:
        st.subheader("Активность сотрудников")
        person_group = st.selectbox("Разрез сотрудников", ["employee_id", "email"],
                                    format_func=lambda c: LABELS[c])
        summary_table(selected, person_group, "people")
    with models:
        st.subheader("Использование моделей")
        share_label = st.radio("Показатель долей", list(METRICS)[:3], horizontal=True)
        st.plotly_chart(share(selected, share_label), width="stretch")
        summary_table(selected, "model", "models")
        with st.expander("Детальная нагрузка по моделям"):
            st.dataframe(display_summary(summarize(selected, ["model", "date"])),
                         hide_index=True, width="stretch")
    with detail:
        st.subheader("Аналитика по разрезам")
        group = st.selectbox("Группировать по", ["organization", "service", "model",
                             "employee_id", "email", "provider", "status", "chat_id", "thread_id"],
                             format_func=lambda c: LABELS[c])
        summary_table(selected, group, "detail")
        with st.expander("Все вызовы выбранной выборки"):
            st.dataframe(details(selected, timezone), hide_index=True, width="stretch")
        st.download_button("Скачать выбранные логи CSV", csv_bytes(details(selected, timezone)),
                           "sb_logs.csv", "text/csv")
    with export:
        render_export(selected, source_name, timezone, period, selections, issues, signature)


def render_export(selected, source_name, timezone, period, selections, issues, signature):
    st.subheader("Отчёт по текущей выборке")
    st.caption("Четыре тепловые карты, три диаграммы долей, сводки и все строки выборки. "
               "Открывается без интернета. Включает идентификаторы и email.")
    filters_text = f"{period[0]} — {period[1]}; " + "; ".join(
        f"{LABELS[c]}: {', '.join(v)}" for c, v in selections.items() if v)
    if st.button("Сформировать HTML-отчёт", type="primary"):
        with st.spinner("Формируем полный отчёт…"):
            st.session_state.sb_report = build_html(selected, source_name, timezone,
                                                    filters_text, issues)
            st.session_state.report_signature = signature
    if st.session_state.get("sb_report"):
        st.download_button("Скачать HTML-отчёт", st.session_state.sb_report.encode("utf-8"),
                           f"sb_report_{datetime.now():%Y%m%d_%H%M%S}.html", "text/html")


def main():
    source = st.sidebar.expander("Источник данных", expanded=True)
    uploaded = source.file_uploader("Загрузить логи · XLSX / CSV", type=["xlsx", "csv", "txt"])
    source.button("Из внутренней системы · скоро", disabled=True,
        help="Будущая интеграция: нужны адрес источника, способ доступа и формат ответа.")
    st.sidebar.caption("Тема: меню ⋮ → Light / Dark.")
    if uploaded is None:
        for key in ("sb_data", "sb_issues", "sb_source_id", "sb_report", "report_signature"):
            st.session_state.pop(key, None)
        with st.container(border=True):
            st.subheader("Начните с выгрузки логов")
            st.write("Добавьте синтетический XLSX или CSV в панели слева. "
                     "Дашборд покажет активность сотрудников, расходы и нагрузку на модели.")
            cols = st.columns(3)
            for col, title, text in zip(cols,
                ["01 · Загрузите", "02 · Исследуйте", "03 · Поделитесь"],
                ["XLSX или CSV, до 100 МБ", "Связанные фильтры и пять вкладок", "Автономный HTML-отчёт"]):
                col.markdown("**" + title + "**")
                col.caption(text)
        with st.expander("Формат данных"):
            st.write(", ".join(SCHEMA))
            st.caption("Время — ISO 8601; без смещения трактуется как UTC. "
                       "Стоимость — рубли, duration_ms — миллисекунды. README не импортируется.")
        return
    content, sheet = uploaded.getvalue(), None
    try:
        if uploaded.name.lower().endswith(".xlsx"):
            sheets = excel_sheets(content)
            default = sheets.index("Вызовы LLM") if "Вызовы LLM" in sheets else 0
            sheet = source.selectbox("Лист с логами", sheets, index=default)
        source_id = hashlib.sha256(content + str(sheet).encode()).hexdigest()
        if st.session_state.get("sb_source_id") != source_id or "sb_data" not in st.session_state:
            st.session_state.pop("sb_report", None)
            st.session_state.pop("sb_data", None)
            with st.spinner("Читаем и проверяем выгрузку…"):
                frame, issues = normalize(read_table(content, uploaded.name, sheet))
            st.session_state.sb_data = frame
            st.session_state.sb_issues = issues
            st.session_state.sb_source_id = source_id
        source.caption(f"{uploaded.name} · {len(st.session_state.sb_data):,} вызовов")
        render_dashboard(st.session_state.sb_data, uploaded.name, source_id, st.session_state.sb_issues)
    except (ValueError, ImportError, OSError, BadZipFile) as error:
        st.error(str(error))


if __name__ == "__main__":
    main()
