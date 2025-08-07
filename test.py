import os
import random
import string
import requests
import mysql.connector
from flask import Flask, render_template, request, jsonify, url_for, session, redirect
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import google.generativeai as genai
from gtts import gTTS
import assemblyai as aai
from langdetect import detect
import pyttsx3
from werkzeug.security import generate_password_hash, check_password_hash

# Flask app setup
app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'webm'}

# Ensure required folders exist
os.makedirs('static/audio', exist_ok=True)
os.makedirs('uploads', exist_ok=True)

# Load environment variables
load_dotenv()
hugging_face = os.getenv('hugging_face')
gemini_api_key = os.getenv('gemini_api_key')
weather_api_key = os.getenv("WEATHERAPI_KEY")
aai.settings.api_key = os.getenv("ASSEMBLYAI_API_KEY")

# Configure Gemini
genai.configure(api_key=gemini_api_key)
model = genai.GenerativeModel('models/gemini-1.5-flash')

# ---------- Database Connection ----------
def get_db_connection():
    return mysql.connector.connect(
        host='localhost',
        user='root',
        password='vijay@19_98',
        database='agribot'
    )

# ---------- Helpers ----------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def fetch_weather(region):
    try:
        url = f"http://api.weatherapi.com/v1/current.json?key={weather_api_key}&q={region}"
        response = requests.get(url)
        data = response.json()
        weather_data = {
            "region": region,
            "condition": data['current']['condition']['text'],
            "temperature_c": data['current']['temp_c'],
            "humidity": data['current']['humidity'],
            "wind_kph": data['current']['wind_kph']
        }
        return weather_data
    except Exception as e:
        print("Weather API Error:", e)
        return None

def get_answer_gemini(question, weather_data=None):
    user_id = session.get('user_id')
    if not user_id:
        return "Please log in."

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT name, crop_type, region FROM users WHERE id = %s", (user_id,))
    user_data = cur.fetchone()
    if not user_data:
        return "User data missing."

    cur.execute("SELECT summary FROM summaries WHERE user_id = %s", (user_id,))
    summary_row = cur.fetchone()
    summary = summary_row['summary'] if summary_row else "No summary available."

    cur.execute("SELECT sender, message FROM chats WHERE user_id = %s ORDER BY id DESC LIMIT 5", (user_id,))
    rows = cur.fetchall()

    cur.close()
    conn.close()

    name = user_data.get('name', 'Farmer')
    region = user_data.get('region', 'your region')
    crop = user_data.get('crop_type', 'your crop')

    weather_context = ""
    if weather_data:
        weather_context = (
            f"Location: {weather_data['region']}\n"
            f"Weather Condition: {weather_data['condition']}\n"
            f"Temperature: {weather_data['temperature_c']}°C\n"
            f"Humidity: {weather_data['humidity']}%\n"
            f"Wind Speed: {weather_data['wind_kph']} kph\n"
        )

    prompt = f"""
You are AgriBot, an agriculture expert chatbot helping a farmer.

Farmer Profile:
Name: {name}
Crop Type: {crop}
Region: {region}
{weather_context}

Past Conversation Summary:
{summary}

Recent Messages:
"""

    for row in reversed(rows):
        prompt += f"{row['sender']}: {row['message']}\n"
    prompt += f"User: {question}\nAgriBot:"

    try:
        response = model.generate_content(prompt.strip())
        return response.text.strip()
    except Exception as e:
        return f"Error: {str(e)}"

def update_summary(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT sender, message FROM chats WHERE user_id = %s ORDER BY id DESC LIMIT 15", (user_id,))
    messages = cur.fetchall()
    dialogue = "\n".join([f"{s}: {m}" for s, m in reversed(messages)])
    prompt = f"Summarize the following conversation between a user and AgriBot:\n{dialogue}\nSummary:"
    try:
        summary = model.generate_content(prompt).text.strip()
        cur.execute("REPLACE INTO summaries (user_id, summary) VALUES (%s, %s)", (user_id, summary))
        conn.commit()
    except:
        pass
    cur.close()
    conn.close()
def get_gemini_reply(question, weather=""):
    prompt = f"User question: {question}\nCurrent weather: {weather}\nAnswer in English:"
    response = model.generate_content(prompt)
    return response.text.strip()

# Translate Gemini English reply to target language
def translate_to_language(text, target_lang_code):
    if target_lang_code == 'en':
        return text
    prompt = f"Translate this English text to {target_lang_code}:\n\"{text}\""
    response = model.generate_content(prompt)
    return response.text.strip()

def maybe_update_summary(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM chats WHERE user_id = %s AND sender = 'User'", (user_id,))
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    if count % 5 == 0:
        update_summary(user_id)

def save_chat(user_id, sender, message):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO chats (user_id, sender, message) VALUES (%s, %s, %s)", (user_id, sender, message))
    conn.commit()
    cur.close()
    conn.close()

from gtts import gTTS
import os

def text_to_audio(text, voice_id, language_code='en'):
    try:
        tts = gTTS(text=text, lang=language_code)
        audio_path = os.path.join('static/audio', f"{voice_id}.mp3")
        tts.save(audio_path)
    except Exception as e:
        print("TTS generation failed:", e)

# ---------- Routes ----------
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cur.fetchone()
        if user:
            if check_password_hash(user['password'], password):
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['region'] = user['region']
                return redirect(url_for('dashboard'))
            else:
                return "❌ Incorrect password"
        else:
            hashed_pw = generate_password_hash(password)
            cur.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, hashed_pw))
            conn.commit()
            user_id = cur.lastrowid
            session['user_id'] = user_id
            session['username'] = username
            cur.close()
            conn.close()
            return redirect(url_for('setup'))
    return render_template('login.html')

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if request.method == 'POST':
        crop_type = request.form['crop_type']
        farm_size = request.form['farm_size']
        region = request.form['region']
        name = "Farmer"
        conn = get_db_connection()
        cur = conn.cursor()
        user_id = session.get('user_id')
        if user_id:
            cur.execute("UPDATE users SET crop_type=%s, region=%s, farm_size=%s WHERE id=%s",
                        (crop_type, region, farm_size, user_id))
        conn.commit()
        cur.close()
        conn.close()
        session['region'] = region
        return redirect(url_for('dashboard'))
    return render_template('setup.html')


# Main chat route
@app.route('/chat', methods=['POST'])
def chat():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'text': "Please log in to use the chatbot."})

    audio = request.files.get('audio')
    text = request.form.get('text')

    if audio and allowed_file(audio.filename) and audio.filename != '':
        try:
            filename = secure_filename(audio.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            audio.save(filepath)

            # Transcribe with language detection
            config = aai.TranscriptionConfig(language_detection=True)
            transcriber = aai.Transcriber(config=config)
            transcript = transcriber.transcribe(filepath)
            user_text = transcript.text
            detected_lang = transcript.json_response.get("language_code", "en")
            user_text_en = translate_to_language(user_text, 'en')
            save_chat(user_id, 'User', user_text_en)

            weather = fetch_weather(session.get('region', ''))
            english_reply = get_gemini_reply(user_text, weather)
            translated_reply = translate_to_language(english_reply, detected_lang)

            save_chat(user_id, 'AgriBot', translated_reply)
            maybe_update_summary(user_id)

            voice_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            text_to_audio(translated_reply, voice_id, detected_lang)

            return jsonify({
                'text': user_text,
                'language': detected_lang,
                'response': translated_reply,
                'voice': url_for('static', filename='audio/' + voice_id + '.mp3')
            })
        except Exception as e:
            return jsonify({'text': 'Failed audio.', 'response': str(e), 'voice': None})

    elif text:
        try:
            detected_lang = detect(text)
        except:
            detected_lang = 'en'
        user_text_en = translate_to_language(text, 'en')
        save_chat(user_id, 'User', user_text_en)

        weather = fetch_weather(session.get('region', ''))
        english_reply = get_answer_gemini(text, weather)
        translated_reply = translate_to_language(english_reply, detected_lang)

        save_chat(user_id, 'AgriBot', english_reply)
        maybe_update_summary(user_id)

        voice_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        text_to_audio(translated_reply, voice_id, detected_lang)


        return jsonify({
            'text': text,
            'language': detected_lang,
            'response': translated_reply,
            'voice': url_for('static', filename='audio/' + voice_id + '.mp3')
        })

    return jsonify({'text': '', 'response': 'Invalid input.', 'voice': None})


@app.route('/user/<int:user_id>')
def get_user(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT name, crop_type, region, farm_size FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    if user:
        return jsonify({
            "name": user[0],
            "crop": user[1],
            "region": user[2],
            "farm_size": user[3]
        })
    return jsonify({"error": "User not found"})

@app.route('/test-db')
def test_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT DATABASE()")
        db_name = cur.fetchone()
        cur.close()
        conn.close()
        return f"✅ Connected to DB: {db_name[0]}"
    except Exception as e:
        return f"❌ DB Connection failed: {str(e)}"

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ---------- Run Server ----------
if __name__ == '__main__':
    app.run(debug=True)