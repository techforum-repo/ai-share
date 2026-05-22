import os
import json
import asyncio
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from dotenv import load_dotenv

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()

st.set_page_config(page_title="Adobe CJA MCP Reporting", layout="wide")
st.title(os.getenv("APP_TITLE", "Adobe CJA MCP Reporting UI"))


with st.sidebar:
    st.header("CJA MCP Settings")

    endpoint = st.text_input(
        "MCP Endpoint",
        os.getenv("ADOBE_CJA_MCP_URL", "https://cja-mcp.adobe.io/mcp")
    )

    access_token = st.text_input(
        "Access Token",
        os.getenv("ADOBE_ACCESS_TOKEN", ""),
        type="password"
    )

    client_id = st.text_input(
        "Client ID / x-api-key",
        os.getenv("ADOBE_CLIENT_ID", "")
    )

    ims_org_id = st.text_input(
        "IMS Org ID",
        os.getenv("ADOBE_IMS_ORG_ID", "")
    )


def headers():
    return {
        "Authorization": f"Bearer {access_token.strip()}",
        "x-api-key": client_id.strip(),
        "x-gw-ims-org-id": ims_org_id.strip(),
        "Accept": "application/json, text/event-stream",
    }


async def call_tool_async(tool_name: str, arguments: dict):
    async with streamablehttp_client(
        endpoint.strip(),
        headers=headers(),
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool_name, arguments)


def call_tool(tool_name: str, arguments: dict):
    try:
        return asyncio.run(call_tool_async(tool_name, arguments))
    except Exception as e:
        sub = getattr(e, "exceptions", None)
        if sub:
            raise Exception("\n".join(str(x) for x in sub))
        raise


async def list_tools_async():
    async with streamablehttp_client(
        endpoint.strip(),
        headers=headers(),
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.list_tools()


def list_tools():
    try:
        return asyncio.run(list_tools_async())
    except Exception as e:
        sub = getattr(e, "exceptions", None)
        if sub:
            raise Exception("\n".join(str(x) for x in sub))
        raise


def extract_json(result):
    if hasattr(result, "content"):
        texts = []
        parsed = []
        for item in result.content:
            text = getattr(item, "text", None)
            if not text:
                continue
            texts.append(text)
            try:
                parsed.append(json.loads(text))
            except Exception:
                parsed.append(None)

        if texts:
            if all(p is not None for p in parsed):
                return parsed[0] if len(parsed) == 1 else parsed
            return {"raw_text": "\n\n".join(texts)}

    try:
        return json.loads(str(result))
    except Exception:
        return {"raw": str(result)}


def display_tool_result(result):
    data = extract_json(result)

    if isinstance(data, dict):
        if "raw_text" in data:
            st.markdown(data["raw_text"])
        elif "raw" in data:
            st.markdown(data["raw"])
        else:
            st.json(data)
    elif isinstance(data, str):
        st.markdown(data)
    else:
        st.json(data)


def normalize_rows(data):
    if isinstance(data, dict):
        rows = data.get("rows")
        if (
            isinstance(rows, list)
            and rows
            and isinstance(rows[0], dict)
            and "value" in rows[0]
            and "data" in rows[0]
        ):
            columns = data.get("columns") or {}
            dim = columns.get("dimension")
            dim_label = (dim or {}).get("id") if isinstance(dim, dict) else None
            dim_label = dim_label or "dimension"

            sample = rows[0].get("data") or []
            col_ids = columns.get("columnIds") or [f"metric_{i}" for i in range(len(sample))]

            flat = []
            for r in rows:
                row_dict = {dim_label: r.get("value")}
                values = r.get("data") or []
                for i, col in enumerate(col_ids):
                    row_dict[col] = values[i] if i < len(values) else None
                flat.append(row_dict)
            return pd.DataFrame(flat)

        for key in ["rows", "data", "results", "report"]:
            if key in data and isinstance(data[key], list):
                return pd.DataFrame(data[key])

    if isinstance(data, list):
        return pd.DataFrame(data)

    return pd.DataFrame([data])


def render_report_output(data, csv_name):
    st.subheader("Raw Response")

    if isinstance(data, dict) and "raw_text" in data:
        st.markdown(data["raw_text"])
    else:
        st.json(data)

    df = normalize_rows(data)

    st.subheader("Table")
    st.dataframe(df, use_container_width=True)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download CSV",
        csv,
        file_name=csv_name,
        mime="text/csv"
    )

    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    text_cols = [c for c in df.columns if c not in numeric_cols]

    if numeric_cols and text_cols:
        x_col = st.selectbox("Chart Label Column", text_cols)
        y_col = st.selectbox("Chart Metric Column", numeric_cols)

        fig, ax = plt.subplots()
        ax.bar(df[x_col].astype(str), df[y_col])
        ax.set_xlabel(x_col)
        ax.set_ylabel(y_col)
        ax.tick_params(axis="x", rotation=45)
        st.pyplot(fig)


tab1, tab_inspect, tab2, tab3, tab4 = st.tabs([
    "Test Connection",
    "Inspect Metadata",
    "Run Report",
    "Raw Tool Call",
    "AI Query Simulator"
])


with tab1:
    st.subheader("Test CJA MCP Connection")

    if st.button("Run describeCja"):
        try:
            result = call_tool("describeCja", {})
            st.success("Connected successfully")
            display_tool_result(result)
        except Exception as e:
            st.error(str(e))

    st.divider()
    st.markdown("#### Server Tool Catalog")
    st.caption(
        "Calls MCP's standard tools/list. Shows every tool the server advertises "
        "and its exact input schema — useful when payloads fail with cryptic errors."
    )

    if st.button("List Tools / Schemas"):
        try:
            result = list_tools()
            st.session_state["tools_list"] = [
                {
                    "name": tool.name,
                    "description": getattr(tool, "description", None),
                    "inputSchema": getattr(tool, "inputSchema", None),
                }
                for tool in result.tools
            ]
        except Exception as e:
            st.error(str(e))

    if "tools_list" in st.session_state:
        tools_data = st.session_state["tools_list"]
        st.success(f"Server advertises {len(tools_data)} tools")

        filter_text = st.text_input(
            "Filter by tool name (substring)",
            value="",
            key="tools_filter"
        )

        if filter_text.strip():
            needle = filter_text.strip().lower()
            filtered = [t for t in tools_data if needle in (t["name"] or "").lower()]
            st.caption(f"Showing {len(filtered)} of {len(tools_data)}")
        else:
            filtered = tools_data

        for tool in filtered:
            with st.expander(tool["name"]):
                if tool.get("description"):
                    st.markdown(tool["description"])
                st.json(tool.get("inputSchema") or {})

    st.divider()

    default_session_data_view_id = os.getenv("ADOBE_DEFAULT_DATA_VIEW_ID", "")

    data_view_for_session = st.text_input(
        "Optional: Set Default Session Data View ID",
        value=default_session_data_view_id
    )

    if st.button("Set Default Session Data View"):
        try:
            if not data_view_for_session.strip():
                st.error("Enter a Data View ID first.")
                st.stop()

            result = call_tool(
                "setDefaultSessionDataViewId",
                {"dataViewId": data_view_for_session.strip()}
            )
            st.success("Default session Data View set")
            display_tool_result(result)

        except Exception as e:
            st.error(str(e))

    st.divider()

    st.markdown("#### Discovery")

    discovery_dv = st.text_input(
        "Data View ID for discovery (optional)",
        value=os.getenv("ADOBE_DEFAULT_DATA_VIEW_ID", ""),
        key="discovery_dv",
        help="Scopes findDimensions / findMetrics to one data view. Leave blank to use session default."
    )
    discovery_search = st.text_input(
        "Search query (optional)",
        value="",
        key="discovery_search",
        placeholder="e.g. page, campaign, visits"
    )
    discovery_limit = st.number_input(
        "Limit",
        min_value=1,
        max_value=500,
        value=100,
        key="discovery_limit"
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("Find Data Views"):
            try:
                result = call_tool("findDataViews", {"page": 0, "limit": int(discovery_limit)})
                display_tool_result(result)
            except Exception as e:
                st.error(str(e))

    with col2:
        if st.button("Find Dimensions"):
            try:
                fd_payload = {"limit": int(discovery_limit)}
                if discovery_dv.strip():
                    fd_payload["dataViewId"] = discovery_dv.strip()
                if discovery_search.strip():
                    fd_payload["searchQuery"] = discovery_search.strip()
                result = call_tool("findDimensions", fd_payload)
                display_tool_result(result)
            except Exception as e:
                st.error(str(e))

    with col3:
        if st.button("Find Metrics"):
            try:
                fm_payload = {"limit": int(discovery_limit)}
                if discovery_dv.strip():
                    fm_payload["dataViewId"] = discovery_dv.strip()
                if discovery_search.strip():
                    fm_payload["searchQuery"] = discovery_search.strip()
                result = call_tool("findMetrics", fm_payload)
                display_tool_result(result)
            except Exception as e:
                st.error(str(e))


with tab_inspect:
    st.subheader("Inspect CJA Metadata")
    st.caption(
        "Discovery tools for inspecting dimensions, metrics, and dimension item values. "
        "Data View ID is optional if a session default has been set on the Test Connection tab."
    )

    inspect_data_view_id = st.text_input(
        "Data View ID (shared)",
        value=os.getenv("ADOBE_DEFAULT_DATA_VIEW_ID", ""),
        key="inspect_dv"
    )

    st.divider()
    st.markdown("### Search Dimension Items")
    st.caption("Adobe requires startDate, endDate, page, and limit on this call.")

    sdi_dim = st.text_input(
        "Dimension ID",
        value=os.getenv("ADOBE_DEFAULT_DIMENSION_ID", ""),
        key="sdi_dim"
    )
    sdi_start = st.text_input(
        "Start Date",
        value=os.getenv("ADOBE_DEFAULT_START_DATE", "2026-05-01T00:00:00.000"),
        key="sdi_start"
    )
    sdi_end = st.text_input(
        "End Date",
        value=os.getenv("ADOBE_DEFAULT_END_DATE", "2026-05-21T23:59:59.999"),
        key="sdi_end"
    )
    sdi_search_and = st.text_input(
        "Search (AND, optional)",
        value="",
        key="sdi_search_and",
        placeholder="e.g. checkout"
    )
    sdi_search_or = st.text_input(
        "Search (OR, optional)",
        value="",
        key="sdi_search_or",
        placeholder="e.g. cart|wishlist"
    )
    sdi_page = st.number_input(
        "Page",
        min_value=0,
        value=0,
        key="sdi_page"
    )
    sdi_limit = st.number_input(
        "Limit",
        min_value=1,
        max_value=500,
        value=int(os.getenv("ADOBE_DEFAULT_LIMIT", "10")),
        key="sdi_limit"
    )

    if st.button("Search Items"):
        try:
            if not sdi_dim.strip():
                st.error("Dimension ID is required.")
                st.stop()
            if not sdi_start.strip() or not sdi_end.strip():
                st.error("Start Date and End Date are required.")
                st.stop()

            sdi_payload = {
                "dimensionId": sdi_dim.strip(),
                "startDate": sdi_start.strip(),
                "endDate": sdi_end.strip(),
                "page": int(sdi_page),
                "limit": int(sdi_limit),
            }
            if inspect_data_view_id.strip():
                sdi_payload["dataViewId"] = inspect_data_view_id.strip()
            if sdi_search_and.strip():
                sdi_payload["searchAnd"] = sdi_search_and.strip()
            if sdi_search_or.strip():
                sdi_payload["searchOr"] = sdi_search_or.strip()

            st.caption("Payload sent to searchDimensionItems")
            st.code(json.dumps(sdi_payload, indent=2), language="json")

            result = call_tool("searchDimensionItems", sdi_payload)
            display_tool_result(result)
        except Exception as e:
            st.error(str(e))

    st.divider()
    st.markdown("### Describe Dimension")
    st.caption(
        "Adobe's describeDimension expects the dimension ID without the `variables/` prefix. "
        "Paste either form — the app will strip it automatically."
    )

    dd_dim = st.text_input(
        "Dimension ID to describe",
        value=os.getenv("ADOBE_DEFAULT_DIMENSION_ID", ""),
        key="dd_dim"
    )

    if st.button("Describe Dimension"):
        try:
            if not dd_dim.strip():
                st.error("Dimension ID is required.")
                st.stop()

            dim_clean = dd_dim.strip()
            if dim_clean.startswith("variables/"):
                dim_clean = dim_clean[len("variables/"):]

            dd_payload = {"dimensionId": dim_clean}
            if inspect_data_view_id.strip():
                dd_payload["dataViewId"] = inspect_data_view_id.strip()

            st.caption("Payload sent to describeDimension")
            st.code(json.dumps(dd_payload, indent=2), language="json")

            result = call_tool("describeDimension", dd_payload)
            display_tool_result(result)
        except Exception as e:
            st.error(str(e))

    st.divider()
    st.markdown("### Describe Metric")
    st.caption(
        "Adobe's describeMetric expects the metric ID without the `metrics/` prefix. "
        "Paste either form — the app will strip it automatically."
    )

    dm_metric = st.text_input(
        "Metric ID to describe",
        value=os.getenv("ADOBE_DEFAULT_METRIC_ID", ""),
        key="dm_metric"
    )

    if st.button("Describe Metric"):
        try:
            if not dm_metric.strip():
                st.error("Metric ID is required.")
                st.stop()

            metric_clean = dm_metric.strip()
            if metric_clean.startswith("metrics/"):
                metric_clean = metric_clean[len("metrics/"):]

            dm_payload = {"metricId": metric_clean}
            if inspect_data_view_id.strip():
                dm_payload["dataViewId"] = inspect_data_view_id.strip()

            st.caption("Payload sent to describeMetric")
            st.code(json.dumps(dm_payload, indent=2), language="json")

            result = call_tool("describeMetric", dm_payload)
            display_tool_result(result)
        except Exception as e:
            st.error(str(e))


with tab2:
    st.subheader("Run CJA Report")

    data_view_id = st.text_input(
        "Data View ID",
        value=os.getenv("ADOBE_DEFAULT_DATA_VIEW_ID", "")
    )

    dimension_id = st.text_input(
        "Dimension ID",
        value=os.getenv("ADOBE_DEFAULT_DIMENSION_ID", ""),
        placeholder=os.getenv("ADOBE_DIMENSION_PLACEHOLDER", "Enter CJA dimension ID")
    )

    metric_id = st.text_input(
        "Metric ID",
        value=os.getenv("ADOBE_DEFAULT_METRIC_ID", ""),
        placeholder=os.getenv("ADOBE_METRIC_PLACEHOLDER", "Enter CJA metric ID")
    )

    start_date = st.text_input(
        "Start Date",
        value=os.getenv("ADOBE_DEFAULT_START_DATE", "2026-05-01T00:00:00.000")
    )

    end_date = st.text_input(
        "End Date",
        value=os.getenv("ADOBE_DEFAULT_END_DATE", "2026-05-21T23:59:59.999")
    )

    limit = st.number_input(
        "Row Limit",
        min_value=1,
        max_value=500,
        value=int(os.getenv("ADOBE_DEFAULT_LIMIT", "10"))
    )

    payload = {
        "dataViewId": data_view_id.strip()
    }

    if start_date.strip():
        payload["startDate"] = start_date.strip()

    if end_date.strip():
        payload["endDate"] = end_date.strip()

    if dimension_id.strip():
        payload["dimensionIds"] = [dimension_id.strip()]

    if metric_id.strip():
        payload["metricIds"] = [metric_id.strip()]

    if limit:
        payload["settings"] = {"limit": int(limit)}

    st.caption("Payload sent to runReport")
    st.code(json.dumps(payload, indent=2), language="json")

    if st.button("Run Report"):
        try:
            if not data_view_id.strip():
                st.error("Data View ID is required.")
                st.stop()

            if not start_date.strip():
                st.error("Start Date is required.")
                st.stop()

            if not end_date.strip():
                st.error("End Date is required.")
                st.stop()

            if not dimension_id.strip():
                st.error("Dimension ID is required.")
                st.stop()

            if not metric_id.strip():
                st.error("Metric ID is required.")
                st.stop()

            result = call_tool("runReport", payload)
            st.session_state["report_data"] = extract_json(result)

        except Exception as e:
            st.error(str(e))

    if "report_data" in st.session_state:
        render_report_output(st.session_state["report_data"], "cja_report.csv")


with tab3:
    st.subheader("Call Any CJA MCP Tool")

    tool_name = st.text_input("Tool Name", value="describeCja")

    args_text = st.text_area(
        "Arguments JSON",
        value="{}",
        height=200
    )

    if st.button("Call Tool"):
        try:
            args = json.loads(args_text)
            result = call_tool(tool_name, args)
            display_tool_result(result)
        except json.JSONDecodeError:
            st.error("Invalid JSON arguments")
        except Exception as e:
            st.error(str(e))


with tab4:
    st.subheader("AI Query Simulator")

    st.caption("Simulates how an AI agent converts a natural language question into an MCP tool call.")

    query_map = {
        os.getenv("SIM_QUERY_1_LABEL", "Show top dimension by metric"): {
            "dimensionIds": [os.getenv("SIM_QUERY_1_DIMENSION_ID", "")],
            "metricIds": [os.getenv("SIM_QUERY_1_METRIC_ID", "")]
        },
        os.getenv("SIM_QUERY_2_LABEL", "Show second dimension by metric"): {
            "dimensionIds": [os.getenv("SIM_QUERY_2_DIMENSION_ID", "")],
            "metricIds": [os.getenv("SIM_QUERY_2_METRIC_ID", "")]
        },
        os.getenv("SIM_QUERY_3_LABEL", "Show third dimension by metric"): {
            "dimensionIds": [os.getenv("SIM_QUERY_3_DIMENSION_ID", "")],
            "metricIds": [os.getenv("SIM_QUERY_3_METRIC_ID", "")]
        }
    }

    query = st.selectbox(
        "Select a sample question",
        list(query_map.keys())
    )

    data_view_id_sim = st.text_input(
        "Simulator Data View ID",
        value=os.getenv("ADOBE_DEFAULT_DATA_VIEW_ID", "")
    )

    start_date_sim = st.text_input(
        "Simulator Start Date",
        value=os.getenv("ADOBE_DEFAULT_START_DATE", "2026-05-01T00:00:00.000")
    )

    end_date_sim = st.text_input(
        "Simulator End Date",
        value=os.getenv("ADOBE_DEFAULT_END_DATE", "2026-05-21T23:59:59.999")
    )

    limit_sim = st.number_input(
        "Simulator Row Limit",
        min_value=1,
        max_value=500,
        value=int(os.getenv("ADOBE_DEFAULT_LIMIT", "10"))
    )

    selected = query_map[query]

    simulated_payload = {
        "dataViewId": data_view_id_sim.strip(),
        "startDate": start_date_sim.strip(),
        "endDate": end_date_sim.strip(),
        "dimensionIds": selected["dimensionIds"],
        "metricIds": selected["metricIds"],
        "settings": {
            "limit": int(limit_sim)
        }
    }

    st.caption("Simulated MCP runReport payload")
    st.code(json.dumps(simulated_payload, indent=2), language="json")

    if st.button("Run Simulated Query"):
        try:
            if not data_view_id_sim.strip():
                st.error("Data View ID is required.")
                st.stop()

            if not start_date_sim.strip():
                st.error("Start Date is required.")
                st.stop()

            if not end_date_sim.strip():
                st.error("End Date is required.")
                st.stop()

            if not selected["dimensionIds"][0]:
                st.error("Simulator dimension ID is missing. Add it to the .env file.")
                st.stop()

            if not selected["metricIds"][0]:
                st.error("Simulator metric ID is missing. Add it to the .env file.")
                st.stop()

            result = call_tool("runReport", simulated_payload)
            st.session_state["sim_report_data"] = extract_json(result)

        except Exception as e:
            st.error(str(e))

    if "sim_report_data" in st.session_state:
        render_report_output(st.session_state["sim_report_data"], "cja_simulated_query.csv")