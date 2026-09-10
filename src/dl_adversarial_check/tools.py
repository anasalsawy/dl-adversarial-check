"""Tool schemas for adversarial checking."""

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search for evidence to support or refute a claim. Use for pro-evidence, counter-evidence, and edge cases.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query. Examples: 'climate change human caused evidence', 'climate change natural causes', 'climate models limitations'",
                    },
                    "source": {
                        "type": "string",
                        "enum": ["wikipedia", "duckduckgo"],
                        "description": "Search engine. Wikipedia for factual baseline, DuckDuckGo for broader perspectives.",
                    },
                },
                "required": ["query", "source"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_web",
            "description": "Fetch a URL for detailed evidence (scientific papers, official reports, expert sources).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full URL to fetch (https://...)",
                    },
                },
                "required": ["url"],
            },
        },
    },
]
