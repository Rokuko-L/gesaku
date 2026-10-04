"""Offline tests for core.retrieval — no LLM, no network."""

import os
import unittest
from pathlib import Path

from core import retrieval


CHARACTERS = """# CHARACTER REGISTRY

## **1. THE NARRATOR: DANIEL "DANNY" VEY**
**Age:** 17
**Role:** Protagonist. Hyper-observant teen.

### **Personality & Flaws**
- Hypervigilant. Speech Patterns: clipped sentences.

---

## **2. SUPPORTING CHARACTERS**

### **A. MATEO RIVERA (Replaced Best Friend)**
**Age:** 17
**Role:** Danny's best friend.

#### **Speech Patterns**
- Repeats phrases after replacement.

### **B. MS. HARGROVE (Teacher)**
**Role:** Suspicious teacher.
"""

WORLD = """# WORLD BIBLE

## **I. THE RULES OF THE HORROR**
The Choir mimics and replaces.

## **II. THE SCHOOL AS A LIVING ENTITY**
Blackthorn Academy is a predator.

### **History & Lore**
- Founded 1920s.
"""

OUTLINE = """
### Chapter 2: The Wrong Laugh
1. Danny confronts Mateo after the nurse's office.
2. Ms. Hargrove watches from the hallway.
3. Blackthorn Academy corridors shift overnight.
Orientation Facts:
  - Mateo's laugh is delayed
  - The Choir's hum is subsonic
"""


class TestRetrievalMode(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("GESAKU_RETRIEVAL_MODE", None)

    def test_default_is_dump(self):
        os.environ.pop("GESAKU_RETRIEVAL_MODE", None)
        self.assertEqual(retrieval.retrieval_mode(), "dump")

    def test_scoped_and_unknown(self):
        os.environ["GESAKU_RETRIEVAL_MODE"] = "scoped"
        self.assertEqual(retrieval.retrieval_mode(), "scoped")
        os.environ["GESAKU_RETRIEVAL_MODE"] = "bogus"
        self.assertEqual(retrieval.retrieval_mode(), "dump")


class TestQueryTerms(unittest.TestCase):
    def test_extracts_proper_nouns_and_skips_stopwords(self):
        terms = retrieval.extract_query_terms(OUTLINE)
        lowered = {t.lower() for t in terms}
        self.assertTrue(any("danny" in t for t in lowered))
        self.assertTrue(any("mateo" in t for t in lowered))
        self.assertTrue(any("blackthorn" in t for t in lowered))
        self.assertNotIn("chapter", lowered)
        self.assertNotIn("the", lowered)
        self.assertNotIn("role", lowered)
        self.assertNotIn("age", lowered)

    def test_orientation_fact_heads(self):
        terms = retrieval.extract_query_terms("Mateo: delayed laugh\nChoir: hum")
        lowered = {t.lower() for t in terms}
        self.assertIn("mateo", lowered)
        self.assertIn("choir", lowered)

    def test_schema_labels_not_terms(self):
        terms = retrieval.extract_query_terms("**Role:** hero\n**Age:** 17\nDanny: yes")
        lowered = {t.lower() for t in terms}
        self.assertNotIn("role", lowered)
        self.assertNotIn("age", lowered)
        self.assertIn("danny", lowered)


class TestSectionSelect(unittest.TestCase):
    def test_dump_returns_full_texts(self):
        pack = retrieval.build_retrieval_pack(
            chapter_num=2,
            chapter_outline=OUTLINE,
            characters_text=CHARACTERS,
            world_text=WORLD,
            mode="dump",
        )
        self.assertEqual(pack.mode, "dump")
        self.assertIn("DANIEL", pack.characters_block)
        self.assertIn("Blackthorn", pack.world_block)
        self.assertFalse(pack.character_fallback)

    def test_scoped_parent_includes_child_body(self):
        """BLOCKER regression: matched ## parent must carry ### children."""
        pack = retrieval.build_retrieval_pack(
            chapter_num=2,
            chapter_outline=OUTLINE,
            characters_text=CHARACTERS,
            world_text=WORLD,
            mode="scoped",
        )
        self.assertEqual(pack.mode, "scoped")
        self.assertIn("MATEO", pack.characters_block.upper())
        # Child body under matched parent — not just the heading line
        self.assertIn("Speech Patterns", pack.characters_block)
        self.assertIn("Repeats phrases", pack.characters_block)
        self.assertIn("Hypervigilant", pack.characters_block)
        # World ## parent should carry ### History when Blackthorn/Choir hit
        if "Blackthorn" in pack.world_block or "THE SCHOOL" in pack.world_block.upper():
            self.assertIn("Founded 1920s", pack.world_block)

    def test_scoped_versus_dump_contrast(self):
        scoped = retrieval.build_retrieval_pack(
            chapter_num=2,
            chapter_outline=OUTLINE,
            characters_text=CHARACTERS,
            world_text=WORLD,
            mode="scoped",
        )
        dump = retrieval.build_retrieval_pack(
            chapter_num=2,
            chapter_outline=OUTLINE,
            characters_text=CHARACTERS,
            world_text=WORLD,
            mode="dump",
        )
        # Fixture is small — scoped may fallback; dump must equal full source
        self.assertEqual(dump.characters_block.strip(), CHARACTERS.strip())
        if not scoped.character_fallback:
            self.assertLessEqual(len(scoped.characters_block), len(dump.characters_block))

    def test_scoped_fallback_flag_when_no_hits(self):
        lonely_chars = "# REGISTRY\n\n## **ZZZ UNKNOWN PERSON**\nRole: nobody.\n"
        lonely_world = "# WORLD\n\n## **QQQ NOWHERE**\nNothing.\n"
        pack = retrieval.build_retrieval_pack(
            chapter_num=3,
            chapter_outline="### Chapter 3\nNothing distinctive here at all.",
            characters_text=lonely_chars,
            world_text=lonely_world,
            mode="scoped",
        )
        self.assertTrue(pack.characters_block)
        self.assertTrue(pack.world_block)
        # Either fallback flag or some section was selected — both OK;
        # blocks must be non-empty when sources exist.
        self.assertTrue(pack.character_fallback or pack.character_hits)

    def test_scoped_sealed_terms_not_injected(self):
        """Pack must not invent sealed canon — sealed lives only at call site."""
        pack = retrieval.build_retrieval_pack(
            chapter_num=2,
            chapter_outline=OUTLINE,
            characters_text=CHARACTERS,
            world_text=WORLD,
            canon_view="",  # writer supplies only writer_view_md
            mode="scoped",
        )
        self.assertNotIn("sealed", pack.characters_block.lower())
        self.assertNotIn("visible_from", pack.world_block.lower())
        self.assertEqual(pack.canon_view, "")

    def test_fulltext_hit_marked_fallback(self):
        # One giant H1 section ≈ whole file → fallback flag for honest telemetry
        blob = "# ONLY\n" + ("Danny word " * 50)
        pack = retrieval.build_retrieval_pack(
            chapter_num=1,
            chapter_outline="Danny",
            characters_text=blob,
            world_text="",
            mode="scoped",
        )
        self.assertTrue(pack.character_fallback)

    def test_no_pipeline_import(self):
        import core.retrieval as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from pipeline", src)
        self.assertNotIn("import pipeline", src)

    def test_telemetry_shape(self):
        pack = retrieval.build_retrieval_pack(
            chapter_num=2,
            chapter_outline=OUTLINE,
            characters_text=CHARACTERS,
            world_text=WORLD,
            mode="scoped",
        )
        tel = pack.to_telemetry()
        self.assertEqual(tel["mode"], "scoped")
        self.assertIn("query_terms", tel)
        self.assertIn("character_fallback", tel)

    def test_legacy_sidecars_are_retired(self):
        # A pre-rename sidecar matched the `*_chNN.json` eval glob and shadowed
        # the real eval file; writing the new name must retire it. Both renamed
        # producers are covered (retrieval `_telemetry`, open pass `_open`).
        import tempfile
        from core import paths as paths_mod
        with tempfile.TemporaryDirectory() as tmp:
            for new_name, legacy_name in (
                ("retrieval_ch01_telemetry.json", "retrieval_ch01.json"),
                ("continuity_ch01_open.json", "continuity_ch01.json"),
            ):
                legacy = Path(tmp) / legacy_name
                legacy.write_text("{}", encoding="utf-8")
                paths_mod.retire_shadowing_sidecar(Path(tmp) / new_name)
                self.assertFalse(legacy.exists(), legacy_name)

    def test_retire_ignores_unrenamed_names(self):
        # A current name must never be deleted by its own cleanup.
        import tempfile
        from core import paths as paths_mod
        with tempfile.TemporaryDirectory() as tmp:
            current = Path(tmp) / "continuity_ch01_closed.json"
            current.write_text("{}", encoding="utf-8")
            paths_mod.retire_shadowing_sidecar(current)
            self.assertTrue(current.exists())

    def test_heading_name_candidates(self):
        names = retrieval._heading_name_candidates('**1. THE NARRATOR: DANIEL "DANNY" VEY**')
        joined = " ".join(names).upper()
        self.assertIn("DANIEL", joined)
        self.assertTrue("VEY" in joined or "DANIEL" in joined)

    def test_named_section_budgets(self):
        self.assertEqual(retrieval.MAX_CHARACTER_SECTIONS, 6)
        self.assertEqual(retrieval.MAX_WORLD_SECTIONS, 4)

    def test_markdown_sections_parent_owns_children(self):
        secs = retrieval.markdown_sections(CHARACTERS)
        by_h = {h: b for h, _l, b in secs}
        danny = next(b for h, b in by_h.items() if "DANIEL" in h.upper() or "DANNY" in h.upper())
        self.assertIn("Personality & Flaws", danny)
        self.assertIn("Hypervigilant", danny)


if __name__ == "__main__":
    unittest.main()
