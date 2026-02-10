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
            
            print(f"\n🤖 JARVIS MVP Online")
            print(f"   Tool-uri active: {len(self.available_tools)}")
            print("   (Scrie 'exit' pentru a ieși)\n")
            
            await self.chat_loop()
    
    async def chat_loop(self):
        """Bucla principală de interacțiune cu logică REACT îmbunătățită."""
        
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")

        system_prompt = f"""Ești JARVIS, un asistent AI avansat conectat la unelte externe.
                DATA CURENTĂ: {current_time}

                PROTOCOL DE OPERARE (OBLIGATORIU):

                1. GÂNDIRE PAS-CU-PAS: Pentru cereri complexe, descompune în pași mici.
                2. SCEPTICISM RADICAL: Folosește TOOL-urile disponibile pentru date reale, nu cunoștințe interne.
                3. EXECUȚIE ITERATIVĂ: 
                - Fă research cu tool-uri (web_search, etc.)
                - Analizează rezultatele
                - Dacă e nevoie de mai multe date, apelează alte tool-uri
                - Abia apoi formulează răspunsul final
                4. PERSISTENȚĂ: Continuă să folosești tool-uri până când ai informații complete pentru a răspunde.
                5. SALVARE: Dacă utilizatorul cere salvare, folosește tool-ul write_file DOAR după ce ai adunat toate informațiile necesare.

                REGULI CRITICE:
                - NU răspunde "am salvat în fișier" dacă fișierul e gol sau conține doar template-uri.
                - Asigură-te că fișierul conține date concrete, prețuri reale, detalii specifice.
                - Dacă tool-ul write_file e apelat, conținutul trebuie să fie complet, nu placeholder-e."""

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
                            temperature=0.7
                        )
                    except Exception as e:
                        print(f"❌ Eroare API: {e}")
                        break

                    message = response.choices[0].message
                    
                    # Construim mesajul curat pentru istoric
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
                        
                        # Execuție paralelă
                        tasks = []
                        tool_calls_ordered = []

                        for tool_call in message.tool_calls:
                            tool_name = tool_call.function.name
                            try:
                                args = json.loads(tool_call.function.arguments)
                            except json.JSONDecodeError:
                                args = {}
                            
                            # Trunchiem args pentru afișare
                            args_str = str(args)[:80] + "..." if len(str(args)) > 80 else str(args)
                            print(f"   [🚀 START] {tool_name} -> {args_str}")
                            
                            tool_info = self.tool_registry.get(tool_name)
                            if tool_info:
                                tasks.append(tool_info["session"].call_tool(tool_name, arguments=args))
                                tool_calls_ordered.append(tool_call)
                            else:
                                async def fake_error():
                                    return type('obj', (object,), {
                                        "content": [type('obj', (object,), {"text": json.dumps({"error": f"Tool {tool_name} not found"})})]
                                    })()
                                tasks.append(fake_error())
                                tool_calls_ordered.append(tool_call)

                        # Executăm toate task-urile în paralel
                        if tasks:
                            results = await asyncio.gather(*tasks, return_exceptions=True)
                        else:
                            results = []

                        # Procesăm rezultatele
                        for i, result in enumerate(results):
                            tool_call = tool_calls_ordered[i]
                            tool_name = tool_call.function.name
                            
                            if isinstance(result, Exception):
                                error_msg = f"Error executing {tool_name}: {str(result)}"
                                print(f"   [❌ FAIL] {tool_name}: {str(result)[:50]}")
                                content = json.dumps({"error": error_msg})
                            else:
                                # Extragem conținutul din rezultatul MCP
                                if hasattr(result, 'content') and result.content:
                                    content = result.content[0].text
                                    # Încercăm să parsăm ca JSON pentru validare
                                    try:
                                        parsed = json.loads(content)
                                        # Dacă e un rezultat de search, extragem info utilă
                                        if isinstance(parsed, dict) and 'results' in parsed:
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
                        # Nu mai sunt tool calls, avem răspuns final
                        if message.content:
                            print(f"\n🤖 JARVIS: {message.content}")
                        else:
                            print(f"\n🤖 JARVIS: [Nu am primit conținut în răspuns]")
                        final_response_shown = True
                        break
                        
                if turn_count >= max_turns:
                    print("\n⚠️  Atenție: Număr maxim de iterații atins. Posibil ciclu infinit sau task prea complex.")
                    
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