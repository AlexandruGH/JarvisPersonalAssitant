from mcp.server.fastmcp import FastMCP
from duckduckgo_search import DDGS
import json

# Inițializăm serverul
mcp = FastMCP("DuckDuckGo")

@mcp.tool()
def web_search(query: str, max_results: int = 5) -> str:
    """
    Caută pe internet folosind DuckDuckGo.
    Args:
        query: Termenul de căutare
        max_results: Numărul maxim de rezultate (default 5)
    """
    try:
        # Folosim biblioteca DDGS pentru căutare
        results = list(DDGS().text(query, max_results=max_results))
        
        if not results:
            return "Nu am găsit rezultate."
            
        # Formatăm rezultatele frumos
        formatted = []
        for r in results:
            formatted.append(f"Titlu: {r.get('title')}\nLink: {r.get('href')}\nRezumat: {r.get('body')}\n---")
            
        return "\n".join(formatted)
        
    except Exception as e:
        return f"Eroare la căutare: {str(e)}"

if __name__ == "__main__":
    mcp.run()