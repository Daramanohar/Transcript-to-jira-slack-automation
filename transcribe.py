#!/usr/bin/env python3
"""Transcribe a local recording to TXT and PDF using faster-whisper."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Transcribe a local call recording into transcript.txt and transcript.pdf."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to recording file (.mp4/.m4a/.mp3/.wav/.aac).",
    )
    parser.add_argument(
        "--model",
        default="small",
        help='faster-whisper model size (default: "small").',
    )
    parser.add_argument(
        "--outdir",
        default="./data",
        help='Output folder (default: "./data").',
    )
    parser.add_argument(
        "--lang",
        default="en",
        help='Language code for transcription (default: "en").',
    )
    return parser.parse_args()


def format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS."""
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def write_txt(path: Path, source_name: str, generated_at: str, lines: list[str]) -> None:
    """Write UTF-8 plain-text transcript."""
    content = [f"Source: {source_name} | Generated: {generated_at}", "", *lines]
    path.write_text("\n".join(content) + "\n", encoding="utf-8")


def write_pdf(path: Path, source_name: str, generated_at: str, lines: list[str]) -> None:
    """Write wrapped PDF transcript using reportlab."""
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TranscriptTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        alignment=TA_LEFT,
        spaceAfter=8,
    )
    subtitle_style = ParagraphStyle(
        "TranscriptSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor="#444444",
        spaceAfter=14,
    )
    body_style = ParagraphStyle(
        "TranscriptBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        wordWrap="LTR",
        splitLongWords=1,
        spaceAfter=6,
    )

    story = [
        Paragraph("Meeting Transcript", title_style),
        Paragraph(escape(f"Source: {source_name} | Generated: {generated_at}"), subtitle_style),
        Spacer(1, 0.1 * inch),
    ]

    for line in lines:
        story.append(Paragraph(escape(line), body_style))

    doc.build(story)


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser()

    if not input_path.exists() or not input_path.is_file():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 1

    outdir = Path(args.outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    txt_path = outdir / "transcript.txt"
    pdf_path = outdir / "transcript.pdf"

    lines: list[str] = []
    duration = 0.0

    try:
        from faster_whisper import WhisperModel

        # CPU + int8 keeps memory use modest and works without GPU setup.
        model = WhisperModel(args.model, device="cpu", compute_type="int8")

        # faster-whisper returns a segment generator; iterate to stream progress.
        segments, info = model.transcribe(
            str(input_path),
            language=args.lang,
            vad_filter=True,
            beam_size=5,
        )
        duration = float(getattr(info, "duration", 0.0) or 0.0)

        for segment in segments:
            line = f"[{format_timestamp(segment.start)}] {segment.text.strip()}"
            lines.append(line)
            print(line, flush=True)

    except Exception as exc:  # noqa: BLE001 - CLI should show readable failures.
        print(f"Error: transcription failed: {exc}", file=sys.stderr)
        print(
            "Hint: check that ffmpeg is installed and available on PATH, "
            "then verify the input file is a supported audio/video file.",
            file=sys.stderr,
        )
        return 1

    try:
        write_txt(txt_path, input_path.name, generated_at, lines)
        write_pdf(pdf_path, input_path.name, generated_at, lines)
    except Exception as exc:  # noqa: BLE001 - output failures should be readable.
        print(f"Error: failed to write output files: {exc}", file=sys.stderr)
        return 1

    print("\nDone.")
    print(f"TXT: {txt_path.resolve()}")
    print(f"PDF: {pdf_path.resolve()}")
    print(f"Segments: {len(lines)}")
    print(f"Audio duration: {format_timestamp(duration)} ({duration:.2f} seconds)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
