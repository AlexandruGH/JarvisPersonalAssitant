import os
import sys
import json
import asyncio
from contextlib import AsyncExitStack
from typing import Dict, Any, List
from datetime import datetime
# MCP Client imports
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# AI Client
from groq import Groq
from dotenv import load_dotenv

# Încărcare variabile de mediu
load_dotenv()

class JarvisMVP:
    def __init__(self):
        # Verificare cheie API
        if not os.getenv("GROQ_API_KEY"):
            print("❌ EROARE: Lipsește GROQ_API_KEY în .env")
            sys.exit(1)

        self.groq = Groq(api_key=os.getenv("GROQ_API_KEY"))
        self.tool_registry = {}  
        self.sessions = []       
        self.available_tools = [] 
        
    def load_config(self) -> Dict[str, Any]:
        """Încarcă configurația serverelor MCP din fișierul JSON."""
        try:
            with open("config_mcp.json", "r") as f:
                return json.load(f)
        except FileNotFoundError:
            print("❌ EROARE: Nu găsesc fișierul config_mcp.json")
            sys.exit(1)
        except json.JSONDecodeError:
            print("❌ EROARE: Fișierul config_mcp.json nu este un JSON valid.")
            sys.exit(1)
    
    async def start(self):
        """Inițializează conexiunile MCP și pornește bucla de chat."""
        config = self.load_config()
        
        async with AsyncExitStack() as stack:
            print("\n🔌 Conectare la servere MCP...")
            
            for server_name, server_conf in config.get("mcpServers", {}).items():
                try:
                    command = server_conf["command"]
                    if command == "python": 
                        command = sys.executable

                    server_params = StdioServerParameters(
                        command=command,
                        args=server_conf["args"],
                        env=os.environ.copy()
                    )
                    
                    read_stream, write_stream = await stack.enter_async_context(
                        stdio_client(server_params)
                    )
                    
                    session = await stack.enter_async_context(
                        ClientSession(read_stream, write_stream)
                    )
                    
                    await session.initialize()
                    self.sessions.append(session)
                    
                    tools_result = await session.list_tools()
                    
                    tool_names = [t.name for t in tools_result.tools]
                    print(f"   ✅ {server_name}: {tool_names}")
                    
                    for tool in tools_result.tools:
                        self.tool_registry[tool.name] = {
                            "session": session,
                            "description": tool.description
                        }
                        self.available_tools.append({
                            "type": "function",
                            "function": {
                                "name": tool.name,
                                "description": tool.description,
                                "parameters": tool.inputSchema
                            }
                        })
                        
                except Exception as e:
                    print(f"   ❌ Eroare la conectarea serverului {server_name}: {e}")
                    continue
            
            # --- MODIFICARE: Adăugăm Tool-ul Nativ de Clarificare ---
            self.available_tools.append({
                "type": "function",
                "function": {
                    "name": "ask_user",
                    "description": "Folosește acest tool când ai nevoie de clarificări, detalii suplimentare sau confirmări de la utilizator. Oprește execuția pentru a primi input.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string", 
                                "description": "Întrebarea specifică pentru utilizator"
                            }
                        },
                        "required": ["question"]
                    }
                }
            })
            # --------------------------------------------------------

            print(f"\n🤖 JARVIS MVP Online")
            print(f"   Tool-uri active: {len(self.available_tools)}")
            print("   (Scrie 'exit' pentru a ieși)\n")
            
            await self.chat_loop()
    
    async def chat_loop(self):
        """Bucla principală de interacțiune cu logică REACT îmbunătățită."""
        
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")

        # --- MODIFICARE: Prompt Actualizat pentru a încuraja întrebările ---
        system_prompt = f"""Ești JARVIS, un asistent AI avansat.
                DATA CURENTĂ: {current_time}

                PROTOCOL DE OPERARE:
                1. CLARIFICARE (CRITIC): Dacă cererea utilizatorului este vagă (ex: "fă un fișier", "caută asta"), NU GHICI. Folosește tool-ul `ask_user` pentru a întreba detalii (nume fișier, context, etc.).
                2. GÂNDIRE PAS-CU-PAS: Descompune problema.
                3. EXECUȚIE: Folosește tool-urile disponibile (search, filesystem).
                4. VALIDARE: Verifică rezultatele înainte de a răspunde final.

                REGULI:
                - Nu inventa informații.
                - Dacă folosești `ask_user`, așteaptă răspunsul, nu continua să ghicești.
                - Răspunde în limba română.
                """

        messages = [
            {"role": "system", "content": system_prompt}
        ]        
        
        while True:
            try:
                user_input = input("\n👤 Tu: ").strip()
                if user_input.lower() in ["exit", "quit"]: 
                    break
                if not user_input: 
                    continue
                
                messages.append({"role": "user", "content": user_input})
                
                max_turns = 20
                turn_count = 0
                final_response_shown = False
                
                while turn_count < max_turns and not final_response_shown:
                    turn_count += 1
                    
                    try:
                        response = self.groq.chat.completions.create(
                            model="llama-3.3-70b-versatile", 
                            messages=messages,
                            tools=self.available_tools if self.available_tools else None,
                            tool_choice="auto",
                            temperature=0.6 # Temperatură ușor mai mică pentru precizie
                        )
                    except Exception as e:
                        print(f"❌ Eroare API: {e}")
                        break

                    message = response.choices[0].message
                    
                    clean_msg = {"role": message.role}
                    if message.content:
                        clean_msg["content"] = message.content
                    else:
                        clean_msg["content"] = None
                        
                    if message.tool_calls:
                        clean_msg["tool_calls"] = [
                            {
                                "id": tc.id, 
                                "type": "function", 
                                "function": {
                                    "name": tc.function.name, 
                                    "arguments": tc.function.arguments
                                }
                            } for tc in message.tool_calls
                        ]
                    
                    messages.append(clean_msg)

                    if message.tool_calls:
                        print(f"\n⚡ [Pasul {turn_count}] Execut PARALEL {len(message.tool_calls)} acțiuni...")
                        
                        tasks = []
                        tool_calls_ordered = []

                        for tool_call in message.tool_calls:
                            tool_name = tool_call.function.name
                            try:
                                args = json.loads(tool_call.function.arguments)
                            except json.JSONDecodeError:
                                args = {}
                            
                            args_str = str(args)[:80] + "..." if len(str(args)) > 80 else str(args)
                            print(f"   [🚀 START] {tool_name} -> {args_str}")
                            
                            # --- MODIFICARE: Interceptare tool ask_user ---
                            if tool_name == "ask_user":
                                question = args.get("question", "Am nevoie de clarificări.")
                                print(f"\n❓ JARVIS ÎNTREABĂ: {question}")
                                # Oprim execuția asincronă pentru a lua input de la tastatură
                                user_answer = input("   Răspunsul tău: ")
                                
                                # Creăm o funcție fake async pentru a păstra structura listei de task-uri
                                async def return_user_input():
                                    # Simulăm structura de răspuns MCP
                                    return type('obj', (object,), {
                                        "content": [type('obj', (object,), {"text": json.dumps({"user_response": user_answer})})]
                                    })()
                                
                                tasks.append(return_user_input())
                                tool_calls_ordered.append(tool_call)
                            
                            # Logica standard pentru MCP tools
                            elif self.tool_registry.get(tool_name):
                                tool_info = self.tool_registry.get(tool_name)
                                tasks.append(tool_info["session"].call_tool(tool_name, arguments=args))
                                tool_calls_ordered.append(tool_call)
                            else:
                                async def fake_error():
                                    return type('obj', (object,), {
                                        "content": [type('obj', (object,), {"text": json.dumps({"error": f"Tool {tool_name} not found"})})]
                                    })()
                                tasks.append(fake_error())
                                tool_calls_ordered.append(tool_call)

                        # Executăm task-urile (inclusiv pe cel fake de user input)
                        if tasks:
                            results = await asyncio.gather(*tasks, return_exceptions=True)
                        else:
                            results = []

                        for i, result in enumerate(results):
                            tool_call = tool_calls_ordered[i]
                            tool_name = tool_call.function.name
                            
                            if isinstance(result, Exception):
                                error_msg = f"Error executing {tool_name}: {str(result)}"
                                print(f"   [❌ FAIL] {tool_name}: {str(result)[:50]}")
                                content = json.dumps({"error": error_msg})
                            else:
                                if hasattr(result, 'content') and result.content:
                                    content = result.content[0].text
                                    try:
                                        parsed = json.loads(content)
                                        if tool_name == "ask_user":
                                            summary = "[ask_user] Răspuns primit"
                                        elif isinstance(parsed, dict) and 'results' in parsed:
                                            summary = f"[{tool_name}] Găsit {len(parsed['results'])} rezultate"
                                        else:
                                            summary = f"[{tool_name}] Success"
                                    except:
                                        summary = f"[{tool_name}] Success (text)"
                                else:
                                    content = json.dumps({"status": "success", "tool": tool_name})
                                    summary = f"[{tool_name}] Success"
                                
                                print(f"   [✅ DONE] {summary}")

                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": str(content)
                            })
                            
                    else:
                        if message.content:
                            print(f"\n🤖 JARVIS: {message.content}")
                        else:
                            print(f"\n🤖 JARVIS: [Aștept instrucțiuni...]")
                        final_response_shown = True
                        break
                        
                if turn_count >= max_turns:
                    print("\n⚠️  Atenție: Limita de pași atinsă.")
                    
            except KeyboardInterrupt:
                print("\n\n👋 La revedere!")
                break
            except Exception as e:
                print(f"\n❌ Eroare în bucla principală: {e}")
                import traceback
                traceback.print_exc()

if __name__ == "__main__":
    try:
        asyncio.run(JarvisMVP().start())
    except KeyboardInterrupt:
        print("\n\n👋 Oprit de utilizator.")