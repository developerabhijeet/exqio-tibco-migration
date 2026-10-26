"""Closed list of injection rules (layer 2), matched on normalised, casefolded text."""
import re

# Leet mapping used by the obfuscated_override pass: 1->i 0->o 3->e 5->s 7->t 4->a @->a $->s.
LEET = str.maketrans("103574@$", "ioestaas")

_DET = r"(?:all|any|the|your|my|previous|prior|above|earlier|preceding|former|these|those|every|of)"
_NOUN = r"(?:instructions|rules|prompts?|guidelines?|context)"

OVERRIDE = re.compile(
    rf"\b(?:ignore|disregard|forget|override)\s+(?:{_DET}\s+){{1,4}}{_NOUN}\b"
    r"|\b(?:ignore|disregard|forget)\s+(?:everything|all)\s+(?:above|before|prior|previous|you were told)\b")

# The override phrase with every gap removed ("ignorepreviousinstructions").
OVERRIDE_COMPACT = re.compile(
    r"(?:ignore|disregard|forget|override)(?:all|any|the|your|my|previous|prior|above|earlier|"
    r"preceding|former|these|those|every|of){1,4}(?:instructions|rules|prompts?|guidelines?|context)")

ROLE_CHANGE = re.compile(
    r"\byou\s+are\s+now\b"
    r"|(?:^|[.!?;:\n]\s*|\b(?:please|now|you|just|and|then|to)\s+)act\s+as\b"
    r"|\bpretend\s+(?:to\s+be|you\s+are|that\s+you)\b"
    r"|\brole\s*-?\s*play\s+as\b"
    r"|\bfrom\s+now\s+on,?\s+you\b"
    r"|\bnew\s+persona\b|\bdeveloper\s+mode\b|\bdan\s+(?:mode|prompt|jailbreak)\b|\b(?:act|behave|respond|reply)\s+(?:like|as)\s+dan\b|\byou(?:'re|\s+are)\s+dan\b|^\s*dan\s*[.!?]*\s*$|\bdo\s+anything\s+now\b|\bjail\s*break")

PROMPT_EXFILTRATION = re.compile(
    r"\b(?:show|print|reveal|repeat|output|display|tell|give|leak|dump)\s+(?:me\s+)?(?:your|the)\s+"
    r"(?:system|hidden|initial|original|secret)\s+(?:prompt|instructions?|rules)\b"
    # "your" addressed to the assistant, with or without an adjective
    r"|\b(?:show|print|reveal|repeat|output|display|tell|give|leak|dump)\s+(?:me\s+)?your\s+(?:\w+\s+)?(?:prompt|instructions|rules)\b"
    r"|\bwhat(?:'s|s|\s+is|\s+are|\s+were)\s+your\s+(?:\w+\s+)?(?:prompt|instructions|rules|guidelines)\b"
    r"|\bwhat(?:'s|s|\s+is|\s+are|\s+were)\s+the\s+(?:system|hidden|initial|original|secret)\s+(?:prompt|instructions?|rules)\b"
    r"|\bwhat\s+(?:\w+\s+)?(?:instructions|rules|prompt)\s+(?:were|are|did)\s+you\s+(?:given|get|receive|follow)\b"
    r"|\bwhat\s+(?:were|are)\s+you\s+told\b")

DELIMITER_FORGERY = re.compile(
    r"<\s*/?\s*(?:source|history|system|question)\b"
    r"|<\|[a-z_]+\|>|\[/?inst\]|###\s*system\b|\brole\s*:\s*system\b|\bbegin\s+system\b"
    r"|(?m:^[ \t]*(?:system|assistant)[ \t]*:)")

TOOL_COERCION = re.compile(
    r"\b(?:call|run|execute|invoke)\s+(?:a|an|the|this|that|any)\s+(?:tool|function|command(?!\s*[- ]?line)|shell)\b"
    r"|\b(?:curl|wget)\b|https?://\S+\s+(?:and|then)\b")

ENCODED_PAYLOAD = re.compile(r"[a-z0-9+/=]{120,}|[0-9a-f]{160,}")

MULTILINGUAL_OVERRIDE = re.compile(
    # Spanish, Portuguese
    r"\b(?:ignora|ignore|olvida|olvide)\s+(?:todas\s+)?(?:las|as|os|los)\s+(?:instrucciones|reglas|instruções|instrucoes|regras)\b"
    # French
    r"|\b(?:ignorez?|oubliez?)\s+(?:toutes\s+)?(?:les|vos)\s+(?:instructions|règles|regles|consignes)\b"
    # German
    r"|\b(?:ignorier(?:e|en)?|vergiss|vergessen)\s+(?:alle\s+)?(?:vorherigen\s+|bisherigen\s+|deine\s+)?(?:anweisungen|regeln|instruktionen)\b"
    # Hindi, romanised
    r"|\b(?:pichhle|pichle|purane|pehle)\s+(?:sabhi\s+)?nirdesh\w*\s+(?:ko\s+)?(?:andekha|ignore|bhool)"
    # Hindi, Devanagari
    r"|(?:निर्देश|आदेश)\S*\s+(?:को\s+)?(?:अनदेखा|अनदेखी|नजरअंदाज|नज़रअंदाज़|भूल)"
    # Simplified Chinese
    r"|(?:忽略|无视|忘记|忘掉)(?:之前|以前|先前|上面|以上|所有)?(?:的)?(?:所有)?(?:指令|指示|规则|提示|说明)")

# Order is the order the rules are tried; obfuscated_override applies OVERRIDE to the
# leet-mapped, separator-collapsed text (see guard.detect_injection).
INJECTION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("override", OVERRIDE),
    ("role_change", ROLE_CHANGE),
    ("prompt_exfiltration", PROMPT_EXFILTRATION),
    ("delimiter_forgery", DELIMITER_FORGERY),
    ("tool_coercion", TOOL_COERCION),
    ("encoded_payload", ENCODED_PAYLOAD),
    ("multilingual_override", MULTILINGUAL_OVERRIDE),
    ("obfuscated_override", OVERRIDE),
)
