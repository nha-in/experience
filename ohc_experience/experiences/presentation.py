"""Read-only presentation helpers for the workflow portal."""

from django.urls import reverse


def overview_progress(tracks):
    """Count canonical milestones once, including milestones shared by tracks."""
    tiles = {
        tile["definition"].key: tile for track in tracks for tile in track["tiles"]
    }
    return {
        "total": len(tiles),
        "approved": sum(tile["status"] == "approved" for tile in tiles.values()),
        "active_tracks": sum(bool(track["tiles"]) for track in tracks),
        "awaiting_review": sum(
            tile["status"] in {"new", "in_review", "query_raised"}
            for tile in tiles.values()
        ),
    }


def _agent_skill_summary(description):
    """What the opening sentence lists, past its "Use when ...:" lead-in."""
    sentence = description.split(". ")[0].strip().removesuffix(".")
    scope = sentence.split(": ", 1)[-1]
    return f"{scope[:1].upper()}{scope[1:]}." if scope else ""


def agent_skill_groups(workspace, tracks):
    """Agent Skills by track, then the shared ones that belong to no single track.

    Any skill can be installed. A skill is recommended when the product applied
    for a milestone it carries.
    """
    program = workspace.definition
    applied = {tile["definition"].key for track in tracks for tile in track["tiles"]}
    groups = {}
    for skill in program.agent_skills.skills():
        milestones = skill["milestones"]
        recommended = any(key in applied for key in milestones)
        track = None
        if not skill["shared"]:
            track = next(
                (
                    track
                    for track in program.tracks
                    for key in milestones
                    if key in track.keys
                ),
                None,
            )
        group = groups.setdefault(
            track.code if track else "",
            {"definition": track, "matched": False, "skills": []},
        )
        group["matched"] = group["matched"] or recommended
        group["skills"].append(
            {
                "definition": skill,
                "summary": _agent_skill_summary(skill["description"]),
                "recommended": recommended,
            },
        )
    rows = [
        {
            "code": code,
            "title": f"{code} Agent Skills" if code else "Shared Agent Skills",
            "caption": group["definition"].description
            if group["definition"]
            else "Used across tracks rather than belonging to one.",
            "matched": group["matched"],
            "skills": group["skills"],
        }
        for code, group in groups.items()
    ]
    # What this product applied for first, and each track before the shared skills.
    return sorted(rows, key=lambda row: (not row["matched"], not row["code"]))


def default_agent_skill(groups, tracks):
    """The skill the install panel opens on: the own skill of the first milestone
    that is not approved yet."""
    statuses = {}
    for track in tracks:
        for tile in track["tiles"]:
            # A milestone two tracks share keeps the place it was first given.
            statuses.setdefault(tile["definition"].key, tile["status"])
    current = next(
        (key for key, status in statuses.items() if status != "approved"),
        "",
    )
    rows = [row for group in groups for row in group["skills"]]
    chosen = next(
        (
            row
            for row in rows
            if not row["definition"]["shared"]
            and current in row["definition"]["milestones"]
        ),
        next(iter(rows), None),
    )
    return chosen["definition"]["slug"] if chosen else ""


def _step(title, detail, action, url, tone="primary"):
    return {
        "title": title,
        "detail": detail,
        "action": action,
        "url": url,
        "tone": tone,
    }


def _review_attention(requests):
    for item, url in requests:
        if (
            item
            and item.status == "query_raised"
            and item.queries.filter(
                submission=item.selected_submission,
                status="open",
            ).exists()
        ):
            return _step(
                "A reviewer needs your response",
                f"Reply to the question on {item.form.name}.",
                "Respond to query",
                f"{url}#queries",
                "warning",
            )
    for item, url in requests:
        if item and item.status == "rejected":
            return _step(
                "An update is needed",
                (
                    f"Review the feedback on {item.form.name}, update "
                    "your evidence and resubmit."
                ),
                "Review feedback",
                url,
                "warning",
            )
    return None


def overview_next_step(workspace, tracks, organisation_review):
    """Prioritise an actionable current request, then the next available form."""
    organisation_url = reverse("experiences:organisation")
    product_url = reverse("experiences:product-edit", args=[workspace.reference])
    tiles = [tile for track in tracks for tile in track["tiles"]]
    requests = [
        (organisation_review, organisation_url),
        *((tile["item"], tile["url"]) for tile in tiles),
    ]
    attention = _review_attention(requests)
    if attention:
        return attention
    organisation = workspace.product.organisation
    if (
        not organisation.is_verified
        and organisation_review
        and organisation_review.status == "draft"
    ):
        return _step(
            f"Complete your {organisation.noun} verification",
            (
                f"Submit your {organisation.noun} details for verification. You can "
                "submit milestones meanwhile, but they are approved only once "
                f"your {organisation.noun} is verified."
            ),
            "Continue verification",
            organisation_url,
        )
    return _milestone_next_step(tiles, product_url)


def _milestone_next_step(tiles, product_url):
    for tile in tiles:
        if tile["status"] == "draft" and not tile.get("locked_by"):
            return _step(
                f"Continue with {tile['definition'].code}",
                "Add the required evidence, then submit it for review.",
                "Continue milestone",
                tile["url"],
            )
    if tiles and all(tile["status"] == "approved" for tile in tiles):
        return _step(
            "All applied milestones are approved",
            "Your approval records are below. Add more milestones at any time.",
            "Manage milestones",
            product_url,
        )
    if not tiles:
        return _step(
            "Choose your first milestones",
            "Select the integration tracks that match your product.",
            "Choose milestones",
            product_url,
        )
    current = next(
        (
            tile
            for tile in tiles
            if tile["status"] in {"new", "in_review", "query_raised"}
        ),
        tiles[0],
    )
    return _step(
        "Your requests are with the review team",
        "Your evidence is saved. Track status and replies appear below.",
        "View current request",
        current["url"],
        "info",
    )
