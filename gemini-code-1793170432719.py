import os
import json
import textwrap
from datetime import datetime
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from dotenv import load_dotenv

from src.auth import get_authenticated_services
from src.youtube_client import (
    get_recent_longform_videos,
    get_retention_curve,
    fetch_video_transcript
)
from src.aligner import build_structured_timeline, align_retention_and_transcript
from src.llm_analyzer import analyze_with_llm

load_dotenv()
os.makedirs("cache", exist_ok=True)
os.makedirs("reports", exist_ok=True)

st.set_page_config(
    page_title="YouTube Retention & Transcript Studio",
    page_icon="📈",
    layout="wide"
)

def load_cached_videos():
    """Reads all cached video JSON files from the local cache/ directory."""
    videos = {}
    if not os.path.exists("cache"):
        return videos
    for fname in os.listdir("cache"):
        if fname.endswith(".json"):
            fpath = os.path.join("cache", fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    vid = data["metadata"]["video_id"]
                    videos[vid] = data
            except Exception:
                continue
    # Sort by published date descending
    sorted_vids = dict(
        sorted(videos.items(), key=lambda item: item[1]["metadata"]["published_at"], reverse=True)
    )
    return sorted_vids

def wrap_hover_text(text, width=65):
    """Wraps long transcript strings with <br> tags so Plotly hover tooltips stay clean."""
    return "<br>".join(textwrap.wrap(text, width=width))

# --- SIDEBAR CONTROLS ---
with st.sidebar:
    st.title("⚙️ Channel Sync & Settings")
    
    api_key_input = st.text_input(
        "Gemini API Key",
        value=os.getenv("GEMINI_API_KEY", ""),
        type="password",
        help="Loaded from .env if set, or enter manually here."
    )
    if api_key_input:
        os.environ["GEMINI_API_KEY"] = api_key_input

    target_count = st.slider("Number of Recent Videos", min_value=1, max_value=25, value=int(os.getenv("TARGET_VIDEO_COUNT", 10)))
    min_duration = st.number_input("Min Duration (sec, excludes Shorts)", min_value=61, max_value=600, value=int(os.getenv("MIN_DURATION_SECONDS", 181)))
    lang_input = st.text_input("Transcript Languages (comma-separated)", value=os.getenv("TRANSCRIPT_LANGUAGES", "en,hi"))
    force_refresh = st.checkbox("Force re-fetch from YouTube API (ignore cache)", value=False)

    if st.button("🔄 Sync Latest Long-Form Videos", type="primary", use_container_width=True):
        with st.status("Authenticating & fetching videos from YouTube...", expanded=True) as status:
            try:
                yt_data, yt_analytics = get_authenticated_services()
                st.write(f"Scanning uploads playlist for last {target_count} videos (>={min_duration}s)...")
                recent_videos = get_recent_longform_videos(
                    yt_data,
                    target_count=target_count,
                    min_duration_sec=min_duration
                )
                languages = [l.strip() for l in lang_input.split(",") if l.strip()]

                for meta in recent_videos:
                    vid = meta["video_id"]
                    cache_path = os.path.join("cache", f"{vid}.json")

                    if os.path.exists(cache_path) and not force_refresh:
                        st.write(f"✔️ Loaded from cache: **{meta['title']}**")
                        continue

                    st.write(f"⬇️ Fetching Analytics & Transcript: **{meta['title']}**...")
                    curve = get_retention_curve(yt_analytics, vid, start_date=meta["published_at"])
                    if not curve:
                        st.warning(f"Skipping {meta['title']}: Analytics curve not yet available.")
                        continue

                    transcript = fetch_video_transcript(vid, languages=languages)
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump({"metadata": meta, "curve": curve, "transcript": transcript}, f, indent=2)

                status.update(label="Sync Complete!", state="complete", expanded=False)
                st.rerun()
            except Exception as e:
                status.update(label="Sync Failed", state="error")
                st.error(f"Error: {e}")

# --- MAIN PAGE ---
st.title("📈 YouTube Retention & Transcript Studio")

cached_videos = load_cached_videos()

if not cached_videos:
    st.info("No cached video data found yet. Click **🔄 Sync Latest Long-Form Videos** in the sidebar to pull your channel's retention curves and transcripts.")
    st.stop()

tab_single, tab_compare, tab_ai = st.tabs([
    "🎯 Single Video Inspector",
    "📊 Multi-Video Curve Comparison",
    "🧠 Gemini Retention Strategist"
])

# ==========================================
# TAB 1: SINGLE VIDEO INSPECTOR
# ==========================================
with tab_single:
    video_options = {
        vid: f"{data['metadata']['title']} ({data['metadata']['published_at']})"
        for vid, data in cached_videos.items()
    }
    selected_vid = st.selectbox("Select a Video to Inspect:", options=list(video_options.keys()), format_func=lambda x: video_options[x])

    vid_data = cached_videos[selected_vid]
    meta = vid_data["metadata"]
    curve = vid_data["curve"]
    transcript = vid_data["transcript"]

    timeline_rows = build_structured_timeline(meta, curve, transcript)
    df = pd.DataFrame(timeline_rows)
    df["wrapped_spoken"] = df["spoken_text"].apply(wrap_hover_text)

    # Top KPI Metrics
    hook_30s_rows = df[df["end_sec"] <= 35]
    hook_retention = hook_30s_rows.iloc[-1]["retention_pct"] if not hook_30s_rows.empty else df.iloc[0]["retention_pct"]
    avg_retention = df["retention_pct"].mean()
    avg_relative = df["relative_score"].mean()
    worst_drop_row = df.iloc[1:].sort_values(by="delta_pct").iloc[0] if len(df) > 1 else df.iloc[0]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Duration", f"{meta['duration_seconds'] // 60}m {meta['duration_seconds'] % 60}s")
    col2.metric("~30s Hook Retention", f"{hook_retention:.1f}%")
    col3.metric("Average Retention", f"{avg_retention:.1f}%", f"Relative Score: {avg_relative:.2f}")
    col4.metric("Steepest 1% Drop", f"{worst_drop_row['delta_pct']:+.2f}%", f"at {worst_drop_row['timestamp_range']}")

    # Interactive Dual-Trace Plotly Chart (Absolute Retention + Relative Performance)
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Main Retention Curve
    fig.add_trace(
        go.Scatter(
            x=df["bucket_pct"],
            y=df["retention_pct"],
            mode="lines+markers",
            name="Audience Retention (%)",
            line=dict(color="#FF4B4B", width=3),
            marker=dict(size=5),
            customdata=df[["timestamp_range", "delta_pct", "relative_score", "wrapped_spoken"]],
            hovertemplate=(
                "<b>⏱️ %{customdata[0]} (%{x}% of video)</b><br>"
                "<b>Retention:</b> %{y:.1f}% (Delta: %{customdata[1]:+.2f}%)<br>"
                "<b>Relative Score:</b> %{customdata[2]:.2f}<br><br>"
                "<b>🗣️ Spoken Transcript:</b><br><i>\"%{customdata[3]}\"</i>"
                "<extra></extra>"
            )
        ),
        secondary_y=False
    )

    # Relative Retention Performance Trace (0.50 = YouTube benchmark)
    fig.add_trace(
        go.Scatter(
            x=df["bucket_pct"],
            y=df["relative_score"],
            mode="lines",
            name="Relative Score (vs YouTube avg)",
            line=dict(color="#00B4D8", width=2, dash="dot"),
            hoverinfo="skip"
        ),
        secondary_y=True
    )

    # Highlight Top 3 Steepest Drop-Off Points (excluding bucket 1)
    if len(df) > 3:
        top_drops = df.iloc[1:].nsmallest(3, "delta_pct")
        fig.add_trace(
            go.Scatter(
                x=top_drops["bucket_pct"],
                y=top_drops["retention_pct"],
                mode="markers",
                name="Top 3 Steepest Drops",
                marker=dict(color="#FFD166", size=13, symbol="x", line=dict(width=2, color="black")),
                customdata=top_drops[["timestamp_range", "delta_pct", "relative_score", "wrapped_spoken"]],
                hovertemplate=(
                    "<b>⚠️ STEEP DROP AT %{customdata[0]}</b><br>"
                    "<b>Retention:</b> %{y:.1f}% (<b>Delta: %{customdata[1]:+.2f}%</b>)<br>"
                    "<b>🗣️ Spoken:</b><br><i>\"%{customdata[3]}\"</i>"
                    "<extra></extra>"
                )
            ),
            secondary_y=False
        )

    fig.update_layout(
        title=f"Interactive Retention Curve & Spoken Dialogue — {meta['title']}",
        xaxis_title="Elapsed Video Percentage (%)",
        hovermode="closest",
        height=520,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_yaxes(title_text="Viewer Retention (%)", secondary_y=False, range=[0, max(105, df["retention_pct"].max() + 5)])
    fig.update_yaxes(title_text="Relative Score (0.5 = Avg)", secondary_y=True, range=[0, 1.05], showgrid=False)

    st.plotly_chart(fig, use_container_width=True)

    # Searchable & Sortable Aligned Transcript Table
    st.subheader("📜 Aligned Timestamp & Transcript Table")
    col_search, col_link = st.columns([3, 1])
    with col_search:
        search_query = st.text_input("🔍 Filter transcript or timestamps:", placeholder="Type a keyword or [No dialogue...")
    with col_link:
        st.markdown("<br>", unsafe_allow_html=True)
        st.link_button("▶️ Open Video on YouTube", f"https://www.youtube.com/watch?v={selected_vid}", use_container_width=True)

    display_df = df[["bucket_pct", "timestamp_range", "retention_pct", "delta_pct", "relative_score", "spoken_text"]].copy()
    if search_query:
        display_df = display_df[display_df["spoken_text"].str.contains(search_query, case=False, na=False)]

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "bucket_pct": st.column_config.NumberColumn("Video %", format="%d%%"),
            "timestamp_range": "Timestamp (MM:SS)",
            "retention_pct": st.column_config.NumberColumn("Retention %", format="%.1f%%"),
            "delta_pct": st.column_config.NumberColumn("1% Delta", format="%+.2f%%"),
            "relative_score": st.column_config.NumberColumn("Relative Score", format="%.2f"),
            "spoken_text": st.column_config.TextColumn("Spoken Dialogue / B-Roll", width="large")
        }
    )

# ==========================================
# TAB 2: MULTI-VIDEO CURVE COMPARISON
# ==========================================
with tab_compare:
    st.subheader("📊 Overlay & Compare Retention Curves Across Videos")
    selected_compare_vids = st.multiselect(
        "Choose videos to overlay:",
        options=list(video_options.keys()),
        default=list(video_options.keys())[:5],
        format_func=lambda x: video_options[x]
    )

    if selected_compare_vids:
        comp_fig = go.Figure()
        for vid in selected_compare_vids:
            vdata = cached_videos[vid]
            vdf = pd.DataFrame(build_structured_timeline(vdata["metadata"], vdata["curve"], vdata["transcript"]))
            vdf["wrapped_spoken"] = vdf["spoken_text"].apply(wrap_hover_text)

            comp_fig.add_trace(
                go.Scatter(
                    x=vdf["bucket_pct"],
                    y=vdf["retention_pct"],
                    mode="lines",
                    name=vdata["metadata"]["title"][:45],
                    customdata=vdf[["timestamp_range", "delta_pct", "wrapped_spoken"]],
                    hovertemplate=(
                        f"<b>{vdata['metadata']['title'][:40]}</b><br>"
                        "⏱️ %{customdata[0]} (%{x}%) | <b>Retention:</b> %{y:.1f}% (%{customdata[1]:+.2f}%)<br>"
                        "🗣️ <i>\"%{customdata[2]}\"</i><extra></extra>"
                    )
                )
            )

        comp_fig.update_layout(
            xaxis_title="Elapsed Video Percentage (%)",
            yaxis_title="Audience Retention (%)",
            hovermode="closest",
            height=550
        )
        st.plotly_chart(comp_fig, use_container_width=True)

# ==========================================
# TAB 3: GEMINI RETENTION STRATEGIST
# ==========================================
with tab_ai:
    st.subheader("🧠 Run Gemini Retention & Script Analysis")
    
    analysis_scope = st.multiselect(
        "Select videos to include in the Gemini prompt:",
        options=list(video_options.keys()),
        default=list(video_options.keys()),
        format_func=lambda x: video_options[x]
    )
    model_choice = st.selectbox("Gemini Model", ["gemini-2.5-pro", "gemini-2.5-flash"], index=0)

    if st.button("✨ Generate Retention & Editing Playbook", type="primary"):
        if not os.getenv("GEMINI_API_KEY"):
            st.error("Please enter your GEMINI_API_KEY in the sidebar or `.env` file first.")
        elif not analysis_scope:
            st.warning("Please select at least one video to analyze.")
        else:
            with st.spinner(f"Analyzing {len(analysis_scope)} aligned video timelines with {model_choice}..."):
                try:
                    reports_to_send = [
                        align_retention_and_transcript(
                            cached_videos[v]["metadata"],
                            cached_videos[v]["curve"],
                            cached_videos[v]["transcript"]
                        )
                        for v in analysis_scope
                    ]
                    insights_md = analyze_with_llm(reports_to_send, model_name=model_choice)

                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    report_path = os.path.join("reports", f"retention_analysis_{timestamp}.md")
                    with open(report_path, "w", encoding="utf-8") as f:
                        f.write(insights_md)

                    st.session_state["latest_report"] = insights_md
                    st.session_state["latest_report_path"] = report_path
                except Exception as e:
                    st.error(f"Gemini API Error: {e}")

    if "latest_report" in st.session_state:
        st.success(f"Saved report to `{st.session_state['latest_report_path']}`")
        st.download_button(
            label="📥 Download Markdown Report",
            data=st.session_state["latest_report"],
            file_name=os.path.basename(st.session_state["latest_report_path"]),
            mime="text/markdown"
        )
        st.markdown("---")
        st.markdown(st.session_state["latest_report"])