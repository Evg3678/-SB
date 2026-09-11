"""Local-only loading, validation and aggregation of internal LLM logs."""
from io import BytesIO, StringIO
from pathlib import Path
import csv
import numpy as np
import pandas as pd

SCHEMA = {
    "email": "email", "организация": "organization",
    "ID сотрудника": "employee_id", "ID чата": "chat_id", "ID треда": "thread_id",
    "сервис": "service", "модель": "model", "провайдер": "provider",
    "статус": "status", "prompt_tokens": "prompt_tokens",
    "completion_tokens": "completion_tokens", "total_tokens": "total_tokens",
    "стоимость, руб": "cost_rub", "started_at": "started_at",
    "finished_at": "finished_at", "duration_ms": "duration_ms",
}
LABELS = {v: k for k, v in SCHEMA.items()}
LABELS.update({"latency_sec": "Длительность, с", "source_row": "Строка источника"})
CATEGORIES = ["organization", "employee_id", "email", "service", "model",
              "provider", "status", "chat_id", "thread_id"]
MISSING = "(не указано)"
METRICS = {
    "Сумма, ₽": ("cost_rub", "sum"),
    "Количество запросов": ("source_row", "size"),
    "Количество токенов": ("total_tokens", "sum"),
    "Средняя задержка, с": ("latency_sec", "mean"),
}


def excel_sheets(content):
    with pd.ExcelFile(BytesIO(content), engine="openpyxl") as book:
        return book.sheet_names


def read_table(content, filename, sheet=None):
    if len(content) > 100 * 1024 * 1024:
        raise ValueError("Файл превышает 100 МБ.")
    ext = Path(filename).suffix.lower()
    if ext == ".xlsx":
        return pd.read_excel(BytesIO(content), sheet_name=sheet or 0,
                             engine="openpyxl", dtype=object, keep_default_na=False)
    if ext not in (".csv", ".txt"):
        raise ValueError("Поддерживаются XLSX и CSV/TXT.")
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeError:
            continue
    else:
        raise ValueError("Не удалось определить кодировку CSV. Сохраните CSV в UTF-8.")
    try:
        delimiter = csv.Sniffer().sniff(text[:16000], delimiters=";,\t").delimiter
    except csv.Error:
        delimiter = ";"
    return pd.read_csv(StringIO(text), sep=delimiter, dtype=object,
                       keep_default_na=False, on_bad_lines="error")


def normalize(raw):
    """Reject malformed records; retain missing cost, duplicates and all statuses."""
    frame = raw.copy()
    frame.columns = [str(c).strip().lstrip("\ufeff") for c in frame.columns]
    if frame.columns.duplicated().any():
        raise ValueError("Обнаружены повторяющиеся названия колонок.")
    absent = [name for name in SCHEMA if name not in frame.columns]
    if absent:
        raise ValueError("Не хватает колонок: " + ", ".join(absent))
    frame = frame[list(SCHEMA)].rename(columns=SCHEMA)
    frame = frame.replace(r"^\s*$", pd.NA, regex=True)
    frame = frame.dropna(how="all").copy()
    if frame.empty:
        raise ValueError("В выбранном листе нет строк логов.")
    frame["source_row"] = frame.index + 2
    issues = []
    for col in CATEGORIES:
        frame[col] = frame[col].fillna(MISSING).astype(str).str.strip()
    frame["status"] = frame["status"].str.lower()
    for col in ("prompt_tokens", "completion_tokens", "total_tokens", "cost_rub", "duration_ms"):
        source = frame[col]
        parsed = pd.to_numeric(source.astype("string").str.replace(" ", "", regex=False)
                               .str.replace("\u00a0", "", regex=False)
                               .str.replace(",", ".", regex=False), errors="coerce")
        bad = ((source.notna() & parsed.isna()) | (parsed.notna() & ~np.isfinite(parsed)))
        if col != "cost_rub":
            bad |= parsed.isna()
        bad |= parsed.lt(0).fillna(False)
        if col.endswith("tokens"):
            bad |= parsed.mod(1).ne(0).fillna(False)
        if bad.any():
            rows = frame.loc[bad, "source_row"].head(5).tolist()
            raise ValueError(f"Некорректные значения «{LABELS[col]}»: строки {rows}. "
                             "Загрузка остановлена; строки не отброшены.")
        frame[col] = parsed.astype(float) if col == "cost_rub" else parsed
    for col in ("started_at", "finished_at"):
        source = frame[col]
        parsed = pd.to_datetime(source, format="mixed", utc=True, errors="coerce")
        if parsed.isna().any():
            rows = frame.loc[parsed.isna(), "source_row"].head(5).tolist()
            raise ValueError(f"Не распознано время {col}: строки {rows}.")
        frame[col] = parsed
    elapsed = (frame.finished_at - frame.started_at).dt.total_seconds() * 1000
    if elapsed.lt(0).any():
        raise ValueError("Есть строки, в которых finished_at раньше started_at.")
    mismatch = frame.total_tokens.ne(frame.prompt_tokens + frame.completion_tokens)
    if mismatch.any():
        issues.append(f"Токены: {int(mismatch.sum())} несовпадений total_tokens с суммой "
                      "prompt_tokens + completion_tokens. Используется total_tokens из источника.")
    if (elapsed - frame.duration_ms).abs().gt(1).any():
        issues.append("Есть расхождения duration_ms и разницы времени более 1 мс. "
                      "Для задержки используется duration_ms.")
    duplicate_count = int(frame.drop(columns="source_row").duplicated().sum())
    if duplicate_count:
        issues.append(f"Полностью совпадающих строк: {duplicate_count}. "
                      "Все сохранены: уникального ID запроса в формате нет.")
    missing_cost = int(frame.cost_rub.isna().sum())
    if missing_cost:
        issues.append(f"Стоимость не указана у {missing_cost} вызовов. "
                      "Они учтены в запросах и токенах, но не в известных расходах.")
    unknown = set(frame.status) - {"success", "error", "cancelled"}
    if unknown:
        issues.append("Есть другие статусы. Они сохранены и доступны в фильтре.")
    frame["latency_sec"] = frame.duration_ms.astype(float) / 1000
    return frame.reset_index(drop=True), issues


def filter_data(frame, start, end, timezone="UTC", selections=None):
    dates = frame.started_at.dt.tz_convert(timezone).dt.date
    mask = dates.ge(start) & dates.le(end)
    for col, values in (selections or {}).items():
        if values:
            mask &= frame[col].isin(values)
    result = frame.loc[mask].copy()
    result["local_time"] = result.started_at.dt.tz_convert(timezone)
    result["date"] = result.local_time.dt.date
    return result


def linked_filters(frame, start, end, timezone, selections):
    """Facets use all other filters; choices within one facet are OR-ed."""
    period = filter_data(frame, start, end, timezone)
    current = {col: list(selections.get(col, [])) for col in CATEGORIES}
    removed = {}
    while True:
        options = {}
        for col in CATEGORIES:
            mask = pd.Series(True, index=period.index)
            for other, values in current.items():
                if other != col and values:
                    mask &= period[other].isin(values)
            options[col] = sorted(period.loc[mask, col].unique())
        cleaned = {col: [v for v in current[col] if v in options[col]]
                   for col in CATEGORIES}
        if cleaned == current:
            return current, options, removed
        for col in CATEGORIES:
            lost = [v for v in current[col] if v not in cleaned[col]]
            if lost:
                removed.setdefault(col, []).extend(lost)
        current = cleaned


def summarize(frame, by):
    result = frame.groupby(by, dropna=False, sort=True).agg(
        requests=("source_row", "size"),
        prompt_tokens=("prompt_tokens", "sum"),
        completion_tokens=("completion_tokens", "sum"),
        total_tokens=("total_tokens", "sum"),
        cost_rub=("cost_rub", lambda x: x.sum(min_count=1)),
        missing_cost=("cost_rub", lambda x: int(x.isna().sum())),
        latency_mean=("latency_sec", "mean"),
        latency_p95=("latency_sec", lambda x: x.quantile(.95)),
        errors=("status", lambda x: int(x.eq("error").sum())),
        cancelled=("status", lambda x: int(x.eq("cancelled").sum())),
    ).reset_index()
    # Employee summaries retain their chat/thread identifiers without splitting
    # the employee's metrics into multiple rows or duplicating request totals.
    if set(by) & {"employee_id", "email", "chat_id", "thread_id"}:
        aggregations = {}
        for column, prefix in (("chat_id", "chat"), ("thread_id", "thread")):
            if column not in by:
                aggregations[prefix + "_count"] = (
                    column, lambda s: s[s.notna() & s.ne(MISSING)].nunique())
                aggregations[prefix + "_ids"] = (
                    column, lambda s: "; ".join(sorted(set(s.dropna()) - {MISSING})))
        if aggregations:
            identifiers = frame.groupby(by, dropna=False, sort=True).agg(
                **aggregations).reset_index()
            result = result.merge(identifiers, on=by, how="left", validate="one_to_one")
    return result


def display_summary(frame):
    return frame.rename(columns={
        **LABELS, "requests": "Запросы", "cost_rub": "Известная стоимость, ₽",
        "missing_cost": "Без стоимости", "latency_mean": "Средняя задержка, с",
        "latency_p95": "P95 задержки, с", "errors": "Ошибки", "cancelled": "Отмены",
        "date": "Дата",
        "chat_count": "Уникальных чатов", "chat_ids": "ID чатов",
        "thread_count": "Уникальных тредов", "thread_ids": "ID тредов",
    })


def details(frame, timezone="UTC"):
    cols = ["source_row"] + list(SCHEMA.values())
    result = frame[cols].copy()
    for col in ("started_at", "finished_at"):
        result[col] = result[col].dt.tz_convert(timezone).astype(str)
    return result.rename(columns=LABELS)


def csv_bytes(frame):
    # Prevent spreadsheet formula interpretation of user-supplied text.
    safe = frame.copy()
    for col in safe.select_dtypes(include=["object", "string"]).columns:
        safe[col] = safe[col].map(
            lambda v: "'" + v if isinstance(v, str) and v.lstrip().startswith(
                ("=", "+", "-", "@", "\t", "\r", "\n")) else v)
    return safe.to_csv(index=False, sep=";", na_rep="").encode("utf-8-sig")
