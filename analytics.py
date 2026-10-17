"""Pure Python statistics; one input row is one shot, never one rally."""

from collections import Counter, defaultdict
from statistics import mean

HEIGHTS = ["above_tape", "above_net_midline", "below_net_midline", "below_net"]
ERRORS = ["into_the_net", "out_the_back", "out_on_sides", "unknown"]


def percentage(n, total):
    return round(100 * n / total, 1) if total else None


def distribution(counts, total=None):
    total = sum(counts.values()) if total is None else total
    return [{"name": k, "value": v, "percent": percentage(v, total)}
            for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]


def contact(shot):
    # Same explicit convention visible in the reference's contact-height note.
    if shot.get("shot_family") in ("Brush", "Kill"):
        return "above_tape"
    return shot.get("contact_point")


def describe(shot):
    fields = ("striker", "striking_side", "shot_variant", "shot_family",
              "direction", "serve_placement")
    return " ".join(str(shot[k]) for k in fields if shot.get(k))


def analyze(rows, perspective="you"):
    selected = 1 if perspective == "you" else 0
    grouped = {}
    for shot in rows:
        grouped.setdefault((shot["match_id"], shot["rally_id"]), []).append(shot)
    rallies = []
    warnings = []
    for (_, rid), shots in grouped.items():
        shots = sorted(shots, key=lambda s: (s.get("shot_number_in_rally") or 0,
                                             s.get("video_timestamp_seconds") or 0))
        last = shots[-1]
        complete = bool(last.get("is_final_shot"))
        won = last.get("rally_won_by_you")
        # Unknown or unfinished rallies must not be silently counted as losses.
        won = (won == selected) if won in (0, 1) and complete else None
        rallies.append({"id": rid, "game": last.get("game_number"),
                        "number": last.get("rally_number_in_game") or last.get("rally_number"),
                        "shots": shots, "length": len(shots), "won": won,
                        "duration": last.get("rally_duration_s"),
                        "start": shots[0].get("rally_start_s"),
                        "end": last.get("rally_end_s"),
                        "score_before": [shots[0].get("score_a_before"), shots[0].get("score_b_before")],
                        "score": [last.get("score_a_after"), last.get("score_b_after")],
                        "outcome": last.get("rally_end_type") if complete else "unknown"})
        if last.get("rally_shots") not in (None, len(shots)):
            warnings.append("部分回合标注拍数与实际记录数不同，平均拍数使用实际记录。")
    players = [{"id": key, "name": name, "is_you": flag}
               for key, name, flag in dict.fromkeys(
                   (s.get("striker"), s.get("striker_name") or s.get("striker"),
                    s.get("striker_is_you")) for s in rows)]
    own = [s for s in rows if s.get("striker_is_you") == selected]
    wins = [r for r in rallies if r["won"] is True]
    losses = [r for r in rallies if r["won"] is False]
    unknown = len(rallies) - len(wins) - len(losses)
    if unknown:
        warnings.append(f"{unknown} 个回合胜负未完整标注，未计入胜率及得失分分布。")
    durations = [r["duration"] for r in rallies if r["duration"] is not None]
    summary = {"shots": len(rows), "own_shots": len(own), "rallies": len(rallies),
               "wins": len(wins), "losses": len(losses), "unknown": unknown,
               "win_rate": percentage(len(wins), len(wins) + len(losses)),
               "avg_shots": round(mean(r["length"] for r in rallies), 1) if rallies else None,
               "avg_duration": round(mean(durations), 1) if durations else None}
    # Winning contribution: selected side's last shot; loss: rally's final shot.
    # This is a distribution of points, not P(win | played a given technique).
    win_counts, loss_counts = Counter(), Counter()
    win_detail, loss_detail = defaultdict(Counter), defaultdict(Counter)
    for r in wins:
        last = r["shots"][-1]
        own_shots = [s for s in r["shots"] if s.get("striker_is_you") == selected]
        family = own_shots[-1].get("shot_family") if own_shots else "Unknown"
        family = family or "Unknown"
        category = "My winner" if last.get("striker_is_you") == selected else {
            "forced_error": "Forced opp. error", "unforced_error": "Drew opp. unforced"
        }.get(last.get("outcome"), "Other")
        win_counts[family] += 1
        win_detail[family][category] += 1
    for r in losses:
        last = r["shots"][-1]
        family = last.get("shot_family") or "Unknown"
        category = "Opp. winner" if last.get("striker_is_you") != selected else {
            "forced_error": "My forced error", "unforced_error": "My unforced error"
        }.get(last.get("outcome"), "Other")
        loss_counts[family] += 1
        loss_detail[family][category] += 1
    winning = distribution(win_counts)
    losing = distribution(loss_counts)
    for item in winning:
        item["detail"] = dict(win_detail[item["name"]])
    for item in losing:
        item["detail"] = dict(loss_detail[item["name"]])

    patterns = {}
    for size in (3, 5):
        patterns[str(size)] = {}
        for mode in ("all", "won", "lost"):
            eligible = [r for r in rallies if len(r["shots"]) >= size and
                        [s.get("shot_number_in_rally") for s in r["shots"][:size]] == list(range(1, size + 1)) and
                        (mode == "all" or r["won"] is (mode == "won"))]
            counts = Counter(tuple(describe(s) for s in r["shots"][:size]) for r in eligible)
            patterns[str(size)][mode] = {"eligible": len(eligible), "items": [
                {"sequence": list(k), "count": v} for k, v in counts.most_common()]}

    # Return individual tagged records so section filters and drilldowns share data.
    heights, replies, errors = [], [], []
    for r in rallies:
        for i, shot in enumerate(r["shots"]):
            base = {"player": shot.get("striker_name") or shot.get("striker"),
                    "family": shot.get("shot_family") or "Unknown",
                    "side": shot.get("striking_side"), "direction": shot.get("direction"),
                    "variant": shot.get("shot_variant"), "rally_id": r["id"],
                    "shot_id": shot.get("shot_id"), "time": shot.get("video_timestamp_seconds")}
            height = contact(shot)
            if height in HEIGHTS:
                heights.append({**base, "category": height})
            if shot.get("outcome") in ("forced_error", "unforced_error"):
                errors.append({**base, "category": shot.get("error_outcome") or "unknown"})
            if i + 1 < len(r["shots"]):
                reply = r["shots"][i + 1]
                height = contact(reply)
                # Do not join across rallies or across missing/interrupted shot numbers.
                consecutive = (shot.get("shot_number_in_rally") is not None and
                               reply.get("shot_number_in_rally") == shot["shot_number_in_rally"] + 1)
                a, b = str(shot.get("striker") or ""), str(reply.get("striker") or "")
                other_team = a[:1] != b[:1] and a[:1] in ("A", "B") and b[:1] in ("A", "B")
                if consecutive and other_team and height in HEIGHTS:
                    replies.append({**base, "category": height})

    games = []
    for number in dict.fromkeys(r["game"] for r in rallies):
        group = [r for r in rallies if r["game"] == number]
        games.append({"number": number, "score": group[-1]["score"], "rallies": len(group)})
    own_names = " / ".join(p["name"] for p in players if p["is_you"] == selected) or "所选方"
    own_errors = Counter(s.get("error_outcome") or "unknown" for s in own
                         if s.get("outcome") in ("forced_error", "unforced_error"))
    own_winners = sum(s.get("outcome") == "winner" for s in own)
    rate = f'{summary["win_rate"]}%' if summary["win_rate"] is not None else "暂无数据"
    text = (f'{own_names} 在所选范围内赢下 {len(wins)} 个回合，输掉 {len(losses)} 个回合，'
            f'回合胜率为 {rate}。共记录 {len(rows)} 拍，其中所选方 {len(own)} 拍，'
            f'直接制胜 {own_winners} 拍，失误 {sum(own_errors.values())} 拍。')
    usage = distribution(Counter(s.get("shot_family") or "Unknown" for s in own))
    notes = [f'所选方直接制胜 {own_winners} 拍；非受迫失误 '
             f'{sum(s.get("outcome") == "unforced_error" for s in own)} 拍。',
             f'下网 {own_errors["into_the_net"]} 次、底线出界 {own_errors["out_the_back"]} 次、'
             f'边线出界 {own_errors["out_on_sides"]} 次、落点未标注 {own_errors["unknown"]} 次。']
    if usage:
        notes.append(f'使用最多的技术是 {usage[0]["name"]}，共 {usage[0]["value"]} 拍，'
                     f'占所选方击球的 {usage[0]["percent"]}%。')
    return {"summary": summary, "summary_text": text, "notes": notes, "players": players,
            "game_scores": games, "rallies": rallies, "winning": winning, "losing": losing,
            "usage": usage, "patterns": patterns, "heights": heights, "replies": replies,
            "errors": errors, "outcomes": distribution(Counter(r["outcome"] or "unknown" for r in wins)),
            "warnings": sorted(set(warnings)), "perspective": perspective,
            "facets": {field: sorted({s[field] for s in rows if s.get(field)})
                       for field in ("striking_side", "direction", "shot_variant")}}
