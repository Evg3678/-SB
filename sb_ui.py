"""Presentation helpers. Calculations and export values stay unchanged."""
import streamlit as st
from sb_data import display_summary, summarize, csv_bytes


def style():
    st.html("""<style>
    .stMainBlockContainer{max-width:1480px;padding-top:2.2rem;padding-bottom:3rem}
    h1{font-weight:720!important;letter-spacing:-.045em!important}
    h2,h3{letter-spacing:-.025em!important}
    [data-testid="stSidebar"]{border-right:1px solid rgba(128,145,165,.16)}
    [data-testid="stMetric"]{border:1px solid rgba(128,145,165,.22);
      border-radius:16px;padding:18px 20px;background:rgba(128,145,165,.045)}
    [data-testid="stMetricValue"]{font-variant-numeric:tabular-nums;font-size:clamp(1.25rem,2vw,2rem)}
    [data-testid="stMetricLabel"]{opacity:.8}
    [data-baseweb="tab-list"]{gap:1.5rem}
    [data-baseweb="tab"]{padding:12px 4px;font-weight:600}
    [data-testid="stDataFrame"]{border-radius:12px;overflow:hidden}
    .sb-eyebrow{font-size:.72rem;font-weight:700;letter-spacing:.18em;opacity:.7;margin:0 0 .5rem}
    .sb-test{border-left:3px solid #c28d3d;padding:.6rem .9rem;
      background:rgba(194,141,61,.09);border-radius:0 8px 8px 0;font-size:.83rem;margin:.5rem 0 1rem}
    @media(max-width:640px){.stMainBlockContainer{padding:1rem}
      [data-testid="stMetric"]{padding:12px}[data-baseweb="tab-list"]{gap:.6rem}}
    </style>""")


def summary_table(frame, group, key):
    full = display_summary(summarize(frame, [group]))
    id_cols = [c for c in ("ID чатов", "ID тредов") if c in full]
    st.dataframe(full.drop(columns=id_cols), hide_index=True, width="stretch",
        column_config={
            "Известная стоимость, ₽": st.column_config.NumberColumn("Известные расходы, ₽", format="%.6f"),
            "Средняя задержка, с": st.column_config.NumberColumn(format="%.3f"),
            "P95 задержка, с": st.column_config.NumberColumn(format="%.3f")})
    if id_cols:
        with st.expander("ID чатов и тредов · детализация"):
            st.caption("Уникальные ID текущей выборки. Все ID также включены в CSV и HTML.")
            st.dataframe(full[[full.columns[0]] + id_cols], hide_index=True, width="stretch")
    st.download_button("Скачать таблицу · CSV", csv_bytes(full), f"sb_{group}.csv",
                       "text/csv", key="csv_" + key)
