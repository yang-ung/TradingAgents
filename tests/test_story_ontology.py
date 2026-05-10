from __future__ import annotations

import pytest


@pytest.mark.unit
def test_story_ontology_tracks_timeline_relations_knowledge_and_json_roundtrip():
    from tradingagents.story_ontology import Character, Faction, KnowledgeFact, Place, Relation, Scene, StoryOntology, TimelineSpan

    ontology = StoryOntology(
        entities=[
            Character(id="char_arin", name="아린", summary="북부 귀족의 사생아", desires=["왕의 죽음의 진실 규명"]),
            Character(id="char_sion", name="시온", summary="왕실 기사"),
            Faction(id="faction_north", name="북부파", summary="왕권 견제 세력"),
            Place(id="place_palace", name="왕궁", summary="왕이 살해된 장소"),
        ],
        relations=[
            Relation(
                id="rel_arin_member",
                subject_id="char_arin",
                predicate="member_of",
                object_id="faction_north",
                span=TimelineSpan(start_chapter=1),
                visibility="public",
            ),
            Relation(
                id="rel_arin_sion",
                subject_id="char_arin",
                predicate="distrusts",
                object_id="char_sion",
                span=TimelineSpan(start_chapter=3, end_chapter=8),
                visibility="private",
            ),
        ],
        knowledge_facts=[
            KnowledgeFact(
                id="know_arin_poison",
                knower_id="char_arin",
                fact="왕은 독살되었다",
                span=TimelineSpan(start_chapter=2),
                certainty=0.4,
                source="anonymous_letter",
            ),
        ],
        scenes=[
            Scene(
                id="scene_trial",
                chapter=4,
                pov_character_id="char_arin",
                location_id="place_palace",
                participant_ids=["char_arin", "char_sion"],
                goal="시온의 반응을 떠본다",
                conflict="누가 왕을 죽였는지 서로 떠본다",
                revealed_facts=["왕은 독살되었다"],
            )
        ],
    )

    active_relation = ontology.active_relations_for("char_arin", chapter=4)
    expired_relation = ontology.active_relations_for("char_arin", chapter=9)
    known_facts = ontology.knowledge_for("char_arin", chapter=4)
    restored = StoryOntology.model_validate_json(ontology.model_dump_json())

    assert [relation.id for relation in active_relation] == ["rel_arin_member", "rel_arin_sion"]
    assert [relation.id for relation in expired_relation] == ["rel_arin_member"]
    assert [fact.fact for fact in known_facts] == ["왕은 독살되었다"]
    assert restored.entities["char_arin"].name == "아린"
    assert restored.scenes["scene_trial"].participant_ids == ["char_arin", "char_sion"]


@pytest.mark.unit
def test_story_ontology_scene_validation_flags_dead_participants_and_pov_knowledge_leaks():
    from tradingagents.story_ontology import Character, Event, KnowledgeFact, Place, Scene, StoryOntology, TimelineSpan

    ontology = StoryOntology(
        entities=[
            Character(id="char_arin", name="아린", summary="추적자"),
            Character(id="char_sion", name="시온", summary="기사"),
            Place(id="place_gate", name="북문", summary="국경 관문"),
        ],
        events=[
            Event(
                id="event_sion_death",
                name="시온의 죽음",
                event_type="death",
                chapter=5,
                participant_ids=["char_sion"],
                location_id="place_gate",
                consequence_ids=["status:char_sion:dead"],
            )
        ],
        knowledge_facts=[
            KnowledgeFact(
                id="know_reader_truth",
                knower_id="char_narrator",
                fact="아린은 왕의 사생아다",
                span=TimelineSpan(start_chapter=6),
                certainty=1.0,
                source="omniscient_narration",
            )
        ],
        scenes=[
            Scene(
                id="scene_gate",
                chapter=7,
                pov_character_id="char_arin",
                location_id="place_gate",
                participant_ids=["char_arin", "char_sion"],
                goal="도망친 사제를 붙잡는다",
                conflict="진실을 모르는 채 추궁한다",
                revealed_facts=["아린은 왕의 사생아다"],
            )
        ],
    )

    issues = ontology.validate_scene("scene_gate")

    assert {issue["code"] for issue in issues} == {"dead_participant", "pov_knowledge_leak"}
    assert any(issue["entity_id"] == "char_sion" for issue in issues)
    assert any(issue["fact"] == "아린은 왕의 사생아다" for issue in issues)
