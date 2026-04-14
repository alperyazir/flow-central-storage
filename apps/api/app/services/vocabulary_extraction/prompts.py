"""LLM prompt templates for vocabulary extraction."""

# System prompt for vocabulary extraction
SYSTEM_PROMPT = """You are a vocabulary extraction API that returns JSON only.
Extract vocabulary words from educational text with definitions and Turkish translations.
CRITICAL: Return ONLY a raw JSON array. No markdown. No code blocks. No explanation. No text before or after the JSON."""

# Main vocabulary extraction prompt
VOCABULARY_EXTRACTION_PROMPT = """Extract {max_words} vocabulary words from this {difficulty}-level educational module.
The content is in {language}.

MODULE TITLE: {module_title}

TEXT:
{module_text}

IMPORTANT: Focus on words that are KEY to learning the module's topic "{module_title}".
- Extract words that students need to learn for this specific topic
- Prioritize nouns, verbs, and adjectives directly related to the theme
- Skip words that are not essential to understanding the topic
- Keep words in their original language ({language}) — do NOT translate them to another language
- Use infinitive for verbs, singular for nouns

Return a JSON array with these fields for each word:
- word: base form in {language}
- translation: Turkish translation (Türkçe çeviri)
- definition: clear explanation in {language}
- part_of_speech: noun/verb/adjective/adverb
- level: A1/A2/B1/B2/C1/C2
- example: example sentence in {language}

OUTPUT FORMAT - Return ONLY this JSON array, nothing else:
[{{"word":"Schule","translation":"okul","definition":"Ein Ort, an dem Kinder lernen","part_of_speech":"noun","level":"A1","example":"Die Kinder gehen in die Schule."}}]"""

# Fallback prompt for simpler extraction
SIMPLE_VOCABULARY_PROMPT = """Extract {max_words} vocabulary words about "{module_title}" from this {language} text. Return ONLY a JSON array.
Focus on words essential to the topic. Skip unrelated common words.
Keep words in their original language. Provide Turkish translations.

TEXT:
{module_text}

OUTPUT (JSON array only, no other text):
[{{"word":"house","translation":"ev","definition":"a building where people live","part_of_speech":"noun","level":"A1","example":"I live in a house."}}]"""

# Bilingual extraction prompt (for mixed language content)
BILINGUAL_VOCABULARY_PROMPT = """Extract vocabulary from this bilingual educational text.

For each vocabulary word, provide:
- The word in the language being taught
- Its Turkish translation
- Definition in the word's language
- Part of speech
- CEFR level
- Example sentence

Text:
---
{module_text}
---

Extract {max_words} vocabulary words. Focus on the words being taught and their Turkish translations.

Respond with ONLY a valid JSON array:
[
  {{
    "word": "greeting",
    "translation": "selamlama",
    "definition": "a polite word or sign of welcome",
    "part_of_speech": "noun",
    "level": "A1",
    "example": "A friendly greeting makes people feel welcome."
  }}
]"""


def build_vocabulary_extraction_prompt(
    module_text: str,
    module_title: str = "",
    difficulty: str = "B1",
    max_words: int = 50,
    max_length: int = 8000,
    language: str = "en",
) -> str:
    """
    Build the vocabulary extraction prompt with the given module text.

    Args:
        module_text: The text content to analyze.
        module_title: Title of the module for topic context.
        difficulty: Target CEFR difficulty level.
        max_words: Maximum number of words to extract.
        max_length: Maximum text length to include.
        language: Source language of the content.

    Returns:
        Formatted prompt string.
    """
    # Truncate text if too long
    if len(module_text) > max_length:
        module_text = module_text[:max_length] + "\n\n[Text truncated...]"

    return VOCABULARY_EXTRACTION_PROMPT.format(
        module_text=module_text,
        module_title=module_title or "Unknown",
        difficulty=difficulty,
        max_words=max_words,
        language=language,
    )


def build_simple_vocabulary_prompt(
    module_text: str,
    module_title: str = "",
    max_words: int = 30,
    max_length: int = 4000,
    language: str = "en",
) -> str:
    """
    Build a simpler vocabulary extraction prompt for fallback.

    Args:
        module_text: The text content to analyze.
        module_title: Title of the module for topic context.
        max_words: Maximum number of words to extract.
        max_length: Maximum text length to include.
        language: Source language of the content.

    Returns:
        Formatted prompt string.
    """
    if len(module_text) > max_length:
        module_text = module_text[:max_length] + "\n\n[Text truncated...]"

    return SIMPLE_VOCABULARY_PROMPT.format(
        module_text=module_text,
        module_title=module_title or "this topic",
        max_words=max_words,
        language=language,
    )


def build_bilingual_vocabulary_prompt(
    module_text: str,
    max_words: int = 50,
    max_length: int = 8000,
) -> str:
    """
    Build the bilingual vocabulary extraction prompt.

    Args:
        module_text: The text content to analyze.
        max_words: Maximum number of words to extract.
        max_length: Maximum text length to include.

    Returns:
        Formatted prompt string.
    """
    if len(module_text) > max_length:
        module_text = module_text[:max_length] + "\n\n[Text truncated...]"

    return BILINGUAL_VOCABULARY_PROMPT.format(
        module_text=module_text,
        max_words=max_words,
    )
