from ddgs import DDGS


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return a formatted summary of results."""
    try:
        results = DDGS().text(query, max_results=max_results, region="us-en")
    except Exception as e:
        return f"Web search failed ({e}). Try again in a moment."

    if not results:
        return "No results found."

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   {r['href']}")
        lines.append(f"   {r['body']}")
        lines.append("")

    return "\n".join(lines).strip()
