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

PAGE_SIZE = 5

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

@st.cache_resource
def sb() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        st.error("Missing SUPABASE_URL or SUPABASE_KEY. Add them to secrets.toml.")
        st.stop()
    return create_client(SUPABASE_URL, SUPABASE_KEY)

client = sb()

st.title("Org Requests Dashboard")

# Helpers
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

@st.cache_data(ttl=60)
def fetch_reps():
    res = client.table("reps").select("rep_id, rep_name").order("rep_name").execute()
    return res.data or []

@st.cache_data(ttl=60)
def fetch_org_ids_for_rep(rep_id: int):
    res = (
        client.table("org_rep_assignments")
        .select("org_id")
        .eq("rep_id", rep_id)
        .eq("active", True)
        .execute()
    )
    rows = res.data or []
    return sorted({int(r["org_id"]) for r in rows if r.get("org_id") is not None})

@st.cache_data(ttl=60)
def fetch_org_details(org_ids):
    if not org_ids:
        return []
    out = []
    for batch in chunked(org_ids, 200):
        res = (
            client.table("orgs")
            .select("organization_id, organization_name, org_city, org_state")
            .in_("organization_id", batch)
            .execute()
        )
        out.extend(res.data or [])
    out.sort(key=lambda r: (r.get("organization_name") or "", r.get("organization_id") or 0))
    return out

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
        c = contact_row.iloc[0]
        if c.get("orgcontactname"):
            st.write(c["orgcontactname"])

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

    count_2025 = len(df_2025)
    count_2026 = len(df_2026)
    yoy_delta = count_2026 - count_2025

    today = date.today()
    cutoff_2025 = date(2025, today.month, today.day)

    df_2025_ytd = df_2025[df_2025["date_requested"].notna() & (df_2025["date_requested"] <= cutoff_2025)]
    df_2026_ytd = df_2026[df_2026["date_requested"].notna() & (df_2026["date_requested"] <= today)]

    ytd_delta = len(df_2026_ytd) - len(df_2025_ytd)

    s2, s3 = st.columns(2)
    with s2:
        a, b = st.columns(2)
        a.metric("2025 Requests", count_2025)
        b.metric(f"2025 YTD (thru {cutoff_2025.strftime('%b %d')})", len(df_2025_ytd), delta=ytd_delta)
    s3.metric("2026 Requests (YoY)", count_2026, delta=yoy_delta)

    tab25, tab26, taball = st.tabs(["2025 Requests", "2026 Requests", "All (Filtered)"])

    cols_show = [
        "team_name",
        "date_requested",
        "tag_level",
        "registration_status",
        "event_name",
        "start_date",
        "start_date_calendar_year",
        "event_id",
        "event_schedule_group_id",
        "orgcontactname",
        "orgcontactemail",
        "orgcontactphone",
        "updated_at",
    ]

    with tab25:
        st.dataframe(df_2025[cols_show], use_container_width=True)
    with tab26:
        st.dataframe(df_2026[cols_show], use_container_width=True)
    with taball:
        st.dataframe(df[cols_show], use_container_width=True)

# ----------------------------
# Orgs by Person
# ----------------------------
st.markdown("### Orgs by Person")

reps = fetch_reps()
if not reps:
    st.warning("No people found. (Create reps + assignments tables + import sheet first.)")
    st.stop()

rep_labels = [f'{r["rep_name"]} (ID {r["rep_id"]})' for r in reps]

rep_choice = st.selectbox("Select Person", ["-- Select --"] + rep_labels, index=0)

if rep_choice == "-- Select --":
    st.stop()

rep = reps[rep_labels.index(rep_choice)]
rep_id = int(rep["rep_id"])

org_ids = fetch_org_ids_for_rep(rep_id)
if not org_ids:
    st.info("No assigned orgs for this person.")
    st.stop()

org_details = fetch_org_details(org_ids)

st.markdown("### Assigned Orgs")

org_options = [
    f'{o["organization_id"]} — {o.get("organization_name","(no name)")} ({o.get("org_city","")}, {o.get("org_state","")})'
    for o in org_details
]

choice = st.selectbox("Select an org to view insights", ["-- Select --"] + org_options, index=0, key="rep_org_pick")

if choice == "-- Select --":
    st.stop()

selected_org = org_details[org_options.index(choice)]
org_id = int(selected_org["organization_id"])

render_org_insights(selected_org, org_id)