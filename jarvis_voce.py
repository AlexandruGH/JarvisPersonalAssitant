import os
import sys
import json
import asyncio
import traceback
from contextlib import AsyncExitStack
from typing import Dict, Any

# Audio
import speech_recognition as sr
import pyttsx3

# MCP & AI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

class JarvisListening:
    def __init__(self):
        # Verificare cheie API
        if not os.getenv("GROQ_API_KEY"):
            print("❌ EROARE: Lipsește GROQ_API_KEY în .env")
            sys.exit(1)

        self.groq = Groq(api_key=os.getenv("GROQ_API_KEY"))
        self.tool_registry = {}
        self.sessions = []
        self.available_tools = []
        
        # --- 1. SETĂRI VOCE (TTS) ---
        try:
            self.engine = pyttsx3.init()
            self.configure_voice()
        except:
            self.engine = None

        # --- 2. SETĂRI URECHI (Microfon) ---
        self.recognizer = sr.Recognizer()
        
        # === REGLAJE FINE PENTRU DETECȚIE ===
        
        # Pragul de energie (sensibilitatea). 
        # 300-400 este standard pentru o cameră liniștită.
        # Dacă e prea mic, prinde și respirația. Dacă e prea mare, trebuie să țipi.
        self.recognizer.energy_threshold = 300 
        
        # Ajustare dinamică (dacă intră zgomot brusc, se adaptează)
        self.recognizer.dynamic_energy_threshold = True 
        
        # Câtă liniște așteaptă ca să considere fraza gata 
        self.recognizer.pause_threshold = 2
        
        # Cât de repede renunță dacă nu aude nimic la început
        self.recognizer.non_speaking_duration = 0.5

    def configure_voice(self):
        try:
            voices = self.engine.getProperty('voices')
            ro_voice = None
            for v in voices:
                if "romania" in v.name.lower() or "andrei" in v.name.lower():
                    ro_voice = v.id
                    break
            if ro_voice: self.engine.setProperty('voice', ro_voice)
            self.engine.setProperty('rate', 160)
        except: pass

    def speak(self, text):
        print(f"\n🤖 JARVIS: {text}")
        if self.engine:
            try:
                self.engine.say(text)
                self.engine.runAndWait()
            except: pass

    def listen_manual(self):
        """Ascultă automat, fără să trebuiască să apeși ENTER"""
        
        with sr.Microphone() as source:
            print("\n" + "-"*30)
            print("🎤 TE ASCULT... (Vorbește acum)")
            
            # Calibrare mai lungă pentru a stabili liniștea corect (1 secundă)
            self.recognizer.adjust_for_ambient_noise(source, duration=1.0)
            
            try:
                # timeout=5 -> Dacă nu vorbești 5 secunde, se oprește și reia bucla
                # phrase_time_limit=15 -> Nu te lasă să vorbești mai mult de 15 secunde odată
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=15)
                
                print("⏳ Procesez...")
                text = self.recognizer.recognize_google(audio, language="ro-RO")
                print(f"👤 Ai spus: {text}")
                return text
                
            except sr.WaitTimeoutError:
                # Nu a auzit nimic, returnează None ca să continue bucla
                return None
            except sr.UnknownValueError:
                print("⚠️ Nu am înțeles cuvintele.")
                return ""
            except Exception as e:
                print(f"Eroare microfon: {e}")
                return ""

    def load_config(self) -> Dict[str, Any]:
        try: return json.load(open("config_mcp.json"))
        except: return {"mcpServers": {}}
    
    async def start(self):
        config = self.load_config()
        
        try:
            async with AsyncExitStack() as stack:
                print("\n🔌 Conectare servere MCP...")
                
                # --- 1. CONECTARE LA SERVERELE MCP ---
                for server_name, server_conf in config.get("mcpServers", {}).items():
                    try:
                        command = server_conf["command"]
                        if command == "python": command = sys.executable

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
                        print(f"   ✅ Conectat la: {server_name}")
                    except Exception as e:
                        print(f"   ❌ Eroare la {server_name}: {e}")
                        continue

                # Verificare unelte
                print(f"🧰 Unelte disponibile: {[t['function']['name'] for t in self.available_tools]}")
                
                # --- 2. BUCLA PRINCIPALĂ DE ASCULTARE ---
                self.speak("Sunt online. Te ascult.")
                
                while True:
                    try:
                        user_input = self.listen_manual()
                        
                        if user_input is None or user_input.strip() == "":
                            continue 

                        if any(x in user_input.lower() for x in ["stop", "ieși", "la revedere", "gata"]):
                            self.speak("La revedere!")
                            break
                        
                        # System Prompt care forțează folosirea uneltelor
                        system_prompt = (
                            "Ești JARVIS. Ai acces la unelte reale (Internet, Search, etc). "
                            "REGULI CRITICE:\n"
                            "1. Dacă utilizatorul cere ceva ce necesită informații externe (vreme, prețuri, vacanțe, știri), "
                            "NU îi spune ce ar trebui făcut. FOLOSEȘTE UNELTELE (tool calls) IMEDIAT.\n"
                            "2. Nu cere permisiunea să cauți. Caută direct.\n"
                            "3. Răspunde scurt în română după ce ai rezultatele."
                        )

                        messages = [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_input}
                        ]
                        
                        print("🤖 Gândesc...")
                        
                        # Apel inițial către AI
                        # Notă: Asigură-te că modelul suportă tools. deepseek-r1-distill-llama-70b suportă, 
                        # dar modelele pure de text nu. Recomand 'openai/gpt-oss-120b' sau 'llama-3.3-70b-versatile' sau 'mixtral-8x7b-32768' pentru tools.
                        response = self.groq.chat.completions.create(
                            model="openai/gpt-oss-120b", # Recomandat să rămâi pe Llama 3.3 pentru tools stabile
                            messages=messages,
                            tools=self.available_tools if self.available_tools else None,
                            tool_choice="auto"
                        )
                        
                        message = response.choices[0].message
                        
                        # --- MODIFICARE CRITICĂ: SANITIZARE MESAJ ---
                        # Construim manual dicționarul pentru a evita câmpul 'reasoning' care dă eroare
                        clean_message = {
                            "role": message.role,
                            "content": message.content,
                            "tool_calls": message.tool_calls
                        }
                        # Ștergem tool_calls dacă e None, altfel API-ul poate da eroare
                        if clean_message["tool_calls"] is None:
                            del clean_message["tool_calls"]

                        # Adăugăm mesajul curat în istoric
                        messages.append(clean_message)
                        
                        if message.tool_calls:
                            self.speak("Caut informații...")
                            
                            for tool_call in message.tool_calls:
                                tool_name = tool_call.function.name
                                try:
                                    tool_args = json.loads(tool_call.function.arguments)
                                    
                                    if tool_info := self.tool_registry.get(tool_name):
                                        print(f"🔧 Rulez: {tool_name}...")
                                        result_obj = await tool_info["session"].call_tool(tool_name, arguments=tool_args)
                                        result_text = result_obj.content[0].text
                                        
                                        messages.append({
                                            "role": "tool", 
                                            "tool_call_id": tool_call.id, 
                                            "content": str(result_text)
                                        })
                                except Exception as tool_err:
                                    print(f"Eroare execuție tool: {tool_err}")
                                    messages.append({
                                        "role": "tool", 
                                        "tool_call_id": tool_call.id, 
                                        "content": "Eroare la executarea comenzii."
                                    })
                            
                            # Apel final către AI cu rezultatele uneltelor
                            final_resp = self.groq.chat.completions.create(
                                model="llama-3.3-70b-versatile", 
                                messages=messages
                            )
                            self.speak(final_resp.choices[0].message.content)
                        else:
                            self.speak(message.content)

                    except KeyboardInterrupt:
                        print("\nOprire forțată.")
                        break
                    except Exception as e:
                        print(f"Eroare în bucla principală: {e}")
                        # traceback.print_exc()

        except Exception as e:
            print(f"Eroare fatală la pornire: {e}")
        finally:
            print("Jarvis s-a oprit.")

if __name__ == "__main__":
    asyncio.run(JarvisListening().start())