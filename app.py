from flask import Flask, request, render_template_string, redirect, url_for, send_file
import os
import re
import requests
from urllib.parse import urlparse, parse_qs
import logging
import io
from googleapiclient.discovery import build
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import base64
from io import BytesIO
from datetime import datetime, timedelta
import isodate
from jinja2 import Environment, FileSystemLoader, Template
from collections import Counter, defaultdict

app = Flask(__name__, static_folder='static')

# Configurar logging
log_stream = io.StringIO()
logging.basicConfig(stream=log_stream, level=logging.DEBUG, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Configura la API de YouTube con la API key de entorno
api_key = os.environ.get('YOUTUBE_API_KEY', 'AIzaSyDZ0Js1TtNxwS2K3XY-HPOiewQpuhInY9E')
youtube = build('youtube', 'v3', developerKey=api_key)

# Funciones auxiliares
def format_number(value):
    return f"{value:,}"

def format_date(value):
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    return str(value)

def format_duration(duration):
    if isinstance(duration, timedelta):
        hours, remainder = divmod(duration.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{int(hours)}:{int(minutes):02d}:{int(seconds):02d}"
        else:
            return f"{int(minutes):02d}:{int(seconds):02d}"
    return str(duration)

# Funciones para extractor de ID de canal
def obtener_id_canal(url):
    logging.info(f"Procesando URL: {url}")
    
    # Normaliza la URL
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    parsed_url = urlparse(url)
    
    # Comprueba si es una URL de YouTube válida
    if 'youtube.com' not in parsed_url.netloc and 'youtu.be' not in parsed_url.netloc:
        logging.warning("URL no válida de YouTube")
        return None

    # Intenta extraer el ID del canal directamente de la URL
    path_parts = parsed_url.path.strip('/').split('/')
    logging.debug(f"Partes de la ruta: {path_parts}")

    if 'channel' in path_parts:
        channel_id = path_parts[path_parts.index('channel') + 1]
        logging.info(f"ID de canal encontrado en la URL: {channel_id}")
        return channel_id
    
    # Maneja URLs de usuario personalizado
    if path_parts and path_parts[0] in ['c', 'user'] or (path_parts and path_parts[0].startswith('@')):
        custom_name = path_parts[-1]
        logging.info(f"Nombre personalizado encontrado: {custom_name}")
        return obtener_id_desde_nombre_personalizado(custom_name)
    
    # Maneja URLs de vídeo
    if 'watch' in path_parts:
        video_id = parse_qs(parsed_url.query).get('v', [None])[0]
        if video_id:
            logging.info(f"ID de video encontrado: {video_id}")
            return obtener_id_desde_video(video_id)
    
    # Si todo lo demás falla, intenta obtener el ID de la página
    logging.info("Intentando obtener ID del contenido de la página")
    return obtener_id_desde_contenido_pagina(url)

def obtener_id_desde_nombre_personalizado(nombre):
    url = f'https://www.youtube.com/{nombre}'
    logging.info(f"Obteniendo ID desde nombre personalizado: {url}")
    return obtener_id_desde_contenido_pagina(url)

def obtener_id_desde_video(video_id):
    url = f'https://www.youtube.com/watch?v={video_id}'
    logging.info(f"Obteniendo ID desde video: {url}")
    return obtener_id_desde_contenido_pagina(url)

def obtener_id_desde_contenido_pagina(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        respuesta = requests.get(url, headers=headers)
        contenido = respuesta.text
        patron_id = r'"channelId":"(UC[a-zA-Z0-9_-]{22})"'
        coincidencia_id = re.search(patron_id, contenido)
        if coincidencia_id:
            channel_id = coincidencia_id.group(1)
            logging.info(f"ID de canal encontrado en el contenido de la página: {channel_id}")
            return channel_id
        else:
            logging.warning("No se encontró el ID del canal en el contenido de la página")
    except requests.RequestException as e:
        logging.error(f"Error al obtener el contenido de la página: {e}")
    return None

# Funciones para SEO Analyzer
def search_videos(keyword, max_results=20):
    try:
        videos = []
        next_page_token = None
        
        while len(videos) < max_results:
            request = youtube.search().list(
                q=keyword,
                type='video',
                part='id,snippet',
                maxResults=50,
                pageToken=next_page_token
            )
            response = request.execute()
            
            for item in response['items']:
                video_id = item['id']['videoId']
                video_data = youtube.videos().list(part='contentDetails,statistics,snippet', id=video_id).execute()
                
                if 'items' not in video_data or len(video_data['items']) == 0:
                    continue
                
                video_item = video_data['items'][0]
                
                duration_str = video_item['contentDetails']['duration']
                duration = isodate.parse_duration(duration_str)
                
                # Filtrar videos que duran más de 1 minuto y 2 segundos
                if duration < timedelta(minutes=1, seconds=2):
                    continue
                
                published_at = datetime.strptime(item['snippet']['publishedAt'], "%Y-%m-%dT%H:%M:%SZ")
                day_of_week = published_at.strftime('%A')
                
                video_stats = video_item['statistics']
                
                video_details = {
                    'title': item['snippet'].get('title', 'Sin título'),
                    'published_at': published_at,
                    'day_of_week': day_of_week,
                    'views': int(video_stats.get('viewCount', 0)),
                    'likes': int(video_stats.get('likeCount', 0)),
                    'comments': int(video_stats.get('commentCount', 0)),
                    'duration': duration,
                    'video_url': f"https://www.youtube.com/watch?v={video_id}",
                    'thumbnail_url': item['snippet'].get('thumbnails', {}).get('medium', {}).get('url', ''),
                    'category': get_video_category(video_item['snippet'].get('categoryId', '')),
                    'channel_title': item['snippet'].get('channelTitle', 'Desconocido')
                }
                
                videos.append(video_details)
                
                if len(videos) == max_results:
                    break
            
            if len(videos) == max_results:
                break
            
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break
        
        return videos
    except Exception as e:
        print(f"Error al buscar videos: {str(e)}")
        return []

def get_video_category(category_id):
    try:
        request = youtube.videoCategories().list(
            part='snippet',
            id=category_id
        )
        response = request.execute()
        if 'items' in response and len(response['items']) > 0:
            return response['items'][0]['snippet']['title']
        return "Desconocida"
    except Exception as e:
        print(f"Error al obtener la categoría del video: {str(e)}")
        return "Desconocida"

def calculate_average_duration(videos):
    total_duration = sum((video['duration'] for video in videos), timedelta())
    return total_duration / len(videos) if videos else timedelta()

def count_unique_channels(videos):
    return len(set(video['channel_title'] for video in videos))

def get_channel_stats(videos):
    channel_stats = defaultdict(lambda: {'videos': 0, 'views': 0, 'likes': 0, 'comments': 0, 'thumbnail': ''})
    for video in videos:
        channel = video['channel_title']
        channel_stats[channel]['videos'] += 1
        channel_stats[channel]['views'] += video['views']
        channel_stats[channel]['likes'] += video['likes']
        channel_stats[channel]['comments'] += video['comments']
        if not channel_stats[channel]['thumbnail']:
            channel_stats[channel]['thumbnail'] = video['thumbnail_url']
    return dict(channel_stats)

def categorize_videos_by_age(videos):
    now = datetime.now()
    six_months_ago = now - timedelta(days=180)
    one_year_ago = now - timedelta(days=365)
    
    last_6_months = []
    last_year = []
    older_than_year = []
    
    for video in videos:
        if video['published_at'] > six_months_ago:
            last_6_months.append(video)
        elif video['published_at'] > one_year_ago:
            last_year.append(video)
        else:
            older_than_year.append(video)
    
    return last_6_months, last_year, older_than_year

def calculate_total_stats(videos):
    return {
        'total_views': sum(video['views'] for video in videos),
        'total_likes': sum(video['likes'] for video in videos),
        'total_comments': sum(video['comments'] for video in videos)
    }

# Rutas para la aplicación
@app.route('/')
def index():
    return render_template_string('''
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>YouTube Tools</title>
        <style>
            :root {
                --primary-color: #FF0000;
                --secondary-color: #282828;
                --text-color: #333333;
                --background-color: #F9F9F9;
                --card-background: #FFFFFF;
                --shadow-color: rgba(0, 0, 0, 0.1);
            }
            
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: 'Roboto', Arial, sans-serif;
                background-color: var(--background-color);
                color: var(--text-color);
                line-height: 1.6;
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                padding: 20px;
            }
            
            .container {
                width: 100%;
                max-width: 1000px;
                display: flex;
                flex-direction: column;
                gap: 20px;
            }
            
            .card {
                background-color: var(--card-background);
                border-radius: 12px;
                box-shadow: 0 8px 16px var(--shadow-color);
                padding: 2rem;
                text-align: center;
                transition: transform 0.3s ease;
            }
            
            .card:hover {
                transform: translateY(-5px);
            }
            
            .tools {
                display: flex;
                flex-direction: row;
                gap: 20px;
                flex-wrap: wrap;
                justify-content: center;
            }
            
            .tool-card {
                flex: 1;
                min-width: 300px;
                max-width: 450px;
            }
            
            .logo {
                margin-bottom: 2rem;
            }
            
            .logo svg {
                width: 120px;
                height: 120px;
                fill: var(--primary-color);
            }
            
            h1 {
                color: var(--secondary-color);
                font-size: 2.5rem;
                margin-bottom: 1.5rem;
                font-weight: 700;
            }
            
            h2 {
                color: var(--secondary-color);
                font-size: 1.8rem;
                margin-bottom: 1rem;
                font-weight: 600;
            }
            
            p {
                margin-bottom: 1.5rem;
            }
            
            .button {
                background-color: var(--primary-color);
                color: white;
                border: none;
                padding: 1rem 2rem;
                border-radius: 50px;
                cursor: pointer;
                font-size: 1rem;
                font-weight: 700;
                text-transform: uppercase;
                transition: all 0.3s ease;
                text-decoration: none;
                display: inline-block;
            }
            
            .button:hover {
                background-color: #E50000;
                transform: translateY(-2px);
                box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
            }
            
            @media (max-width: 768px) {
                .card {
                    padding: 1.5rem;
                }
                
                h1 {
                    font-size: 2rem;
                }
                
                .tools {
                    flex-direction: column;
                }
                
                .tool-card {
                    max-width: 100%;
                }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                <div class="logo">
                    <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                        <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
                    </svg>
                </div>
                <h1>Herramientas YouTube</h1>
                <p>Selecciona una de nuestras herramientas para trabajar con YouTube</p>
            </div>
            
            <div class="tools">
                <div class="card tool-card">
                    <h2>Extractor de ID de Canal</h2>
                    <p>Esta herramienta te permite obtener el ID de un canal de YouTube a partir de cualquier URL relacionada con el canal (URL del canal, de un video, etc).</p>
                    <a href="/extractor" class="button">Ir al Extractor</a>
                </div>
                
                <div class="card tool-card">
                    <h2>Análisis SEO YouTube</h2>
                    <p>Analiza los resultados de búsqueda en YouTube para una palabra clave específica y obtén estadísticas detalladas.</p>
                    <a href="/seo" class="button">Ir al Analizador SEO</a>
                </div>
            </div>
        </div>
    </body>
    </html>
    ''')

@app.route('/extractor', methods=['GET', 'POST'])
def extractor():
    result = None
    debug_info = None
    if request.method == 'POST':
        url = request.form['url']
        log_stream.seek(0)
        log_stream.truncate(0)
        result = obtener_id_canal(url)
        debug_info = log_stream.getvalue()
    
    return render_template_string('''
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Extractor de ID de Canal de YouTube</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                line-height: 1.6;
                margin: 0;
                padding: 20px;
                background-color: #f4f4f4;
            }
            .container {
                max-width: 800px;
                margin: auto;
                background: white;
                padding: 20px;
                border-radius: 5px;
                box-shadow: 0 0 10px rgba(0,0,0,0.1);
            }
            h1 {
                color: #333;
                text-align: center;
            }
            input[type="text"], input[type="submit"] {
                width: 100%;
                padding: 10px;
                margin-bottom: 10px;
            }
            input[type="submit"] {
                background: #333;
                color: #fff;
                border: none;
                cursor: pointer;
            }
            input[type="submit"]:hover {
                background: #555;
            }
            #result, #debug {
                margin-top: 20px;
                padding: 10px;
                background: #e7e7e7;
                border-radius: 5px;
            }
            #debug {
                white-space: pre-wrap;
                font-family: monospace;
                font-size: 12px;
            }
            .home-link {
                display: inline-block;
                margin-top: 20px;
                color: #333;
                text-decoration: none;
            }
            .home-link:hover {
                text-decoration: underline;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Extractor de ID de Canal de YouTube</h1>
            <form method="post">
                <input type="text" name="url" placeholder="Introduce la URL del canal o video de YouTube" required>
                <input type="submit" value="Obtener ID del Canal">
            </form>
            {% if result is not none %}
                <div id="result">
                    {% if result %}
                        El ID del canal es: <strong>{{ result }}</strong>
                    {% else %}
                        No se pudo obtener el ID del canal.
                    {% endif %}
                </div>
            {% endif %}
            {% if debug_info %}
                <div id="debug">
                    <h3>Información de depuración:</h3>
                    {{ debug_info }}
                </div>
            {% endif %}
            <a href="/" class="home-link">← Volver a la página principal</a>
        </div>
    </body>
    </html>
    ''', result=result, debug_info=debug_info)

@app.route('/seo', methods=['GET', 'POST'])
def seo():
    if request.method == 'POST':
        keyword = request.form['keyword']
        return redirect(url_for('generate_report', keyword=keyword))
    
    return render_template_string('''
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Análisis SEO YouTube</title>
        <style>
            :root {
                --primary-color: #FF0000;
                --secondary-color: #282828;
                --text-color: #333333;
                --background-color: #F9F9F9;
                --card-background: #FFFFFF;
                --shadow-color: rgba(0, 0, 0, 0.1);
            }
            
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: 'Roboto', Arial, sans-serif;
                background-color: var(--background-color);
                color: var(--text-color);
                line-height: 1.6;
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                padding: 20px;
            }
            
            .container {
                width: 100%;
                max-width: 600px;
                background-color: var(--card-background);
                border-radius: 12px;
                box-shadow: 0 8px 16px var(--shadow-color);
                padding: 2rem;
                text-align: center;
                transition: transform 0.3s ease;
            }
            
            .container:hover {
                transform: translateY(-5px);
            }
            
            .logo {
                margin-bottom: 2rem;
            }
            
            .logo svg {
                width: 120px;
                height: 120px;
                fill: var(--primary-color);
            }
            
            h1 {
                color: var(--secondary-color);
                font-size: 2.5rem;
                margin-bottom: 1.5rem;
                font-weight: 700;
            }
            
            form {
                display: flex;
                flex-direction: column;
            }
            
            input[type="text"] {
                padding: 1rem;
                margin-bottom: 1rem;
                border: 2px solid var(--secondary-color);
                border-radius: 50px;
                font-size: 1rem;
                transition: all 0.3s ease;
            }
            
            input[type="text"]:focus {
                outline: none;
                border-color: var(--primary-color);
                box-shadow: 0 0 0 3px rgba(255, 0, 0, 0.1);
            }
            
            input[type="submit"] {
                background-color: var(--primary-color);
                color: white;
                border: none;
                padding: 1rem;
                border-radius: 50px;
                cursor: pointer;
                font-size: 1rem;
                font-weight: 700;
                text-transform: uppercase;
                transition: all 0.3s ease;
            }
            
            input[type="submit"]:hover {
                background-color: #E50000;
                transform: translateY(-2px);
                box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
            }
            
            .home-link {
                display: inline-block;
                margin-top: 20px;
                color: #333;
                text-decoration: none;
            }
            
            .home-link:hover {
                text-decoration: underline;
            }
            
            @media (max-width: 768px) {
                .container {
                    padding: 1.5rem;
                }
                
                h1 {
                    font-size: 2rem;
                }
                
                input[type="text"], input[type="submit"] {
                    padding: 0.8rem;
                }
            }
            
            @media (max-width: 480px) {
                .container {
                    padding: 1rem;
                }
                
                h1 {
                    font-size: 1.75rem;
                }
                
                .logo svg {
                    width: 100px;
                    height: 100px;
                }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">
                <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
                </svg>
            </div>
            <h1>Análisis SEO YouTube</h1>
            <form method="post">
                <input type="text" name="keyword" placeholder="Introduce una palabra clave" required>
                <input type="submit" value="Realizar Análisis SEO">
            </form>
            <a href="/" class="home-link">← Volver a la página principal</a>
        </div>
    </body>
    </html>
    ''')

@app.route('/report/<keyword>')
def generate_report(keyword):
    videos = search_videos(keyword, max_results=20)
    
    if videos:
        avg_views = sum(video['views'] for video in videos) / len(videos)
        avg_likes = sum(video['likes'] for video in videos) / len(videos)
        avg_comments = sum(video['comments'] for video in videos) / len(videos)
        avg_duration = calculate_average_duration(videos)
        unique_channels_count = count_unique_channels(videos)
        channel_stats = get_channel_stats(videos)
        last_6_months, last_year, older_than_year = categorize_videos_by_age(videos)
        total_stats = calculate_total_stats(videos)
    else:
        avg_views = avg_likes = avg_comments = 0
        avg_duration = timedelta()
        unique_channels_count = 0
        channel_stats = {}
        last_6_months = last_year = older_than_year = []
        total_stats = {'total_views': 0, 'total_likes': 0, 'total_comments': 0}

    # Cargar la plantilla como un string
    template_content = '''
<!DOCTYPE html>
<html lang="es">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Resultados de búsqueda para: {{ keyword }}</title>
    <style>
        :root {
            --primary-color: #FF0000;
            --secondary-color: #282828;
            --text-color: #333333;
            --background-color: #F9F9F9;
            --card-background: #FFFFFF;
            --shadow-color: rgba(0, 0, 0, 0.1);
            --sum-stats-background: #FFF5F5;
        }

        body {
            font-family: 'Roboto', Arial, sans-serif;
            line-height: 1.6;
            margin: 0;
            padding: 20px;
            background-color: var(--background-color);
        }

        .container {
            max-width: 1200px;
            margin: auto;
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 0 10px var(--shadow-color);
        }

        .section {
            margin-bottom: 40px;
            /* Increased white space between sections */
        }

        .section-title {
            display: flex;
            align-items: center;
            font-size: 1.8rem;
            color: var(--secondary-color);
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid var(--primary-color);
        }

        .section-title svg {
            width: 24px;
            height: 24px;
            margin-right: 10px;
            fill: var(--primary-color);
        }

        .search-result-header {
            background-color: var(--primary-color);
            color: white;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 30px;
            text-align: center;
        }

        .search-result-header h1 {
            margin: 0;
            font-size: 2.2rem;
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
        }

        .stat-box {
            background-color: var(--card-background);
            padding: 15px;
            border-radius: 8px;
            text-align: center;
            box-shadow: 0 2px 4px var(--shadow-color);
        }

        .sum-stat-box {
            background-color: var(--sum-stats-background);
        }

        .videos-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
        }

        .video-card {
            background-color: var(--card-background);
            border: 1px solid #ddd;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 4px var(--shadow-color);
        }

        .video-info {
            padding: 15px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 1rem;
        }

        th,
        td {
            border: 1px solid #ddd;
            padding: 12px;
            text-align
            text-align: left;
        }

        th {
            background-color: var(--secondary-color);
            color: white;
        }

        tr:nth-child(even) {
            background-color: #f2f2f2;
        }

        .channel-icon,
        .video-thumbnail {
            width: 50px;
            height: 50px;
            border-radius: 50%;
            object-fit: cover;
            margin-right: 10px;
        }

        .video-thumbnail {
            width: 120px;
            height: 68px;
            border-radius: 4px;
        }

        .flex-container {
            display: flex;
            align-items: center;
        }
        
        .home-link {
            display: inline-block;
            margin: 20px 0;
            color: #333;
            text-decoration: none;
            font-weight: bold;
        }
        
        .home-link:hover {
            text-decoration: underline;
        }
    </style>
</head>

<body>
    <div class="container">
        <div class="search-result-header">
            <h1>Resultados de búsqueda para: {{ keyword }}</h1>
        </div>
        
        <a href="/seo" class="home-link">← Volver al buscador</a>

        <div class="section">
            <h2 class="section-title">
                <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path
                        d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-1.6 14.8l-1.2-1.2c-1.1.9-2.5 1.4-4.2 1.4-3.6 0-6.5-2.9-6.5-6.5S8.4 5 12 5s6.5 2.9 6.5 6.5c0 1.7-.5 3.1-1.4 4.2l1.2 1.2c.4.4.4 1 0 1.4-.2.2-.5.3-.7.3-.3 0-.5-.1-.7-.3zM12 7c-2.8 0-5 2.2-5 5s2.2 5 5 5 5-2.2 5-5-2.2-5-5-5z" />
                </svg>
                Estadísticas de la Búsqueda
            </h2>
            <h3>Promedios</h3>
            <div class="stats-grid">
                <div class="stat-box">
                    <h4>Promedio de Visualizaciones</h4>
                    {{ avg_views_videos | format_number }}
                </div>
                <div class="stat-box">
                    <h4>Promedio de Me gustas</h4>
                    {{ avg_likes_videos | format_number }}
                </div>
                <div class="stat-box">
                    <h4>Promedio de Comentarios</h4>
                    {{ avg_comments_videos | format_number }}
                </div>
                <div class="stat-box">
                    <h4>Promedio de Duración</h4>
                    {{ avg_duration | format_duration }}
                </div>
            </div>
            <h3>Suma Total de Resultados</h3>
            <div class="stats-grid">
                <div class="stat-box sum-stat-box">
                    <h4>Número de Canales</h4>
                    {{ unique_channels_count }}
                </div>
                <div class="stat-box sum-stat-box">
                    <h4>Total de Visualizaciones</h4>
                    {{ total_stats.total_views | format_number }}
                </div>
                <div class="stat-box sum-stat-box">
                    <h4>Total de Me gustas</h4>
                    {{ total_stats.total_likes | format_number }}
                </div>
                <div class="stat-box sum-stat-box">
                    <h4>Total de Comentarios</h4>
                    {{ total_stats.total_comments | format_number }}
                </div>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">
                <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path d="M10 8v8l6-4-6-4zm11-5v18H3V3h18zm-1 1H4v16h16V4z" />
                </svg>
                Videos Encontrados
            </h2>
            <p>Número de videos: {{ videos|length }}</p>

            <div class="videos-grid">
                {% for video in videos %}
                <div class="video-card">
                    <img src="{{ video.thumbnail_url }}" alt="Miniatura del video" style="width:100%;">
                    <div class="video-info">
                        <h3><a href="{{ video.video_url }}" target="_blank">{{ video.title }}</a></h3>
                        <p><strong>Canal:</strong> {{ video.channel_title }}</p>
                        <p><strong>Visualizaciones:</strong> {{ video.views | format_number }}</p>
                        <p><strong>Me gustas:</strong> {{ video.likes | format_number }}</p>
                        <p><strong>Comentarios:</strong> {{ video.comments | format_number }}</p>
                        <p><strong>Duración:</strong> {{ video.duration | format_duration }}</p>
                        <p><strong>Publicado:</strong> {{ video.published_at | format_date }}</p>
                        <p><strong>Día de Publicación:</strong> {{ video.day_of_week }}</p>
                        <p><strong>Categoría:</strong> {{ video.category }}</p>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">
                <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path
                        d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm.31-8.86c-1.77-.45-2.34-.94-2.34-1.67 0-.84.79-1.43 2.1-1.43 1.38 0 1.9.66 1.94 1.64h1.71c-.05-1.34-.87-2.57-2.49-2.97V5H10.9v1.69c-1.51.32-2.72 1.3-2.72 2.81 0 1.79 1.49 2.69 3.66 3.21 1.95.46 2.34 1.15 2.34 1.87 0 .53-.39 1.39-2.1 1.39-1.6 0-2.23-.72-2.32-1.64H8.04c.1 1.7 1.36 2.66 2.86 2.97V19h2.34v-1.67c1.52-.29 2.72-1.16 2.73-2.77-.01-2.2-1.9-2.96-3.66-3.42z" />
                </svg>
                Canales y Estadísticas
            </h2>
            <table>
                <tr>
                    <th>Canal</th>
                    <th>Número de Videos</th>
                    <th>Total de Visualizaciones</th>
                    <th>Total de Me gustas</th>
                    <th>Total de Comentarios</th>
                </tr>
                {% for channel, stats in channel_stats.items() %}
                <tr>
                    <td>
                        <div class="flex-container">
                            <img src="{{ stats.thumbnail }}" alt="Icono del canal" class="channel-icon">
                            {{ channel }}
                        </div>
                    </td>
                    <td>{{ stats.videos }}</td>
                    <td>{{ stats.views | format_number }}</td>
                    <td>{{ stats.likes | format_number }}</td>
                    <td>{{ stats.comments | format_number }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>

        <div class="section">
            <h2 class="section-title">
                <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path
                        d="M19 3h-1V1h-2v2H8V1H6v2H5c-1.11 0-1.99.9-1.99 2L3 19c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V8h14v11zM7 10h5v5H7z" />
                </svg>
                Videos por Antigüedad
            </h2>

            <h3>Videos de los últimos 6 meses</h3>
            <table>
                <tr>
                    <th>Video</th>
                    <th>Visualizaciones</th>
                </tr>
                {% for video in last_6_months %}
                <tr>
                    <td>
                        <div class="flex-container">
                            <img src="{{ video.thumbnail_url }}" alt="Miniatura del video" class="video-thumbnail">
                            <a href="{{ video.video_url }}" target="_blank">{{ video.title }}</a>
                        </div>
                    </td>
                    <td>{{ video.views | format_number }}</td>
                </tr>
                {% endfor %}
            </table>

            <h3>Videos del último año (excluyendo los últimos 6 meses)</h3>
            <table>
                <tr>
                    <th>Video</th>
                    <th>Visualizaciones</th>
                </tr>
                {% for video in last_year %}
                <tr>
                    <td>
                        <div class="flex-container">
                            <img src="{{ video.thumbnail_url }}" alt="Miniatura del video" class="video-thumbnail">
                            <a href="{{ video.video_url }}" target="_blank">{{ video.title }}</a>
                        </div>
                    </td>
                    <td>{{ video.views | format_number }}</td>
                </tr>
                {% endfor %}
            </table>

            <h3>Videos de más de un año de antigüedad</h3>
            <table>
                <tr>
                    <th>Video</th>
                    <th>Visualizaciones</th>
                </tr>
                {% for video in older_than_year %}
                <tr>
                    <td>
                        <div class="flex-container">
                            <img src="{{ video.thumbnail_url }}" alt="Miniatura del video" class="video-thumbnail">
                            <a href="{{ video.video_url }}" target="_blank">{{ video.title }}</a>
                        </div>
                    </td>
                    <td>{{ video.views | format_number }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
        
        <a href="/seo" class="home-link">← Volver al buscador</a>
    </div>
</body>

</html>
    '''
    
    # Configurar filtros para el template
    template = Template(template_content)
    template.environment = Environment()
    template.environment.filters['format_number'] = format_number
    template.environment.filters['format_date'] = format_date
    template.environment.filters['format_duration'] = format_duration
    
    data = {
        'keyword': keyword,
        'videos': videos,
        'avg_views_videos': avg_views,
        'avg_likes_videos': avg_likes,
        'avg_comments_videos': avg_comments,
        'avg_duration': avg_duration,
        'unique_channels_count': unique_channels_count,
        'channel_stats': channel_stats,
        'last_6_months': last_6_months,
        'last_year': last_year,
        'older_than_year': older_than_year,
        'total_stats': total_stats
    }
    
    return template.render(**data)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))  # Usar el puerto asignado por Render o 8080 por defecto
    app.run(host='0.0.0.0', port=port)