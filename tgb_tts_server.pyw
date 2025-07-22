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
# Determine the base path for bundled executables (PyInstaller) or normal scripts.
if getattr(sys, 'frozen', False):
    BASE_PATH = os.path.dirname(sys.executable)
else:
    BASE_PATH = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_PATH, 'config.ini')
DEFAULT_URL_REGEX = re.compile(r'(https?://[^\s]+|www\.[^\s]+|[^\s]+\.(com|net|org|io|gg|ly|tv|shop|xyz))')
FALLBACK_CONDITION_KEY = "__FALLBACK__"

# --- DATA CLASSES ---
class SharedSettings:
    """A class to hold live, mutable settings shared between the GUI and the Flask server."""
    def __init__(self):
        self.host = "127.0.0.1"; self.port = 80; self.lang = "en"
        self.ordered_models = [
            {"condition": "chatmessage", "format": "{chatname} said: {chatmessage}", "spam_policy": "global"},
            {"condition": "donation:true", "format": "{chatname} donated {amount}!", "spam_policy": "ignore"},
            {"condition": FALLBACK_CONDITION_KEY, "format": "Message received.", "spam_policy": "ignore"}
        ]
        self.spam_filter_enabled = False; self.spam_threshold = 10; self.spam_user_whitelist = []
        self.spam_block_links = True; self.spam_max_symbols = 4; self.spam_max_text_length = 200
        self.spam_keywords = []; self.spam_score_keyword = 5; self.spam_score_repeated_chars = 4
        self.url_regex_compiled = DEFAULT_URL_REGEX

HTML_CONTENT = """
<!DOCTYPE html><html><head><title>TGB TTS Player</title></head><body><audio id="tts-audio-player" autoplay></audio><script>const audioPlayer=document.getElementById("tts-audio-player"),messageQueue=[];let isPlaying=!1;function log(e){console.log(`[TTS Player] ${e}`)}audioPlayer.addEventListener("ended",()=>{isPlaying=!1,playNextInQueue()}),audioPlayer.addEventListener("error",e=>{console.error("Audio error:",e),isPlaying=!1,playNextInQueue()});function playNextInQueue(){if(!isPlaying&&messageQueue.length>0){isPlaying=!0;const e=messageQueue.shift(),t=`/audio.mp3?text=${encodeURIComponent(e)}`;audioPlayer.src=t;const o=audioPlayer.play();void 0!==o&&o.catch(e=>{console.error("Playback error:",e),isPlaying=!1,playNextInQueue()})}}const eventSource=new EventSource("/stream");eventSource.onmessage=function(e){const t=e.data;t&&(messageQueue.push(t),playNextInQueue())},eventSource.onerror=function(e){console.error("EventSource error:",e)};</script></body></html>
"""

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# --- POPUP WINDOWS ---
class InfoPopup(ctk.CTkToplevel):
    """A simple, generic popup window to display information."""
    def __init__(self, parent, title, message):
        """Initializes the information popup."""
        super().__init__(parent); self.transient(parent); self.grab_set(); self.title(title); self.resizable(False, False)
        self.grid_columnconfigure(0, weight=1)
        message_label = ctk.CTkLabel(self, text=message, wraplength=420, justify="left", font=ctk.CTkFont(size=13)); message_label.grid(row=0, column=0, padx=20, pady=20, sticky="ew")
        ok_button = ctk.CTkButton(self, text="OK", command=self.destroy, width=100); ok_button.grid(row=1, column=0, pady=(0, 20)); self.wait_window()

class ModelEditorPopup(ctk.CTkToplevel):
    """A popup window for adding or editing a message model and its spam policy."""
    def __init__(self, parent, callback, model_data, index=None, is_fallback=False):
        """
        Initializes the model editor popup.
        
        Args:
            parent: The parent window.
            callback: The function to call when the save button is pressed.
            model_data (dict): The dictionary containing the model's data.
            index (int, optional): The index of the model being edited. None if new.
            is_fallback (bool): True if the model is the special fallback model.
        """
        super().__init__(parent); self.transient(parent); self.grab_set(); self.callback = callback; self.index = index; self.is_fallback = is_fallback
        self.title("Edit Message Model"); self.grid_columnconfigure(0, weight=1)
        
        main_frame = ctk.CTkFrame(self, fg_color="transparent"); main_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew"); main_frame.grid_columnconfigure(1, weight=1)
        
        ctk.CTkLabel(main_frame, text="Condition:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.condition_entry = ctk.CTkEntry(main_frame); self.condition_entry.grid(row=0, column=1, padx=10, pady=10, sticky="ew"); self.condition_entry.insert(0, model_data.get("condition", ""))
        ctk.CTkLabel(main_frame, text="Message Format:").grid(row=1, column=0, padx=10, pady=5, sticky="nw")
        self.format_textbox = ctk.CTkTextbox(main_frame, height=100, wrap="word"); self.format_textbox.grid(row=1, column=1, padx=10, pady=(5,10), sticky="ew"); self.format_textbox.insert("1.0", model_data.get("format", ""))

        spam_policy = model_data.get("spam_policy", "global")
        ctk.CTkLabel(main_frame, text="Spam Filter Policy:").grid(row=2, column=0, padx=10, pady=10, sticky="w")
        self.spam_policy_combo = ctk.CTkComboBox(main_frame, values=["global", "ignore", "custom"], command=self.toggle_custom_spam_frame, state="readonly"); self.spam_policy_combo.grid(row=2, column=1, padx=10, pady=10, sticky="ew"); self.spam_policy_combo.set(spam_policy)
        
        if self.is_fallback: self.condition_entry.configure(state="disabled")

        self.custom_spam_frame = ctk.CTkFrame(self, fg_color="transparent"); self.custom_spam_frame.grid(row=1, column=0, padx=10, pady=10, sticky="nsew"); self.custom_spam_frame.grid_columnconfigure(0, weight=1)
        
        spam_settings = model_data.get("spam_settings", {})
        
        direct_rules_frame = ctk.CTkFrame(self.custom_spam_frame); direct_rules_frame.grid(row=0, column=0, sticky="ew"); direct_rules_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(direct_rules_frame, text="Direct Blocking Rules", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=2, padx=10, pady=(10,5), sticky="w")
        ctk.CTkLabel(direct_rules_frame, text="Block Links:").grid(row=1, column=0, padx=10, pady=5, sticky="w"); self.custom_block_links_combo = ctk.CTkComboBox(direct_rules_frame, values=["Yes", "No"], state="readonly"); self.custom_block_links_combo.grid(row=1, column=1, padx=10, pady=5, sticky="ew"); self.custom_block_links_combo.set(spam_settings.get("block_links", "Yes"))
        ctk.CTkLabel(direct_rules_frame, text="Max Symbols:").grid(row=2, column=0, padx=10, pady=5, sticky="w"); self.custom_max_symbols_entry = ctk.CTkEntry(direct_rules_frame); self.custom_max_symbols_entry.grid(row=2, column=1, padx=10, pady=5, sticky="ew"); self.custom_max_symbols_entry.insert(0, spam_settings.get("max_symbols", "4"))
        ctk.CTkLabel(direct_rules_frame, text="Max Text Length:").grid(row=3, column=0, padx=10, pady=5, sticky="w"); self.custom_max_length_entry = ctk.CTkEntry(direct_rules_frame); self.custom_max_length_entry.grid(row=3, column=1, padx=10, pady=5, sticky="ew"); self.custom_max_length_entry.insert(0, spam_settings.get("max_text_length", "200"))
        ctk.CTkLabel(direct_rules_frame, text="User Whitelist (one per line):").grid(row=4, column=0, padx=10, pady=5, sticky="nw"); self.custom_whitelist_textbox = ctk.CTkTextbox(direct_rules_frame, height=60); self.custom_whitelist_textbox.grid(row=4, column=1, padx=10, pady=(5,10), sticky="ew"); self.custom_whitelist_textbox.insert("1.0", spam_settings.get("user_whitelist", ""))

        score_rules_frame = ctk.CTkFrame(self.custom_spam_frame); score_rules_frame.grid(row=1, column=0, sticky="ew", pady=(10,0)); score_rules_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(score_rules_frame, text="Score-Based Filtering", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=2, padx=10, pady=(10,5), sticky="w")
        ctk.CTkLabel(score_rules_frame, text="Spam Score Threshold:").grid(row=1, column=0, padx=10, pady=5, sticky="w"); self.custom_threshold_entry = ctk.CTkEntry(score_rules_frame); self.custom_threshold_entry.grid(row=1, column=1, padx=10, pady=5, sticky="ew"); self.custom_threshold_entry.insert(0, spam_settings.get("threshold", "10"))
        ctk.CTkLabel(score_rules_frame, text="Score for Repeated Chars:").grid(row=2, column=0, padx=10, pady=5, sticky="w"); self.custom_repeated_chars_entry = ctk.CTkEntry(score_rules_frame); self.custom_repeated_chars_entry.grid(row=2, column=1, padx=10, pady=5, sticky="ew"); self.custom_repeated_chars_entry.insert(0, spam_settings.get("score_repeated_chars", "4"))
        ctk.CTkLabel(score_rules_frame, text="Score for Forbidden Keyword:").grid(row=3, column=0, padx=10, pady=5, sticky="w"); self.custom_keyword_score_entry = ctk.CTkEntry(score_rules_frame); self.custom_keyword_score_entry.grid(row=3, column=1, padx=10, pady=5, sticky="ew"); self.custom_keyword_score_entry.insert(0, spam_settings.get("score_keyword", "5"))
        ctk.CTkLabel(score_rules_frame, text="Forbidden Keywords (one per line):").grid(row=4, column=0, padx=10, pady=5, sticky="nw"); self.custom_keywords_textbox = ctk.CTkTextbox(score_rules_frame, height=80); self.custom_keywords_textbox.grid(row=4, column=1, padx=10, pady=(5,10), sticky="ew"); self.custom_keywords_textbox.insert("1.0", spam_settings.get("keywords", ""))
        
        self.toggle_custom_spam_frame()

        button_frame = ctk.CTkFrame(self, fg_color="transparent"); button_frame.grid(row=2, column=0, columnspan=2, pady=10)
        self.save_button = ctk.CTkButton(button_frame, text="Save", command=self.on_save); self.save_button.pack(side="left", padx=10)
        self.cancel_button = ctk.CTkButton(button_frame, text="Cancel", command=self.destroy, fg_color="#E74C3C", hover_color="#C0392B"); self.cancel_button.pack(side="left", padx=10)
        self.wait_window()
    
    def toggle_custom_spam_frame(self, event=None):
        """Shows or hides the custom spam settings frame based on the combobox selection."""
        if self.spam_policy_combo.get() == "custom":
            self.custom_spam_frame.grid()
            self.geometry("600x800")
        else:
            self.custom_spam_frame.grid_remove()
            self.geometry("600x320")

    def on_save(self):
        """Validates input, packages model data, and calls the parent callback function."""
        new_condition = self.condition_entry.get().strip(); new_format = self.format_textbox.get("1.0", "end-1c")
        if not self.is_fallback and not new_condition: return
        
        new_model = {"condition": new_condition if not self.is_fallback else FALLBACK_CONDITION_KEY, "format": new_format, "spam_policy": self.spam_policy_combo.get()}
        
        if new_model["spam_policy"] == "custom":
            new_model["spam_settings"] = {
                "block_links": self.custom_block_links_combo.get(), "max_symbols": self.custom_max_symbols_entry.get(),
                "max_text_length": self.custom_max_length_entry.get(),
                "threshold": self.custom_threshold_entry.get(), "user_whitelist": self.custom_whitelist_textbox.get("1.0", "end-1c"),
                "score_repeated_chars": self.custom_repeated_chars_entry.get(), "score_keyword": self.custom_keyword_score_entry.get(),
                "keywords": self.custom_keywords_textbox.get("1.0", "end-1c")
            }
        self.callback(self.index, new_model); self.destroy()

# --- UTILITY AND SERVER LOGIC ---
log_queue = queue.Queue()

def log(module_name: str, level: str, message: str):
    """Puts a log message into the thread-safe queue to be processed by the GUI."""
    log_queue.put((level, module_name, message))

def check_condition(condition_str: str, data: dict) -> bool:
    """
    Checks if a JSON payload (`data`) meets a given condition string.

    Args:
        condition_str (str): The condition to check (e.g., "key:value" or "key").
        data (dict): The incoming JSON data.
    
    Returns:
        bool: True if the condition is met, False otherwise.
    """
    if ":" in condition_str:
        key, expected_value_str = condition_str.split(":", 1)
        if key not in data: return False
        actual_value = data[key]
        if expected_value_str.lower() == 'true': return actual_value is True
        if expected_value_str.lower() == 'false': return actual_value is False
        return str(actual_value) == expected_value_str
    else: return condition_str in data

def analyze_message_for_spam(text: str, settings):
    """
    Analyzes a message for spam based on a given set of rules.
    
    This function applies direct blocking rules first. If none are met, it calculates
    a cumulative score based on score-based rules.
    
    Args:
        text (str): The message content to analyze.
        settings (SharedSettings): An object containing the spam filter rules to apply.
        
    Returns:
        tuple[float, list[str]]: A tuple containing the final spam score and a list of reasons.
                                 A score of float('inf') indicates an instant block.
    """
    score = 0; reasons = []; normalized_text = unicodedata.normalize('NFKC', text).lower()
    
    # Direct blocking rules (return infinite score for an instant block)
    if len(text) > settings.spam_max_text_length: return float('inf'), [f"Exceeded Max Length ({len(text)}/{settings.spam_max_text_length})"]
    if settings.spam_block_links and settings.url_regex_compiled.search(normalized_text): return float('inf'), ["Link Detected"]
    symbol_count = sum(1 for c in text if not c.isalnum() and not c.isspace())
    if symbol_count > settings.spam_max_symbols: return float('inf'), [f"Exceeded Max Symbols ({symbol_count}/{settings.spam_max_symbols})"]
    
    # Score-based rules
    if settings.spam_score_repeated_chars > 0 and re.search(r'(.)\1{4,}', text): score += settings.spam_score_repeated_chars; reasons.append("Repeated Chars")
    for keyword in settings.spam_keywords:
        if keyword in normalized_text: score += settings.spam_score_keyword; reasons.append(f"Keyword: '{keyword}'")
    return score, reasons

def create_flask_app(shared_settings: SharedSettings):
    """
    Creates and configures the Flask application instance.
    
    Args:
        shared_settings (SharedSettings): The live settings object.
        
    Returns:
        Flask: The configured Flask app.
    """
    app = Flask(__name__); CORS(app); logging.getLogger('werkzeug').setLevel(logging.ERROR); message_queue = queue.Queue()
    
    @app.route('/')
    def audio_source_page():
        """Serves the main HTML page with the audio player."""
        return Response(HTML_CONTENT, mimetype='text/html')

    @app.route('/speak', methods=['POST'])
    def receive_message():
        """Main endpoint to receive JSON alerts, process them, and queue them for TTS."""
        data = request.get_json()
        if not data: log('SERVER', 'WARN', f"POST with invalid data from {request.remote_addr}"); return jsonify({"status": "error", "message": "Invalid JSON"}), 400
        log('SERVER', 'INFO', f"JSON Received: {data}")
        
        matched_model = None
        for model in shared_settings.ordered_models:
            if model["condition"] == FALLBACK_CONDITION_KEY: matched_model = model; log('SERVER', 'INFO', "No specific condition met. Evaluating fallback."); break
            if check_condition(model["condition"], data): matched_model = model; log('SERVER', 'INFO', f"Condition '{model['condition']}' met. Evaluating model."); break
        
        if not matched_model: log('SERVER', 'WARN', "No model matched and no fallback found."); return jsonify({"status": "error", "message": "No matching model"}), 400

        spam_policy = matched_model.get("spam_policy", "global")
        if shared_settings.spam_filter_enabled and 'chatmessage' in data and spam_policy != "ignore":
            active_spam_settings = shared_settings
            if spam_policy == "custom":
                log('SERVER', 'INFO', "Using CUSTOM spam filter for this condition.")
                custom_settings = SharedSettings(); custom_settings.spam_user_whitelist = [u.strip() for u in matched_model["spam_settings"].get("user_whitelist", "").split('\n') if u.strip()]
                if data.get('chatname', '').lower() in [u.lower() for u in custom_settings.spam_user_whitelist]: log('SERVER', 'INFO', f"User '{data.get('chatname')}' is in CUSTOM whitelist. Skipping spam check."); spam_policy = "ignore"
                else:
                    custom_settings.spam_block_links = matched_model["spam_settings"].get("block_links", "Yes").lower() == 'yes'
                    custom_settings.spam_max_symbols = int(matched_model["spam_settings"].get("max_symbols", 4))
                    custom_settings.spam_max_text_length = int(matched_model["spam_settings"].get("max_text_length", 200))
                    custom_settings.spam_threshold = int(matched_model["spam_settings"].get("threshold", 10))
                    custom_settings.spam_score_repeated_chars = int(matched_model["spam_settings"].get("score_repeated_chars", 4)); custom_settings.spam_score_keyword = int(matched_model["spam_settings"].get("score_keyword", 5))
                    custom_settings.spam_keywords = [k.strip().lower() for k in matched_model["spam_settings"].get("keywords", "").split('\n') if k.strip()]; active_spam_settings = custom_settings
            else: # Global policy
                log('SERVER', 'INFO', "Using GLOBAL spam filter for this condition.")
                if data.get('chatname', '').lower() in [u.lower() for u in shared_settings.spam_user_whitelist]: log('SERVER', 'INFO', f"User '{data.get('chatname')}' is in GLOBAL whitelist. Skipping spam check."); spam_policy = "ignore"
            
            if spam_policy != "ignore":
                score, reasons = analyze_message_for_spam(data['chatmessage'], active_spam_settings)
                if score >= active_spam_settings.spam_threshold:
                    log('SERVER', 'WARN', f"SPAM BLOCKED (Score: {score}/{active_spam_settings.spam_threshold}) by: {', '.join(reasons)}. Message: '{data['chatmessage']}'"); return jsonify({"status": "spam_detected", "score": score, "reasons": reasons}), 200
        
        try:
            final_message = matched_model["format"].format(**data).strip()
            log('SERVER', 'INFO', f"Formatted Message: '{final_message}'")
            if final_message: message_queue.put(final_message); return jsonify({"status": "success", "message": "Message queued."})
            else: log('SERVER', 'INFO', "Formatted message is empty. Nothing to speak."); return jsonify({"status": "success", "message": "Message resulted in empty text, not queued."})
        except KeyError as e: error_msg = f"Key {e} not found in JSON for the selected format."; log('SERVER', 'ERROR', error_msg); return jsonify({"status": "error", "message": error_msg}), 400
    
    @app.route('/stream')
    def stream_messages():
        """Provides an event stream for the client to receive queued messages."""
        client_addr = request.remote_addr
        def event_stream():
            log('SERVER', 'INFO', f"Client {client_addr} connected.")
            try:
                while True: yield f"data: {message_queue.get()}\n\n"
            except GeneratorExit: log('SERVER', 'INFO', f"Client {client_addr} disconnected.")
        return Response(event_stream(), mimetype='text/event-stream')
    
    @app.route('/audio.mp3')
    def generate_audio():
        """Generates a TTS audio file from the provided text parameter."""
        text = request.args.get('text', 'No text'); lang = shared_settings.lang; log('SERVER', 'INFO', f"Generating audio for: '{text}' in '{lang}'")
        try: tts = gTTS(text=text, lang=lang); fp = io.BytesIO(); tts.write_to_fp(fp); fp.seek(0); return send_file(fp, mimetype='audio/mpeg')
        except Exception as e: log('SERVER', 'ERROR', f"gTTS Error: {e}"); return "Error generating audio", 500
    
    return app

# --- MAIN GUI APPLICATION ---
class App(ctk.CTk):
    """The main GUI application class."""
    def __init__(self):
        """Initializes the main application window and its components."""
        super().__init__(); self.title("TGB TTS Server - v0.2 (BETA)"); self.geometry("1100x750")
        self.LANGUAGES = {'en':'English (US)','pt-br':'Brazilian Portuguese','en-uk':'English (UK)','en-au':'English (Australia)','en-in':'English (India)','pt':'Portuguese (Portugal)','es':'Spanish (Spain)','es-mx':'Spanish (Mexico)','fr':'French','de':'German','it':'Italian','ja':'Japanese','ko':'Korean','ru':'Russian','zh-cn':'Chinese (Mandarin/China)','zh-tw':'Chinese (Mandarin/Taiwan)'}
        self.language_display_list = [f"{name} ({code})" for code, name in self.LANGUAGES.items()]
        self.config = configparser.ConfigParser(); self.shared_settings = SharedSettings(); self.server_thread = None; self.http_server = None; self.is_server_running = False
        
        self.initialize_config_file()
        
        self.grid_columnconfigure(0, weight=1); self.grid_rowconfigure(0, weight=1)
        self.tab_view = ctk.CTkTabview(self, anchor="w"); self.tab_view.grid(row=0, column=0, padx=10, pady=(10, 0), sticky="nsew")
        self.tab_view.add("Dashboard"); self.tab_view.add("Settings"); self.tab_view.add("Message Models"); self.tab_view.add("Spam Filter")
        
        self.bottom_frame = ctk.CTkFrame(self, fg_color="transparent"); self.bottom_frame.grid(row=1, column=0, padx=10, pady=(5, 10), sticky="ew"); self.bottom_frame.grid_columnconfigure(0, weight=1)
        credits_sub_frame = ctk.CTkFrame(self.bottom_frame, fg_color="transparent"); credits_sub_frame.pack(side="right")
        github_label = ctk.CTkLabel(credits_sub_frame, text="github.com/tonygabarron/TgbTtsServer", text_color="#3498DB", cursor="hand2", font=ctk.CTkFont(size=11)); github_label.pack(side="left", padx=(0,10))
        github_label.bind("<Button-1>", lambda e: self.open_link("https://github.com/tonygabarron/TgbTtsServer"))
        donate_button = ctk.CTkButton(credits_sub_frame, text="☕ Support the Project", command=lambda: self.open_link("https://ko-fi.com/tonygabarron"), fg_color="#E67E22", hover_color="#D35400", height=24, width=140); donate_button.pack(side="left")
        
        self.populate_dashboard_tab(self.tab_view.tab("Dashboard")); self.populate_settings_tab(self.tab_view.tab("Settings")); self.populate_models_tab(self.tab_view.tab("Message Models")); self.populate_spam_filter_tab(self.tab_view.tab("Spam Filter"))
        self.load_config(); self.populate_models_list(); self.update_url_displays(); self.process_log_queue()
        
        if self.config.getboolean('server_config', 'autostart', fallback=True): log('GUI', 'INFO', "Autostart is enabled. Starting server..."); self.after(100, self.start_server)
    
    def initialize_config_file(self):
        """Creates a default config.ini file if one doesn't exist."""
        if not os.path.exists(CONFIG_FILE):
            log('GUI', 'INFO', f"Config file not found. Creating a new one at {CONFIG_FILE}")
            default_config = configparser.ConfigParser()
            default_config['server_config'] = {'host': '127.0.0.1', 'port': '80', 'autostart': 'true'}; default_config['tts_config'] = {'lang': 'pt-br'}
            ordered_models = [{"condition": "chatmessage", "format": "{chatname} said: {chatmessage}", "spam_policy": "global"},{"condition": "donation:true", "format": "{chatname} donated {amount}!", "spam_policy": "ignore"},{"condition": FALLBACK_CONDITION_KEY, "format": "Message received.", "spam_policy": "ignore"}]
            default_config['message_logic'] = {'models_json': json.dumps(ordered_models)}
            default_config['spam_filter'] = {'enabled': 'false', 'block_links': 'yes', 'max_symbols': '4', 'max_text_length': '200', 'threshold': '10', 'user_whitelist': '', 'score_keyword': '5','score_repeated_chars':'4', 'keywords': 'followers\nviews\npromo\n.gg/\nbit.ly', 'url_regex': DEFAULT_URL_REGEX.pattern}
            with open(CONFIG_FILE, 'w', encoding='utf-8') as configfile: default_config.write(configfile)

    def populate_dashboard_tab(self, tab):
        """Sets up the widgets for the Dashboard tab."""
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
        test_payload = {"chatname": "TestUser", "chatmessage": "1,2,3. Can you hear me?"}; self.json_example_textbox.insert("1.0", json.dumps(test_payload, indent=4))
        log_frame = ctk.CTkFrame(tab); log_frame.grid(row=1, column=0, padx=10, pady=(0,10), sticky="nsew"); log_frame.grid_rowconfigure(1, weight=1); log_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(log_frame, text="Server Logs", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, pady=(5,5))
        self.log_textbox = ctk.CTkTextbox(log_frame, state="disabled", wrap="word"); self.log_textbox.grid(row=1, column=0, padx=10, pady=(0,10), sticky="nsew")
        self.log_textbox.tag_config("INFO", foreground="#FFFFFF"); self.log_textbox.tag_config("WARN", foreground="#F39C12"); self.log_textbox.tag_config("ERROR", foreground="#E74C3C"); self.log_textbox.tag_config("GUI", foreground="#AAAAAA"); self.log_textbox.tag_config("SERVER", foreground="#3498DB")

    def populate_settings_tab(self, tab):
        """Sets up the widgets for the Settings tab."""
        tab.grid_columnconfigure(0, weight=1)
        config_frame = ctk.CTkFrame(tab); config_frame.grid(row=0, column=0, padx=10, pady=10, sticky="new"); config_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(config_frame, text="General Settings", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, columnspan=2, pady=(5,10), sticky="w")
        ctk.CTkLabel(config_frame, text="Host:").grid(row=1, column=0, padx=10, pady=5, sticky="w"); self.host_entry = ctk.CTkEntry(config_frame); self.host_entry.grid(row=1, column=1, padx=10, pady=5, sticky="ew")
        ctk.CTkLabel(config_frame, text="Port:").grid(row=2, column=0, padx=10, pady=5, sticky="w"); self.port_entry = ctk.CTkEntry(config_frame); self.port_entry.grid(row=2, column=1, padx=10, pady=5, sticky="ew")
        ctk.CTkLabel(config_frame, text="Language (Voice):").grid(row=3, column=0, padx=10, pady=5, sticky="w"); self.lang_combobox = ctk.CTkComboBox(config_frame, values=self.language_display_list, state="readonly"); self.lang_combobox.grid(row=3, column=1, padx=10, pady=5, sticky="ew")
        self.autostart_checkbox = ctk.CTkCheckBox(config_frame, text="Start server on launch"); self.autostart_checkbox.grid(row=4, column=0, columnspan=2, padx=10, pady=10, sticky="w")
        save_settings_button = ctk.CTkButton(config_frame, text="Save Settings", command=self.save_config); save_settings_button.grid(row=5, column=0, columnspan=2, padx=10, pady=10, sticky="ew")

    def populate_models_tab(self, tab):
        """Sets up the widgets for the Message Models tab."""
        tab.grid_rowconfigure(1, weight=1); tab.grid_columnconfigure(0, weight=1)
        models_frame = ctk.CTkFrame(tab); models_frame.grid(row=0, column=0, rowspan=4, padx=10, pady=10, sticky="nsew"); models_frame.grid_columnconfigure(0, weight=1); models_frame.grid_rowconfigure(1, weight=1)
        headers_frame = ctk.CTkFrame(models_frame, fg_color="transparent"); headers_frame.grid(row=0, column=0, sticky="ew", pady=(0,5)); headers_frame.grid_columnconfigure(1, weight=1); headers_frame.grid_columnconfigure(2, weight=1); headers_frame.grid_columnconfigure(3, weight=2)
        ctk.CTkLabel(headers_frame, text="Priority", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=(5,0), sticky="w")
        cond_header_frame = ctk.CTkFrame(headers_frame, fg_color="transparent"); cond_header_frame.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(cond_header_frame, text="Condition", font=ctk.CTkFont(weight="bold")).pack(side="left"); info_button = ctk.CTkButton(cond_header_frame, text="?", width=28, height=28, command=self.show_condition_info); info_button.pack(side="left", padx=5)
        ctk.CTkLabel(headers_frame, text="Spam Policy", font=ctk.CTkFont(weight="bold")).grid(row=0, column=2, padx=5, sticky="w")
        ctk.CTkLabel(headers_frame, text="Message Format", font=ctk.CTkFont(weight="bold")).grid(row=0, column=3, padx=5, sticky="w")
        self.models_list_frame = ctk.CTkScrollableFrame(models_frame, fg_color="transparent"); self.models_list_frame.grid(row=1, column=0, pady=0, sticky="nsew"); self.models_list_frame.grid_columnconfigure(1, weight=1); self.models_list_frame.grid_columnconfigure(2, weight=1); self.models_list_frame.grid_columnconfigure(3, weight=2)
        add_model_button = ctk.CTkButton(models_frame, text="Add New Model", command=self.add_model); add_model_button.grid(row=2, column=0, padx=0, pady=(10,0), sticky="ew")

    def show_condition_info(self):
        """Displays a popup with information on how conditions work."""
        message = ("Conditions are checked from top to bottom. The first one that matches the incoming JSON is used.\n\nSyntax:\n1. key:value\n   - Checks if the key exists AND its value matches.\n   - Example: 'donation:true' or 'type:follow'\n\n2. key\n   - Checks only if the key exists, regardless of value.\n   - Example: 'chatmessage'\n\nEach condition can have its own spam filter policy (Global, Ignore, or Custom), which you can set by clicking 'Edit'.")
        InfoPopup(self, "How Conditions Work", message)
    
    def populate_spam_filter_tab(self, tab):
        """Sets up the widgets for the Spam Filter tab."""
        tab.grid_columnconfigure((0, 1), weight=1); tab.grid_rowconfigure(3, weight=1)
        left_column_frame = ctk.CTkFrame(tab); left_column_frame.grid(row=0, column=0, rowspan=4, padx=(10, 5), pady=10, sticky="nsew"); left_column_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(left_column_frame, text="Global Spam & Security Filter", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, columnspan=3, pady=(5,0), padx=10, sticky="w")
        self.spam_enabled_var = ctk.BooleanVar(); self.spam_enabled_checkbox = ctk.CTkCheckBox(left_column_frame, text="Enable Spam Filter Globally", variable=self.spam_enabled_var, command=self.toggle_spam_widgets_state); self.spam_enabled_checkbox.grid(row=1, column=0, columnspan=3, pady=(5,15), padx=10, sticky="w")
        
        rules_frame = ctk.CTkFrame(left_column_frame); rules_frame.grid(row=2, column=0, columnspan=3, padx=10, pady=5, sticky="ew"); rules_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(rules_frame, text="Global Spam Rules", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=3, pady=5, padx=10, sticky="w")
        ctk.CTkLabel(rules_frame, text="Block Links:").grid(row=1, column=0, padx=10, pady=8, sticky="w"); self.spam_block_links_combo = ctk.CTkComboBox(rules_frame, values=["Yes", "No"], state="readonly"); self.spam_block_links_combo.grid(row=1, column=1, padx=5, pady=8, sticky="ew"); self.spam_block_links_combo.set("Yes")
        ctk.CTkLabel(rules_frame, text="Max Symbols:").grid(row=2, column=0, padx=10, pady=8, sticky="w"); self.spam_max_symbols_entry = ctk.CTkEntry(rules_frame); self.spam_max_symbols_entry.grid(row=2, column=1, padx=5, pady=8, sticky="ew")
        ctk.CTkLabel(rules_frame, text="Max Text Length:").grid(row=3, column=0, padx=10, pady=8, sticky="w"); self.spam_max_length_entry = ctk.CTkEntry(rules_frame); self.spam_max_length_entry.grid(row=3, column=1, padx=5, pady=8, sticky="ew")

        scores_frame = ctk.CTkFrame(left_column_frame); scores_frame.grid(row=3, column=0, columnspan=3, padx=10, pady=5, sticky="ew"); scores_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(scores_frame, text="Global Spam Score Settings", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=3, pady=5, padx=10, sticky="w")
        self.spam_widgets_to_toggle = []
        scores_data = [("Spam Score Threshold:", "If a message's score reaches or exceeds this value, it will be blocked."), ("Score for Forbidden Keyword:", "Points added for EACH forbidden keyword found. The score is cumulative."), ("Score for Repeated Chars:", "Points added if the message contains 5 or more repeated characters in a row (e.g., 'aaaaa').")]
        for i, (label_text, tooltip_text) in enumerate(scores_data, start=1):
            ctk.CTkLabel(scores_frame, text=label_text).grid(row=i, column=0, padx=10, pady=8, sticky="w")
            entry = ctk.CTkEntry(scores_frame); entry.grid(row=i, column=1, padx=5, pady=8, sticky="ew")
            info_button = ctk.CTkButton(scores_frame, text="?", width=28, height=28, command=lambda t=label_text, m=tooltip_text: InfoPopup(self, t, m)); info_button.grid(row=i, column=2, padx=(0,10), pady=8, sticky="w")
            self.spam_widgets_to_toggle.append(entry)
        (self.spam_threshold_entry, self.spam_keyword_score_entry, self.spam_repeated_chars_entry) = self.spam_widgets_to_toggle
        
        test_frame = ctk.CTkFrame(left_column_frame); test_frame.grid(row=4, column=0, columnspan=3, padx=10, pady=(15,10), sticky="ew"); test_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(test_frame, text="Test a Message (using Global Settings)", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=2, pady=(0,5), sticky="w")
        self.spam_test_entry = ctk.CTkEntry(test_frame, placeholder_text="Enter a message to test..."); self.spam_test_entry.grid(row=1, column=0, padx=(0,5), pady=5, sticky="ew")
        self.spam_test_button = ctk.CTkButton(test_frame, text="Test", width=60, command=self.run_spam_test); self.spam_test_button.grid(row=1, column=1, padx=(0,5), pady=5, sticky="e")
        self.spam_test_result_label = ctk.CTkLabel(test_frame, text="Result will be shown here.", text_color="gray", wraplength=400, justify="left"); self.spam_test_result_label.grid(row=2, column=0, columnspan=2, pady=(5,0), sticky="w")

        right_column_frame = ctk.CTkFrame(tab); right_column_frame.grid(row=0, column=1, rowspan=4, padx=(5, 10), pady=10, sticky="nsew"); right_column_frame.grid_columnconfigure(0, weight=1); right_column_frame.grid_rowconfigure(5, weight=1)
        whitelist_header_frame = ctk.CTkFrame(right_column_frame, fg_color="transparent"); whitelist_header_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(5,0))
        ctk.CTkLabel(whitelist_header_frame, text="Global User Whitelist (one per line):", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w")
        info_whitelist_button = ctk.CTkButton(whitelist_header_frame, text="?", width=28, height=28, command=lambda: InfoPopup(self, "Global User Whitelist", "Messages from users in this list will bypass the spam filter if the condition's policy is 'Global'. Based on the 'chatname' key. Case-insensitive.")); info_whitelist_button.grid(row=0, column=1, padx=5, sticky="w")
        self.spam_whitelist_textbox = ctk.CTkTextbox(right_column_frame, height=80); self.spam_whitelist_textbox.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")
        regex_info_frame = ctk.CTkFrame(right_column_frame, fg_color="transparent"); regex_info_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(5,0))
        ctk.CTkLabel(regex_info_frame, text="URL Detection Regex (Global):", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w")
        restore_regex_button = ctk.CTkButton(regex_info_frame, text="Restore Default", height=28, command=lambda: (self.url_regex_entry.delete(0, "end"), self.url_regex_entry.insert(0, DEFAULT_URL_REGEX.pattern))); restore_regex_button.grid(row=0, column=1, padx=5, sticky="w")
        self.url_regex_entry = ctk.CTkEntry(right_column_frame); self.url_regex_entry.grid(row=3, column=0, padx=10, pady=(0,10), sticky="ew")
        ctk.CTkLabel(right_column_frame, text="Global Forbidden Keywords:", font=ctk.CTkFont(weight="bold")).grid(row=4, column=0, sticky="nw", padx=10, pady=5)
        self.spam_keywords_textbox = ctk.CTkTextbox(right_column_frame); self.spam_keywords_textbox.grid(row=5, column=0, padx=10, pady=(0, 10), sticky="nsew")
        
        self.spam_widgets_to_toggle.extend([self.spam_block_links_combo, self.spam_max_symbols_entry, self.spam_max_length_entry, self.spam_whitelist_textbox, self.url_regex_entry, self.spam_keywords_textbox, self.spam_test_entry, self.spam_test_button])
        save_spam_button = ctk.CTkButton(tab, text="Save Global Settings", command=self.save_config); save_spam_button.grid(row=5, column=0, columnspan=2, padx=10, pady=10, sticky="ew")

    def run_spam_test(self):
        """Tests a message against the current global spam filter settings in the UI."""
        message = self.spam_test_entry.get()
        if not message: self.spam_test_result_label.configure(text="Please enter a message to test.", text_color="gray"); return
        test_settings = SharedSettings(); test_settings.spam_block_links = self.spam_block_links_combo.get().lower() == 'yes'; test_settings.spam_max_symbols = int(self.spam_max_symbols_entry.get() or 0); test_settings.spam_max_text_length = int(self.spam_max_length_entry.get() or 0)
        test_settings.spam_score_keyword = int(self.spam_keyword_score_entry.get() or 0); test_settings.spam_score_repeated_chars = int(self.spam_repeated_chars_entry.get() or 0)
        test_settings.spam_keywords = [k.strip().lower() for k in self.spam_keywords_textbox.get("1.0", "end-1c").split('\n') if k.strip()]
        try: test_settings.url_regex_compiled = re.compile(self.url_regex_entry.get())
        except re.error: test_settings.url_regex_compiled = DEFAULT_URL_REGEX
        score, reasons = analyze_message_for_spam(message, test_settings); threshold = int(self.spam_threshold_entry.get() or 0)
        
        if score == float('inf'): color = "#E74C3C"; result_text = f"BLOCKED! Reason: {reasons[0]}"
        elif score >= threshold: color = "#E74C3C"; result_text = f"BLOCKED! Score: {score}/{threshold}. Reasons: {', '.join(reasons)}"
        else:
            color = "#2ECC71"; result_text = f"PASS! Score: {score}/{threshold}."
            if reasons: result_text += f" Points from: {', '.join(reasons)}"
        self.spam_test_result_label.configure(text=result_text, text_color=color)

    def toggle_spam_widgets_state(self):
        """Enables or disables spam filter widgets based on the main checkbox."""
        state = "normal" if self.spam_enabled_var.get() else "disabled"; [widget.configure(state=state) for widget in self.spam_widgets_to_toggle]

    def populate_models_list(self):
        """Re-draws the list of message models in the UI based on the current data."""
        for widget in self.models_list_frame.winfo_children(): widget.destroy()
        num_models = len(self.shared_settings.ordered_models)
        for i, model in enumerate(self.shared_settings.ordered_models):
            is_fallback = model["condition"] == FALLBACK_CONDITION_KEY
            prio_frame = ctk.CTkFrame(self.models_list_frame, fg_color="transparent"); prio_frame.grid(row=i, column=0, padx=(5,5), pady=3, sticky="ns"); up_button = ctk.CTkButton(prio_frame, text="▲", width=25, command=lambda idx=i: self.move_model_up(idx)); up_button.pack(side="top", pady=(0,2)); down_button = ctk.CTkButton(prio_frame, text="▼", width=25, command=lambda idx=i: self.move_model_down(idx)); down_button.pack(side="bottom")
            condition_text = "Fallback (Always Last)" if is_fallback else model.get("condition", "")
            spam_policy_text = model.get("spam_policy", "global").capitalize()
            ctk.CTkLabel(self.models_list_frame, text=condition_text, anchor="w", fg_color="gray20", corner_radius=5).grid(row=i, column=1, padx=(0,5), pady=3, sticky="ew")
            ctk.CTkLabel(self.models_list_frame, text=spam_policy_text, anchor="w", fg_color="gray20", corner_radius=5).grid(row=i, column=2, padx=(0,5), pady=3, sticky="ew")
            format_display_text = (model.get("format", "")[:60] + '...') if len(model.get("format", "")) > 60 else model.get("format", "")
            ctk.CTkLabel(self.models_list_frame, text=format_display_text, anchor="w", fg_color="gray20", corner_radius=5).grid(row=i, column=3, padx=(0,5), pady=3, sticky="ew")
            edit_button = ctk.CTkButton(self.models_list_frame, text="Edit", width=60, command=lambda idx=i, m=model: self.edit_model(idx, m)); edit_button.grid(row=i, column=4, padx=5, pady=3)
            delete_button = ctk.CTkButton(self.models_list_frame, text="X", width=30, fg_color="#E74C3C", hover_color="#C0392B", command=lambda idx=i: self.delete_model(idx)); delete_button.grid(row=i, column=5, padx=(0,5), pady=3)
            if is_fallback: up_button.configure(state="disabled"); down_button.configure(state="disabled"); delete_button.configure(state="disabled")
            else:
                if i == 0: up_button.configure(state="disabled")
                if i == num_models - 2: down_button.configure(state="disabled")

    def move_model_up(self, index):
        """Moves a message model up in the priority list."""
        if index > 0: self.shared_settings.ordered_models.insert(index - 1, self.shared_settings.ordered_models.pop(index)); self.save_config(); self.populate_models_list()
    
    def move_model_down(self, index):
        """Moves a message model down in the priority list."""
        if index < len(self.shared_settings.ordered_models) - 2: self.shared_settings.ordered_models.insert(index + 1, self.shared_settings.ordered_models.pop(index)); self.save_config(); self.populate_models_list()
    
    def add_model(self):
        """Opens the editor popup to create a new message model."""
        model_data = {"spam_policy": "global"}; ModelEditorPopup(self, self.on_model_saved, model_data=model_data)
    
    def edit_model(self, index, model_data):
        """Opens the editor popup to edit an existing message model."""
        ModelEditorPopup(self, self.on_model_saved, index=index, model_data=model_data, is_fallback=(model_data["condition"] == FALLBACK_CONDITION_KEY))
    
    def delete_model(self, index):
        """Deletes a message model from the list."""
        self.shared_settings.ordered_models.pop(index); self.save_config(); self.populate_models_list(); log('GUI', 'INFO', "Model deleted.")
    
    def on_model_saved(self, index, new_model_data):
        """Callback function for when a model is saved in the editor popup."""
        if index is None: self.shared_settings.ordered_models.insert(-1, new_model_data); log('GUI', 'INFO', f"Model for condition '{new_model_data['condition']}' added.")
        else: self.shared_settings.ordered_models[index] = new_model_data; log('GUI', 'INFO', f"Model for condition '{new_model_data['condition']}' updated.")
        self.save_config(); self.populate_models_list()
    
    def open_link(self, url):
        """Opens a URL in the default web browser."""
        webbrowser.open_new_tab(url); log('GUI', 'INFO', f"Opening URL: {url}")
    
    def load_config(self):
        """Loads all settings from the config.ini file into the UI and shared settings object."""
        log('GUI', 'INFO', f"Loading settings from {CONFIG_FILE}"); self.config.read(CONFIG_FILE)
        # Server Config
        if not self.config.has_section('server_config'): self.config.add_section('server_config')
        self.host_entry.insert(0, self.config.get('server_config', 'host', fallback='127.0.0.1')); self.port_entry.insert(0, self.config.get('server_config', 'port', fallback='80'))
        if self.config.getboolean('server_config', 'autostart', fallback=True): self.autostart_checkbox.select()
        else: self.autostart_checkbox.deselect()
        # TTS Config
        if not self.config.has_section('tts_config'): self.config.add_section('tts_config')
        lang_code = self.config.get('tts_config', 'lang', fallback='pt-br'); display_lang = next((f"{n} ({c})" for c, n in self.LANGUAGES.items() if c == lang_code), self.language_display_list[0]); self.lang_combobox.set(display_lang)
        # Message Logic & Spam Filter
        if not self.config.has_section('message_logic'): log('GUI', 'INFO', "Older config version found. Migrating to new format."); self.save_config(log_output=False); return
        models_json = self.config.get('message_logic', 'models_json', fallback='[]'); 
        try: loaded_models = json.loads(models_json)
        except json.JSONDecodeError: loaded_models = []
        if loaded_models and 'spam_policy' not in loaded_models[0]:
            log('GUI', 'INFO', "Migrating models to include spam_policy.")
            for model in loaded_models: model['spam_policy'] = 'global'
            self.shared_settings.ordered_models = loaded_models; self.save_config(log_output=False)
        else: self.shared_settings.ordered_models = loaded_models
        if not self.config.has_section('spam_filter'): self.config.add_section('spam_filter')
        self.spam_enabled_var.set(self.config.getboolean('spam_filter', 'enabled', fallback=False))
        self.spam_block_links_combo.set("Yes" if self.config.getboolean('spam_filter', 'block_links', fallback=True) else "No")
        self.spam_max_symbols_entry.insert(0, self.config.get('spam_filter', 'max_symbols', fallback='4'))
        self.spam_max_length_entry.insert(0, self.config.get('spam_filter', 'max_text_length', fallback='200'))
        self.spam_threshold_entry.insert(0, self.config.get('spam_filter', 'threshold', fallback='10'))
        self.spam_whitelist_textbox.insert("1.0", self.config.get('spam_filter', 'user_whitelist', fallback=''))
        self.spam_keyword_score_entry.insert(0, self.config.get('spam_filter', 'score_keyword', fallback='5'))
        self.spam_repeated_chars_entry.insert(0, self.config.get('spam_filter', 'score_repeated_chars', fallback='4'))
        self.spam_keywords_textbox.insert("1.0", self.config.get('spam_filter', 'keywords', fallback='followers\nviews\npromo\n.gg/\nbit.ly'))
        self.url_regex_entry.insert(0, self.config.get('spam_filter', 'url_regex', fallback=DEFAULT_URL_REGEX.pattern))
        self.toggle_spam_widgets_state(); self.save_config(log_output=False)

    def save_config(self, log_output=True):
        """Saves all current UI settings to the config.ini file and updates the live settings object."""
        if log_output: log('GUI', 'INFO', "Saving all settings...")
        self.config.set('server_config', 'host', self.host_entry.get()); self.config.set('server_config', 'port', self.port_entry.get()); self.config.set('server_config', 'autostart', str(self.autostart_checkbox.get() == 1).lower())
        selected_display = self.lang_combobox.get(); lang_code = selected_display[selected_display.rfind('(') + 1:-1]; self.config.set('tts_config', 'lang', lang_code)
        if not self.config.has_section('message_logic'): self.config.add_section('message_logic')
        self.config.set('message_logic', 'models_json', json.dumps(self.shared_settings.ordered_models, indent=2))
        if not self.config.has_section('spam_filter'): self.config.add_section('spam_filter')
        self.config.set('spam_filter', 'enabled', str(self.spam_enabled_var.get()).lower())
        self.config.set('spam_filter', 'block_links', self.spam_block_links_combo.get().lower())
        self.config.set('spam_filter', 'max_symbols', self.spam_max_symbols_entry.get())
        self.config.set('spam_filter', 'max_text_length', self.spam_max_length_entry.get())
        self.config.set('spam_filter', 'threshold', self.spam_threshold_entry.get())
        self.config.set('spam_filter', 'user_whitelist', self.spam_whitelist_textbox.get("1.0", "end-1c"))
        self.config.set('spam_filter', 'score_keyword', self.spam_keyword_score_entry.get()); self.config.set('spam_filter', 'score_repeated_chars', self.spam_repeated_chars_entry.get())
        self.config.set('spam_filter', 'keywords', self.spam_keywords_textbox.get("1.0", "end-1c")); self.config.set('spam_filter', 'url_regex', self.url_regex_entry.get())
        with open(CONFIG_FILE, 'w', encoding='utf-8') as configfile: self.config.write(configfile)
        self.shared_settings.host = self.host_entry.get(); self.shared_settings.port = int(self.port_entry.get()); self.shared_settings.lang = lang_code
        self.shared_settings.spam_filter_enabled = self.spam_enabled_var.get()
        self.shared_settings.spam_block_links = self.spam_block_links_combo.get().lower() == 'yes'
        self.shared_settings.spam_max_symbols = int(self.spam_max_symbols_entry.get() or 0)
        self.shared_settings.spam_max_text_length = int(self.spam_max_length_entry.get() or 0)
        self.shared_settings.spam_threshold = int(self.spam_threshold_entry.get() or 0)
        self.shared_settings.spam_user_whitelist = [u.strip() for u in self.spam_whitelist_textbox.get("1.0", "end-1c").split('\n') if u.strip()]
        self.shared_settings.spam_score_keyword = int(self.spam_keyword_score_entry.get() or 0); self.shared_settings.spam_score_repeated_chars = int(self.spam_repeated_chars_entry.get() or 0)
        self.shared_settings.spam_keywords = [k.strip().lower() for k in self.spam_keywords_textbox.get("1.0", "end-1c").split('\n') if k.strip()]
        try: self.shared_settings.url_regex_compiled = re.compile(self.url_regex_entry.get())
        except re.error as e: log('GUI', 'ERROR', f"Invalid Regex! Using default. Error: {e}"); self.shared_settings.url_regex_compiled = DEFAULT_URL_REGEX
        self.update_url_displays()
        if log_output: log('GUI', 'INFO', "Settings saved and applied live.")

    def copy_to_clipboard(self, text):
        """Copies the given text to the system clipboard."""
        self.clipboard_clear(); self.clipboard_append(text); self.update(); log('GUI', 'INFO', f"Text '{text}' copied to clipboard.")
    
    def log_to_gui(self, level, module, message):
        """Adds a formatted log message to the log textbox in the Dashboard."""
        self.log_textbox.configure(state="normal"); color_tag="INFO"
        if level=="ERROR":color_tag="ERROR"
        elif level=="WARN":color_tag="WARN"
        elif module=="SERVER":color_tag="SERVER"
        elif module=="GUI":color_tag="GUI"
        log_line=f"[{level.upper()}] [{module.upper()}] {message}\n"; start_index=self.log_textbox.index("end-1c"); self.log_textbox.insert("end",log_line); end_index=self.log_textbox.index("end-1c")
        self.log_textbox.tag_add(color_tag,start_index,end_index); self.log_textbox.see("end"); self.log_textbox.configure(state="disabled")
    
    def process_log_queue(self):
        """Periodically checks the log queue and displays new messages."""
        try:
            while True: level,module,message=log_queue.get_nowait(); self.log_to_gui(level,module,message)
        except queue.Empty:pass
        self.after(200,self.process_log_queue)
    
    def toggle_server(self):
        """Starts or stops the Flask server based on its current state."""
        if self.is_server_running:self.stop_server()
        else:self.start_server()
    
    def start_server(self):
        """Starts the Flask server in a separate thread."""
        log('GUI','INFO',"Attempting to start the server..."); self.save_config(log_output=False)
        try:
            host=self.shared_settings.host; port=self.shared_settings.port; flask_app=create_flask_app(self.shared_settings)
            self.http_server=make_server(host,port,flask_app,threaded=True); self.server_thread=threading.Thread(target=self.http_server.serve_forever); self.server_thread.daemon=True; self.server_thread.start()
            self.is_server_running=True; self.update_gui_for_server_state(); log('SERVER','INFO',f"Server started on http://{host}:{port}")
        except Exception as e: log('GUI','ERROR',f"Failed to start server: {e}"); self.is_server_running=False; self.update_gui_for_server_state()
    
    def stop_server(self):
        """Stops the Flask server."""
        if not self.is_server_running:return
        log('GUI','INFO',"Stopping the server...")
        try: shutdown_thread=threading.Thread(target=self.http_server.shutdown); shutdown_thread.daemon=True; shutdown_thread.start(); self.is_server_running=False; self.update_gui_for_server_state(); log('SERVER','INFO',"Server stopped.")
        except Exception as e: log('GUI','ERROR',f"Error stopping server: {e}")
    
    def update_gui_for_server_state(self):
        """Updates the GUI elements (labels, buttons) to reflect the server's running state."""
        if self.is_server_running: self.status_label.configure(text="Status: Active",text_color="#2ECC71"); self.start_stop_button.configure(text="Stop Server"); self.speak_button.configure(state="normal"); self.host_entry.configure(state="disabled"); self.port_entry.configure(state="disabled")
        else: self.status_label.configure(text="Status: Stopped",text_color="#F39C12"); self.start_stop_button.configure(text="Start Server"); self.speak_button.configure(state="disabled"); self.host_entry.configure(state="normal"); self.port_entry.configure(state="normal")
    
    def update_url_displays(self):
        """Updates the read-only URL fields in the Dashboard based on current settings."""
        host = self.host_entry.get(); port = self.port_entry.get(); display_host = host if host != '0.0.0.0' else '127.0.0.1'
        audio_source_url = f"http://{display_host}:{port}"; post_url = f"http://{display_host}:{port}/speak"
        for entry, url in [(self.audio_source_url_entry, audio_source_url), (self.post_url_entry, post_url)]: entry.configure(state="normal"); entry.delete(0, "end"); entry.insert(0, url); entry.configure(state="readonly")
    
    def send_custom_json_request(self):
        """Sends a POST request to the server using the JSON from the example textbox."""
        log('GUI','INFO',"Sending custom JSON request..."); self.save_config(log_output=False); url=self.post_url_entry.get(); json_string=self.json_example_textbox.get("1.0","end-1c")
        try: payload=json.loads(json_string)
        except json.JSONDecodeError as e: log('GUI','ERROR',f"Invalid JSON in text box: {e}"); return
        try:
            response=requests.post(url,json=payload,timeout=5)
            if response.status_code==200: log('GUI','INFO',"Custom JSON sent successfully! Check the Audio Source Page.")
            else: log('GUI','WARN',f"Server responded with error: {response.status_code} - {response.text}")
        except requests.exceptions.RequestException as e: log('GUI','ERROR',f"Failed to send custom request: {e}")
    
    def on_closing(self):
        """Handles the window close event, ensuring the server is stopped first."""
        if self.is_server_running:self.stop_server()
        self.destroy()

if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
