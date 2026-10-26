"""Static word lists and identifier splitting for the scope guard."""
import re

# Words (single tokens) and phrases (token sequences) that mark a message as not about the project.
# A word of the project lexicon overrides these (a process called CookDealerRates is on topic).
OFF_DOMAIN_WORDS: frozenset[str] = frozenset({
    # cooking
    "recipe", "recipes", "cook", "cooking", "bake", "baking", "dosa", "biryani", "pizza", "pasta",
    "sandwich", "dessert", "cuisine", "chef",
    # weather, sports
    "weather", "horoscope", "zodiac", "football", "cricket", "soccer", "basketball", "tennis",
    "baseball", "nba", "nfl", "ipl", "olympics", "olympic",
    # films, music, entertainment, jokes, poems
    "movie", "movies", "film", "films", "song", "songs", "sing", "lyrics", "music", "actor", "actress",
    "celebrity", "joke", "jokes", "poem", "poems", "poetry", "riddle", "riddles",
    # travel, dating, health
    "travel", "vacation", "tourist", "dating", "girlfriend", "boyfriend", "horoscopes",
    "symptoms", "diet",
    # politics, markets
    "politics", "election", "elections", "bitcoin", "crypto", "ethereum", "dogecoin",
    # homework and general coding asks
    "homework", "essay", "translate", "leetcode",
    # phrases
    "capital of", "who won", "who is the president", "president of", "prime minister",
    "write a poem", "write a story", "write an essay", "write a song", "write a python script",
    "regex for", "stock tips", "stock price", "stock prices", "health advice", "medical advice",
    "meaning of life", "how are you", "what's up", "tell me about yourself", "are you human",
    "your favorite", "your favourite", "i am bored", "i'm bored", "good night",
})

# Always-project words, added to the per-project lexicon.
MIGRATION_VOCABULARY: frozenset[str] = frozenset({
    "tibco", "businessworks", "bw", "process", "workflow", "activity", "rule", "finding", "gate",
    "design", "build", "verify", "parity", "deploy", "terraform", ".net", "dotnet", "csharp",
    "service", "memory", "mapping", "domain", "queue", "topic", "table", "jms", "rest", "soap", "sql",
    "scenario", "test", "migration", "modernized", "lineage", "revision", "phase", "estate", "exiqoian",
})

_TOKEN = re.compile(r"[a-z0-9_]+")
_PARTS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def tokens(text: str) -> list[str]:
    """Matching tokens of already casefolded text (letters, digits, underscore)."""
    return _TOKEN.findall(text)


def split_identifier(name: str) -> list[str]:
    """The casefolded whole identifier followed by its parts split on case and ``_``/other
    separators, de-duplicated in order."""
    whole = name.lower()
    out = {whole: None}
    for part in _PARTS.findall(re.sub(r"[^A-Za-z0-9]+", " ", name)):
        out.setdefault(part.lower(), None)
    return list(out)


# Off-domain entries split into single tokens and token phrases, tokenised like the message.
OFF_DOMAIN_SINGLE: frozenset[str] = frozenset(w for w in OFF_DOMAIN_WORDS if len(tokens(w)) == 1)
OFF_DOMAIN_PHRASES: tuple[str, ...] = tuple(" ".join(tokens(w)) for w in OFF_DOMAIN_WORDS if len(tokens(w)) > 1)
