"""TINM journal reader — affiche le rapport 7 jours sur l'usage réel.

Usage:
    python tinm_journal.py              # rapport 7 derniers jours
    python tinm_journal.py --days 14    # rapport 14 jours
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from tinm_paths import TINM_PCP_DIR as PCP_DIR


def load_journal(days: int) -> list[dict]:
    journal = PCP_DIR / "journal.jsonl"
    if not journal.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    entries = []
    for line in journal.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
            if ts >= cutoff:
                entries.append(e)
        except Exception:
            continue
    return entries


def report(days: int = 7) -> str:
    entries = load_journal(days)
    if not entries:
        return f"Aucune entrée dans les {days} derniers jours. Le journal se remplit automatiquement à chaque nouvelle session."

    sessions = [e for e in entries if e.get("event") == "session_start"]
    threads = sorted({e["thread"] for e in sessions})

    lines = [f"# Rapport TINM — {days} derniers jours", ""]
    lines.append(f"**Sessions enregistrées** : {len(sessions)}")
    lines.append(f"**Threads actifs** : {', '.join(threads)}")
    lines.append("")

    if sessions:
        first = sessions[0]
        last = sessions[-1]
        first_turns = first.get("turns", 0)
        last_turns = last.get("turns", 0)
        turns_delta = last_turns - first_turns
        first_art = first.get("artifacts", 0)
        last_art = last.get("artifacts", 0)
        art_delta = last_art - first_art

        lines.append("## Croissance du thread principal")
        lines.append(f"- Turns : {first_turns} → {last_turns} (+{turns_delta} sur la période)")
        lines.append(f"- Artifacts : {first_art} → {last_art} (+{art_delta})")
        lines.append("")

        lines.append("## Anchor par session (indicateur de dérive)")
        lines.append("_(Les termes doivent refléter ton vrai sujet de travail)_")
        lines.append("")
        for e in sessions:
            ts = e["ts"][:10]
            anchor = " · ".join(e.get("anchor", []))
            lines.append(f"- **{ts}** → `{anchor}`")

        lines.append("")
        lines.append("## Comment lire ce rapport")
        lines.append("- **Anchor stable** = TINM suit bien le fil de ton travail")
        lines.append("- **Anchor dérivé** (ex: termes Apollo quand tu travailles sur TINM) = signal de bruit")
        lines.append("- **Turns qui croît** = tu utilises TINM régulièrement")
        lines.append("- **Artifacts qui croît** = les livrables sont bien trackés")

    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rapport TINM 7 jours")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    print(report(args.days))
