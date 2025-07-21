# TgbTtsServer - Your Universal Text-to-Speech Bridge for OBS

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

**TgbTtsServer** is a simple yet powerful local server designed to receive text messages via web requests, convert them to speech using Google's TTS engine, and serve the audio through a local web page. This makes it a perfect tool to create a dedicated, controllable audio source for your stream alerts in OBS Studio.

*(Note: The screenshot below might be in a different language, but the layout and functionality are the same.)*
![Control Panel Screenshot](https://raw.githubusercontent.com/tonygabarron/TgbTtsServer/main/assets/gui_screenshot.png)

## The Original Problem & The Solution

This project was born out of a specific need for livestreaming. Many great alert services, like **Social Stream Ninja**, are fantastic for displaying visual notifications on screen. However, their audio is often embedded within the browser source, offering no direct way to control its volume independently within OBS Studio. You either hear the alert at its default volume or not at all.

**TgbTtsServer solves this problem by acting as a smart audio middleman.**

1.  **Your alert service (e.g., Social Stream Ninja)** sends a webhook/POST request to TgbTtsServer when an event occurs (like a new follower).
2.  **TgbTtsServer** receives the data, formats a custom message based on your templates (e.g., "{user} just followed!"), and generates an audio file for that message.
3.  **OBS Studio**, using a separate "Browser" source pointing to TgbTtsServer, automatically plays this audio.

The result? You now have a dedicated "TTS Alerts" audio source in your OBS Audio Mixer, which you can mute, apply filters to, or adjust the volume of, completely independent of your visual alerts!

## Key Features

-   **Easy-to-use GUI:** A simple control panel to manage the server, configure settings, and view logs.
-   **Customizable Message Models:** Define different speech templates for various events (follows, donations, subscriptions, etc.) using a simple key-value system.
-   **Versatile API:** While built for stream alerts, it can be integrated with any application capable of sending a JSON POST request. Use it for Discord bots, home automation notifications, and more!
-   **Simple OBS Integration:** Works flawlessly as a standard "Browser" source in OBS Studio.
-   **Live Configuration:** Most settings can be changed and are applied instantly without restarting the server.
-   **Standalone Executable:** Comes with a compiler script to create a single `.exe` file that runs without needing Python or any libraries installed.

## Installation & Setup

### Using the Compiled Version (Recommended for most users)

1.  Go to the [**Releases**](https://github.com/tonygabarron/TgbTtsServer/releases) page of this repository.
2.  Download the latest `.zip` file.
3.  Extract the contents to a folder on your computer.
4.  Run `tgb_tts_server.pyw` (or `tgb_tts_server.exe` if compiled) to launch the control panel.

### Running from Source (For developers)

1.  Clone the repository:
    ```bash
    git clone https://github.com/tonygabarron/TgbTtsServer.git
    cd TgbTtsServer
    ```
2.  Install the required Python libraries:
    ```bash
    pip install -r requirements.txt
    ```
    *(If a `requirements.txt` is not available, install them manually: `pip install customtkinter requests Flask gTTS Flask-Cors Werkzeug`)*
3.  Run the application using the provided batch file or directly with Python:
    ```bash
    # Using the batch file
    start.bat

    # Or directly with Python
    python tgb_tts_server.pyw
    ```

## Usage Guide

### 1. Start the Server

-   Open the application.
-   Review the settings (Host, Port, Language). The defaults (`127.0.0.1`, port `80`) work for most local setups.
-   Click **"Start Server"**. The status will change to "Active".

### 2. Configure in OBS Studio

1.  In OBS, add a new **"Browser"** source. Name it something like "TTS Alerts Audio".
2.  In the source properties, set the **URL** to the "URL for Audio Source" shown in the app (e.g., `http://127.0.0.1:80`).
3.  **IMPORTANT:** Check the box that says **"Control audio via OBS"**.
4.  Click "OK". You will now see a new entry in your "Audio Mixer" for this source.

### 3. Sending Alerts (The Magic Part)

Your applications need to send a `POST` request with a JSON body to the "URL for Alerts" shown in the app (e.g., `http://127.0.0.1:80/speak`).

The power of the server lies in the **`messageKey`** field in the JSON. This key tells the server which message model to use.

**Example:**
Let's say you have these models configured in the app:
-   `follow`: "{chatname} is now following the channel!"
-   `donation`: "{chatname} just donated {amount} dollars! Thank you!"

To trigger a **follow alert**, your application should send this JSON:

```json
{
  "messageKey": "follow",
  "chatname": "StreamFan123"
}
```

To trigger a **donation alert**, it should send this:

```json
{
  "messageKey": "donation",
  "chatname": "GenerousViewer",
  "amount": "5.00"
}
```

If the `messageKey` is not found or not provided, the server will use the "Message Format Default".

You can test this directly from the app using the "Alert JSON Example" box and the "Speak" button!

> **Important Note on Testing in a Web Browser:**
> Modern web browsers (like Chrome, Firefox, etc.) have strict autoplay policies that prevent audio from playing automatically until you interact with the page (e.g., by clicking on it).
>
> If you open the "URL for Audio Source" in your browser to test the audio, you must **first click anywhere on the blank page** that loads. After that single click, the browser will be "activated," and any test alerts you send from the app will play correctly.
>
> This browser limitation **does not affect OBS Studio**. The browser source in OBS does not have this restriction and will play the audio automatically without any manual interaction. The "Speak" button is perfect for confirming your JSON is valid and the server is responding.

## Building the Executable

For convenience, you can compile the entire application into a single `.exe` file.

1.  Ensure you have Python installed.
2.  Install PyInstaller: `pip install pyinstaller`
3.  Run the `compiler.bat` file.
4.  The standalone executable will be created inside the `dist` folder. You can share this single file with others.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
