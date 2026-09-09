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
                (
                    f"Reply to the question on {item.form.name} to "
                    "move this request forward."
                ),
                "Respond to query",
                f"{url}#queries",
                "warning",
            )
    for item, url in requests:
        if item and item.status == "sent_back":
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


def overview_next_step(workspace, tracks, organisation_review, registration):
    """Prioritise an actionable current request, then the next available form."""
    organisation_url = reverse("experiences:organisation")
    product_url = reverse("experiences:product-edit", args=[workspace.reference])
    tiles = [tile for track in tracks for tile in track["tiles"]]
    requests = [
        (organisation_review, organisation_url),
        (registration, product_url),
        *((tile["item"], tile["url"]) for tile in tiles),
    ]
    attention = _review_attention(requests)
    if attention:
        return attention
    if not workspace.product.organisation.is_verified:
        if organisation_review and organisation_review.status == "draft":
            return _step(
                "Complete your organisation verification",
                (
                    "Complete your organisation details and submit them "
                    "for verification before requesting milestone exit."
                ),
                "Continue organisation",
                organisation_url,
            )
        return _step(
            "Organisation verification is in progress",
            (
                "You can prepare your product details while the team "
                "verifies your organisation."
            ),
            "View organisation",
            organisation_url,
            "info",
        )
    if registration and registration.status == "draft":
        return _step(
            "Your product registration needs to be submitted",
            (
                "Review your saved product details and submit the registration. "
                "Product approval is required before requesting milestone exit."
            ),
            "Continue registration",
            product_url,
        )
    if registration and registration.status in {"new", "in_review", "query_raised"}:
        return _step(
            "Your product registration is under review",
            (
                "Your submitted details are saved. You can follow the "
                "review and any queries here."
            ),
            "View registration",
            product_url,
            "info",
        )
    return _milestone_next_step(tiles, product_url)


def _milestone_next_step(tiles, product_url):
    for tile in tiles:
        if tile["status"] == "draft":
            return _step(
                f"Continue with {tile['definition'].code}",
                (
                    "Add the required evidence at your pace. Save a "
                    "draft, then submit when it is ready."
                ),
                "Continue milestone",
                tile["url"],
            )
    if tiles and all(tile["status"] == "approved" for tile in tiles):
        return _step(
            "All applied milestones are approved",
            (
                "Your approval records are available below. You can "
                "add more milestones as your product grows."
            ),
            "Manage milestones",
            product_url,
        )
    if not tiles:
        return _step(
            "Choose your first milestones",
            (
                "Select the integration tracks that match your "
                "product to begin the review process."
            ),
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
        (
            "Your evidence is saved. Follow your track status below "
            "for decisions and responses."
        ),
        "View current request",
        current["url"],
        "info",
    )
