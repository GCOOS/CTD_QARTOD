"""Interactive Dash viewer for QC'd NetCDF outputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import xarray as xr

from dataset_profile import DatasetProfile, default_profile, resolve_config_path
from qc_config import QC_FLAGS
from qc_data_loader import flatten_mapping, get_coord_for_var, get_scalar_var, load_mapping

FLAG_ORDER = ("PASS", "NOT_EVALUATED", "SUSPECT", "FAIL", "MISSING")
FLAG_LABELS = {name: f"{name} ({QC_FLAGS[name]})" for name in FLAG_ORDER}
FLAG_COLORS = {
    "PASS": "#1f8f4d",
    "NOT_EVALUATED": "#9aa3ad",
    "SUSPECT": "#d97706",
    "FAIL": "#dc2626",
    "MISSING": "#2563eb",
}
FLAG_BY_VALUE = {value: name for name, value in QC_FLAGS.items()}

TEST_LABELS = {
    "agg": "Aggregate",
    "gap": "Gap",
    "syntax": "Syntax",
    "location": "Location",
    "gross_range": "Gross Range",
    "decreasing_radiance_test": "Decreasing Radiance",
    "climatology": "Climatology",
    "flat_line": "Flat Line",
    "spike": "Spike",
    "rate_of_change": "Rate Of Change",
}


@dataclass(frozen=True)
class QCTestOption:
    """A selectable QC result attached to a data variable."""

    label: str
    value: str
    qc_name: str


@dataclass(frozen=True)
class QCVariableOption:
    """A data variable with at least one selectable QC result."""

    label: str
    value: str


def default_viz_data_root(profile: DatasetProfile | None = None) -> Path:
    """Return the default NetCDF tree for visualization."""
    prof = profile or default_profile()
    if prof.output.mode == "duplicate":
        return prof.output.directory
    return prof.data_root


def discover_qc_files(data_root: Path | str) -> dict[str, list[Path]]:
    """Discover QC NetCDF files as ``{cruise: [files...]}``."""
    root = Path(data_root)
    if not root.exists() or not root.is_dir():
        return {}

    by_cruise: dict[str, list[Path]] = {}
    for path in sorted(root.glob("*/*.nc")):
        if path.is_file():
            by_cruise.setdefault(path.parent.name, []).append(path)
    return by_cruise


def _mapped_variable_names(mapping: dict[str, Iterable[str]]) -> list[str]:
    """Return mapped variable names in mapping-file order with duplicates removed."""
    names: list[str] = []
    seen: set[str] = set()
    for var_names in mapping.values():
        for name in var_names:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _test_label(var_name: str, qc_name: str, qc_var: xr.DataArray | None = None) -> str:
    prefix = f"{var_name}_qc_"
    suffix = qc_name[len(prefix) :] if qc_name.startswith(prefix) else qc_name
    label = TEST_LABELS.get(suffix, suffix.replace("_", " ").title())
    if qc_var is not None:
        long_name = str(qc_var.attrs.get("long_name") or "").strip()
        if long_name:
            marker = " Quality Flag"
            if long_name.endswith(marker):
                long_name = long_name[: -len(marker)]
            if label.lower() in long_name.lower():
                label = label
    return label


def _qc_names_from_ancillary(ds: xr.Dataset, var_name: str) -> list[str]:
    raw = str(ds[var_name].attrs.get("ancillary_variables") or "")
    names = [item for item in raw.split() if item in ds and item.startswith(f"{var_name}_qc_")]
    return names


def _qc_names_from_prefix(ds: xr.Dataset, var_name: str) -> list[str]:
    prefix = f"{var_name}_qc_"
    return sorted(name for name in ds.variables if name.startswith(prefix))


def discover_tests_for_variable(ds: xr.Dataset, var_name: str) -> list[QCTestOption]:
    """Return selectable QC tests for a variable, preferring ancillary metadata."""
    seen: set[str] = set()
    names: list[str] = []
    for name in [*_qc_names_from_ancillary(ds, var_name), *_qc_names_from_prefix(ds, var_name)]:
        if name not in seen:
            seen.add(name)
            names.append(name)

    def sort_key(name: str) -> tuple[int, str]:
        suffix = name.removeprefix(f"{var_name}_qc_")
        return (0 if suffix == "agg" else 1, TEST_LABELS.get(suffix, suffix))

    return [
        QCTestOption(label=_test_label(var_name, name, ds[name]), value=name, qc_name=name)
        for name in sorted(names, key=sort_key)
    ]


def discover_variables(
    ds: xr.Dataset,
    mapping: dict[str, Iterable[str]] | None = None,
) -> list[QCVariableOption]:
    """Return mapped numeric data variables that have at least one QC flag variable."""
    options: list[QCVariableOption] = []
    names = _mapped_variable_names(mapping) if mapping is not None else list(ds.data_vars)
    for name in names:
        if name not in ds or "_qc_" in name:
            continue
        data_var = ds[name]
        if not np.issubdtype(data_var.dtype, np.number):
            continue
        if not discover_tests_for_variable(ds, name):
            continue
        options.append(QCVariableOption(label=name, value=name))
    return options


def flag_summary(flags: Iterable[int]) -> list[dict[str, object]]:
    """Summarize QARTOD flags for display."""
    arr = np.asarray(flags).ravel()
    total = int(arr.size)
    rows: list[dict[str, object]] = []
    for name in FLAG_ORDER:
        value = QC_FLAGS[name]
        count = int(np.count_nonzero(arr == value))
        rows.append(
            {
                "flag": name,
                "value": value,
                "count": count,
                "percent": (count / total * 100.0) if total else 0.0,
                "color": FLAG_COLORS[name],
            }
        )
    return rows


def _broadcast_optional(source: xr.DataArray | None, target: xr.DataArray) -> np.ndarray | None:
    if source is None:
        return None
    src = np.asarray(source)
    if src.shape == target.shape:
        return src
    try:
        return np.broadcast_to(src, target.shape)
    except ValueError:
        pass
    if src.ndim == 1:
        for axis, size in enumerate(target.shape):
            if src.size == size:
                shape = [1] * len(target.shape)
                shape[axis] = src.size
                try:
                    return np.broadcast_to(src.reshape(shape), target.shape)
                except ValueError:
                    return None
    return None


def _depth_for_variable(ds: xr.Dataset, data_var: xr.DataArray, profile: DatasetProfile) -> np.ndarray | None:
    depth = get_coord_for_var(ds, data_var, profile.metadata.depth)
    return _broadcast_optional(depth, data_var)


def load_plot_data(
    nc_path: Path | str,
    variable: str,
    qc_name: str,
    profile: DatasetProfile | None = None,
) -> dict[str, object]:
    """Load flattened arrays and metadata for one variable/test selection."""
    prof = profile or default_profile()
    path = Path(nc_path)
    with xr.open_dataset(path, decode_cf=False, mask_and_scale=True) as ds:
        if variable not in ds:
            raise KeyError(f"Variable not found: {variable}")
        if qc_name not in ds:
            raise KeyError(f"QC variable not found: {qc_name}")

        data_var = ds[variable]
        values = np.asarray(data_var.values).ravel()
        flags = np.asarray(ds[qc_name].values, dtype=np.int16).ravel()
        if values.size != flags.size:
            raise ValueError(f"{variable} and {qc_name} do not have matching shapes")

        depth = _depth_for_variable(ds, data_var, prof)
        depth_values = np.asarray(depth).ravel() if depth is not None else None
        if depth_values is not None and depth_values.size != values.size:
            depth_values = None

        return {
            "path": str(path),
            "file_name": path.name,
            "cruise": path.parent.name,
            "title": ds.attrs.get("title") or path.name,
            "station": get_scalar_var(ds, prof.metadata.station) or "unknown",
            "cruise_id": get_scalar_var(ds, prof.metadata.cruise_id) or path.parent.name,
            "variable": variable,
            "variable_label": variable,
            "variable_units": str(data_var.attrs.get("units") or "").strip(),
            "qc_name": qc_name,
            "test_label": _test_label(variable, qc_name, ds[qc_name]),
            "index": np.arange(values.size),
            "values": values,
            "depth": depth_values,
            "flags": flags,
            "summary": flag_summary(flags),
        }


def _dropdown_options(items: Iterable[object]) -> list[dict[str, str]]:
    return [{"label": item.label, "value": item.value} for item in items]


def _file_options(files: Iterable[Path]) -> list[dict[str, str]]:
    return [{"label": path.name, "value": str(path)} for path in files]


def adjacent_file(files: Iterable[Path], current: Path | str | None, direction: int) -> str | None:
    """Return the previous/next file path string within a cruise file list."""
    paths = [str(path) for path in files]
    if not paths:
        return None
    if current not in paths:
        return paths[0]
    idx = paths.index(str(current))
    next_idx = idx + direction
    if 0 <= next_idx < len(paths):
        return paths[next_idx]
    return paths[idx]


def _empty_figure(message: str):
    import plotly.graph_objects as go

    fig = go.Figure()
    fig.add_annotation(text=message, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
    fig.update_layout(template="plotly_white", height=620, margin={"l": 56, "r": 24, "t": 48, "b": 48})
    return fig


def _make_figure(payload: dict[str, object], visible_flags: list[str] | None):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    visible = set(visible_flags or FLAG_ORDER)
    idx = np.asarray(payload["index"])
    values = np.asarray(payload["values"])
    depth = payload["depth"]
    flags = np.asarray(payload["flags"])
    units = payload["variable_units"]
    variable_label = payload["variable"]
    value_title = f"{variable_label} ({units})" if units else str(variable_label)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.1,
        subplot_titles=("Depth by sample index", "Variable value by sample index"),
    )

    if depth is not None:
        fig.add_trace(
            go.Scattergl(
                x=idx,
                y=np.asarray(depth),
                mode="lines",
                line={"color": "#8aa0b2", "width": 1.2},
                name="Depth path",
                hoverinfo="skip",
                showlegend=False,
            ),
            row=1,
            col=1,
        )
    fig.add_trace(
        go.Scattergl(
            x=idx,
            y=values,
            mode="lines",
            line={"color": "#5f6f5b", "width": 1.2},
            name="Value path",
            hoverinfo="skip",
            showlegend=False,
        ),
        row=2,
        col=1,
    )

    for flag_name in FLAG_ORDER:
        if flag_name not in visible:
            continue
        flag_value = QC_FLAGS[flag_name]
        mask = flags == flag_value
        if not np.any(mask):
            continue
        color = FLAG_COLORS[flag_name]
        label = FLAG_LABELS[flag_name]
        if depth is not None:
            depth_values = np.asarray(depth)
            fig.add_trace(
                go.Scattergl(
                    x=idx[mask],
                    y=depth_values[mask],
                    mode="markers",
                    marker={"size": 6, "color": color, "opacity": 0.78},
                    name=label,
                    legendgroup=flag_name,
                    showlegend=False,
                    hovertemplate="index=%{x}<br>depth=%{y}<extra>" + label + "</extra>",
                ),
                row=1,
                col=1,
            )
        fig.add_trace(
            go.Scattergl(
                x=idx[mask],
                y=values[mask],
                mode="markers",
                marker={"size": 6, "color": color, "opacity": 0.82},
                name=label,
                legendgroup=flag_name,
                hovertemplate="index=%{x}<br>value=%{y}<extra>" + label + "</extra>",
            ),
            row=2,
            col=1,
        )

    if depth is None:
        fig.add_annotation(
            text="Depth unavailable for this variable",
            x=0.5,
            y=0.78,
            xref="paper",
            yref="paper",
            showarrow=False,
            font={"color": "#64748b", "size": 14},
        )
    else:
        fig.update_yaxes(title_text="depth", autorange="reversed", row=1, col=1)

    fig.update_yaxes(title_text=value_title, row=2, col=1)
    fig.update_xaxes(title_text="sample index", row=2, col=1)
    fig.update_layout(
        template="plotly_white",
        height=720,
        margin={"l": 70, "r": 28, "t": 70, "b": 56},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.04, "xanchor": "left", "x": 0},
        paper_bgcolor="#f7f8f5",
        plot_bgcolor="#ffffff",
    )
    return fig


def create_app(data_root: Path | str | None = None, profile: DatasetProfile | None = None):
    """Create the Dash QC viewer app."""
    from dash import Dash, Input, Output, State, callback_context, dash_table, dcc, html

    prof = profile or default_profile()
    root = Path(data_root) if data_root is not None else default_viz_data_root(prof)
    mapping = load_mapping(resolve_config_path("variable_mapping", prof))
    mapped_names = flatten_mapping(mapping)
    files_by_cruise = discover_qc_files(root)
    cruise_options = [{"label": cruise, "value": cruise} for cruise in sorted(files_by_cruise)]
    initial_cruise = cruise_options[0]["value"] if cruise_options else None
    initial_file = str(files_by_cruise[initial_cruise][0]) if initial_cruise else None

    app = Dash(__name__, title="CTD QC Viewer")

    app.layout = html.Div(
        className="app-shell",
        children=[
            dcc.Store(id="data-root", data=str(root)),
            html.Header(
                className="topbar",
                children=[
                    html.Div(
                        [
                            html.P("SFER CTD", className="eyebrow"),
                            html.H1("QC result viewer"),
                        ]
                    ),
                    html.Div(
                        [
                            html.Span("Data root", className="meta-label"),
                            html.Code(str(root), className="root-path"),
                        ],
                        className="root-chip",
                    ),
                ],
            ),
            html.Main(
                className="workspace",
                children=[
                    html.Aside(
                        className="controls",
                        children=[
                            html.Label("Cruise", htmlFor="cruise-dropdown"),
                            dcc.Dropdown(id="cruise-dropdown", options=cruise_options, value=initial_cruise, clearable=False),
                            html.Label("Files in cruise", htmlFor="file-radio"),
                            html.Div(
                                dcc.RadioItems(
                                    id="file-radio",
                                    options=[],
                                    value=None,
                                    labelStyle={
                                        "display": "block",
                                        "margin": "4px 0",
                                        "fontSize": "12px",
                                        "wordBreak": "break-all",
                                    },
                                    inputStyle={"marginRight": "6px"},
                                ),
                                className="file-list",
                            ),
                            html.Div(
                                [
                                    html.Button("Prev", id="btn-prev-file", n_clicks=0, className="nav-button"),
                                    html.Button("Next", id="btn-next-file", n_clicks=0, className="nav-button"),
                                ],
                                className="file-nav",
                            ),
                            html.Label("Variable", htmlFor="variable-dropdown"),
                            dcc.Dropdown(id="variable-dropdown", clearable=False),
                            html.Label("QC test", htmlFor="test-dropdown"),
                            dcc.Dropdown(id="test-dropdown", clearable=False),
                            html.Label("Visible flags", htmlFor="flag-checklist"),
                            dcc.Checklist(
                                id="flag-checklist",
                                options=[{"label": FLAG_LABELS[name], "value": name} for name in FLAG_ORDER],
                                value=list(FLAG_ORDER),
                                className="flag-list",
                            ),
                            html.Div(id="status-message", className="status-message"),
                        ],
                    ),
                    html.Section(
                        className="results",
                        children=[
                            html.Div(id="file-metadata", className="metadata-strip"),
                            dcc.Graph(id="qc-graph", config={"displaylogo": False, "responsive": True}),
                            dash_table.DataTable(
                                id="summary-table",
                                columns=[
                                    {"name": "Flag", "id": "flag"},
                                    {"name": "Value", "id": "value"},
                                    {"name": "Count", "id": "count"},
                                    {"name": "Percent", "id": "percent", "type": "numeric", "format": {"specifier": ".2f"}},
                                ],
                                data=[],
                                style_as_list_view=True,
                                style_cell={"padding": "10px 12px", "fontFamily": "system-ui", "fontSize": "14px"},
                                style_header={"backgroundColor": "#edf1ea", "fontWeight": "700"},
                                style_data_conditional=[
                                    {
                                        "if": {"filter_query": f"{{flag}} = '{name}'"},
                                        "borderLeft": f"5px solid {FLAG_COLORS[name]}",
                                    }
                                    for name in FLAG_ORDER
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )

    app.index_string = """
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <style>
            :root {
                --bg: #f7f8f5;
                --ink: #182016;
                --muted: #667263;
                --panel: #ffffff;
                --line: #d9dfd3;
                --accent: #315c47;
            }
            * { box-sizing: border-box; }
            body {
                margin: 0;
                background: var(--bg);
                color: var(--ink);
                font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            }
            .app-shell { min-height: 100vh; }
            .topbar {
                display: flex;
                align-items: flex-end;
                justify-content: space-between;
                gap: 24px;
                padding: 28px 34px 20px;
                border-bottom: 1px solid var(--line);
            }
            .eyebrow {
                margin: 0 0 6px;
                color: var(--accent);
                font-size: 12px;
                font-weight: 800;
                letter-spacing: .08em;
                text-transform: uppercase;
            }
            h1 { margin: 0; font-size: 34px; line-height: 1.08; letter-spacing: 0; }
            .root-chip {
                max-width: min(58vw, 780px);
                display: grid;
                gap: 4px;
                justify-items: end;
                color: var(--muted);
                font-size: 13px;
            }
            .meta-label { font-weight: 700; }
            .root-path {
                color: #263322;
                background: #e7ece2;
                border: 1px solid #d5ddcf;
                border-radius: 6px;
                padding: 7px 9px;
                white-space: normal;
                overflow-wrap: anywhere;
            }
            .workspace {
                display: grid;
                grid-template-columns: minmax(260px, 330px) minmax(0, 1fr);
                gap: 24px;
                padding: 24px 34px 36px;
            }
            .controls {
                align-self: start;
                display: grid;
                gap: 10px;
                padding: 18px;
                background: var(--panel);
                border: 1px solid var(--line);
                border-radius: 8px;
            }
            .controls label {
                margin-top: 8px;
                color: #33402f;
                font-size: 13px;
                font-weight: 750;
            }
            .flag-list label {
                display: block;
                margin: 8px 0;
                color: #33402f;
                font-size: 14px;
            }
            .file-list {
                max-height: 34vh;
                overflow-y: auto;
                margin-top: -2px;
                padding: 7px 8px;
                border: 1px solid var(--line);
                border-radius: 6px;
                background: #fbfcfa;
            }
            .file-nav {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 8px;
            }
            .nav-button {
                min-height: 34px;
                border: 1px solid #cbd6c5;
                border-radius: 6px;
                background: #edf3e9;
                color: #253520;
                font-weight: 750;
                cursor: pointer;
            }
            .nav-button:hover { background: #e1ebdc; }
            .status-message {
                min-height: 20px;
                color: #8a3b12;
                font-size: 13px;
                line-height: 1.45;
            }
            .results {
                min-width: 0;
                display: grid;
                gap: 16px;
            }
            .metadata-strip {
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 10px;
            }
            .metadata-item {
                background: #ffffff;
                border: 1px solid var(--line);
                border-radius: 8px;
                padding: 12px 14px;
                min-width: 0;
            }
            .metadata-item span {
                display: block;
                color: var(--muted);
                font-size: 12px;
                font-weight: 750;
            }
            .metadata-item strong {
                display: block;
                margin-top: 4px;
                overflow-wrap: anywhere;
                font-size: 14px;
                line-height: 1.35;
            }
            #qc-graph {
                overflow: hidden;
                border: 1px solid var(--line);
                border-radius: 8px;
                background: #ffffff;
            }
            @media (max-width: 980px) {
                .topbar { display: grid; align-items: start; }
                .root-chip { max-width: 100%; justify-items: start; }
                .workspace { grid-template-columns: 1fr; padding: 18px; }
                .metadata-strip { grid-template-columns: 1fr 1fr; }
            }
            @media (max-width: 620px) {
                h1 { font-size: 28px; }
                .metadata-strip { grid-template-columns: 1fr; }
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
"""

    @app.callback(
        Output("file-radio", "options"),
        Output("file-radio", "value"),
        Input("cruise-dropdown", "value"),
        Input("btn-prev-file", "n_clicks"),
        Input("btn-next-file", "n_clicks"),
        State("file-radio", "value"),
    )
    def sync_file_controls(
        cruise: str | None,
        _prev_clicks: int,
        _next_clicks: int,
        current_file: str | None,
    ):
        if not cruise or cruise not in files_by_cruise:
            return [], None
        files = files_by_cruise[cruise]
        options = _file_options(files)
        values = {item["value"] for item in options}
        triggered = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else ""

        if triggered == "btn-prev-file":
            value = adjacent_file(files, current_file, -1)
        elif triggered == "btn-next-file":
            value = adjacent_file(files, current_file, 1)
        else:
            value = current_file if current_file in values else (options[0]["value"] if options else None)
        return options, value

    @app.callback(
        Output("variable-dropdown", "options"),
        Output("variable-dropdown", "value"),
        Input("file-radio", "value"),
        State("variable-dropdown", "value"),
    )
    def update_variables(file_path: str | None, current: str | None):
        if not file_path:
            return [], None
        try:
            with xr.open_dataset(file_path, decode_cf=False, mask_and_scale=True) as ds:
                options = _dropdown_options(discover_variables(ds, mapping))
        except Exception:
            return [], None
        values = {item["value"] for item in options}
        value = current if current in values else (options[0]["value"] if options else None)
        return options, value

    @app.callback(
        Output("test-dropdown", "options"),
        Output("test-dropdown", "value"),
        Input("file-radio", "value"),
        Input("variable-dropdown", "value"),
        State("test-dropdown", "value"),
    )
    def update_tests(file_path: str | None, variable: str | None, current: str | None):
        if not file_path or not variable:
            return [], None
        try:
            with xr.open_dataset(file_path, decode_cf=False, mask_and_scale=True) as ds:
                options = _dropdown_options(discover_tests_for_variable(ds, variable))
        except Exception:
            return [], None
        values = {item["value"] for item in options}
        value = current if current in values else (options[0]["value"] if options else None)
        return options, value

    @app.callback(
        Output("qc-graph", "figure"),
        Output("summary-table", "data"),
        Output("file-metadata", "children"),
        Output("status-message", "children"),
        Input("file-radio", "value"),
        Input("variable-dropdown", "value"),
        Input("test-dropdown", "value"),
        Input("flag-checklist", "value"),
    )
    def update_view(file_path: str | None, variable: str | None, qc_name: str | None, visible_flags: list[str] | None):
        if not root.exists():
            return _empty_figure("QC data root does not exist"), [], [], f"Missing data root: {root}"
        if not files_by_cruise:
            return _empty_figure("No QC NetCDF files found"), [], [], f"No .nc files found under {root}"
        if not file_path or not variable or not qc_name:
            return _empty_figure("Choose a file, variable, and QC test"), [], [], ""

        try:
            payload = load_plot_data(file_path, variable, qc_name, prof)
        except Exception as exc:
            return _empty_figure(str(exc)), [], [], str(exc)

        if variable not in mapped_names:
            return _empty_figure(f"{variable} is not present in the variable mapping"), [], [], (
                f"{variable} is not present in {resolve_config_path('variable_mapping', prof)}"
            )

        metadata = [
            html.Div([html.Span("Cruise"), html.Strong(str(payload["cruise_id"]))], className="metadata-item"),
            html.Div([html.Span("Station"), html.Strong(str(payload["station"]))], className="metadata-item"),
            html.Div([html.Span("File"), html.Strong(str(payload["file_name"]))], className="metadata-item"),
            html.Div([html.Span("Selection"), html.Strong(f"{payload['variable']} / {payload['test_label']}")], className="metadata-item"),
        ]
        return _make_figure(payload, visible_flags), payload["summary"], metadata, ""

    return app


def run_dashboard(
    data_root: Path | str | None = None,
    profile: DatasetProfile | None = None,
    host: str = "127.0.0.1",
    port: int = 8050,
    debug: bool = False,
) -> None:
    """Run the Dash development server."""
    app = create_app(data_root=data_root, profile=profile)
    app.run(host=host, port=port, debug=debug)
