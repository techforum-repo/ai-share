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

st.set_page_config(page_title="Adobe Target MCP Reporting", layout="wide")
st.title(os.getenv("APP_TITLE", "Adobe Target MCP Reporting UI"))


# --- OAuth 2.0 interactive login support ---------------------------------
import time
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientMetadata

OAUTH_SCOPE = os.getenv(
    "OAUTH_SCOPE",
    "AdobeID openid additional_info.projectedProductContext "
    "read_organizations additional_info.roles",
)


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def oauth_port():
    """Stable loopback port for this session. Auto-picks a free port unless
    OAUTH_CALLBACK_PORT is set, so a stale listener can never block sign-in."""
    if "oauth_port" not in st.session_state:
        env = os.getenv("OAUTH_CALLBACK_PORT")
        st.session_state["oauth_port"] = int(env) if env else _free_port()
    return st.session_state["oauth_port"]


def oauth_redirect_uri():
    return f"http://localhost:{oauth_port()}/callback"


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        code = qs.get("code", [None])[0]
        error = qs.get("error", [None])[0]

        # Ignore unrelated requests (e.g. the browser's /favicon.ico) so a
        # captured authorization code is never clobbered with None.
        if parsed.path != "/callback" or (code is None and error is None):
            self.send_response(204)
            self.end_headers()
            return

        self.server.oauth_code = code
        self.server.oauth_state = qs.get("state", [None])[0]
        self.server.oauth_error = error
        body = (
            b"<html><body style='font-family:sans-serif'>"
            b"<h3>Adobe sign-in complete.</h3>"
            b"<p>You can close this tab and return to the app.</p>"
            b"</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class CallbackServer:
    """One-shot localhost HTTP server that captures the OAuth redirect."""

    def __init__(self, port):
        self.port = port
        self.httpd = None
        self.thread = None

    @property
    def running(self):
        return self.httpd is not None

    def start(self):
        if self.httpd:
            return
        self.httpd = HTTPServer(("127.0.0.1", self.port), _CallbackHandler)
        self.httpd.oauth_code = None
        self.httpd.oauth_state = None
        self.httpd.oauth_error = None
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def ready(self):
        return bool(self.httpd and (self.httpd.oauth_code or self.httpd.oauth_error))

    def result(self):
        return (self.httpd.oauth_code, self.httpd.oauth_state, self.httpd.oauth_error)

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
            self.thread = None


class StreamlitTokenStorage(TokenStorage):
    """Persists OAuth tokens and client registration in Streamlit session state."""

    async def get_tokens(self):
        return st.session_state.get("oauth_tokens")

    async def set_tokens(self, tokens):
        st.session_state["oauth_tokens"] = tokens

    async def get_client_info(self):
        return st.session_state.get("oauth_client_info")

    async def set_client_info(self, client_info):
        st.session_state["oauth_client_info"] = client_info


def stop_callback_server():
    srv = st.session_state.pop("oauth_callback_server", None)
    if srv is not None:
        srv.stop()


def build_auth_provider():
    async def redirect_handler(auth_url: str) -> None:
        st.session_state["oauth_last_auth_url"] = auth_url
        print(f"\n[Adobe Target MCP] Sign in here:\n{auth_url}\n")
        stop_callback_server()
        server = CallbackServer(oauth_port())
        server.start()
        st.session_state["oauth_callback_server"] = server
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

    async def callback_handler():
        server = st.session_state.get("oauth_callback_server")
        deadline = time.time() + 300
        try:
            while time.time() < deadline and not (server and server.ready()):
                await asyncio.sleep(0.25)
            if not (server and server.ready()):
                raise RuntimeError("Timed out waiting for the Adobe login redirect.")
            code, state, error = server.result()
        finally:
            stop_callback_server()
        if error or not code:
            raise RuntimeError(f"OAuth callback failed: {error or 'no authorization code'}")
        return code, state

    return OAuthClientProvider(
        server_url=endpoint.strip(),
        client_metadata=OAuthClientMetadata(
            redirect_uris=[oauth_redirect_uri()],
            client_name="Adobe Target MCP Explorer",
            scope=OAUTH_SCOPE or None,
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=StreamlitTokenStorage(),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )


async def call_tool_async(tool_name: str, arguments: dict):
    async with streamablehttp_client(endpoint.strip(), auth=build_auth_provider()) as (read, write, _):
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
    async with streamablehttp_client(endpoint.strip(), auth=build_auth_provider()) as (read, write, _):
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


with st.sidebar:
    st.header("Target MCP Settings")

    endpoint = st.text_input(
        "MCP Endpoint",
        os.getenv("ADOBE_TARGET_MCP_URL", "https://targetmcp.adobe.io/mcp"),
    )

    st.markdown("#### Authentication")

    if st.session_state.get("oauth_tokens"):
        st.success("Signed in to Adobe.")
        if st.button("Sign out"):
            stop_callback_server()
            for k in ("oauth_tokens", "oauth_client_info", "oauth_last_auth_url"):
                st.session_state.pop(k, None)
            st.rerun()
    else:
        st.caption(
            "Signs in with Adobe via OAuth 2.0. Opens Adobe login in your browser "
            f"and captures the redirect on `{oauth_redirect_uri()}`. Assumes the app "
            "runs on your local machine."
        )
        if st.button("Sign in with Adobe"):
            try:
                with st.spinner("Complete the Adobe login in your browser…"):
                    result = list_tools()
                st.session_state["tools_list"] = [
                    {
                        "name": t.name,
                        "description": getattr(t, "description", None),
                        "inputSchema": getattr(t, "inputSchema", None),
                    }
                    for t in result.tools
                ]
                st.success(f"Signed in. Server advertises {len(result.tools)} tools.")
                st.rerun()
            except Exception as e:
                stop_callback_server()
                st.error(str(e))
                url = st.session_state.get("oauth_last_auth_url")
                if url:
                    st.markdown(f"[Open Adobe login manually]({url})")


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


# Keys under which Target MCP tools commonly nest their list payloads.
LIST_KEYS = [
    "activities", "offers", "audiences", "mboxes", "properties",
    "experiences", "metrics", "tokens", "revisions", "items",
    "rows", "data", "results", "report",
]


def _find_report_rows(data):
    """A performance report nests its per-experience rows deep inside `report`.
    Find the richest list-of-dicts (preferring an experiences/options array)
    so the table shows one row per experience instead of one JSON blob."""
    scope = data.get("report", data) if isinstance(data, dict) else data
    found = []

    def walk(obj, path):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, path + [k])
        elif isinstance(obj, list):
            if obj and all(isinstance(x, dict) for x in obj):
                found.append((path, obj))
            for x in obj:
                walk(x, path + ["[]"])

    walk(scope, [])
    if not found:
        return None

    def score(item):
        path, rows = item
        keys = set()
        for r in rows:
            keys |= set(r.keys())
        s = len(rows)
        if {"experienceId", "experienceName"} & keys:
            s += 1000
        if {"name", "experienceName", "optionName", "optionLocalId"} & keys:
            s += 100
        if path and path[-1] in ("intervals", "reportingAudiences", "metrics"):
            s -= 1000
        return s

    return max(found, key=score)[1]


def normalize_rows(data):
    if isinstance(data, dict):
        for key in LIST_KEYS:
            value = data.get(key)
            if isinstance(value, list) and value:
                if all(isinstance(v, dict) for v in value):
                    return pd.json_normalize(value)
                return pd.DataFrame({key: value})

        if "report" in data or "reportParameters" in data:
            rows = _find_report_rows(data)
            if rows:
                return pd.json_normalize(rows)

    if isinstance(data, list):
        if data and all(isinstance(v, dict) for v in data):
            return pd.json_normalize(data)
        return pd.DataFrame(data)

    return pd.DataFrame([data])


def report_experience_rows(data):
    """If `data` is a Target performance report, return its experience-stat
    rows (an empty list means a report with no statistics). Otherwise None."""
    if not isinstance(data, dict):
        return None
    if "report" not in data and "reportParameters" not in data:
        return None
    return _find_report_rows(data) or []


def report_summary_df(data):
    """A compact field/value table of the report's `activity` block."""
    activity = data.get("activity") if isinstance(data, dict) else None
    if not isinstance(activity, dict):
        return None
    flat = pd.json_normalize(activity)
    if flat.empty:
        return None
    row = flat.iloc[0]
    return pd.DataFrame({"field": row.index, "value": row.values})


def render_report_output(data, csv_name):
    st.subheader("Raw Response")

    if isinstance(data, dict) and "raw_text" in data:
        st.markdown(data["raw_text"])
    else:
        st.json(data)

    exp_rows = report_experience_rows(data)
    if exp_rows == []:
        st.info(
            "This report has no experience statistics — the activity is likely "
            "saved or paused, or has no traffic in the selected window. Pick an "
            "approved, running activity (shown first in the picker) to see metrics."
        )
        summary = report_summary_df(data)
        if summary is not None:
            st.subheader("Activity")
            st.dataframe(summary, use_container_width=True, hide_index=True)
        return

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


def add_pagination(payload, limit, offset):
    if limit:
        payload["limit"] = int(limit)
    if offset:
        payload["offset"] = int(offset)
    return payload


def extract_activities(data):
    """Pull (id, name, type, state) tuples out of a list_target_activities
    response, tolerating a few field-name and nesting variations."""
    rows = None
    if isinstance(data, dict):
        for key in ("activities", "items", "results", "data", "rows"):
            if isinstance(data.get(key), list):
                rows = data[key]
                break
    elif isinstance(data, list):
        rows = data

    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        rid = next((r[k] for k in ("id", "activityId", "activity_id") if r.get(k) is not None), None)
        if rid is None:
            continue
        name = next((r[k] for k in ("name", "activityName", "activity_name") if r.get(k)), None)
        atype = next((r[k] for k in ("type", "activityType", "activity_type") if r.get(k)), None)
        state = next((r[k] for k in ("state", "status") if r.get(k)), None)
        out.append({"id": rid, "name": name or f"(id {rid})", "type": atype, "state": state})

    # Surface running/approved activities first — they're the ones with data.
    out.sort(key=lambda a: (a.get("state") != "approved", str(a.get("name"))))
    return out


def activity_option_labels(activities):
    labels = []
    for a in activities:
        label = f"{a['name']}  ·  id {a['id']}"
        if a.get("type"):
            label += f"  ·  {a['type']}"
        if a.get("state"):
            label += f"  ·  {a['state']}"
        labels.append(label)
    return labels


tab1, tab_inspect, tab2, tab3, tab4 = st.tabs([
    "Test Connection",
    "Inspect Entities",
    "Performance Reports",
    "Raw Tool Call",
    "AI Query Simulator"
])


with tab1:
    st.subheader("Test Target MCP Connection")
    st.caption(
        "list_target_properties takes no parameters, which makes it a good "
        "lightweight connectivity check."
    )

    if st.button("Run list_target_properties"):
        try:
            result = call_tool("list_target_properties", {})
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

    st.markdown("#### Discovery")
    st.caption(
        "Listing tools enumerate what's available in the org before constructing "
        "a more specific request."
    )

    disc_limit = st.number_input(
        "Limit",
        min_value=1,
        max_value=200,
        value=int(os.getenv("ADOBE_DEFAULT_LIMIT", "10")),
        key="disc_limit"
    )
    disc_offset = st.number_input(
        "Offset",
        min_value=0,
        value=0,
        key="disc_offset"
    )
    disc_name = st.text_input(
        "Name filter (optional)",
        value="",
        key="disc_name",
        placeholder="e.g. homepage, checkout"
    )
    disc_type = st.selectbox(
        "Activity type (for activities)",
        ["", "ab", "xt", "abt"],
        key="disc_type"
    )

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        if st.button("List Activities"):
            try:
                payload = add_pagination({}, disc_limit, disc_offset)
                if disc_name.strip():
                    payload["name_contains"] = disc_name.strip()
                if disc_type:
                    payload["activity_type"] = disc_type
                display_tool_result(call_tool("list_target_activities", payload))
            except Exception as e:
                st.error(str(e))

    with col2:
        if st.button("List Offers"):
            try:
                payload = add_pagination({}, disc_limit, disc_offset)
                if disc_name.strip():
                    payload["name"] = disc_name.strip()
                display_tool_result(call_tool("list_target_offers", payload))
            except Exception as e:
                st.error(str(e))

    with col3:
        if st.button("List Audiences"):
            try:
                payload = add_pagination({}, disc_limit, disc_offset)
                display_tool_result(call_tool("list_target_audiences", payload))
            except Exception as e:
                st.error(str(e))

    with col4:
        if st.button("List Mboxes"):
            try:
                payload = add_pagination({}, disc_limit, disc_offset)
                if disc_name.strip():
                    payload["name"] = disc_name.strip()
                display_tool_result(call_tool("list_target_mboxes", payload))
            except Exception as e:
                st.error(str(e))

    with col5:
        if st.button("List Properties"):
            try:
                display_tool_result(call_tool("list_target_properties", {}))
            except Exception as e:
                st.error(str(e))


with tab_inspect:
    st.subheader("Inspect Target Entities")
    st.caption(
        "Fetch full metadata for a specific activity, offer, audience, or mbox. "
        "IDs are integers (mboxes use a name). Discover them on the Test Connection tab."
    )

    st.markdown("### Get Activity")
    st.caption(
        "Activities have a type-specific tool: get_ab_activity, get_xt_activity, "
        "or get_abt_activity. Pick the type that matches the activity."
    )

    ga_id = st.text_input(
        "Activity ID",
        value=os.getenv("ADOBE_DEFAULT_ACTIVITY_ID", ""),
        key="ga_id"
    )
    ga_type = st.selectbox(
        "Activity Type",
        ["ab", "xt", "abt"],
        key="ga_type"
    )

    if st.button("Get Activity"):
        try:
            if not ga_id.strip():
                st.error("Activity ID is required.")
                st.stop()

            tool_by_type = {
                "ab": "get_ab_activity",
                "xt": "get_xt_activity",
                "abt": "get_abt_activity",
            }
            payload = {"activity_id": int(ga_id.strip())}

            st.caption(f"Payload sent to {tool_by_type[ga_type]}")
            st.code(json.dumps(payload, indent=2), language="json")

            display_tool_result(call_tool(tool_by_type[ga_type], payload))
        except ValueError:
            st.error("Activity ID must be an integer.")
        except Exception as e:
            st.error(str(e))

    st.divider()
    st.markdown("### Get Offer")

    go_id = st.text_input(
        "Offer ID",
        value=os.getenv("ADOBE_DEFAULT_OFFER_ID", ""),
        key="go_id"
    )

    if st.button("Get Offer"):
        try:
            if not go_id.strip():
                st.error("Offer ID is required.")
                st.stop()

            payload = {"offer_id": int(go_id.strip())}
            st.caption("Payload sent to get_target_offer")
            st.code(json.dumps(payload, indent=2), language="json")

            display_tool_result(call_tool("get_target_offer", payload))
        except ValueError:
            st.error("Offer ID must be an integer.")
        except Exception as e:
            st.error(str(e))

    st.divider()
    st.markdown("### Get Audience")

    gau_id = st.text_input(
        "Audience ID",
        value=os.getenv("ADOBE_DEFAULT_AUDIENCE_ID", ""),
        key="gau_id"
    )

    if st.button("Get Audience"):
        try:
            if not gau_id.strip():
                st.error("Audience ID is required.")
                st.stop()

            payload = {"audience_id": int(gau_id.strip())}
            st.caption("Payload sent to get_target_audience")
            st.code(json.dumps(payload, indent=2), language="json")

            display_tool_result(call_tool("get_target_audience", payload))
        except ValueError:
            st.error("Audience ID must be an integer.")
        except Exception as e:
            st.error(str(e))

    st.divider()
    st.markdown("### Get Mbox")
    st.caption("Mboxes are keyed by name. Also lists which activities use the location.")

    gm_name = st.text_input(
        "Mbox Name",
        value=os.getenv("ADOBE_DEFAULT_MBOX_NAME", ""),
        key="gm_name"
    )

    col_mbox1, col_mbox2 = st.columns(2)

    with col_mbox1:
        if st.button("Get Mbox"):
            try:
                if not gm_name.strip():
                    st.error("Mbox Name is required.")
                    st.stop()

                payload = {"mbox_name": gm_name.strip()}
                st.caption("Payload sent to get_target_mbox")
                st.code(json.dumps(payload, indent=2), language="json")

                display_tool_result(call_tool("get_target_mbox", payload))
            except Exception as e:
                st.error(str(e))

    with col_mbox2:
        if st.button("List Mbox Profile Attributes"):
            try:
                display_tool_result(
                    call_tool("list_target_mbox_profile_attributes", {})
                )
            except Exception as e:
                st.error(str(e))


with tab2:
    st.subheader("Activity Performance Reports")
    st.caption(
        "Target reporting tools take an activity_id (integer) and an optional "
        "report_interval. The interval is an ISO 8601 range (start/end), not a "
        "relative keyword — leave it blank to use the server's default window."
    )

    report_kind = st.selectbox(
        "Report Type",
        [
            "A/B Performance (get_ab_performance_report)",
            "A/B Orders (get_ab_orders_report)",
            "XT Performance (get_xt_performance_report)",
            "XT Orders (get_xt_orders_report)",
            "Analytics for Target (get_a4t_report)",
            "By Activity Name (get_activity_report_by_name)",
        ]
    )

    tool_for_kind = {
        "A/B Performance (get_ab_performance_report)": "get_ab_performance_report",
        "A/B Orders (get_ab_orders_report)": "get_ab_orders_report",
        "XT Performance (get_xt_performance_report)": "get_xt_performance_report",
        "XT Orders (get_xt_orders_report)": "get_xt_orders_report",
        "Analytics for Target (get_a4t_report)": "get_a4t_report",
        "By Activity Name (get_activity_report_by_name)": "get_activity_report_by_name",
    }
    selected_report_tool = tool_for_kind[report_kind]
    by_name = selected_report_tool == "get_activity_report_by_name"

    # Each report type maps to the activity_type worth listing for selection.
    load_type_for_tool = {
        "get_ab_performance_report": "ab",
        "get_ab_orders_report": "ab",
        "get_xt_performance_report": "xt",
        "get_xt_orders_report": "xt",
        "get_a4t_report": None,
        "get_activity_report_by_name": None,
    }
    load_type = load_type_for_tool[selected_report_tool]
    cache_key = load_type or "all"

    activities_cache = st.session_state.setdefault("report_activities_by_type", {})

    load_col, hint_col = st.columns([1, 3])
    with load_col:
        if st.button("Load activities"):
            try:
                payload = {"limit": 200}
                if load_type:
                    payload["activity_type"] = load_type
                result = call_tool("list_target_activities", payload)
                activities_cache[cache_key] = extract_activities(extract_json(result))
            except Exception as e:
                st.error(str(e))
    with hint_col:
        label = "all types" if load_type is None else f"{load_type} activities"
        st.caption(f"Loads {label} so you can pick one instead of typing an ID.")

    activities = activities_cache.get(cache_key)

    report_activity_name = ""
    report_activity_id = ""

    if activities:
        labels = activity_option_labels(activities)
        idx = st.selectbox(
            "Activity",
            range(len(activities)),
            format_func=lambda i: labels[i],
            key=f"report_pick_{cache_key}",
        )
        chosen = activities[idx]
        report_activity_name = str(chosen["name"])
        report_activity_id = str(chosen["id"])
    else:
        if cache_key in activities_cache:
            st.info("No matching activities found. Enter a value manually.")
        if by_name:
            report_activity_name = st.text_input(
                "Activity Name",
                value="",
                placeholder="e.g. Homepage Hero Test",
            )
        else:
            report_activity_id = st.text_input(
                "Activity ID",
                value=os.getenv("ADOBE_DEFAULT_ACTIVITY_ID", ""),
            )

    report_interval = st.text_input(
        "Report Interval (optional)",
        value=os.getenv("ADOBE_DEFAULT_REPORT_INTERVAL", ""),
        help="ISO 8601 range, e.g. 2026-05-01T00:00:00Z/2026-05-29T00:00:00Z. "
             "Leave blank for the server default. Relative keywords are NOT accepted.",
    )

    report_payload = {}
    if by_name:
        if report_activity_name.strip():
            report_payload["activity_name"] = report_activity_name.strip()
    elif report_activity_id.strip():
        try:
            report_payload["activity_id"] = int(report_activity_id.strip())
        except ValueError:
            report_payload["activity_id"] = report_activity_id.strip()
    if report_interval.strip():
        report_payload["report_interval"] = report_interval.strip()

    st.caption(f"Payload sent to {selected_report_tool}")
    st.code(json.dumps(report_payload, indent=2), language="json")

    if st.button("Run Report"):
        try:
            if by_name:
                if not report_activity_name.strip():
                    st.error("Activity Name is required.")
                    st.stop()
            else:
                if not report_activity_id.strip():
                    st.error("Activity ID is required.")
                    st.stop()

            result = call_tool(selected_report_tool, report_payload)
            st.session_state["report_data"] = extract_json(result)
        except Exception as e:
            st.error(str(e))

    if "report_data" in st.session_state:
        render_report_output(st.session_state["report_data"], "target_report.csv")


with tab3:
    st.subheader("Call Any Target MCP Tool")

    tool_name = st.text_input("Tool Name", value="list_target_properties")

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
    st.caption(
        "Simulates how an AI agent converts a natural language question into an "
        "MCP tool call. Each sample maps to a tool name and argument payload defined "
        "in the .env file."
    )

    def load_sim_query(index):
        label = os.getenv(f"SIM_QUERY_{index}_LABEL")
        if not label:
            return None
        tool = os.getenv(f"SIM_QUERY_{index}_TOOL", "list_target_activities")
        raw_args = os.getenv(f"SIM_QUERY_{index}_ARGS", "{}")
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            args = {"__invalid_args__": raw_args}
        return {"label": label, "tool": tool, "args": args}

    query_map = {}
    for i in range(1, 21):
        entry = load_sim_query(i)
        if entry:
            query_map[entry["label"]] = entry

    if not query_map:
        st.info("No simulator queries configured. Add SIM_QUERY_*_LABEL entries to .env.")
    else:
        query_label = st.selectbox(
            "Select a sample question",
            list(query_map.keys())
        )
        selected = query_map[query_label]

        st.caption(f"Simulated MCP call → {selected['tool']}")
        st.code(json.dumps(selected["args"], indent=2), language="json")

        if st.button("Run Simulated Query"):
            try:
                if "__invalid_args__" in selected["args"]:
                    st.error("This query's SIM_QUERY_*_ARGS is not valid JSON.")
                    st.stop()

                result = call_tool(selected["tool"], selected["args"])
                st.session_state["sim_report_data"] = extract_json(result)
            except Exception as e:
                st.error(str(e))

        if "sim_report_data" in st.session_state:
            render_report_output(
                st.session_state["sim_report_data"],
                "target_simulated_query.csv"
            )
