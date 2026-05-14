"""Build docs/assets/social-preview.png — the 1280×640 image GitHub
shows when someone pastes the repo link in a DM, on X, or in Slack.

Run:
    ~/.tinm/.venv/bin/python docs/assets/build_social_preview.py

Upload the resulting PNG via github.com/momo590/tinm/settings →
"Social preview" → Upload an image.
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# GitHub social preview size. Two values are observed:
#   1280×640 — what github.com docs say to use (sometimes rejected by
#              the upload pipeline with "Something went really wrong")
#   1200× 600 — what GitHub *itself* generates for repos without a
#               custom social preview (via opengraph.githubassets.com)
# We render at 1200×600 because that's the size GitHub's own pipeline
# emits, and so the most certain to round-trip through the upload step.
W, H = 1200, 600

# GitHub-dark palette so the card reads naturally in both DM dark mode
# and X dark mode (most power-users browse in dark).
BG = "#0d1117"
FG = "#e6edf3"          # primary text
DIM = "#7d8590"         # secondary text
ACCENT = "#3fb950"      # GitHub green — the [TINM ...] hook line color
TERM_BG = "#161b22"     # terminal panel background
TERM_BORDER = "#30363d"
NUMBER_HL = "#79c0ff"   # GitHub blue — the +0.114 number

SANS_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def f(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def main() -> None:
    out = Path(__file__).resolve().parent / "social-preview.png"
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # ── Header: brand + tagline ──────────────────────────────────────
    d.text((60, 50), "TINM", font=f(SANS_BOLD, 88), fill=FG)
    d.text(
        (60, 150),
        "Cross-session memory for Claude Code.",
        font=f(SANS, 32),
        fill=FG,
    )
    d.text(
        (60, 195),
        "Stop re-pasting yesterday's context.",
        font=f(SANS, 28),
        fill=DIM,
    )

    # ── Terminal panel: the whoa moment, verbatim ────────────────────
    term_x, term_y = 60, 245
    term_w, term_h = W - 120, 260
    d.rounded_rectangle(
        [term_x, term_y, term_x + term_w, term_y + term_h],
        radius=12,
        fill=TERM_BG,
        outline=TERM_BORDER,
        width=2,
    )

    # Three "traffic light" dots in the title bar.
    for i, c in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        cx = term_x + 22 + i * 22
        cy = term_y + 22
        d.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=c)

    # Terminal content.
    mono = f(MONO, 22)
    mono_b = f(MONO_BOLD, 22)
    ty = term_y + 55
    line_h = 30

    d.text((term_x + 25, ty), "$ claude", font=mono, fill=FG); ty += line_h

    # [TINM — Turn 1 ...] hook line in accent green.
    d.text(
        (term_x + 25, ty),
        "[TINM — Turn 1 | Anchor: tinm pareto wiki2hop phase1]",
        font=mono,
        fill=ACCENT,
    ); ty += line_h

    # User prompt line.
    d.text((term_x + 25, ty), "> What was the biggest absolute effect we", font=mono, fill=FG); ty += line_h
    d.text((term_x + 25, ty), "  measured on the 2WikiMultihopQA pilot?", font=mono, fill=FG); ty += int(line_h * 1.2)

    # Claude's response with the number highlighted.
    d.text((term_x + 25, ty), "  tinm_a085 hit 0.481 — ", font=mono, fill=FG)
    # Measure where to put the highlighted number.
    prefix = "  tinm_a085 hit 0.481 — "
    prefix_w = d.textlength(prefix, font=mono)
    d.text(
        (term_x + 25 + prefix_w, ty),
        "+0.114 absolute lift",
        font=mono_b,
        fill=NUMBER_HL,
    )
    ty += line_h
    d.text(
        (term_x + 25, ty),
        "  over rag_baseline, paired t=4.03.",
        font=mono,
        fill=FG,
    )

    # ── Footer: install one-liner + N=1 evidence callout ─────────────
    # Curl bar (subtle background strip)
    curl_y = 535
    d.rectangle([0, curl_y - 15, W, curl_y + 45], fill="#010409")
    d.text(
        (60, curl_y),
        "curl -fsSL ...momo590/tinm/main/mvp/scripts/install.sh | bash",
        font=f(MONO, 22),
        fill=DIM,
    )

    # Save as both PNG (no optimize, vanilla sRGB) and JPG. GitHub's
    # Social preview pipeline rejects some optimize-True PNGs with a
    # generic "Something went really wrong" error; vanilla PNG and JPG
    # both work reliably. Try uploading the JPG first.
    out_png = out.with_suffix(".png")
    out_jpg = out.with_suffix(".jpg")
    img.save(out_png, "PNG")
    img.convert("RGB").save(out_jpg, "JPEG", quality=92, optimize=False)
    print(f"wrote {out_png} ({out_png.stat().st_size // 1024} KB, {W}×{H})")
    print(f"wrote {out_jpg} ({out_jpg.stat().st_size // 1024} KB, {W}×{H})")


if __name__ == "__main__":
    main()
