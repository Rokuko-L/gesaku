#!/usr/bin/env python3
"""Outline orientation-fact coverage check.

Deterministic, synonym-aware: a fact passes when any of its content words
(or a synonym) appears in the chapter prose. Feed the failures into the
orientation penalty in `evaluate_chapter`.
"""

import re

def check_orientation_facts(chapter_text, chapter_outline):
    """
    Check if the orientation facts from the outline appear in the chapter text.
    Returns a list of failed facts.

    Matching is synonym-aware: a fact passes if any of its key content words
    OR a synonym appears in the chapter, so paraphrases like "the old powers
    stirred" satisfy "Ancient beings sense the weakening barrier".
    """
    match = re.search(r'Orientation\s+Facts\s*(?:\*\*)?:\s*(.*?)(?=\n\s*(?:\d+\.\s*)?\*\*|\Z)', chapter_outline, re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    
    facts = []
    for line in match.group(1).splitlines():
        line = line.strip().lstrip('-*').strip()
        if line:
            facts.append(line)
            
    if not facts:
        return []
        
    text_lower = chapter_text.lower()
    failed_facts = []
    
    # Common stop words to ignore — including sentence-initial determiners and
    # pronouns, which are capitalized in the fact list ("The", "A", "She", "He")
    # and would otherwise match ANY chapter text, passing every fact.
    stop_words = {
        "with", "from", "over", "under", "about", "after", "through", "between",
        "before", "into", "onto", "your", "their", "them", "then", "there", "they",
        "that", "this", "these", "those", "have", "been", "were", "what", "when",
        "where", "which", "who", "whom", "whose", "why", "how", "will", "would",
        "shall", "should", "could", "might", "must", "some", "any", "each", "every",
        "both", "either", "neither", "somebody", "someone", "something", "anybody",
        "anyone", "anything", "nobody", "nothing", "everything", "everyone", "everybody",
        "the", "a", "an", "and", "of", "to", "in", "for", "on", "at", "by", "as",
        "or", "but", "not", "no", "so", "if", "then", "than", "she", "he", "it",
        "we", "you", "i", "my", "your", "his", "her", "our", "its", "their", "me",
        "him", "us", "them", "with", "was", "is", "are", "be", "been", "being", "had",
    }
    
    # Synonym families for common fantasy concepts — a fact passes if any key
    # word OR any synonym appears in the text, so paraphrase-heavy drafts are
    # not falsely penalized for not using the outline's exact vocabulary.
    synonym_map = {
        "ancient": {"ancient", "old", "elder", "deep", "primordial", "vast", "primeval"},
        "beings": {"beings", "powers", "ones", "things", "creatures", "gods", "spirits",
                   "entities", "olders", "watchers", "forces"},
        "barrier": {"barrier", "veil", "boundary", "wall", "seal", "curtain", "divide",
                    "fabric", "membrane"},
        "weaken": {"weaken", "weakening", "weakened", "crack", "cracks", "cracked",
                   "cracking", "fracture", "fracturing", "thin", "thinning", "strained",
                   "strain", "failing", "tear", "tearing", "shudder", "shuddering"},
        "sense": {"sense", "senses", "sensed", "sensing", "feel", "feels", "felt",
                  "feeling", "perceive", "perceived", "notice", "notices", "noticed",
                  "stir", "stirs", "stirred", "stirring", "awaken", "awoke",
                  "turned", "turn", "attention"},
        "hunt": {"hunt", "hunts", "hunted", "hunting", "hunter", "hunters", "track",
                 "tracks", "tracked", "tracking", "pursue", "pursuit", "chase"},
        "charm": {"charm", "charms", "amulet", "talisman", "pendant", "sigil", "token",
                  "artifact", "trinket", "ward"},
        "power": {"power", "powers", "powerful", "might", "magic", "magical", "magics",
                  "strength", "force", "forces", "abilities", "ability"},
        "voice": {"voice", "voices", "voice's", "words", "speech", "manner", "presence"},
        "demeanor": {"demeanor", "demeanour", "manner", "bearing", "presence", "aura",
                     "appearance", "look"},
        "terrify": {"terrify", "terrifies", "terrified", "terrifying", "terrifyingly",
                    "frighten", "frightens", "frightened", "frightening", "fear",
                    "fears", "feared", "fearful", "afraid", "horror", "horrified",
                    "horrifying", "dread", "dreaded", "panic", "panicked", "terror",
                    "terrors", "cowering", "quail", "shudder"},
        "mortals": {"mortals", "mortal", "humans", "human", "people", "villagers",
                    "folk", "peasants", "townsfolk"},
        "emotional": {"emotional", "emotion", "emotions", "feelings", "feeling",
                      "heart", "anger", "rage", "grief", "despair", "joy", "fury",
                      "outburst", "outbursts", "temper"},
        "outburst": {"outburst", "outbursts", "eruption", "surge", "surges", "surged",
                     "explosion", "explodes", "exploding", "flare", "flared"},
        "world": {"world", "worlds", "realm", "realms", "land", "plane", "earth"},
        "rule": {"rule", "rules", "ruled", "ruling", "reign", "reigns", "reigned",
                 "reigning", "govern", "governs", "governing", "command", "commands",
                 "commanding", "control", "controls", "controlling", "led", "leads",
                 "leading"},
        "nightmare": {"nightmare", "nightmares", "nightmarish", "dream", "dreams",
                      "dreaming", "dreamscape"},
        "blessing": {"blessing", "blessings", "blessed", "boon", "gift", "gifts",
                     "favor", "favour", "grant"},
        "curse": {"curse", "curses", "cursed", "cursing", "affliction", "blight",
                  "hex", "hexed"},
        "disease": {"disease", "diseases", "sick", "sickness", "illness", "plague",
                    "infection", "ailment", "affliction", "fever"},
        "divine": {"divine", "divinely", "holy", "sacred", "god", "gods", "goddess",
                   "godly", "celestial", "seraph", "seraphim", "angels", "heavenly"},
        "goddess": {"goddess", "goddesses", "queen", "deity", "deities", "divinity",
                    "nightmare", "spirit"},
        "fears": {"fears", "fear", "nightmares", "terrors", "dreads", "horrors",
                  "anxieties", "dreams"},
        "harvest": {"harvest", "harvests", "harvested", "harvesting", "collect",
                    "collects", "collected", "collecting", "gather", "gathers",
                    "gathered", "gathering", "reap", "reaps", "reaped"},
        "queen": {"queen", "queen's", "ruler", "monarch", "sovereign", "empress",
                  "ladyship"},
        "compact": {"compact", "shadow", "shadows", "cult", "sect", "order", "faction",
                    "brotherhood", "coven"},
        "friend": {"friend", "friends", "friendship", "ally", "allies", "companion",
                   "companions", "confidant", "guide"},
    }
    
    for fact in facts:
        # Try to find capitalized words (proper nouns)
        proper_nouns = re.findall(r'\b[A-Z][a-z]+\b', fact)
        proper_nouns = [w for w in proper_nouns if w.lower() not in stop_words]
        
        found = False
        # Expand every key content word (proper nouns AND common words) with
        # its synonym family, so paraphrases of the beat still match.
        search_terms = set()
        if proper_nouns:
            for pn in proper_nouns:
                search_terms.add(pn.lower())
                search_terms.update(synonym_map.get(pn.lower(), set()))
        words = re.findall(r'\b[a-zA-Z]{4,}\b', fact)
        for w in words:
            wl = w.lower()
            if wl in stop_words:
                continue
            search_terms.add(wl)
            search_terms.update(synonym_map.get(wl, set()))
        
        if search_terms:
            for term in search_terms:
                if term in text_lower:
                    found = True
                    break
        
        if not found:
            failed_facts.append(fact)
            
    return failed_facts
