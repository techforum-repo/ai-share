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
    except BaseExceptionGroup as eg:
        errors = [str(e) for e in eg.exceptions]
        raise Exception("\n".join(errors))


def extract_json(result):
    if hasattr(result, "content"):
        for item in result.content:
            text = getattr(item, "text", None)
            if text:
                try:
                    return json.loads(text)
                except Exception:
                    return {"raw_text": text}

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


tab1, tab2, tab3, tab4 = st.tabs([
    "Test Connection",
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

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("Find Data Views"):
            try:
                result = call_tool("findDataViews", {})
                display_tool_result(result)
            except Exception as e:
                st.error(str(e))

    with col2:
        if st.button("Find Dimensions"):
            try:
                result = call_tool("findDimensions", {})
                display_tool_result(result)
            except Exception as e:
                st.error(str(e))

    with col3:
        if st.button("Find Metrics"):
            try:
                result = call_tool("findMetrics", {})
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
            data = extract_json(result)
            render_report_output(data, "cja_report.csv")

        except Exception as e:
            st.error(str(e))


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
            data = extract_json(result)
            render_report_output(data, "cja_simulated_query.csv")

        except Exception as e:
            st.error(str(e))