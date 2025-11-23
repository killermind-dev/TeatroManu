from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit
import json
import os
import time
from datetime import datetime
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = 'holamanumundo' 
app.config['STATIC_FOLDER'] = 'static'
socketio = SocketIO(app, cors_allowed_origins="*")

# =================================================================
# CONFIGURACIÓN DE SEGURIDAD
# =================================================================
 

# Variables globales
current_poll = None
poll_results = {}
admin_logged_in = False
poll_active = False
poll_timer = 30
voted_ips = set() 
admin_sids = set()
poll_start_time = None 
eliminated_participants = set() # Almacena los nombres de los participantes eliminados

# Cargar datos de JSON
def load_json(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None

def save_json(filename, data):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ----------------------------------------------------------------
# FUNCIONES AUXILIARES
# ----------------------------------------------------------------

def get_image_url(participant_name):
    # Estandariza el nombre (minúsculas, sin espacios/tildes)
    filename_base = participant_name.lower().replace(' ', '').replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
    filename = f"{filename_base}.jpg"
    filepath = os.path.join(app.static_folder, 'images', filename)

    if os.path.exists(filepath):
        return url_for('static', filename=f'images/{filename}')
    else:
        # Devuelve una URL por defecto si la imagen no existe
        return url_for('static', filename='images/placeholder.jpg') 

def get_poll_options_with_images(names):
    options_with_images = []
    for name in names:
        options_with_images.append({
            'name': name,
            'image_url': get_image_url(name)
        })
    return options_with_images

# ----------------------------------------------------------------
# DECORADORES
# ----------------------------------------------------------------

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function


# ----------------------------------------------------------------
# RUTAS DE FLASK
# ----------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html') 

@app.route('/display')
def display():
    return render_template('display.html')

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    global admin_logged_in
    if request.method == 'POST':
        password = request.json.get('password')
        admin_data = load_json('admin.json')
        if admin_data and password == admin_data.get('password'):
            if admin_logged_in and not session.get('admin'):
                   return jsonify({'success': False, 'message': 'Ya hay un administrador conectado'})
            session['admin'] = True
            admin_logged_in = True
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': 'Contraseña incorrecta'})
    return render_template('admin_login.html')

@app.route('/admin/panel')
@admin_required
def admin_panel():
    return render_template('admin_panel.html')

@app.route('/admin/logout')
def admin_logout():
    global admin_logged_in
    if session.get('admin'):
        session.pop('admin', None)
        admin_logged_in = False
    return redirect(url_for('admin_login'))

@app.route('/api/config')
def get_config():
    questions_data = load_json('questions.json')
    
    if questions_data:
        # Añadimos la lista de eliminados al config
        questions_data['eliminated_participants'] = list(eliminated_participants)
        
        all_participants = questions_data.get('participants', [])
        
        # AÑADIMOS LA DATA DE TODOS LOS PARTICIPANTES (CON IMAGEN) AL JSON DE CONFIG
        questions_data['all_participants_data'] = get_poll_options_with_images(all_participants)
        
    return jsonify(questions_data if questions_data else {})

@app.route('/api/start_poll', methods=['POST'])
@ip_allowed_required
@admin_required
def start_poll():
    global current_poll, poll_results, poll_active, poll_timer, voted_ips, poll_start_time, eliminated_participants
    
    voted_ips = set() 
    data = request.json
    poll_type = data.get('type')
    poll_index = data.get('index')
    timer = data.get('timer', 30)
    
    poll_timer = timer
    poll_start_time = time.time() 
    
    questions_data = load_json('questions.json')
    
    if poll_type == 'elimination':
        
        all_participants = questions_data.get('participants', [])
        active_participants = [p for p in all_participants if p not in eliminated_participants]
        options = get_poll_options_with_images(active_participants)
        num_active = len(active_participants)
        
        if num_active < 1:
             return jsonify({'success': False, 'message': 'No hay suficientes participantes activos para votar.'})

        # --- Lógica de Rondas ---
        if poll_index == 0:
            # Ronda 1: Votación de ELIMINACIÓN (El más votado sale)
            current_poll = {
                'type': 'elimination',
                'round': 1,
                'question': 'VOTA POR EL PARTICIPANTE QUE SERÁ **ELIMINADO**',
                'options': options
            }
        elif poll_index == 1:
            # Ronda 2: Votación de SÓTANO (El más votado se queda solo)
            current_poll = {
                'type': 'elimination',
                'round': 2,
                'question': 'VOTA POR EL PERSONAJE QUE SE QUEDA **SOLO EN EL SÓTANO**', 
                'options': options 
            }
        else:
             return jsonify({'success': False, 'message': 'Índice de ronda inválido.'})


        poll_results = {option['name']: 0 for option in current_poll['options']}
        
    elif poll_type == 'question':
        if 0 <= poll_index < len(questions_data.get('questions', [])):
            question = questions_data['questions'][poll_index]
            current_poll = {
                'type': 'question',
                'question': question['question'],
                'options': question['options'] 
            }
            poll_results = {option: 0 for option in current_poll['options']}
        else:
            return jsonify({'success': False, 'message': 'Índice de pregunta inválido.'})
    else:
        return jsonify({'success': False, 'message': 'Tipo de encuesta inválido.'})
    
    poll_active = True
    
    socketio.emit('new_poll', {
        'poll': current_poll,
        'timer': poll_timer,
        'start_time': poll_start_time
    })
    
    return jsonify({'success': True, 'poll': current_poll})


@app.route('/api/stop_poll', methods=['POST'])
@admin_required
def stop_poll():
    global poll_active, poll_start_time, eliminated_participants, poll_results, current_poll
    
    if current_poll and current_poll.get('type') == 'elimination' and poll_results:
        
        current_round = current_poll.get('round')
        
        # Lógica de eliminación: SOLO se aplica si es la ronda 1
        if current_round == 1:
            if poll_results.values():
                max_votes = max(poll_results.values())
                
                if max_votes > 0:
                    # Empate: Recopilar todos los nombres que tienen el máximo de votos
                    impacted_names = [name for name, votes in poll_results.items() if votes == max_votes]

                    if impacted_names:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] ELIMINACIÓN: {impacted_names}")
                        
                        # Añadir a TODOS los impactados al set de eliminados
                        for name in impacted_names:
                            eliminated_participants.add(name)
        
    poll_active = False
    poll_start_time = None
    
    # Emitir el evento con los resultados finales de esta votación
    socketio.emit('poll_ended', poll_results)
    
    for sid in admin_sids:
        socketio.emit('results_update', poll_results, room=sid)
        
    return jsonify({'success': True, 'results': poll_results})


@app.route('/api/declare_sotano', methods=['POST'])
@admin_required
def declare_sotano():
    global poll_active, poll_start_time, eliminated_participants, poll_results, current_poll
    
    if not poll_results:
        return jsonify({'success': False, 'message': 'No hay resultados para declarar el Sótano.'}), 400

    poll_active = False
    poll_start_time = None
    
    current_participants = set(poll_results.keys())
    sotano_participants = set()
    winner_participants = set()
    
    if poll_results.values():
        max_votes = max(poll_results.values())
        
        # 1. Los más votados son los que se quedan en el SÓTANO (Perdedores Finales)
        sotano_participants = {name for name, votes in poll_results.items() if votes == max_votes and max_votes > 0}
        
        # 2. Los ganadores son TODOS los que NO están en el sótano
        winner_participants = current_participants - sotano_participants

        if sotano_participants:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] SÓTANO: {list(sotano_participants)}")
            
            # Marcar a los que se quedaron en el SÓTANO como ELIMINADOS
            eliminated_participants.update(sotano_participants)
        
    
    # Emitir el evento con los resultados finales de esta votación
    socketio.emit('poll_ended', poll_results) 
    
    for sid in admin_sids:
        socketio.emit('results_update', poll_results, room=sid)
        
    return jsonify({
        'success': True, 
        'message': '¡Sótano Declarado! Los demás son los ganadores.', 
        'sotano_perdedores': list(sotano_participants),
        'ganadores': list(winner_participants)
    })


@app.route('/api/current_poll')
def get_current_poll():
    global current_poll, poll_active, poll_timer, poll_start_time
    
    if current_poll and poll_active and poll_start_time is not None:
        time_elapsed = time.time() - poll_start_time
        time_left = max(0, int(poll_timer - time_elapsed))
        
        if time_left == 0:
            return jsonify({'active': False})
            
        return jsonify({
            'active': True,
            'poll': current_poll,
            'timer': time_left
        })
        
    return jsonify({'active': False})

# ----------------------------------------------------------------
# EVENTOS DE SOCKETIO (Se mantienen igual)
# ----------------------------------------------------------------

@socketio.on('connect')
def handle_connect():
    if session.get('admin'):
        admin_sids.add(request.sid)
        emit('is_admin', {'status': True})
        if poll_active:
            emit('results_update', poll_results)
    else:
        emit('is_admin', {'status': False})

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in admin_sids:
        admin_sids.remove(request.sid)

# --- EN app.py ---

# ... (código anterior) ...

@socketio.on('vote')
def handle_vote(data):
    global poll_results, voted_ips
    
    if not poll_active:
        emit('vote_error', {'message': 'La encuesta no está activa.'})
        return

    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
    
    if client_ip in voted_ips:
        emit('already_voted', {'message': 'Ya has votado en esta encuesta desde esta IP.'})
        return
    
    option = data.get('option')
    if option in poll_results:
        poll_results[option] += 1
        voted_ips.add(client_ip)
        
        emit('vote_confirmed', {'message': '¡Voto registrado con éxito!'})
        
        # 1. Emitir actualización para el panel de administración
        for sid in admin_sids:
            socketio.emit('results_update', poll_results, room=sid)

        # 2. NUEVO: Emitir actualización de votos en tiempo real para el DISPLAY
        socketio.emit('live_votes_update', poll_results)

    else:
        emit('vote_error', {'message': 'Opción de voto inválida.'})

# ... (código posterior) ...
@socketio.on('request_results')
def handle_results_request():
    if session.get('admin'):
        emit('results_update', poll_results)

# ----------------------------------------------------------------
# EJECUCIÓN (Se mantiene igual)
# ----------------------------------------------------------------

if __name__ == '__main__':
    if not os.path.exists('admin.json'):
        save_json('admin.json', {'password': 'admin123'}) 
    
    if not os.path.exists('questions.json'):
        save_json('questions.json', {
            'participants': ['Participante 1', 'Participante 2', 'Participante 3', 'Participante 4'],
            'questions': [
                {
                    'question': '¿Cuál es tu color favorito?',
                    'options': ['Rojo', 'Azul', 'Verde', 'Amarillo']
                },
                {
                    'question': '¿Cuál es tu comida favorita?',
                    'options': ['Pizza', 'Hamburguesa', 'Sushi', 'Tacos']
                }
            ]
        })
    
    # Crea la carpeta de imágenes si no existe (Asegúrate de poner tus imágenes aquí)
    os.makedirs(os.path.join('static', 'images'), exist_ok=True)
    
