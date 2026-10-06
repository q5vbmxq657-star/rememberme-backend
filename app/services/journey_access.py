"""Stable prompt-to-level contract matching the shipped twenty-chapter Journey."""
from fastapi import HTTPException
from psycopg.rows import dict_row
from app.services.plan_access import access_decision, effective_plan

PROMPT_LEVELS = {prompt: level for level, group in enumerate((
    "early-years.first-home early-years.childhood-scene early-years.childhood-dream",
    "people-places.family family.role family.inherited-trait",
    "people-places.home home.favorite-corner home.welcome",
    "people-places.friendship friendships.loyalty friendships.shared-world",
    "voice-character.affection love.partnership love.felt-loved",
    "school-days.teacher school-days.belonging school-days.lesson",
    "people-places.work work-and-purpose.pride work-and-purpose.style",
    "passions.alive passions.craft passions.share",
    "people-places.journey travel.discovery travel.companion",
    "celebrations.tradition celebrations.birthday celebrations.host",
    "everyday-moments.ritual daily-life.morning daily-life.small-habit",
    "food-and-traditions.signature food-and-traditions.table food-and-traditions.recipe",
    "everyday-moments.greeting everyday-moments.phrase voice-character.storytelling",
    "everyday-moments.laughter humor.style humor.shared-joke",
    "voice-character.disagreement voice-character.silence personality.decision",
    "everyday-moments.comfort voice-character.encouragement care-and-comfort.practical",
    "life-values.courage challenges-and-courage.hard-season challenges-and-courage.strength",
    "life-values.regret turning-points.new-direction turning-points.chance",
    "life-values.principle values-and-beliefs.fairness values-and-beliefs.faith",
    "life-values.lesson life-values.hope lessons-and-legacy.remember",
), 1) for prompt in group.split()}


def require_journey_write(cursor, request, user_id):
    guided = [m for m in request.memories if m.sync_metadata.guided_prompt_id]
    if not guided:
        return
    if user_id is None:
        raise HTTPException(403, "Authenticated Journey ownership is required.")
    with cursor.connection.cursor(row_factory=dict_row) as db:
        old = db.execute("""SELECT memory_id,sync_metadata FROM memory_embeddings
            WHERE lower(profile_id)=lower(%s)""", (request.profile_id,)).fetchall()
        existing = {row["memory_id"].lower(): row["sync_metadata"].get("guided_prompt_id") for row in old}
        plan = effective_plan(db, user_id)
        for memory in guided:
            prompt = memory.sync_metadata.guided_prompt_id
            # A downgrade must not prevent editing or syncing previously saved stories.
            if existing.get(memory.id.lower()) == prompt:
                continue
            level = PROMPT_LEVELS.get(prompt)
            if level is None:
                raise HTTPException(422, "This Journey question is not supported. Update STAY.")
            decision = access_decision(plan=plan, action="journey_level", requested_level=level)
            if not decision["allowed"]:
                raise HTTPException(402, detail={**decision,
                    "message": "Your free Journey includes the first five chapters. Explore plans to continue."})
