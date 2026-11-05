"""Static word lists and identifier splitting for the scope guard."""
import itertools
import re
from collections.abc import Iterable
from typing import Self

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
    "my taxes", "income tax", "sleep better", "how to sleep",
})

# Always-project words, added to the per-project lexicon.
MIGRATION_VOCABULARY: frozenset[str] = frozenset({
    "tibco", "businessworks", "bw", "process", "workflow", "activity", "rule", "finding", "gate",
    "design", "build", "verify", "parity", "deploy", "terraform", ".net", "dotnet", "csharp",
    "service", "memory", "mapping", "domain", "queue", "topic", "table", "jms", "rest", "soap", "sql",
    "scenario", "test", "migration", "modernized", "lineage", "revision", "phase", "estate", "exiqoian",
    # target stacks, iterations and connectors (SP5 to SP7)
    "java", "go", "golang", "python", "spring", "fastapi", "stack", "iteration", "jira", "confluence",
    "github", "figma", "connector", "maven", "pytest", "aspnet",
})

# Vocabulary words that are also everyday words or generic coding topics: they keep a short question
# from being "too vague" but cannot override the off-topic filter ("write a python script ...").
AMBIGUOUS_VOCABULARY: frozenset[str] = frozenset({"java", "go", "golang", "python", "spring", "stack", "iteration", "connector", "maven",
                                                  "pytest"})

_TOKEN = re.compile(r"[a-z0-9_]+")
_DOT_NET = re.compile(r"(?<![a-z0-9_])\.net\b")  # ".net" would otherwise tokenise to "net"
_PARTS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def tokens(text: str) -> list[str]:
    """Matching tokens of already casefolded text (letters, digits, underscore)."""
    return _TOKEN.findall(_DOT_NET.sub("dotnet", text))


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


class Lexicon(frozenset):
    """The project's words for the scope guard. The set itself is the STRONG words, which may
    override the off-topic filter: full identifiers made of two or more parts (`GetWeather`,
    `DealerFundingRequest`), two consecutive parts of one ("get weather") and the migration
    vocabulary. ``weak`` holds single name parts and one-word names (`Start`, `Log`, `Sleep`,
    `weather` of `GetWeather`): they only keep a short question from being "too vague", because a
    BW palette name must not make "tell me a joke and log it" on-topic."""

    weak: frozenset[str]

    def __new__(cls, strong: Iterable[str] = (), weak: Iterable[str] = ()) -> Self:
        obj = super().__new__(cls, strong)
        obj.weak = frozenset(weak)
        return obj

    @classmethod
    def from_names(cls, names: Iterable[str | None], base: Iterable[str] = MIGRATION_VOCABULARY) -> Self:
        from engine.knowledge.embeddings import STOPWORDS

        strong: set[str] = set(base)
        weak: set[str] = set()

        def usable(w: str) -> bool:
            return len(w) >= 3 and w not in STOPWORDS

        for name in names:
            if not name:
                continue
            whole, *parts = split_identifier(name)
            if len(parts) >= 2:  # a multi-part identifier: the whole and each adjacent pair
                strong.add(whole)
                strong.update(f"{a} {b}" for a, b in itertools.pairwise(parts) if usable(a) and usable(b))
            elif usable(whole):
                weak.add(whole)
            weak.update(p for p in parts if usable(p))
        return cls(strong, weak - strong)
