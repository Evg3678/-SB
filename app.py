"""Дашборд SB: внутренние логи. Все данные обрабатываются локально."""
import hashlib
import json
from datetime import datetime
from zipfile import BadZipFile
import streamlit as st
from sb_data import (CATEGORIES, LABELS, METRICS, excel_sheets, read_table,
                     normalize, filter_data, linked_filters, summarize, display_summary, details, csv_bytes)
from sb_report import heatmap, share, daily_figure, number, build_html

st.set_page_config(page_title="Дашборд SB", page_icon="📊", layout="wide")
st.title("Дашборд SB")
st.warning("Тестовый стенд. Только синтетические данные. Не загружайте реальные корпоративные логи и персональные данные.")
st.caption("Внутренняя аналитика вызовов LLM. Без авторизации и внешних API.")
st.sidebar.caption("Оформление: ⋮ → Settings → Theme → Light / Dark.")
st.sidebar.caption("Время: Москва (UTC+3).")


def render_dashboard(frame, source_name, source_id, issues):
    st.sidebar.header("Фильтры")
    timezone = "Europe/Moscow"
    dates = frame.started_at.dt.tz_convert(timezone).dt.date
    bounds = (dates.min(), dates.max())
    scope_key = source_id[:12] + timezone
    period = st.sidebar.date_input("Период", value=bounds, min_value=bounds[0],
                                   max_value=bounds[1], key="period_" + scope_key)
    if not isinstance(period, (tuple, list)) or len(period) != 2:
        st.info("Выберите начало и конец периода.")
        return
    keys = {col: "filter_" + source_id[:12] + col for col in CATEGORIES}
    selections, options, removed = linked_filters(
        frame, period[0], period[1], timezone,
        {col: st.session_state.get(key, []) for col, key in keys.items()})
    # Update all widget state before rendering any filter, avoiding order dependence.
    for col, key in keys.items():
        if st.session_state.get(key) != selections[col]:
            st.session_state[key] = selections[col]
    if removed:
        st.sidebar.info("Сняты недоступные значения фильтров: " +
                        ", ".join(LABELS[col] for col in removed))
    for col in CATEGORIES:
        selections[col] = st.sidebar.multiselect(
            LABELS[col], options[col], key=keys[col],
            help="Варианты учитывают период и остальные фильтры. Пустой выбор — все доступные значения.")
    selected = filter_data(frame, period[0], period[1], timezone, selections)
    context = {"period": [str(d) for d in period], "timezone": timezone,
               "selections": selections, "source": source_id}
    signature = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
    if st.session_state.get("report_signature") != signature:
        st.session_state.pop("sb_report", None)
    st.sidebar.caption(f"В выборке {len(selected):,} из {len(frame):,} вызовов.")
    if selected.empty:
        st.warning("Нет логов для выбранных фильтров.")
        return
    known = int(selected.cost_rub.notna().sum())
    total = len(selected)
    metrics = st.columns(4)
    metrics[0].metric("Вызовы LLM", f"{total:,}")
    metrics[1].metric("Токены", f"{int(selected.total_tokens.sum()):,}")
    metrics[2].metric("Известные расходы, ₽", number(selected.cost_rub.sum(min_count=1), 6))
    metrics[3].metric("Средняя задержка, с", number(selected.latency_sec.mean(), 3))
    counts = st.columns(4)
    counts[0].metric("Сотрудники", selected.employee_id.nunique())
    counts[1].metric("Чаты / треды", f"{selected.chat_id.nunique()} / {selected.thread_id.nunique()}")
    counts[2].metric("Ошибки", f"{selected.status.eq('error').sum()} ({selected.status.eq('error').mean():.1%})")
    counts[3].metric("Отмены", f"{selected.status.eq('cancelled').sum()} ({selected.status.eq('cancelled').mean():.1%})")
    st.caption(f"Стоимость указана у {known} из {total} вызовов ({known / total:.1%}). "
               "Пустые значения не заменяются нулями. В расходы входят все выбранные статусы.")
    if known < total:
        st.warning(f"Расходы неполные: для {total - known} вызовов стоимость отсутствует.")
    if issues:
        with st.expander("Качество исходных данных"):
            for issue in issues:
                st.write(issue)
    st.plotly_chart(daily_figure(selected), width="stretch")
    st.header("Статистика по разрезам")
    group = st.selectbox("Группировать по", ["model", "organization", "service",
                         "employee_id", "email", "provider", "status", "chat_id", "thread_id"],
                         format_func=lambda c: LABELS[c])
    summary = display_summary(summarize(selected, [group]))
    st.dataframe(summary, hide_index=True, width="stretch")
    st.download_button("Скачать сводку CSV", csv_bytes(summary), "sb_summary.csv", "text/csv")
    with st.expander("Тепловая карта", expanded=False):
        heat_label = st.radio("Показатель карты", list(METRICS), horizontal=True)
        st.caption("Все модели выборки. Для одного дня — интервалы 15 минут; иначе — дни. "
                   "Пустая ячейка суммы или задержки означает отсутствие данных.")
        st.plotly_chart(heatmap(selected, heat_label), width="stretch")
    st.header("Соотношение использования моделей")
    share_label = st.radio("Показатель долей", list(METRICS)[:3], horizontal=True)
    st.plotly_chart(share(selected, share_label), width="stretch")
    st.header("Детальная нагрузка по моделям")
    load = display_summary(summarize(selected, ["model", "date"]))
    st.dataframe(load, hide_index=True, width="stretch")
    with st.expander("Все вызовы выбранной выборки"):
        st.dataframe(details(selected, timezone), hide_index=True, width="stretch")
    st.download_button("Скачать выбранные логи CSV", csv_bytes(details(selected, timezone)),
                       "sb_logs.csv", "text/csv")
    st.header("HTML-отчёт")
    st.caption("Включает четыре тепловые карты, три диаграммы долей, сводки и все запросы "
               "выборки. Работает без интернета. Отчёт содержит внутренние идентификаторы и email.")
    filters_text = f"{period[0]} — {period[1]}; " + "; ".join(
        f"{LABELS[c]}: {', '.join(v)}" for c, v in selections.items() if v)
    if st.button("Сформировать HTML-отчёт", type="primary"):
        with st.spinner("Формирую локальный отчёт…"):
            st.session_state.sb_report = build_html(selected, source_name, timezone,
                                                    filters_text, issues)
            st.session_state.report_signature = signature
    if st.session_state.get("sb_report"):
        st.download_button("Скачать HTML-отчёт", st.session_state.sb_report.encode("utf-8"),
                           f"sb_report_{datetime.now():%Y%m%d_%H%M%S}.html", "text/html")


def main():
    st.subheader("Загрузка логов")
    st.button("Загрузить из внутренней системы — будущая интеграция", disabled=True,
              help="Нужны адрес источника, способ доступа, формат ответа и правила выгрузки.")
    uploaded = st.file_uploader("Выберите выгрузку XLSX или CSV", type=["xlsx", "csv", "txt"])
    if uploaded is None:
        for key in ("sb_data", "sb_issues", "sb_source_id", "sb_report", "report_signature"):
            st.session_state.pop(key, None)
        st.info("Загрузите внутреннюю выгрузку. Логин, пароль и мастер-ключ не нужны.")
        with st.expander("Формат данных"):
            from sb_data import SCHEMA
            st.write(", ".join(SCHEMA))
            st.caption("started_at и finished_at — ISO 8601. Время без смещения считается UTC. "
                       "Стоимость — рубли; duration_ms — миллисекунды. README не импортируется.")
        return
    content = uploaded.getvalue()
    sheet = None
    try:
        if uploaded.name.lower().endswith(".xlsx"):
            sheets = excel_sheets(content)
            default = sheets.index("Вызовы LLM") if "Вызовы LLM" in sheets else 0
            sheet = st.selectbox("Лист с логами", sheets, index=default)
        source_id = hashlib.sha256(content + str(sheet).encode()).hexdigest()
        if st.session_state.get("sb_source_id") != source_id or "sb_data" not in st.session_state:
            st.session_state.pop("sb_report", None)
            st.session_state.pop("sb_data", None)
            with st.spinner("Читаю и проверяю выгрузку…"):
                frame, issues = normalize(read_table(content, uploaded.name, sheet))
            st.session_state.sb_data = frame
            st.session_state.sb_issues = issues
            st.session_state.sb_source_id = source_id
        st.success(f"Загружено {len(st.session_state.sb_data):,} вызовов из {uploaded.name}")
        render_dashboard(st.session_state.sb_data, uploaded.name, source_id,
                         st.session_state.sb_issues)
    except (ValueError, ImportError, OSError, BadZipFile) as error:
        st.error(str(error))


if __name__ == "__main__":
    main()
