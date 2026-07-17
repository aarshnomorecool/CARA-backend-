"""
Per-user category preference weights are always derived from the CURRENT set
of interaction rows for that user+category, replayed through an EMA in
timestamp order - never mutated incrementally in place. This makes the
weight self-healing: removing an interaction (e.g. unbookmarking) and
recomputing correctly undoes its influence, instead of leaving a permanent
mark the way an irreversible "bump the running average on write" update
would.

Real bug this fixes (2026-07-17): DELETE /users/{id}/saved-places/{place_id}
only ever deleted the interaction log row, never touched `preferences` -
bookmarking then unbookmarking a place permanently inflated that category's
weight forever, with no way to undo it. Two hospital bookmark/unbookmark
cycles (one from live backend diagnosis, one from on-device testing) pushed
hospital to 48% preference weight despite zero hospitals ever appearing in
Saved - which then dominated "Matches your taste" and, via preference_weight
as a ranking feature, the general recommendation feed even for neutral-mood
queries.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.interaction import Interaction, InteractionAction
from app.models.place import Place, PlaceCategory
from app.models.preference import Preference
from ml.features import CATEGORIES

# How strongly each action type pulls the EMA toward itself. bookmark/
# order_intent are strong explicit signals; a click is a weak positive
# signal; dismiss pulls the weight down.
ACTION_SIGNAL = {
    InteractionAction.click: 0.4,
    InteractionAction.bookmark: 0.9,
    InteractionAction.dismiss: 0.05,
    InteractionAction.order_intent: 1.0,
}
EMA_ALPHA = 0.3
DEFAULT_PREFERENCE_WEIGHT = 1 / len(CATEGORIES)  # matches recommendations.py's cold-start default


def recompute_preference(db: Session, user_id: int, category: PlaceCategory) -> None:
    """Replays every interaction currently logged for this user+category (in
    timestamp order) through the EMA formula from the cold-start default, and
    writes the result. Call this after ANY write that adds or removes an
    interaction for a given user+category - never mutate `Preference.weight`
    directly elsewhere."""
    actions = list(
        db.execute(
            select(Interaction.action)
            .join(Place, Place.place_id == Interaction.place_id)
            .where(Interaction.user_id == user_id, Place.category == category)
            .order_by(Interaction.timestamp)
        ).scalars()
    )

    weight = DEFAULT_PREFERENCE_WEIGHT
    for action in actions:
        weight = max(0.0, min(1.0, (1 - EMA_ALPHA) * weight + EMA_ALPHA * ACTION_SIGNAL[action]))

    pref = db.get(Preference, (user_id, category))
    if pref is None:
        db.add(Preference(user_id=user_id, category=category, weight=weight))
    else:
        pref.weight = weight
