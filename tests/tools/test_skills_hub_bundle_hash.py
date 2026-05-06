from tools.skills_hub import SkillBundle, bundle_content_hash


def test_bundle_content_hash_accepts_text_and_bytes_files():
    bundle = SkillBundle(
        name="mixed",
        files={"SKILL.md": "# Skill\n", "assets/icon.png": b"\x89PNG\r\n"},
        source="github",
        identifier="owner/repo/skills/mixed",
        trust_level="community",
    )

    digest = bundle_content_hash(bundle)

    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 16


def test_bundle_content_hash_is_deterministic_independent_of_file_order():
    bundle_a = SkillBundle(
        name="ordered",
        files={"b.txt": "two", "a.txt": b"one"},
        source="github",
        identifier="owner/repo/skills/ordered",
        trust_level="community",
    )
    bundle_b = SkillBundle(
        name="ordered",
        files={"a.txt": b"one", "b.txt": "two"},
        source="github",
        identifier="owner/repo/skills/ordered",
        trust_level="community",
    )

    assert bundle_content_hash(bundle_a) == bundle_content_hash(bundle_b)
