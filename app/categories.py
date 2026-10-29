from __future__ import annotations

CATEGORY_TAXONOMY_VERSION = "prompt-categories-v1"

RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "legitimacy_trust",
        (
            "legit",
            "scam",
            "trust",
            "review",
            "actually work",
            "gimmick",
            "hype",
            "safe",
            "terms of service",
            "skeptic",
            "empty promise",
            "effective",
        ),
    ),
    (
        "pricing_value",
        (
            "cost",
            "price",
            "pricing",
            "worth",
            "overpriced",
            "retainer",
            "expensive",
            "value",
            "money",
            "fee",
            "budget",
        ),
    ),
    (
        "onboarding_support",
        (
            "setup",
            "onboarding",
            "cancel",
            "sign up",
            "self serve",
            "talk to sales",
            "available",
            "outside the us",
            "support",
            "how long",
            "how fast",
        ),
    ),
    (
        "comparison_alternatives",
        (
            "better than",
            "compare",
            " vs ",
            "alternative",
            "prefer between",
            "trustworthy for ai brand tracking",
            "than profound",
            "than otterly",
            "than dageno",
            "than peec",
        ),
    ),
    (
        "implementation_howto",
        (
            "how ",
            "fix",
            "influence",
            "get recommended",
            "show up",
            "improve",
            "track if",
            "tracking",
            "measure",
            "features",
            "integration",
            "prompt hack",
            "without spamming",
        ),
    ),
    (
        "tool_selection",
        (
            "what software",
            "what tools",
            "what kind of tool",
            "what features",
            "which tool",
            "best software",
            "platforms",
            "internal tracker",
        ),
    ),
    (
        "use_case_fit",
        (
            "b2b",
            "saas",
            "enterprise software",
            "ecommerce",
            "d2c",
            "shopify",
            "small software",
            "startup",
        ),
    ),
    (
        "brand_about",
        (
            "what is",
            "who is",
            "four module",
            "audit score",
            "what exactly",
            "does kasparro track",
            "does kasparro support",
            "is kasparro missing",
        ),
    ),
]


def infer_category(text: str) -> str:
    lowered = f" {text.lower()} "
    for category, keywords in RULES:
        if any(keyword in lowered for keyword in keywords):
            return category
    return "general"


def categories_for_prompts(prompts: list[dict]) -> dict[str, str]:
    return {prompt["prompt_id"]: infer_category(prompt["text"]) for prompt in prompts}
