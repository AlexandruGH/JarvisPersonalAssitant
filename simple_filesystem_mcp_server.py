import os
from mcp.server.fastmcp import FastMCP

# Inițializăm serverul
mcp = FastMCP("SimpleFilesystem")

# Definim folderul de lucru (siguranță)
WORKSPACE_DIR = os.path.abspath("./workspace")
if not os.path.exists(WORKSPACE_DIR):
    os.makedirs(WORKSPACE_DIR)

def _get_safe_path(filename: str) -> str:
    """Asigură că fișierul este în interiorul workspace-ului"""
    full_path = os.path.abspath(os.path.join(WORKSPACE_DIR, filename))
    if not full_path.startswith(WORKSPACE_DIR):
        raise ValueError("Acces interzis în afara folderului workspace!")
    return full_path

@mcp.tool()
def list_files() -> str:
    """Listează fișierele din directorul workspace."""
    try:
        files = os.listdir(WORKSPACE_DIR)
        return f"Fișiere în workspace: {', '.join(files)}" if files else "Workspace gol."
    except Exception as e:
        return f"Eroare la listare: {str(e)}"

@mcp.tool()
def read_file(filename: str) -> str:
    """Citește conținutul unui fișier din workspace."""
    try:
        path = _get_safe_path(filename)
        if not os.path.exists(path):
            return "Eroare: Fișierul nu există."
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Eroare la citire: {str(e)}"

@mcp.tool()
def write_file(filename: str, content: str) -> str:
    """Creează sau suprascrie un fișier în workspace."""
    try:
        path = _get_safe_path(filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Succes: Am scris în {filename}"
    except Exception as e:
        return f"Eroare la scriere: {str(e)}"

if __name__ == "__main__":
    # Rulează serverul pe stdio
    mcp.run()