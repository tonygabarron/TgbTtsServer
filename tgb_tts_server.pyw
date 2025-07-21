#tgb_tts_server.pyw
import tkinter
import customtkinter as ctk
import configparser
import threading
import queue
import requests
import io
import os
import sys
import json
import logging
import webbrowser
import re
import unicodedata
from flask import Flask, request, jsonify, Response, send_file
from flask_cors import CORS
from gtts import gTTS
from werkzeug.serving import make_server

# --- CONSTANTS AND INITIAL SETUP ---
# MODIFICADO: Lógica para determinar o caminho base do executável/script
if getattr(sys, 'frozen', False):
    # Se estiver rodando como um executável (.exe) compilado pelo PyInstaller
    BASE_PATH = os.path.dirname(sys.executable)
else:
    # Se estiver rodando como um script normal (.pyw)
    BASE_PATH = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_PATH, 'config.ini')
# Default Regex, can be overridden by user settings
DEFAULT_URL_REGEX = re.compile(r'(https?://[^\s]+|www\.[^\s]+|[^\s]+\.(com|net|org|io|gg|ly|tv|shop|xyz))')

# --- Object for real-time shared settings ---
class SharedSettings:
    def __init__(self):
        self.host = "127.0.0.1"
        self.port = 80
        self.lang = "en"
        self.message_key = "messageKey"
        self.message_format_default = "{chatname} said: {chatmessage}"
        self.message_models = {
            "message": "{chatname} said: {chatmessage}",
            "follow": "{chatname} is now following!",
            "donation": "{chatname} donated {amount}!"
        }
        self.spam_filter_enabled = False
        self.spam_threshold = 10
        self.spam_keywords = []
        self.spam_score_link = 10
        self.spam_score_caps = 3
        self.spam_score_symbols = 3
        self.spam_score_keyword = 5
        self.url_regex_compiled = DEFAULT_URL_REGEX

HTML_CONTENT = """
<!DOCTYPE html><html><head><title>TGB TTS Player</title></head><body><audio id="tts-audio-player" autoplay></audio><script>const audioPlayer=document.getElementById("tts-audio-player"),messageQueue=[];let isPlaying=!1;function log(e){console.log(`[TTS Player] ${e}`)}audioPlayer.addEventListener("ended",()=>{isPlaying=!1,playNextInQueue()}),audioPlayer.addEventListener("error",e=>{console.error("Audio error:",e),isPlaying=!1,playNextInQueue()});function playNextInQueue(){if(!isPlaying&&messageQueue.length>0){isPlaying=!0;const e=messageQueue.shift(),t=`/audio.mp3?text=${encodeURIComponent(e)}`;audioPlayer.src=t;const o=audioPlayer.play();void 0!==o&&o.catch(e=>{console.error("Playback error:",e),isPlaying=!1,playNextInQueue()})}}const eventSource=new EventSource("/stream");eventSource.onmessage=function(e){const t=e.data;t&&(messageQueue.push(t),playNextInQueue())},eventSource.onerror=function(e){console.error("EventSource error:",e)};</script></body></html>
"""

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# --- POPUP WINDOWS ---
class InfoPopup(ctk.CTkToplevel):
    def __init__(self, parent, title, message):
        super().__init__(parent)
        self.transient(parent)
        self.grab_set()
        self.title(title)
        self.resizable(False, False)

        self.grid_columnconfigure(0, weight=1)

        message_label = ctk.CTkLabel(self, text=message, wraplength=420, justify="left", font=ctk.CTkFont(size=13))
        message_label.grid(row=0, column=0, padx=20, pady=20, sticky="ew")

        ok_button = ctk.CTkButton(self, text="OK", command=self.destroy, width=100)
        ok_button.grid(row=1, column=0, pady=(0, 20))
        
        self.wait_window()


class ModelEditorPopup(ctk.CTkToplevel):
    def __init__(self, parent, callback, model_key="", model_format=""):
        super().__init__(parent); self.transient(parent); self.grab_set(); self.callback = callback; self.original_key = model_key
        self.title("Edit Message Model"); self.geometry("500x300"); self.grid_columnconfigure(1, weight=1); self.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(self, text="Message Key:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.key_entry = ctk.CTkEntry(self); self.key_entry.grid(row=0, column=1, padx=10, pady=10, sticky="ew"); self.key_entry.insert(0, model_key)
        ctk.CTkLabel(self, text="Message Format:").grid(row=1, column=0, padx=10, pady=5, sticky="nw")
        self.format_textbox = ctk.CTkTextbox(self, wrap="word"); self.format_textbox.grid(row=1, column=1, padx=10, pady=(5,10), sticky="nsew"); self.format_textbox.insert("1.0", model_format)
        button_frame = ctk.CTkFrame(self, fg_color="transparent"); button_frame.grid(row=2, column=0, columnspan=2, pady=10)
        self.save_button = ctk.CTkButton(button_frame, text="Save", command=self.on_save); self.save_button.pack(side="left", padx=10)
        self.cancel_button = ctk.CTkButton(button_frame, text="Cancel", command=self.destroy, fg_color="#E74C3C", hover_color="#C0392B"); self.cancel_button.pack(side="left", padx=10)
        self.wait_window()
    def on_save(self):
        new_key = self.key_entry.get().strip(); new_format = self.format_textbox.get("1.0", "end-1c")
        if not new_key or not new_format: return
        self.callback(self.original_key, new_key, new_format); self.destroy()

# --- FLASK SERVER LOGIC ---
log_queue = queue.Queue()
def log(module_name: str, level: str, message: str): log_queue.put((level, module_name, message))

def analyze_message_for_spam(text: str, settings: SharedSettings):
    score = 0; reasons = []; normalized_text = unicodedata.normalize('NFKC', text).lower()
    if settings.spam_score_link > 0 and settings.url_regex_compiled.search(normalized_text):
        score += settings.spam_score_link; reasons.append("Link Detected")
    for keyword in settings.spam_keywords:
        if keyword in normalized_text: score += settings.spam_score_keyword; reasons.append(f"Keyword: '{keyword}'"); break
    if settings.spam_score_caps > 0 and len(text) > 15 and sum(1 for c in text if c.isupper()) / len(text) > 0.7: score += settings.spam_score_caps; reasons.append("Excessive Caps")
    if settings.spam_score_symbols > 0 and len(text) > 10 and sum(1 for c in text if not c.isalnum() and not c.isspace()) / len(text) > 0.4: score += settings.spam_score_symbols; reasons.append("Excessive Symbols")
    return score, reasons

def create_flask_app(shared_settings: SharedSettings):
    app = Flask(__name__); CORS(app); logging.getLogger('werkzeug').setLevel(logging.ERROR); message_queue = queue.Queue()
    @app.route('/')
    def audio_source_page(): return Response(HTML_CONTENT, mimetype='text/html')
    @app.route('/speak', methods=['POST'])
    def receive_message():
        data = request.get_json();
        if not data: log('SERVER', 'WARN', f"POST with invalid data from {request.remote_addr}"); return jsonify({"status": "error", "message": "Invalid JSON"}), 400
        log('SERVER', 'INFO', f"JSON Received: {data}")
        if shared_settings.spam_filter_enabled and 'chatmessage' in data:
            score, reasons = analyze_message_for_spam(data['chatmessage'], shared_settings)
            if score >= shared_settings.spam_threshold: log('SERVER', 'WARN', f"SPAM BLOCKED (Score: {score}/{shared_settings.spam_threshold}) by: {', '.join(reasons)}. Message: '{data['chatmessage']}'"); return jsonify({"status": "spam_detected", "score": score, "reasons": reasons}), 200
        selector_key = shared_settings.message_key; model_key_value = data.get(selector_key); final_format = shared_settings.message_format_default
        if model_key_value and shared_settings.message_models.get(model_key_value): final_format = shared_settings.message_models.get(model_key_value); log('SERVER', 'INFO', f"Using message model for key: '{model_key_value}'")
        try: final_message = final_format.format(**data); log('SERVER', 'INFO', f"Formatted Message: '{final_message}'"); message_queue.put(final_message); return jsonify({"status": "success", "message": "Message queued."})
        except KeyError as e: error_msg = f"Key {e} not found in JSON for the selected format."; log('SERVER', 'ERROR', error_msg); return jsonify({"status": "error", "message": error_msg}), 400
    @app.route('/stream')
    def stream_messages():
        client_addr = request.remote_addr
        def event_stream():
            log('SERVER', 'INFO', f"Client {client_addr} connected.")
            try:
                while True: yield f"data: {message_queue.get()}\n\n"
            except GeneratorExit: log('SERVER', 'INFO', f"Client {client_addr} disconnected.")
        return Response(event_stream(), mimetype='text/event-stream')
    @app.route('/audio.mp3')
    def generate_audio():
        text = request.args.get('text', 'No text'); lang = shared_settings.lang; log('SERVER', 'INFO', f"Generating audio for: '{text}' in '{lang}'")
        try: tts = gTTS(text=text, lang=lang); fp = io.BytesIO(); tts.write_to_fp(fp); fp.seek(0); return send_file(fp, mimetype='audio/mpeg')
        except Exception as e: log('SERVER', 'ERROR', f"gTTS Error: {e}"); return "Error generating audio", 500
    return app

# --- MAIN GUI ---
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("TGB TTS Server - v0.1 (BETA)"); self.geometry("1100x700")
        self.LANGUAGES = {'en':'English (US)','pt-br':'Brazilian Portuguese','en-uk':'English (UK)','en-au':'English (Australia)','en-in':'English (India)','pt':'Portuguese (Portugal)','es':'Spanish (Spain)','es-mx':'Spanish (Mexico)','fr':'French','de':'German','it':'Italian','ja':'Japanese','ko':'Korean','ru':'Russian','zh-cn':'Chinese (Mandarin/China)','zh-tw':'Chinese (Mandarin/Taiwan)'}
        self.language_display_list = [f"{name} ({code})" for code, name in self.LANGUAGES.items()]
        self.config = configparser.ConfigParser(); self.shared_settings = SharedSettings(); self.server_thread = None; self.http_server = None; self.is_server_running = False
        
        # ADICIONADO: Garante que o config.ini exista antes de carregar
        self.initialize_config_file()
        
        self.grid_columnconfigure(0, weight=1); self.grid_rowconfigure(0, weight=1)

        self.tab_view = ctk.CTkTabview(self, anchor="w"); self.tab_view.grid(row=0, column=0, padx=10, pady=(10, 0), sticky="nsew")
        self.tab_view.add("Dashboard"); self.tab_view.add("Settings"); self.tab_view.add("Message Models"); self.tab_view.add("Spam Filter")
        
        self.bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.bottom_frame.grid(row=1, column=0, padx=10, pady=(5, 10), sticky="ew")
        self.bottom_frame.grid_columnconfigure(0, weight=1)
        credits_sub_frame = ctk.CTkFrame(self.bottom_frame, fg_color="transparent"); credits_sub_frame.pack(side="right")
        github_label = ctk.CTkLabel(credits_sub_frame, text="github.com/tonygabarron/TgbTtsServer", text_color="#3498DB", cursor="hand2", font=ctk.CTkFont(size=11)); github_label.pack(side="left", padx=(0,10))
        github_label.bind("<Button-1>", lambda e: self.open_link("https://github.com/tonygabarron/TgbTtsServer"))
        donate_button = ctk.CTkButton(credits_sub_frame, text="☕ Support the Project", command=lambda: self.open_link("https://ko-fi.com/tonygabarron"), fg_color="#E67E22", hover_color="#D35400", height=24, width=140); donate_button.pack(side="left")

        self.populate_dashboard_tab(self.tab_view.tab("Dashboard")); self.populate_settings_tab(self.tab_view.tab("Settings")); self.populate_models_tab(self.tab_view.tab("Message Models")); self.populate_spam_filter_tab(self.tab_view.tab("Spam Filter"))
        self.load_config(); self.populate_models_list(); self.update_url_displays(); self.process_log_queue()
        if self.config.getboolean('server_config', 'autostart', fallback=True):
            log('GUI', 'INFO', "Autostart is enabled. Starting server..."); self.after(100, self.start_server)
    
    # ADICIONADO: Nova função para criar o config.ini se não existir
    def initialize_config_file(self):
        if not os.path.exists(CONFIG_FILE):
            log('GUI', 'INFO', f"Config file not found. Creating a new one at {CONFIG_FILE}")
            default_config = configparser.ConfigParser()
            
            default_config['server_config'] = {
                'host': '127.0.0.1',
                'port': '80',
                'autostart': 'true'
            }
            default_config['tts_config'] = {
                'lang': 'pt-br',
                'message_key': 'messageKey',
                'message_format_default': '{chatname} said: {chatmessage}'
            }
            default_config['message_models'] = {
                'message': '{chatname} said: {chatmessage}',
                'follow': '{chatname} is now following!',
                'donation': '{chatname} donated {amount}!'
            }
            default_config['spam_filter'] = {
                'enabled': 'false',
                'threshold': '10',
                'score_link': '10',
                'score_caps': '3',
                'score_symbols': '3',
                'score_keyword': '5',
                'keywords': 'followers\nviews\npromo\n.gg/\nbit.ly',
                'url_regex': DEFAULT_URL_REGEX.pattern
            }
            
            with open(CONFIG_FILE, 'w', encoding='utf-8') as configfile:
                default_config.write(configfile)

    def populate_dashboard_tab(self, tab):
        tab.grid_rowconfigure(1, weight=1); tab.grid_columnconfigure(0, weight=1)
        control_usage_frame = ctk.CTkFrame(tab); control_usage_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ew"); control_usage_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(control_usage_frame, text="Server Control & Usage", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, columnspan=3, pady=(5,10))
        self.status_label = ctk.CTkLabel(control_usage_frame, text="Status: Stopped", text_color="#F39C12", font=ctk.CTkFont(weight="bold")); self.status_label.grid(row=1, column=0, padx=10, pady=5)
        self.start_stop_button = ctk.CTkButton(control_usage_frame, text="Start Server", command=self.toggle_server); self.start_stop_button.grid(row=1, column=1, columnspan=2, padx=10, pady=5, sticky="ew")
        ctk.CTkFrame(control_usage_frame, height=2, fg_color="gray20").grid(row=2, column=0, columnspan=3, pady=10, sticky="ew")
        ctk.CTkLabel(control_usage_frame, text="URL for Audio Source:").grid(row=3, column=0, padx=10, pady=5, sticky="w")
        self.audio_source_url_entry = ctk.CTkEntry(control_usage_frame, state="readonly"); self.audio_source_url_entry.grid(row=3, column=1, padx=(10,5), pady=5, sticky="ew")
        self.copy_audio_source_button = ctk.CTkButton(control_usage_frame, text="Copy", width=60, command=lambda: self.copy_to_clipboard(self.audio_source_url_entry.get())); self.copy_audio_source_button.grid(row=3, column=2, padx=(0,10), pady=5)
        ctk.CTkLabel(control_usage_frame, text="URL for Alerts (POST):").grid(row=4, column=0, padx=10, pady=5, sticky="w")
        self.post_url_entry = ctk.CTkEntry(control_usage_frame, state="readonly"); self.post_url_entry.grid(row=4, column=1, padx=(10,5), pady=5, sticky="ew")
        self.copy_post_button = ctk.CTkButton(control_usage_frame, text="Copy", width=60, command=lambda: self.copy_to_clipboard(self.post_url_entry.get())); self.copy_post_button.grid(row=4, column=2, padx=(0,10), pady=5)
        ctk.CTkLabel(control_usage_frame, text="Alert JSON Example:").grid(row=5, column=0, padx=10, pady=(10, 0), sticky="w")
        self.json_example_textbox = ctk.CTkTextbox(control_usage_frame, height=120, wrap="word"); self.json_example_textbox.grid(row=6, column=0, columnspan=2, padx=(10,5), pady=(5, 10), sticky="ew")
        self.speak_button = ctk.CTkButton(control_usage_frame, text="Speak", width=60, command=self.send_custom_json_request, state="disabled"); self.speak_button.grid(row=6, column=2, padx=(0,10), pady=(5, 10), sticky="ns")
        test_payload = {"messageKey": "message", "chatname": "TestUser", "chatmessage": "1, 2, 3 Testing"}; self.json_example_textbox.insert("1.0", json.dumps(test_payload, indent=4))
        log_frame = ctk.CTkFrame(tab); log_frame.grid(row=1, column=0, padx=10, pady=(0,10), sticky="nsew"); log_frame.grid_rowconfigure(1, weight=1); log_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(log_frame, text="Server Logs", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, pady=(5,5))
        self.log_textbox = ctk.CTkTextbox(log_frame, state="disabled", wrap="word"); self.log_textbox.grid(row=1, column=0, padx=10, pady=(0,10), sticky="nsew")
        self.log_textbox.tag_config("INFO", foreground="#FFFFFF"); self.log_textbox.tag_config("WARN", foreground="#F39C12"); self.log_textbox.tag_config("ERROR", foreground="#E74C3C"); self.log_textbox.tag_config("GUI", foreground="#AAAAAA"); self.log_textbox.tag_config("SERVER", foreground="#3498DB")

    def populate_settings_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        
        config_frame = ctk.CTkFrame(tab)
        config_frame.grid(row=0, column=0, padx=10, pady=10, sticky="new")
        config_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(config_frame, text="General Settings", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, columnspan=2, pady=(5,10), sticky="w")
        ctk.CTkLabel(config_frame, text="Host:").grid(row=1, column=0, padx=10, pady=5, sticky="w")
        self.host_entry = ctk.CTkEntry(config_frame); self.host_entry.grid(row=1, column=1, padx=10, pady=5, sticky="ew")
        ctk.CTkLabel(config_frame, text="Port:").grid(row=2, column=0, padx=10, pady=5, sticky="w")
        self.port_entry = ctk.CTkEntry(config_frame); self.port_entry.grid(row=2, column=1, padx=10, pady=5, sticky="ew")
        ctk.CTkLabel(config_frame, text="Language (Voice):").grid(row=3, column=0, padx=10, pady=5, sticky="w")
        self.lang_combobox = ctk.CTkComboBox(config_frame, values=self.language_display_list, state="readonly"); self.lang_combobox.grid(row=3, column=1, padx=10, pady=5, sticky="ew")
        ctk.CTkLabel(config_frame, text="Message Key:").grid(row=4, column=0, padx=10, pady=5, sticky="w")
        self.selector_key_entry = ctk.CTkEntry(config_frame); self.selector_key_entry.grid(row=4, column=1, padx=10, pady=5, sticky="ew")
        ctk.CTkLabel(config_frame, text="Message Format Default:").grid(row=5, column=0, padx=10, pady=5, sticky="nw")
        self.format_default_textbox = ctk.CTkTextbox(config_frame, height=60, wrap="word"); self.format_default_textbox.grid(row=5, column=1, padx=10, pady=5, sticky="ew")
        self.autostart_checkbox = ctk.CTkCheckBox(config_frame, text="Start server on launch"); self.autostart_checkbox.grid(row=6, column=0, columnspan=2, padx=10, pady=10, sticky="w")

        save_settings_button = ctk.CTkButton(config_frame, text="Save Settings", command=self.save_config)
        save_settings_button.grid(row=7, column=0, columnspan=2, padx=10, pady=10, sticky="ew")

    def populate_models_tab(self, tab):
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=1)
        
        models_frame = ctk.CTkFrame(tab)
        models_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        models_frame.grid_columnconfigure(0, weight=1)
        models_frame.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(models_frame, text="Message Models", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, pady=(5, 5), sticky="w")
        
        headers_frame = ctk.CTkFrame(models_frame, fg_color="transparent")
        headers_frame.grid(row=1, column=0, sticky="ew")
        headers_frame.grid_columnconfigure(0, weight=1)
        headers_frame.grid_columnconfigure(1, weight=2)
        ctk.CTkLabel(headers_frame, text="Message Key", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=(5,0), sticky="w")
        ctk.CTkLabel(headers_frame, text="Message Format", font=ctk.CTkFont(weight="bold")).grid(row=0, column=1, padx=5, sticky="w")

        self.models_list_frame = ctk.CTkScrollableFrame(models_frame, fg_color="transparent")
        self.models_list_frame.grid(row=2, column=0, pady=0, sticky="nsew")
        self.models_list_frame.grid_columnconfigure(0, weight=1)
        self.models_list_frame.grid_columnconfigure(1, weight=2)
        
        add_model_button = ctk.CTkButton(models_frame, text="Add New Model", command=self.add_model)
        add_model_button.grid(row=3, column=0, padx=0, pady=10, sticky="ew")

    def populate_spam_filter_tab(self, tab):
        tab.grid_columnconfigure((0, 1), weight=1); tab.grid_rowconfigure(1, weight=1)

        left_column_frame = ctk.CTkFrame(tab)
        left_column_frame.grid(row=0, column=0, rowspan=2, padx=(10, 5), pady=10, sticky="nsew")
        left_column_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(left_column_frame, text="Spam & Security Filter", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, columnspan=3, pady=(5,0), padx=10, sticky="w")
        self.spam_enabled_var = ctk.BooleanVar(); self.spam_enabled_checkbox = ctk.CTkCheckBox(left_column_frame, text="Enable Spam Filter", variable=self.spam_enabled_var, command=self.toggle_spam_widgets_state)
        self.spam_enabled_checkbox.grid(row=1, column=0, columnspan=3, pady=(5,15), padx=10, sticky="w")

        ctk.CTkLabel(left_column_frame, text="Spam Score Settings", font=ctk.CTkFont(weight="bold")).grid(row=2, column=0, columnspan=3, pady=5, padx=10, sticky="w")
        
        self.spam_widgets_to_toggle = []
        scores_data = [
            ("Spam Score Threshold:", "If a message's spam score reaches or exceeds this value, it will be blocked."),
            ("Score for Links:", "Points added if the message contains a URL or link. Use 0 to disable."),
            ("Score for Excessive Caps:", "Points added if >70% of the message is in UPPERCASE. Use 0 to disable."),
            ("Score for Excessive Symbols:", "Points added if >40% of the message consists of symbols/emojis. Use 0 to disable."),
            ("Score for Forbidden Keyword:", "Points added if a forbidden keyword from the list is found.")
        ]
        
        for i, (label_text, tooltip_text) in enumerate(scores_data, start=3):
            ctk.CTkLabel(left_column_frame, text=label_text).grid(row=i, column=0, padx=10, pady=8, sticky="w")
            entry = ctk.CTkEntry(left_column_frame); entry.grid(row=i, column=1, padx=5, pady=8, sticky="ew")
            info_button = ctk.CTkButton(left_column_frame, text="?", width=28, height=28, command=lambda t=label_text, m=tooltip_text: InfoPopup(self, t, m)); info_button.grid(row=i, column=2, padx=(0,10), pady=8, sticky="w")
            self.spam_widgets_to_toggle.append(entry)
        
        (self.spam_threshold_entry, self.spam_link_score_entry, self.spam_caps_score_entry, 
         self.spam_symbols_score_entry, self.spam_keyword_score_entry) = self.spam_widgets_to_toggle

        right_column_frame = ctk.CTkFrame(tab)
        right_column_frame.grid(row=0, column=1, rowspan=2, padx=(5, 10), pady=10, sticky="nsew")
        right_column_frame.grid_columnconfigure(0, weight=1); right_column_frame.grid_rowconfigure(3, weight=1)

        regex_info_frame = ctk.CTkFrame(right_column_frame, fg_color="transparent")
        regex_info_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(5,0))
        ctk.CTkLabel(regex_info_frame, text="URL Detection Regex (Editable):", font=ctk.CTkFont(weight="bold")).pack(side="left")
        info_regex_button = ctk.CTkButton(regex_info_frame, text="?", width=28, height=28, command=lambda: InfoPopup(self, "URL Detection Regex", "Edit the Regular Expression (Regex) for link detection. CAUTION: An invalid or poorly written regex can cause errors or fail to detect links. If unsure, restore the default value."))
        info_regex_button.pack(side="left", padx=5)
        self.url_regex_entry = ctk.CTkEntry(right_column_frame); self.url_regex_entry.grid(row=1, column=0, padx=10, pady=(0,10), sticky="ew")
        
        keyword_info_frame = ctk.CTkFrame(right_column_frame, fg_color="transparent")
        keyword_info_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=5)
        ctk.CTkLabel(keyword_info_frame, text="Forbidden Keywords (one per line):", font=ctk.CTkFont(weight="bold")).pack(side="left")
        info_keyword_button = ctk.CTkButton(keyword_info_frame, text="?", width=28, height=28, command=lambda: InfoPopup(self, "Forbidden Keywords", "Enter words or phrases to be penalized, one per line. The filter is case-insensitive. E.g., 'promo', '.gg/', 'followers'")); info_keyword_button.pack(side="left", padx=5)
        self.spam_keywords_textbox = ctk.CTkTextbox(right_column_frame, height=120)
        self.spam_keywords_textbox.grid(row=3, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self.spam_widgets_to_toggle.append(self.spam_keywords_textbox)
        
        save_spam_button = ctk.CTkButton(tab, text="Save Settings", command=self.save_config)
        save_spam_button.grid(row=2, column=0, columnspan=2, padx=10, pady=10, sticky="ew")

    def toggle_spam_widgets_state(self):
        state = "normal" if self.spam_enabled_var.get() else "disabled"
        for widget in self.spam_widgets_to_toggle:
            if isinstance(widget, (ctk.CTkEntry, ctk.CTkTextbox)):
                widget.configure(state=state)

    def populate_models_list(self):
        for widget in self.models_list_frame.winfo_children(): widget.destroy()
        models = self.shared_settings.message_models
        for i, (key, format_str) in enumerate(models.items()):
            key_label = ctk.CTkLabel(self.models_list_frame, text=key, anchor="w", fg_color="gray20", corner_radius=5)
            key_label.grid(row=i, column=0, padx=(5,5), pady=(3,3), sticky="ew")
            
            format_display_text = (format_str[:80] + '...') if len(format_str) > 80 else format_str
            format_label = ctk.CTkLabel(self.models_list_frame, text=format_display_text, anchor="w", fg_color="gray20", corner_radius=5)
            format_label.grid(row=i, column=1, padx=(0,5), pady=(3,3), sticky="ew")

            edit_button = ctk.CTkButton(self.models_list_frame, text="Edit", width=60, command=lambda k=key, f=format_str: self.edit_model(k, f))
            edit_button.grid(row=i, column=2, padx=5, pady=(3,3))

            delete_button = ctk.CTkButton(self.models_list_frame, text="X", width=30, fg_color="#E74C3C", hover_color="#C0392B", command=lambda k=key: self.delete_model(k))
            delete_button.grid(row=i, column=3, padx=(0,5), pady=(3,3))

    def add_model(self): ModelEditorPopup(self, self.on_model_saved)
    def edit_model(self, key, format_str): ModelEditorPopup(self, self.on_model_saved, model_key=key, model_format=format_str)
    def delete_model(self, key):
        if key in self.shared_settings.message_models:
            del self.shared_settings.message_models[key]; self.save_config(); self.populate_models_list(); log('GUI', 'INFO', f"Model for key '{key}' deleted.")
    def on_model_saved(self, original_key, new_key, new_format):
        if original_key and original_key != new_key and original_key in self.shared_settings.message_models: del self.shared_settings.message_models[original_key]
        self.shared_settings.message_models[new_key] = new_format; self.save_config(); self.populate_models_list(); log('GUI', 'INFO', f"Model for key '{new_key}' saved.")
    def open_link(self, url):
        webbrowser.open_new_tab(url); log('GUI', 'INFO', f"Opening URL: {url}")
    def load_config(self):
        log('GUI', 'INFO', f"Loading settings from {CONFIG_FILE}"); self.config.read(CONFIG_FILE)
        if not self.config.has_section('server_config'): self.config.add_section('server_config')
        self.host_entry.insert(0, self.config.get('server_config', 'host', fallback='127.0.0.1')); self.port_entry.insert(0, self.config.get('server_config', 'port', fallback='80'))
        if self.config.getboolean('server_config', 'autostart', fallback=True): self.autostart_checkbox.select()
        else: self.autostart_checkbox.deselect()
        if not self.config.has_section('tts_config'): self.config.add_section('tts_config')
        lang_code = self.config.get('tts_config', 'lang', fallback='pt-br'); display_lang = next((f"{n} ({c})" for c, n in self.LANGUAGES.items() if c == lang_code), self.language_display_list[0])
        self.lang_combobox.set(display_lang)
        self.selector_key_entry.insert(0, self.config.get('tts_config', 'message_key', fallback='messageKey')); self.format_default_textbox.insert("1.0", self.config.get('tts_config', 'message_format_default', fallback='{chatname} said: {chatmessage}'))
        if not self.config.has_section('message_models'): self.config.add_section('message_models')
        self.shared_settings.message_models = dict(self.config.items('message_models'))
        if not self.shared_settings.message_models: self.shared_settings.message_models = {"message": "{chatname} said: {chatmessage}", "follow": "{chatname} is now following!", "donation": "{chatname} donated {amount}!"}
        if not self.config.has_section('spam_filter'): self.config.add_section('spam_filter')
        self.spam_enabled_var.set(self.config.getboolean('spam_filter', 'enabled', fallback=False))
        self.spam_threshold_entry.insert(0, self.config.get('spam_filter', 'threshold', fallback='10')); self.spam_link_score_entry.insert(0, self.config.get('spam_filter', 'score_link', fallback='10'))
        self.spam_caps_score_entry.insert(0, self.config.get('spam_filter', 'score_caps', fallback='3')); self.spam_symbols_score_entry.insert(0, self.config.get('spam_filter', 'score_symbols', fallback='3'))
        self.spam_keyword_score_entry.insert(0, self.config.get('spam_filter', 'score_keyword', fallback='5'))
        keywords_str = self.config.get('spam_filter', 'keywords', fallback='followers\nviews\npromo\n.gg/\nbit.ly'); self.spam_keywords_textbox.insert("1.0", keywords_str)
        regex_str = self.config.get('spam_filter', 'url_regex', fallback=DEFAULT_URL_REGEX.pattern)
        self.url_regex_entry.insert(0, regex_str)
        self.toggle_spam_widgets_state()
        self.save_config(log_output=False)
    def save_config(self, log_output=True):
        if log_output: log('GUI', 'INFO', "Saving all settings...")
        self.config.set('server_config', 'host', self.host_entry.get()); self.config.set('server_config', 'port', self.port_entry.get()); self.config.set('server_config', 'autostart', str(self.autostart_checkbox.get() == 1).lower())
        selected_display = self.lang_combobox.get(); lang_code = selected_display[selected_display.rfind('(') + 1:-1]
        self.config.set('tts_config', 'lang', lang_code); self.config.set('tts_config', 'message_key', self.selector_key_entry.get()); self.config.set('tts_config', 'message_format_default', self.format_default_textbox.get("1.0", "end-1c"))
        if 'message_models' in self.config: self.config.remove_section('message_models')
        self.config.add_section('message_models')
        for name, fmt in self.shared_settings.message_models.items(): self.config.set('message_models', name, fmt)
        self.config.set('spam_filter', 'enabled', str(self.spam_enabled_var.get()).lower()); self.config.set('spam_filter', 'threshold', self.spam_threshold_entry.get()); self.config.set('spam_filter', 'score_link', self.spam_link_score_entry.get())
        self.config.set('spam_filter', 'score_caps', self.spam_caps_score_entry.get()); self.config.set('spam_filter', 'score_symbols', self.spam_symbols_score_entry.get()); self.config.set('spam_filter', 'score_keyword', self.spam_keyword_score_entry.get())
        self.config.set('spam_filter', 'keywords', self.spam_keywords_textbox.get("1.0", "end-1c"))
        self.config.set('spam_filter', 'url_regex', self.url_regex_entry.get())

        with open(CONFIG_FILE, 'w', encoding='utf-8') as configfile: self.config.write(configfile)
        
        self.shared_settings.host = self.host_entry.get(); self.shared_settings.port = int(self.port_entry.get()); self.shared_settings.lang = lang_code; self.shared_settings.message_key = self.selector_key_entry.get()
        self.shared_settings.message_format_default = self.format_default_textbox.get("1.0", "end-1c"); self.shared_settings.spam_filter_enabled = self.spam_enabled_var.get()
        self.shared_settings.spam_threshold = int(self.spam_threshold_entry.get() or 0); self.shared_settings.spam_score_link = int(self.spam_link_score_entry.get() or 0)
        self.shared_settings.spam_score_caps = int(self.spam_caps_score_entry.get() or 0); self.shared_settings.spam_score_symbols = int(self.spam_symbols_score_entry.get() or 0)
        self.shared_settings.spam_score_keyword = int(self.spam_keyword_score_entry.get() or 0)
        self.shared_settings.spam_keywords = [k.strip().lower() for k in self.spam_keywords_textbox.get("1.0", "end-1c").split('\n') if k.strip()]
        try:
            self.shared_settings.url_regex_compiled = re.compile(self.url_regex_entry.get())
        except re.error as e:
            log('GUI', 'ERROR', f"Invalid Regex! Using default. Error: {e}")
            self.shared_settings.url_regex_compiled = DEFAULT_URL_REGEX
        
        self.update_url_displays()
        if log_output: log('GUI', 'INFO', "Settings saved and applied live.")
    def copy_to_clipboard(self, text):
        self.clipboard_clear(); self.clipboard_append(text); self.update(); log('GUI', 'INFO', f"Text '{text}' copied to clipboard.")
    def log_to_gui(self, level, module, message):
        self.log_textbox.configure(state="normal"); color_tag="INFO"
        if level=="ERROR":color_tag="ERROR"
        elif level=="WARN":color_tag="WARN"
        elif module=="SERVER":color_tag="SERVER"
        elif module=="GUI":color_tag="GUI"
        log_line=f"[{level.upper()}] [{module.upper()}] {message}\n"; start_index=self.log_textbox.index("end-1c"); self.log_textbox.insert("end",log_line); end_index=self.log_textbox.index("end-1c")
        self.log_textbox.tag_add(color_tag,start_index,end_index); self.log_textbox.see("end"); self.log_textbox.configure(state="disabled")
    def process_log_queue(self):
        try:
            while True: level,module,message=log_queue.get_nowait(); self.log_to_gui(level,module,message)
        except queue.Empty:pass
        self.after(200,self.process_log_queue)
    def toggle_server(self):
        if self.is_server_running:self.stop_server()
        else:self.start_server()
    def start_server(self):
        log('GUI','INFO',"Attempting to start the server..."); self.save_config(log_output=False)
        try:
            host=self.shared_settings.host; port=self.shared_settings.port; flask_app=create_flask_app(self.shared_settings)
            self.http_server=make_server(host,port,flask_app,threaded=True); self.server_thread=threading.Thread(target=self.http_server.serve_forever); self.server_thread.daemon=True; self.server_thread.start()
            self.is_server_running=True; self.update_gui_for_server_state(); log('SERVER','INFO',f"Server started on http://{host}:{port}")
        except Exception as e:
            log('GUI','ERROR',f"Failed to start server: {e}"); self.is_server_running=False; self.update_gui_for_server_state()
    def stop_server(self):
        if not self.is_server_running:return
        log('GUI','INFO',"Stopping the server...")
        try:
            shutdown_thread=threading.Thread(target=self.http_server.shutdown); shutdown_thread.daemon=True; shutdown_thread.start()
            self.is_server_running=False; self.update_gui_for_server_state(); log('SERVER','INFO',"Server stopped.")
        except Exception as e: log('GUI','ERROR',f"Error stopping server: {e}")
    def update_gui_for_server_state(self):
        if self.is_server_running:
            self.status_label.configure(text="Status: Active",text_color="#2ECC71"); self.start_stop_button.configure(text="Stop Server"); self.speak_button.configure(state="normal")
            self.host_entry.configure(state="disabled"); self.port_entry.configure(state="disabled")
        else:
            self.status_label.configure(text="Status: Stopped",text_color="#F39C12"); self.start_stop_button.configure(text="Start Server"); self.speak_button.configure(state="disabled")
            self.host_entry.configure(state="normal"); self.port_entry.configure(state="normal")
    def update_url_displays(self):
        host = self.host_entry.get(); port = self.port_entry.get(); display_host = host if host != '0.0.0.0' else '127.0.0.1'
        audio_source_url = f"http://{display_host}:{port}"; post_url = f"http://{display_host}:{port}/speak"
        for entry, url in [(self.audio_source_url_entry, audio_source_url), (self.post_url_entry, post_url)]:
            entry.configure(state="normal"); entry.delete(0, "end"); entry.insert(0, url); entry.configure(state="readonly")
    def send_custom_json_request(self):
        log('GUI','INFO',"Sending custom JSON request..."); self.save_config(log_output=False); url=self.post_url_entry.get(); json_string=self.json_example_textbox.get("1.0","end-1c")
        try: payload=json.loads(json_string)
        except json.JSONDecodeError as e: log('GUI','ERROR',f"Invalid JSON in text box: {e}"); return
        try:
            response=requests.post(url,json=payload,timeout=5)
            if response.status_code==200: log('GUI','INFO',"Custom JSON sent successfully! Check the Audio Source Page.")
            else: log('GUI','WARN',f"Server responded with error: {response.status_code} - {response.text}")
        except requests.exceptions.RequestException as e: log('GUI','ERROR',f"Failed to send custom request: {e}")
    def on_closing(self):
        if self.is_server_running:self.stop_server()
        self.destroy()

if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()