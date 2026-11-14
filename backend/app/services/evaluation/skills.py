"""Deterministic skill-name normalisation and matching.

``skill_key`` maps spelling variants to one key ("Postgres", "postgresql", "psql"
-> "postgresql"), so requirement matching is reproducible and does not depend on
the LLM. Unknown names are normalised (lower case, whitespace collapsed) and kept.
"""

from __future__ import annotations

import re

_ALIASES: dict[str, str] = {
    # languages
    "py": "python",
    "python3": "python",
    "python 3": "python",
    "python2": "python",
    "pypy": "python",
    "js": "javascript",
    "ecmascript": "javascript",
    "es6": "javascript",
    "ts": "typescript",
    "golang": "go",
    "cpp": "c++",
    "c plus plus": "c++",
    "cplusplus": "c++",
    "c sharp": "c#",
    "csharp": "c#",
    "dotnet": ".net",
    ".net core": ".net",
    "asp.net core": "asp.net",
    "objective c": "objective-c",
    "shell": "bash",
    "shell scripting": "bash",
    # frameworks / libraries
    "node": "node.js",
    "nodejs": "node.js",
    "node js": "node.js",
    "react.js": "react",
    "reactjs": "react",
    "react js": "react",
    "vue.js": "vue",
    "vuejs": "vue",
    "angularjs": "angular",
    "next": "next.js",
    "nextjs": "next.js",
    "express.js": "express",
    "expressjs": "express",
    "springboot": "spring boot",
    "sklearn": "scikit-learn",
    "scikit learn": "scikit-learn",
    "tf": "tensorflow",
    "torch": "pytorch",
    "fast api": "fastapi",
    # databases
    "postgres": "postgresql",
    "psql": "postgresql",
    "postgre": "postgresql",
    "mongo": "mongodb",
    "ms sql": "sql server",
    "mssql": "sql server",
    "microsoft sql server": "sql server",
    "elastic search": "elasticsearch",
    # cloud / devops
    "amazon web services": "aws",
    "google cloud platform": "gcp",
    "google cloud": "gcp",
    "microsoft azure": "azure",
    "k8s": "kubernetes",
    "ci/cd": "ci/cd",
    "cicd": "ci/cd",
    "ci cd": "ci/cd",
    "github actions": "github actions",
    # concepts
    "rest api": "rest",
    "rest apis": "rest",
    "restful": "rest",
    "restful api": "rest",
    "restful apis": "rest",
    "ml": "machine learning",
    "dl": "deep learning",
    "nlp": "natural language processing",
    "dsa": "data structures and algorithms",
    "data structures": "data structures and algorithms",
    "algorithms": "data structures and algorithms",
    "dp": "dynamic programming",
    "oop": "object-oriented programming",
    "object oriented programming": "object-oriented programming",
}

# Codeforces problem tags that indicate general algorithmic skill.
ALGORITHMIC_TAGS = frozenset(
    {
        "dp",
        "greedy",
        "graphs",
        "trees",
        "math",
        "implementation",
        "data structures",
        "binary search",
        "sortings",
        "dfs and similar",
        "number theory",
        "strings",
        "constructive algorithms",
        "brute force",
        "two pointers",
        "bitmasks",
        "combinatorics",
        "geometry",
        "shortest paths",
        "dsu",
        "divide and conquer",
        "hashing",
        "graph matchings",
        "flows",
        "probabilities",
        "games",
        "interactive",
        "matrices",
    }
)
_TAG_KEYS = {
    "dp": "dynamic programming",
    "graphs": "graph algorithms",
    "shortest paths": "graph algorithms",
    "dfs and similar": "graph algorithms",
    "data structures": "data structures and algorithms",
}


def skill_key(name: str) -> str:
    """Normalise a skill name to its comparison key."""
    value = re.sub(r"\s+", " ", name.strip().lower())
    value = value.strip(" .,;:")
    return _ALIASES.get(value, value)


def language_key(raw: str) -> str | None:
    """Map a platform language label ("GNU C++17 (64)", "PyPy 3-64", "Jupyter Notebook")."""
    value = raw.lower()
    if "c++" in value or "g++" in value or "clang++" in value:
        return "c++"
    if "python" in value or "pypy" in value:
        return "python"
    if "javascript" in value or "node.js" in value or value.startswith("js"):
        return "javascript"
    if "typescript" in value:
        return "typescript"
    if "kotlin" in value:
        return "kotlin"
    if re.search(r"\bjava\b", value):
        return "java"
    if "c#" in value or "mono" in value or ".net" in value:
        return "c#"
    if "rust" in value:
        return "rust"
    if value.startswith("go") or " go " in f" {value} ":
        return "go"
    if "jupyter" in value:
        return "python"
    if re.fullmatch(r"(gnu )?c\d*(\s.*)?", value) or value.startswith("gnu c11"):
        return "c"
    cleaned = skill_key(raw.split("(")[0])
    return cleaned or None


def tag_key(tag: str) -> str:
    return _TAG_KEYS.get(tag.lower(), skill_key(tag))


def mentions(text: str, key: str) -> bool:
    """Whole-word, case-insensitive mention of a skill key (or its aliases) in free text."""
    lowered = f" {text.lower()} "
    variants = {key, *(alias for alias, target in _ALIASES.items() if target == key)}
    for variant in variants:
        if len(variant) < 2:
            continue
        pattern = r"(?<![a-z0-9+#.])" + re.escape(variant) + r"(?![a-z0-9+#])"
        if re.search(pattern, lowered):
            return True
    return False
