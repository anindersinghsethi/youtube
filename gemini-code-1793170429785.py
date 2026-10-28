def build_structured_timeline(metadata, retention_curve, transcript):
    """Returns a list of dicts mapping each 1% retention bucket to timestamps and spoken text."""
    duration = metadata["duration_seconds"]
    rows = []

    prev_ratio = 0.0
    prev_watch_ratio = retention_curve[0]["watch_ratio"] if retention_curve else 1.0

    for point in retention_curve:
        curr_ratio = point["elapsed_ratio"]
        start_sec = prev_ratio * duration
        end_sec = curr_ratio * duration

        lines = [
            seg["text"].replace("\n", " ")
            for seg in transcript
            if start_sec <= seg["start"] < end_sec
        ]
        spoken_text = " ".join(lines).strip()
        if not spoken_text:
            spoken_text = "[No dialogue / B-roll / Music]"

        delta_pct = (point["watch_ratio"] - prev_watch_ratio) * 100
        mm_ss = f"{int(start_sec // 60):02d}:{int(start_sec % 60):02d} - {int(end_sec // 60):02d}:{int(end_sec % 60):02d}"

        rows.append({
            "bucket_pct": int(curr_ratio * 100),
            "start_sec": int(start_sec),
            "end_sec": int(end_sec),
            "timestamp_range": mm_ss,
            "retention_pct": round(point["watch_ratio"] * 100, 2),
            "delta_pct": round(delta_pct, 2),
            "relative_score": round(point["relative_performance"], 2),
            "spoken_text": spoken_text
        })

        prev_ratio = curr_ratio
        prev_watch_ratio = point["watch_ratio"]

    return rows

def align_retention_and_transcript(metadata, retention_curve, transcript):
    """Formats the structured timeline into a compact text block for the LLM prompt."""
    rows = build_structured_timeline(metadata, retention_curve, transcript)
    aligned_lines = [
        f"[{r['timestamp_range']} | {r['bucket_pct']:3d}%] "
        f"Retention: {r['retention_pct']:5.1f}% (Delta: {r['delta_pct']:+5.2f}%) | "
        f"Relative Score: {r['relative_score']:.2f} | "
        f"Spoken: \"{r['spoken_text']}\""
        for r in rows
    ]

    header = (
        f"========================================\n"
        f"VIDEO TITLE: {metadata['title']} (ID: {metadata['video_id']})\n"
        f"PUBLISHED: {metadata['published_at']} | DURATION: {metadata['duration_seconds']} seconds\n"
        f"========================================"
    )
    return header + "\n" + "\n".join(aligned_lines)