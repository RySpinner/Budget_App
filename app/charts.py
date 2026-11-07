"""Chart builders for the Dashboard (Altair).

Colors come from a validated palette (light and dark variants, checked for
colorblind separation). Data is passed in as integer cents; the axes turn
cents into "$" labels inside the chart, and tooltips use pre-formatted text,
so no money is converted to floating point in Python.
"""

import altair as alt
import pandas as pd

# Categorical slots, in fixed order. A category keeps its slot (by its sort
# order), so filtering never repaints the others.
SERIES = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
    "dark":  ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
}
INK = {
    "light": {"primary": "#0b0b0b", "secondary": "#52514e", "muted": "#898781",
              "grid": "#e1e0d9", "axis": "#c3c2b7", "surface": "#fcfcfb", "track": "#cde2fb"},
    "dark":  {"primary": "#f0efec", "secondary": "#c3c2b7", "muted": "#898781",
              "grid": "#2c2c2a", "axis": "#383835", "surface": "#1a1a19", "track": "#0d366b"},
}
UNCATEGORIZED = "Uncategorized"
DOLLAR_LABELS = "'$' + format(datum.value / 100, ',.0f')"


def _axis(mode: str, **overrides) -> alt.Axis:
    ink = INK[mode]
    settings = dict(labelColor=ink["muted"], titleColor=ink["secondary"], gridColor=ink["grid"],
                    domainColor=ink["axis"], tickColor=ink["axis"])
    settings.update(overrides)
    return alt.Axis(**settings)


def _money_axis(mode: str, title: str | None = None) -> alt.Axis:
    return _axis(mode, labelExpr=DOLLAR_LABELS, title=title, grid=True, format="d", tickCount=6)


def _finish(chart: alt.Chart, mode: str) -> alt.Chart:
    ink = INK[mode]
    return (chart.configure_view(strokeWidth=0)
                 .configure_legend(labelColor=ink["secondary"], titleColor=ink["secondary"], orient="top")
                 .configure(background="transparent"))


def category_colors(names_in_order: list[str], mode: str) -> alt.Scale:
    """Fixed color per category name (slot = position in the category sort order); gray for uncategorized."""
    palette = SERIES[mode]
    domain = list(names_in_order) + [UNCATEGORIZED]
    rng = [palette[i % len(palette)] for i in range(len(names_in_order))] + [INK[mode]["muted"]]
    return alt.Scale(domain=domain, range=rng)


def pace_chart(df: pd.DataFrame, mode: str) -> alt.Chart:
    """Bullet chart: spent bar, faint full-budget track, and a tick at budget-to-date.

    df columns: Category, spent, budget (nullable), pace (nullable), plus text
    columns Spent, Budget, Pace to date, Status for the tooltip.
    """
    ink, blue = INK[mode], SERIES[mode][0]
    order = list(df["Category"])
    tooltip = ["Category", "Spent", "Pace to date", "Budget", "Status"]
    y = alt.Y("Category:N", sort=order, title=None,
              axis=_axis(mode, labelColor=ink["secondary"], ticks=False, labelLimit=220))
    base = alt.Chart(df).encode(y=y, tooltip=tooltip)

    track = base.mark_bar(color=ink["track"], height=18, cornerRadiusEnd=4).encode(
        x=alt.X("budget:Q", axis=_money_axis(mode))
    ).transform_filter("isValid(datum.budget)")
    spent = base.mark_bar(color=blue, height=10, cornerRadiusEnd=4).encode(x="spent:Q")
    pace = base.mark_tick(color=ink["primary"], thickness=2, size=26).encode(
        x="pace:Q"
    ).transform_filter("isValid(datum.pace)")

    return _finish(alt.layer(track, spent, pace).properties(height=alt.Step(40)), mode)


def _month_order(df: pd.DataFrame) -> list[str]:
    """Month labels in date order. Sorting the axis by month_start directly isn't
    safe: on a stacked chart Vega-Lite sorts by the *sum* of that field across a
    column's segments, so a month with more categories would sort later."""
    return list(df.sort_values("month_start")["Month"].drop_duplicates())


def trend_chart(df: pd.DataFrame, categories_in_order: list[str], mode: str) -> alt.Chart:
    """Stacked monthly columns by top-level category.

    df columns: Month (label), month_start (date), Category, cents, Amount (text).
    """
    ink = INK[mode]
    order = categories_in_order + [UNCATEGORIZED]
    df = df.assign(stack_order=df["Category"].map({name: i for i, name in enumerate(order)}))
    chart = alt.Chart(df).mark_bar(stroke=ink["surface"], strokeWidth=2, cornerRadius=2).encode(
        x=alt.X("Month:N", sort=_month_order(df), title=None,
                axis=_axis(mode, labelAngle=0, labelColor=ink["secondary"])),
        y=alt.Y("sum(cents):Q", title=None, axis=_money_axis(mode)),
        color=alt.Color("Category:N", scale=category_colors(categories_in_order, mode), title=None),
        order=alt.Order("stack_order:Q"),
        tooltip=["Month", "Category", "Amount"],
    ).properties(height=300)
    return _finish(chart, mode)


def ranked_bars(df: pd.DataFrame, mode: str) -> alt.Chart:
    """Horizontal bars, one hue, largest first. df columns: Subcategory, cents, Amount (text)."""
    ink = INK[mode]
    chart = alt.Chart(df).mark_bar(color=SERIES[mode][0], height=14, cornerRadiusEnd=4).encode(
        y=alt.Y("Subcategory:N", sort="-x", title=None,
                axis=_axis(mode, labelColor=ink["secondary"], ticks=False, labelLimit=220)),
        x=alt.X("cents:Q", title=None, axis=_money_axis(mode)),
        tooltip=["Subcategory", "Amount"],
    ).properties(height=alt.Step(32))
    return _finish(chart, mode)


def category_trend_chart(df: pd.DataFrame, mode: str) -> alt.Chart:
    """Spent per month (solid line + points) against the monthly budget (dashed step line).

    df columns: Month (label), month_start, Series ("Spent"/"Budget"), cents, Amount (text).
    """
    ink, blue = INK[mode], SERIES[mode][0]
    color = alt.Color("Series:N", scale=alt.Scale(domain=["Spent", "Budget"], range=[blue, ink["secondary"]]),
                      title=None, legend=alt.Legend(orient="top", symbolType="stroke"))
    dash = alt.StrokeDash("Series:N", scale=alt.Scale(domain=["Spent", "Budget"], range=[[1, 0], [5, 4]]),
                          legend=None)
    base = alt.Chart(df).encode(
        x=alt.X("Month:N", sort=_month_order(df), title=None,
                axis=_axis(mode, labelAngle=0, labelColor=ink["secondary"])),
        y=alt.Y("cents:Q", title=None, axis=_money_axis(mode)),
        color=color,
        tooltip=["Month", "Series", "Amount"],
    )
    lines = base.mark_line(strokeWidth=2, interpolate="step-after").encode(strokeDash=dash).transform_filter(
        "datum.Series == 'Budget'")
    spent = base.mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=64, filled=True)).transform_filter(
        "datum.Series == 'Spent'")
    return _finish(alt.layer(lines, spent).properties(height=260), mode)
