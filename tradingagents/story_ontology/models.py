from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class TimelineSpan(BaseModel):
    start_chapter: int | None = Field(default=None, ge=0)
    end_chapter: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> "TimelineSpan":
        if self.start_chapter is not None and self.end_chapter is not None and self.start_chapter > self.end_chapter:
            raise ValueError("start_chapter must be <= end_chapter")
        return self

    def contains(self, chapter: int) -> bool:
        if self.start_chapter is not None and chapter < self.start_chapter:
            return False
        if self.end_chapter is not None and chapter > self.end_chapter:
            return False
        return True


class Entity(BaseModel):
    entity_type: str
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    summary: str = ""
    tags: list[str] = Field(default_factory=list)


class Character(Entity):
    entity_type: Literal["character"] = "character"
    desires: list[str] = Field(default_factory=list)
    fears: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    affiliations: list[str] = Field(default_factory=list)
    status: str = "alive"


class Faction(Entity):
    entity_type: Literal["faction"] = "faction"
    ideology: str = ""
    resources: list[str] = Field(default_factory=list)


class Place(Entity):
    entity_type: Literal["place"] = "place"
    region: str = ""
    traits: list[str] = Field(default_factory=list)


EntityNode = Annotated[Character | Faction | Place, Field(discriminator="entity_type")]


class Relation(BaseModel):
    id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    span: TimelineSpan = Field(default_factory=TimelineSpan)
    visibility: Literal["public", "private", "secret"] = "private"
    intensity: float | None = Field(default=None, ge=0, le=1)


class KnowledgeFact(BaseModel):
    id: str = Field(min_length=1)
    knower_id: str = Field(min_length=1)
    fact: str = Field(min_length=1)
    span: TimelineSpan = Field(default_factory=TimelineSpan)
    certainty: float = Field(default=1.0, ge=0, le=1)
    source: str = ""


class Event(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    chapter: int = Field(ge=0)
    participant_ids: list[str] = Field(default_factory=list)
    location_id: str | None = None
    consequence_ids: list[str] = Field(default_factory=list)


class Scene(BaseModel):
    id: str = Field(min_length=1)
    chapter: int = Field(ge=0)
    pov_character_id: str = Field(min_length=1)
    location_id: str | None = None
    participant_ids: list[str] = Field(default_factory=list)
    goal: str = ""
    conflict: str = ""
    revealed_facts: list[str] = Field(default_factory=list)


class Rule(BaseModel):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    applies_to: list[str] = Field(default_factory=list)
    condition: str = ""
    effect: str = ""
    exceptions: list[str] = Field(default_factory=list)


class StoryOntology(BaseModel):
    entities: dict[str, EntityNode] = Field(default_factory=dict)
    relations: dict[str, Relation] = Field(default_factory=dict)
    knowledge_facts: dict[str, KnowledgeFact] = Field(default_factory=dict)
    events: dict[str, Event] = Field(default_factory=dict)
    scenes: dict[str, Scene] = Field(default_factory=dict)
    rules: dict[str, Rule] = Field(default_factory=dict)

    @field_validator("entities", mode="before")
    @classmethod
    def normalize_entities(cls, value):
        return _normalize_collection(value)

    @field_validator("relations", "knowledge_facts", "events", "scenes", "rules", mode="before")
    @classmethod
    def normalize_other_collections(cls, value):
        return _normalize_collection(value)

    def active_relations_for(self, subject_id: str, *, chapter: int) -> list[Relation]:
        return [
            relation
            for relation in self.relations.values()
            if relation.subject_id == subject_id and relation.span.contains(chapter)
        ]

    def knowledge_for(self, knower_id: str, *, chapter: int) -> list[KnowledgeFact]:
        return [
            fact
            for fact in self.knowledge_facts.values()
            if fact.knower_id == knower_id and fact.span.contains(chapter)
        ]

    def validate_scene(self, scene_id: str) -> list[dict[str, str | int]]:
        scene = self.scenes[scene_id]
        issues: list[dict[str, str | int]] = []
        deceased_ids = self._deceased_participants_before(scene.chapter)
        for participant_id in scene.participant_ids:
            if participant_id in deceased_ids:
                issues.append(
                    {
                        "code": "dead_participant",
                        "scene_id": scene.id,
                        "entity_id": participant_id,
                        "fact": "",
                        "chapter": scene.chapter,
                    }
                )

        known_facts = {fact.fact for fact in self.knowledge_for(scene.pov_character_id, chapter=scene.chapter)}
        for fact in scene.revealed_facts:
            if fact not in known_facts:
                issues.append(
                    {
                        "code": "pov_knowledge_leak",
                        "scene_id": scene.id,
                        "entity_id": scene.pov_character_id,
                        "fact": fact,
                        "chapter": scene.chapter,
                    }
                )
        return issues

    def _deceased_participants_before(self, chapter: int) -> set[str]:
        deceased: set[str] = set()
        for event in self.events.values():
            if event.chapter > chapter:
                continue
            consequence_ids = set(event.consequence_ids)
            for participant_id in event.participant_ids:
                if event.event_type == "death" or f"status:{participant_id}:dead" in consequence_ids:
                    deceased.add(participant_id)
        return deceased


def _normalize_collection(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {item["id"] if isinstance(item, dict) else item.id: item for item in value}
    raise TypeError("ontology collections must be dicts or lists")
