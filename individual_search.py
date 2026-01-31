import os
import pandas as pd
import streamlit as st
from supabase import create_client, Client
from difflib import SequenceMatcher
from datetime import date

st.set_page_config(page_title="Org Requests Dashboard", layout="wide")

APP_PASSWORD = (st.secrets.get("APP_PASSWORD", "") if hasattr(st, "secrets") else "")
SUPABASE_URL = (st.secrets.get("SUPABASE_URL", "") if hasattr(st, "secrets") else "")
SUPABASE_KEY = (st.secrets.get("SUPABASE_KEY", "") if hasattr(st, "secrets") else "")

PAGE_SIZE = 10  # ✅ limit to 10 orgs per search page

# ----------------------------
# Password Gate
# ----------------------------
def _auth_ui():
    st.markdown("# Sign in")
    st.caption("Enter the shared password to access this dashboard.")

    pw = st.text_input("Password", type="password", key="pw_input")
    login = st.button("Login", use_container_width=True)

    if login:
        if not APP_PASSWORD:
            st.error("APP_PASSWORD is not set in secrets.toml.")
            st.stop()

        if pw == APP_PASSWORD:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Wrong password.")

if APP_PASSWORD:
    if "authed" not in st.session_state:
        st.session_state["authed"] = False

    if not st.session_state["authed"]:
        _auth_ui()
        st.stop()

# ----------------------------
# Supabase
# ----------------------------
@st.cache_resource
def sb() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        st.error("Missing SUPABASE_URL or SUPABASE_KEY.")
        st.stop()
    return create_client(SUPABASE_URL, SUPABASE_KEY)

client = sb()

# ----------------------------
# Header + Link to Rep App
# ----------------------------
st.title("Org Requests Dashboard")

st.markdown(
    "**Looking for orgs by person?** "
    "[Go to Assigned Org Dashboard](https://orgdashboard.streamlit.app/)",
    unsafe_allow_html=True,
)

# ----------------------------
# Helpers
# ----------------------------
def is_int(s: str) -> bool:
    try:
        int(s)
        return True
    except Exception:
        return False

def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()

def chunked(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

def pg_org_link(org_id: int) -> str:
    url = f"https://www.perfectgame.org/PGBA/Team/default.aspx?orgid={org_id}"
    return f'<a href="{url}" target="_blank">{org_id}</a>'

# ----------------------------
# Queries
# ----------------------------
@st.cache_data(ttl=60)
def fetch_org_candidates(query: str):
    query = (query or "").strip()
    if not query:
        return []

    if is_int(query):
        res = (
            client.table("orgs")
            .select("organization_id, organization_name, org_city, org_state")
            .eq("organization_id", int(query))
            .execute()
        )
        return res.data or []

    res = (
        client.table("orgs")
        .select("organization_id, organization_name, org_city, org_state")
        .ilike("organization_name", f"%{query}%")
        .limit(200)
        .execute()
    )

    rows = res.data or []
    rows.sort(
        key=lambda o: (
            -similarity(query, o.get("organization_name") or ""),
            len(o.get("organization_name") or ""),
        )
    )
    return rows

@st.cache_data(ttl=60)
def get_requests_for_org(org_id: int):
    res = (
        client.table("requests")
        .select(
            "organization_id, organization_year_id, team_name, date_requested, tag_level, event_name, "
            "start_date_calendar_year, start_date, event_schedule_group_id, event_id, "
            "accountingregionid, accountinggroupid, orgcontactname, orgcontactemail, "
            "orgcontactphone, registration_status, updated_at"
        )
        .eq("organization_id", org_id)
        .order("start_date", desc=False)
        .execute()
    )
    return res.data or []

def with_all(options):
    opts = sorted([o for o in options if o not in (None, "", "nan")])
    return ["All"] + opts

def apply_dropdown_filters(d: pd.DataFrame, status_choice: str, event_choice: str) -> pd.DataFrame:
    out = d.copy()
    if status_choice != "All":
        out = out[out["registration_status"] == status_choice]
    if event_choice != "All":
        out = out[out["event_name"] == event_choice]
    return out

# ----------------------------
# Org Insights
# ----------------------------
def render_org_insights(selected_org, org_id: int):
    org_name = selected_org.get("organization_name", "(no name)")
    st.markdown(f'### {org_name} — Org ID: {pg_org_link(org_id)}', unsafe_allow_html=True)

    data = get_requests_for_org(org_id)
    df = pd.DataFrame(data)

    if df.empty:
        st.info("No requests found for this org.")
        st.stop()

    st.markdown("### Contact")

    contact_row = df[df["orgcontactname"].notna()].head(1)
    if contact_row.empty:
        st.caption("No contact info found for this org.")
    else:
        st.write(contact_row.iloc[0]["orgcontactname"])

    df["start_date_calendar_year"] = pd.to_numeric(df.get("start_date_calendar_year"), errors="coerce")
    df["start_date"] = pd.to_datetime(df.get("start_date"), errors="coerce").dt.date
    df["date_requested"] = pd.to_datetime(df.get("date_requested"), errors="coerce").dt.date

    st.markdown("### Filters")

    status_options = with_all(df["registration_status"].dropna().unique())
    event_options = with_all(df["event_name"].dropna().unique())

    col1, col2 = st.columns(2)
    with col1:
        status_choice = st.selectbox("Registration Status", status_options, index=0, key=f"status_{org_id}")
    with col2:
        event_choice = st.selectbox("Event Name", event_options, index=0, key=f"event_{org_id}")

    df = apply_dropdown_filters(df, status_choice, event_choice)

    df_2025 = df[df["start_date_calendar_year"] == 2025]
    df_2026 = df[df["start_date_calendar_year"] == 2026]

    s2, s3 = st.columns(2)
    with s2:
        st.metric("2025 Requests", len(df_2025))
    with s3:
        st.metric("2026 Requests", len(df_2026), delta=len(df_2026) - len(df_2025))

    st.dataframe(df, use_container_width=True)

# ----------------------------
# Search UI
# ----------------------------
if "org_results" not in st.session_state:
    st.session_state.org_results = []
if "org_page" not in st.session_state:
    st.session_state.org_page = 0
if "searched_query" not in st.session_state:
    st.session_state.searched_query = ""

st.markdown("### Find an Organization")

q = st.text_input(
    "Search by Organization Name or Organization ID",
    placeholder="e.g. 78598 or Canes Baseball",
    key="org_query",
)

do_search = st.button("Search", use_container_width=True)

if do_search:
    st.session_state.searched_query = q.strip()
    st.session_state.org_results = fetch_org_candidates(st.session_state.searched_query)
    st.session_state.org_page = 0
    if "org_pick" in st.session_state:
        st.session_state["org_pick"] = "-- Select an organization --"

results = st.session_state.org_results
page = st.session_state.org_page

if st.session_state.searched_query and not results:
    st.warning("No orgs found.")
    st.stop()

if not results:
    st.info("Type a org name or id and click Search.")
    st.stop()

total = len(results)
total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
page = max(0, min(page, total_pages - 1))
st.session_state.org_page = page

start = page * PAGE_SIZE
end = min(start + PAGE_SIZE, total)
page_rows = results[start:end]

p1, p2, p3 = st.columns([1, 1, 6])
with p1:
    if st.button("⬅ Prev", disabled=(page == 0)):
        st.session_state.org_page = max(0, page - 1)
        st.session_state["org_pick"] = "-- Select an organization --"
        st.rerun()
with p2:
    if st.button("Next ➡", disabled=(page >= total_pages - 1)):
        st.session_state.org_page = min(total_pages - 1, page + 1)
        st.session_state["org_pick"] = "-- Select an organization --"
        st.rerun()
with p3:
    st.caption(f"Showing {start+1}-{end} of {total} (Page {page+1} / {total_pages})")

options = [
    f'{o["organization_id"]} — {o.get("organization_name","(no name)")} ({o.get("org_city","")}, {o.get("org_state","")})'
    for o in page_rows
]

choice = st.radio(
    "Select the correct organization",
    ["-- Select an organization --"] + options,
    index=0,
    key="org_pick",
)

if choice == "-- Select an organization --":
    st.stop()

selected_org = page_rows[options.index(choice)]
org_id = int(selected_org["organization_id"])

render_org_insights(selected_org, org_id)